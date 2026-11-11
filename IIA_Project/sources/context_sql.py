"""CONTEXT-ADDED (R2): generate the ONE scored `WITH RECURSIVE` statement a source runs for POST /context.

Idea: a size-l object summary / Precis-style weighted schema graph, evaluated *inside* the DBMS.
The source's FK graph (data/context_graph.json) is the schema graph; edge weight
w(e) = min(1, w0 / (1 + ln avg fan-out)) (or an expert override), so 1:1 edges keep affinity and
high fan-out edges (owner -> every capture at a camera) decay it fast.

Statement shape (identical in SQLite, PostgreSQL, MySQL 8; only scalar functions differ):
  step   : UNION ALL of every FK edge in both directions -> (src_t, src_pk, dst_t, dst_pk, w)
  seed   : rows matching seed plates / chassis (canonicalised) / pks, each with a starting weight
  walk   : RECURSIVE over step; affinity *= w * gamma; stop below theta or at max_hops; cycle guard
           = the path string '>T#pk>T#pk>' must not already contain the next node
  node   : best path per table#pk (max affinity)          -- window functions only OUTSIDE the recursion
  scored : relevance per row from its own columns (keywords, time window, decision boosts), clip [0,1]
  ranked : score = affinity x relevance; per-parent ROW_NUMBER caps diversification
  elig   : global ROW_NUMBER over rows that survive the per-parent cap
  output : kind='row' for the top n; kind='hint' = bridge columns only of every other theta-closure row
           (one per bridge value, best affinity), so the mediator can still follow them;
           u_cut = (n+1)-th eligible score, u_div = best row cut by the per-parent cap,
           u_pruned = best affinity cut by theta / max_hops, u_hint = best hint beyond the hint cap.
Every name comes from the graph and is validated as an identifier; every user value is a bind
parameter. Numbers (weights, n, theta) are inlined after float()/int() so the SQL shown is readable.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sources.context_dialect import IDENT, Dialect, ident, pk_cols, pk_expr  # noqa: F401  (re-exported)

REL_DEFAULTS = {"rel_base": 0.3, "rel_keyword": 0.4, "rel_time": 0.3}
CLOSURE_CAP = 50_000
HINT_CAP = 500
_OPS = {"=", "!=", "<>", "<", "<=", ">", ">="}

def edge_weight(explicit: Optional[float], fanout: float, w0: float) -> float:
    """w0 / (1 + ln avg fan-out), clamped to [0, 1] (fan-out < 1 would otherwise exceed 1)."""
    if explicit is not None:
        return max(0.0, min(1.0, float(explicit)))
    return max(0.0, min(1.0, w0 / (1.0 + math.log(max(1.0, float(fanout))))))


def fanout_sql(edges: List[Dict[str, Any]]) -> str:
    """One cheap statement measuring both directions' average fan-out of every edge (statistics, not data)."""
    cols = []
    for i, e in enumerate(edges):
        f, c, t, tc = ident(e["from"]), ident(e["col"]), ident(e["to"]), ident(e["to_col"])
        cols += [f"(SELECT COUNT({c}) FROM {f}) AS n{i}", f"(SELECT COUNT(DISTINCT {c}) FROM {f}) AS d{i}",
                 f"(SELECT COUNT({tc}) FROM {t}) AS tn{i}", f"(SELECT COUNT(DISTINCT {tc}) FROM {t}) AS td{i}"]
    return "SELECT " + ", ".join(cols) if cols else "SELECT 1 AS none"


def weights_from_counts(edges: List[Dict[str, Any]], counts: Dict[str, Any], w0: float) -> List[Tuple[float, float]]:
    """(forward child->parent, reverse parent->children) weight per edge."""
    out = []
    for i, e in enumerate(edges):
        rev = (counts.get(f"n{i}") or 0) / max(1, counts.get(f"d{i}") or 0)
        fwd = (counts.get(f"tn{i}") or 0) / max(1, counts.get(f"td{i}") or 0)
        out.append((edge_weight(e.get("w"), fwd, w0), edge_weight(e.get("w"), rev, w0)))
    return out


class _Binds:
    def __init__(self) -> None:
        self.params: Dict[str, Any] = {}

    def add(self, value: Any) -> str:
        name = f"b{len(self.params)}"
        self.params[name] = value
        return f":{name}"

    def many(self, values: Iterable[Any]) -> str:
        return ", ".join(self.add(v) for v in values)


def _lit(name: str) -> str:
    return "'" + ident(name) + "'"


def _num(x: float) -> str:
    return f"{float(x):.6f}"


def relevance_expr(d: Dialect, spec: Dict[str, Any], alias: str, req: Dict[str, Any], rel: Dict[str, float],
                   b: _Binds) -> str:
    terms = [_num(rel["rel_base"])]
    kws = [k.lower() for k in req.get("keywords") or [] if k]
    text_cols = [ident(c) for c in spec.get("text", [])]
    if kws and text_cols:
        pieces: List[str] = []
        for i, c in enumerate(text_cols):
            pieces += ([] if i == 0 else ["' '"]) + [f"COALESCE({d.txt(f'{alias}.{c}')}, '')"]
        blob = f"LOWER({d.cat(*pieces) if len(pieces) > 1 else pieces[0]})"
        hits = " + ".join(f"CASE WHEN {d.pos(blob, b.add(k))} > 0 THEN 1 ELSE 0 END" for k in kws)
        terms.append(f"{_num(rel['rel_keyword'] / len(kws))} * ({hits})")
    window, tm = req.get("window"), spec.get("time")
    if window and tm:
        day = d.day(f"{alias}.{ident(tm['col'])}", tm["fmt"])
        lo, hi = d.day_param(b.add(window[0])), d.day_param(b.add(window[1]))
        terms.append(f"CASE WHEN {day} BETWEEN {lo} AND {hi} THEN {_num(rel['rel_time'])} ELSE 0 END")
    for dec in spec.get("decision", []):
        if dec["op"] not in _OPS:
            raise ValueError(f"unsupported decision operator {dec['op']!r}")
        terms.append(f"CASE WHEN {alias}.{ident(dec['col'])} {dec['op']} {b.add(dec['value'])} "
                     f"THEN {_num(dec['boost'])} ELSE 0 END")
    return d.dbl(d.least("1", d.greatest("0", " + ".join(terms))))


def _seed_branches(d: Dialect, tables: Dict[str, Any], req: Dict[str, Any], b: _Binds) -> List[str]:
    seeds, out = req.get("seeds") or {}, []
    weights = {k: float(v) for k, v in (seeds.get("weights") or {}).items()}

    for kind in ("plate", "chassis"):
        vals = ["".join(ch for ch in str(v) if ch.isalnum()).upper() for v in seeds.get(kind) or []]
        for name, spec in tables.items():
            if spec.get(kind) and vals:
                col = d.canon(f"x.{ident(spec[kind])}")
                for w, vs in _groups(f"{kind}:", vals, weights).items():
                    out.append(f"SELECT {_lit(name)}, {pk_expr(d, spec, 'x')}, {_num(w)}, "
                               f"{d.cat(repr(kind + ':'), col)} FROM {name} x WHERE {col} IN ({b.many(vs)})")
    by_table: Dict[str, List[str]] = {}
    for group in ("pks", "person_ids"):
        for t, vals in (seeds.get(group) or {}).items():
            if t in tables:
                by_table.setdefault(t, []).extend(str(v) for v in vals)
    for t, vals in by_table.items():
        pk = pk_expr(d, tables[t], "x")
        for w, vs in _groups(f"pk:{t}#", vals, weights).items():
            out.append(f"SELECT {_lit(t)}, {pk}, {_num(w)}, {d.cat(repr('pk:' + t + '#'), pk)} "
                       f"FROM {t} x WHERE {pk} IN ({b.many(vs)})")
    if not out and req.get("keywords"):  # free-text query with no entity: rows matching a keyword seed it
        kws = [k.lower() for k in req["keywords"] if k]
        for name, spec in tables.items():
            if spec.get("text"):
                cols = [f"COALESCE({d.txt('x.' + ident(c))}, '')" for c in spec["text"]]
                blob = f"LOWER({d.cat(*cols) if len(cols) > 1 else cols[0]})"
                cond = " OR ".join(f"{d.pos(blob, b.add(k))} > 0" for k in kws)
                out.append(f"SELECT * FROM (SELECT {_lit(name)} AS t, {pk_expr(d, spec, 'x')} AS pk, 1.0 AS w, "
                           f"'kw:' AS skey FROM {name} x WHERE {cond} LIMIT 50) ks_{name}")
    return out


def _groups(prefix: str, vals: Iterable[str], weights: Dict[str, float]) -> Dict[float, List[str]]:
    """Seed values grouped by starting weight, so each group is one `IN (...)` branch with a literal weight."""
    g: Dict[float, List[str]] = {}
    for v in sorted(set(vals)):
        g.setdefault(round(weights.get(f"{prefix}{v}", 1.0), 6), []).append(v)
    return g


def build_context_sql(graph: Dict[str, Any], source_id: str, dialect: str, req: Dict[str, Any],
                      weights: List[Tuple[float, float]], tables_present: Optional[Iterable[str]] = None
                      ) -> Tuple[str, Dict[str, Any]]:
    """Return (sql, bind params). `weights` = (forward, reverse) per edge of the *filtered* edge list."""
    d, b = Dialect(dialect), _Binds()
    tables, edges = usable_graph(graph, source_id, tables_present)
    params = {**REL_DEFAULTS, **graph.get("params", {})}
    closure = req.get("mode") == "closure"
    opt = lambda k, default: req.get(k) if req.get(k) is not None else params.get(k, default)  # noqa: E731
    theta = 0.0 if closure else max(0.0, float(opt("theta", 0.05)))
    n = CLOSURE_CAP if closure else max(1, int(opt("n", 10)))
    per_parent = 2**31 - 1 if closure else max(1, int(opt("per_parent", 3)))
    max_hops = max(0, int(opt("max_hops", 3)))
    gamma = float(params.get("gamma_hops", 1.0))
    hint_cap = max(0, int(opt("max_hints", HINT_CAP)))
    hint_min = max(0.0, float(req.get("hint_min") or 0.0))  # below this a hint cannot beat the mediator's tau_n
    seeds = _seed_branches(d, tables, req, b)
    if not seeds:
        return "", {}
    steps = []
    for e, (wf, wr) in zip(edges, weights):
        f, t, ft, tt = e["from"], e["to"], tables[e["from"]], tables[e["to"]]
        join = f"FROM {f} s JOIN {t} o ON o.{ident(e['to_col'])} = s.{ident(e['col'])}"
        steps.append(f"SELECT {_lit(f)}, {pk_expr(d, ft, 's')}, {_lit(t)}, {pk_expr(d, tt, 'o')}, {_num(wf)} {join}")
        steps.append(f"SELECT {_lit(t)}, {pk_expr(d, tt, 'o')}, {_lit(f)}, {pk_expr(d, ft, 's')}, {_num(wr)} {join}")
    if not steps:  # a source with one table: keep the CTE well-formed, it simply never matches
        steps.append(f"SELECT 'none', 'none', 'none', 'none', 0.0 FROM {next(iter(tables))} s WHERE 1 = 0")
    node_id = lambda tt, pp: d.cat(tt, "'#'", pp)  # noqa: E731
    step_aff = f"w.aff * e.w * {_num(gamma)}"
    scored, hint_cols = [], _bridge_cols(graph, source_id, tables)
    for name, spec in tables.items():
        battr, bcol = hint_cols.get(name, (None, None))
        bval = (d.canon(f"x.{bcol}") if battr in ("plate", "chassis") else d.txt(f"x.{bcol}")) if bcol else "NULL"
        scored.append(f"SELECT n.t, n.pk, n.path, n.parent, n.hops, n.aff, n.skey, "
                      f"{relevance_expr(d, spec, 'x', req, params, b)} AS rel, "
                      f"{_lit(battr) if battr else 'NULL'} AS battr, {bval} AS bval "
                      f"FROM node n JOIN {name} x ON n.t = {_lit(name)} AND {pk_expr(d, spec, 'x')} = n.pk")
    sql = f"""WITH RECURSIVE
step (src_t, src_pk, dst_t, dst_pk, w) AS (
  {chr(10).join(('  UNION ALL ' if i else '') + s for i, s in enumerate(steps))}
),
seed (t, pk, w, skey) AS (
  {chr(10).join(('  UNION ALL ' if i else '') + s for i, s in enumerate(seeds))}
),
walk (t, pk, path, parent, hops, aff, skey) AS (
  SELECT {d.txt('t', 64)}, {d.txt('pk', 255)}, {d.txt(d.cat("'>'", node_id('t', 'pk'), "'>'"), 4000)},
         {d.txt(d.cat("'^'", node_id('t', 'pk')), 320)}, 0, {d.dbl('w')}, {d.txt('skey', 300)} FROM seed
  UNION ALL
  SELECT e.dst_t, e.dst_pk, {d.cat('w.path', node_id('e.dst_t', 'e.dst_pk'), "'>'")}, {node_id('w.t', 'w.pk')},
         w.hops + 1, {step_aff}, w.skey
  FROM walk w JOIN step e ON e.src_t = w.t AND e.src_pk = w.pk
  WHERE w.hops < {max_hops} AND {step_aff} >= {_num(theta)}
    AND {d.pos('w.path', d.cat("'>'", node_id('e.dst_t', 'e.dst_pk'), "'>'"))} = 0
),
best AS (
  SELECT t, pk, path, parent, hops, aff, skey,
         ROW_NUMBER() OVER (PARTITION BY t, pk ORDER BY aff DESC, hops, path) AS rn_node FROM walk
),
node AS (SELECT t, pk, path, parent, hops, aff, skey FROM best WHERE rn_node = 1),
scored AS (
  {chr(10).join(('  UNION ALL ' if i else '') + s for i, s in enumerate(scored))}
),
ranked AS (
  SELECT s.*, s.aff * s.rel AS score,
         ROW_NUMBER() OVER (PARTITION BY s.parent ORDER BY s.aff * s.rel DESC, s.t, s.pk) AS rn_parent
  FROM scored s
),
elig AS (
  SELECT r.*, ROW_NUMBER() OVER (ORDER BY r.score DESC, r.t, r.pk) AS rn FROM ranked r WHERE r.rn_parent <= {per_parent}
),
hint AS (
  SELECT r.t, r.pk, r.hops, r.aff, r.skey, r.battr, r.bval,
         ROW_NUMBER() OVER (PARTITION BY r.battr, r.bval ORDER BY r.aff DESC, r.t, r.pk) AS rn_val
  FROM ranked r LEFT JOIN elig e ON e.t = r.t AND e.pk = r.pk AND e.rn <= {n}
  WHERE e.t IS NULL AND r.battr IS NOT NULL AND r.bval IS NOT NULL AND r.aff >= {_num(hint_min)}
),
hint_top AS (
  SELECT h.*, ROW_NUMBER() OVER (ORDER BY h.aff DESC, h.t, h.pk) AS rn_h FROM hint h WHERE h.rn_val = 1
)
SELECT 'row' AS kind, e.t, e.pk, e.path, e.hops, e.aff, e.score, e.skey, e.battr, e.bval,
       (SELECT MAX(score) FROM elig WHERE rn = {n} + 1) AS u_cut,
       (SELECT MAX({step_aff}) FROM walk w JOIN step e ON e.src_t = w.t AND e.src_pk = w.pk
         WHERE w.hops >= {max_hops} OR {step_aff} < {_num(theta)}) AS u_pruned,
       (SELECT MAX(aff) FROM hint_top WHERE rn_h > {hint_cap}) AS u_hint,
       (SELECT MAX(score) FROM ranked WHERE rn_parent > {per_parent}) AS u_div
FROM elig e WHERE e.rn <= {n}
UNION ALL
SELECT 'hint', h.t, h.pk, NULL, h.hops, h.aff, NULL, h.skey, h.battr, h.bval, NULL, NULL, NULL, NULL
FROM hint_top h WHERE h.rn_h <= {hint_cap}"""
    # CONTEXT-ADDED (R2: count_only -- how many rows the full closure WOULD ship, plus its distinct bridge
    # values, without shipping those rows: lets bounded mode report the B0 baseline at the cost of integers/values)
    if req.get("count_only"):
        sql = sql[: sql.index("SELECT 'row' AS kind")] + """
SELECT 'count' AS kind, NULL AS battr, NULL AS bval, (SELECT COUNT(*) FROM node) AS n_rows
UNION ALL
SELECT DISTINCT 'value', s.battr, s.bval, NULL FROM scored s WHERE s.battr IS NOT NULL AND s.bval IS NOT NULL"""
    # CONTEXT-END
    return sql, b.params


def usable_graph(graph: Dict[str, Any], source_id: str, present: Optional[Iterable[str]] = None
                 ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """The source's tables/edges, minus anything the DB does not have (or the wrapper does not publish)."""
    src = graph["sources"][source_id]
    keep = None if present is None else {p.lower() for p in present}
    tables = {ident(t): s for t, s in src["tables"].items() if keep is None or t.lower() in keep}
    edges = [e for e in src.get("edges", []) if e["from"] in tables and e["to"] in tables]
    return tables, edges


def _bridge_cols(graph: Dict[str, Any], source_id: str, tables: Dict[str, Any]) -> Dict[str, Tuple[str, str]]:
    out: Dict[str, Tuple[str, str]] = {}
    for bname, bridge in graph.get("bridges", {}).items():
        for sid, table, col in bridge.get("members", []):
            if sid == source_id and table in tables and table not in out:
                out[table] = (ident(bname), ident(col))
    return out
