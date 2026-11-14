"""CONTEXT-ADDED (R2/R3): what the Context Explorer runs for one question, with no Streamlit in it.

parse → (plate) discover + indicators + documents + grounded summary, or (no plate) entity top-k.
Each backend module is imported on its own at call time: a module another workstream has not
delivered yet, or one that fails, removes only its own part of the page and is listed in
`missing` / `problems`; the rest still renders (a source failure degrades, never crashes).
Results are computed fresh for every question; the page keeps only the latest one so a widget
click does not re-query, and asking again always reads live data.
"""
from __future__ import annotations

import importlib
import json
import re
import time
# CONTEXT-ADDED (R2/R3: graph metadata is read once per process; it is schema, not source data)
from functools import lru_cache
# CONTEXT-END
from typing import Any, Callable, Dict, List, Optional

N_PER_SOURCE = 10      # each database's budget: its best 10 rows
THETA = 0.05           # stop following a path once its affinity falls below this
PLATE_RE = re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{3,4}$")


def module(name: str, missing: List[str]) -> Optional[Any]:
    try:
        return importlib.import_module(name)
    except ImportError:
        missing.append(name.split(".")[-1])
        return None


def _step(problems: List[str], label: str, fn: Callable[[], Any]) -> Any:
    try:
        return fn()
    except Exception as exc:  # one failing part must not take the page down
        problems.append(f"{label}: {type(exc).__name__}: {str(exc)[:160]}")
        return None


def fallback_parse(text: str) -> Any:
    """Without the query-understanding module, a plate is still a plate (canonical form)."""
    from mediator.context_types import ParsedQuery  # noqa: PLC0415

    canon = re.sub(r"[^A-Z0-9]", "", text.upper())
    return ParsedQuery(text=text, plates=[canon] if PLATE_RE.match(canon) else [],
                       keywords=[] if PLATE_RE.match(canon) else text.split(), notes=["plate rule only"])


def run_query(text: str, use_llm: bool = True) -> Dict[str, Any]:
    missing: List[str] = []
    problems: List[str] = []
    t0 = time.perf_counter()
    out: Dict[str, Any] = {"query": text, "missing": missing, "problems": problems}
    qu = module("mediator.query_understanding", missing)
    parsed = _step(problems, "understanding the question", lambda: qu.parse(text, use_llm=use_llm)) if qu else None
    out["parsed"] = parsed or fallback_parse(text)
    plates = list(getattr(out["parsed"], "plates", []) or [])
    out["plate"] = plates[0] if plates else None
    if plates:
        cd = module("mediator.context_discovery", missing)
        # CONTEXT-REPLACED (R2: the baseline is now counted live inside each DBMS (count_only /context) instead of
        # quoted from the last evaluation run; costs ~2 s of database work but ships integers, not the rows):
        # ctx = _step(problems, "context discovery", lambda: cd.discover(
        #     out["parsed"], n=N_PER_SOURCE, theta=THETA, mode="bounded")) if cd else None
        ctx = _step(problems, "context discovery", lambda: cd.discover(
            out["parsed"], n=N_PER_SOURCE, theta=THETA, mode="bounded", with_baseline=True)) if cd else None
        # CONTEXT-END
        out["context"] = ctx
        an = module("mediator.analytics", missing)
        out["indicators"] = (_step(problems, "warning signs", lambda: an.indicators_for(plates[0], context=ctx))
                             if an else None)
        dr = module("mediator.doc_retrieval", missing)
        # CONTEXT-REPLACED (R3: show 8, and put documents that contradict a database record first -- a contradiction
        # can change the verdict, so it should not sit just below the fold; order is otherwise BM25 score):
        # docs = _step(problems, "documents", lambda: dr.retrieve(ctx, k=5, mode="context+anchors")) if dr and ctx else None
        docs = _step(problems, "documents", lambda: dr.retrieve(ctx, k=8, mode="context+anchors")) if dr and ctx else None
        if docs is not None and getattr(docs, "docs", None):
            docs.docs.sort(key=lambda d: (not d.conflicts, -d.score))
        # CONTEXT-END
        out["docs"] = docs
        gs = module("mediator.grounded_summary", missing)
        out["summary"] = (_step(problems, "summary", lambda: gs.summarise(ctx, docs, use_llm=use_llm))
                          if gs and ctx is not None and docs is not None else None)
    else:
        tk = module("mediator.entity_topk", missing)
        out["topk"] = _step(problems, "ranking vehicles", lambda: tk.topk(out["parsed"], k=10)) if tk else None
    out["elapsed_ms"] = (time.perf_counter() - t0) * 1000
    return out


def table_sources() -> Dict[str, str]:
    """TABLE -> source, from data/context_graph.json (for colouring path elements of other sources)."""
    try:
        from mediator.context_eval_serve import load_graph  # noqa: PLC0415
        return {t: sid for sid, src in load_graph().get("sources", {}).items() for t in src.get("tables", {})}
    except Exception:
        return {}


def measured_baseline(plate: Optional[str]) -> Optional[int]:
    """Rows the copy-everything baseline (B0) shipped for this plate in the last evaluation run, if measured.

    Used only when discover() gives no baseline itself; running B0 live on every search would ship the very
    data this page argues against moving, so the page quotes the measurement and says where it came from.
    """
    try:
        from mediator.context_eval_serve import ROOT  # noqa: PLC0415
        data = json.loads((ROOT / "data" / "context_eval.json").read_text(encoding="utf-8"))
        return next(int(m["rows"]) for m in data.get("movement", []) if m.get("query_id") == plate
                    and m.get("mode") == "closure" and m.get("scale") == "x1")
    except Exception:
        return None


def moved(ctx: Any, plate: Optional[str] = None) -> Dict[str, Any]:
    """Totals of what crossed the network for this answer, and the bounded-expansion pruning count."""
    stats = list(getattr(ctx, "stats", []) or [])
    trace = [t for t in getattr(ctx, "trace", None) or [] if not t.get("final")]  # last entry is a summary
    pruned = sum(int(t.get("pruned", 0) or 0) for t in trace)
    considered = pruned + sum(int(t.get("expanded", 0) or 0) for t in trace)
    baseline, from_eval = getattr(ctx, "baseline_rows", None), False
    if not baseline:
        baseline = measured_baseline(plate)
        from_eval = baseline is not None
    return {"rows": sum(s.rows_shipped for s in stats), "bytes": sum(s.bytes_shipped for s in stats),
            "round_trips": sum(s.round_trips for s in stats), "baseline": baseline, "baseline_from_eval": from_eval,
            "pruned": pruned, "considered": considered, "stats": stats}


# CONTEXT-ADDED (R2/R3: the case file's identity line and plain record lines, read through data/context_graph.json --
# which column is the make, the date, the linked name -- so no source column is named here; only the source id "REG"
# (the registration agency, already fixed in context_widgets.SOURCE_ORDER) picks whose row is the identity)
GRAPH_FILE = __import__("pathlib").Path(__file__).resolve().parents[2] / "data" / "context_graph.json"


@lru_cache(maxsize=1)
def _graph_file(mtime: float) -> Dict[str, Any]:
    return json.loads(GRAPH_FILE.read_text(encoding="utf-8"))


def graph() -> Dict[str, Any]:
    """The context graph (schema metadata, not source data); re-read when the file changes, never cached as empty."""
    try:
        return _graph_file(GRAPH_FILE.stat().st_mtime)
    except (OSError, ValueError):
        return {"sources": {}}


def _spec(sid: str, table: str) -> Dict[str, Any]:
    return (graph().get("sources", {}).get(sid, {}).get("tables", {}).get(table) or {})


def _edges(sid: str, table: str) -> List[Dict[str, Any]]:
    return [e for e in graph().get("sources", {}).get(sid, {}).get("edges", []) or [] if e.get("from") == table]


def _kind(col: str, spec: Dict[str, Any]) -> Optional[str]:
    try:
        from mediator.doc_retrieval import column_kind  # noqa: PLC0415
        return column_kind(col, spec)
    except Exception:
        return None


def _anchor(sid: str, table: str, row: Dict[str, Any]) -> Optional[tuple]:
    """(column, printed identifier) when the agency prints this record's number on paper, e.g. POL-000562."""
    try:
        from mediator.doc_retrieval import EXTRA_ANCHORS  # noqa: PLC0415
    except Exception:
        return None
    for (s, t, col), (_, pattern) in EXTRA_ANCHORS.items():
        if (s, t) == (sid, table) and row.get(col) not in (None, ""):
            try:
                return col, pattern.format(int(row[col])) if "d}" in pattern else pattern.format(row[col])
            except (TypeError, ValueError):
                return col, str(row[col])
    return None


def display_id(sid: str, table: str, pk: Any) -> Optional[str]:
    """How a record is named to a person: the printed number if the agency has one, a natural key as is,
    nothing for a bare surrogate number (an internal id means nothing to a reader)."""
    pk_col = _spec(sid, table).get("pk")
    hit = _anchor(sid, table, {pk_col: pk}) if isinstance(pk_col, str) else None
    if hit:
        return hit[1]
    text = "|".join(map(str, pk)) if isinstance(pk, (list, tuple)) else str(pk if pk is not None else "")
    return None if not text or text.isdigit() else text


def _name_of(row: Any) -> Optional[str]:
    """A linked record's readable name (insurer, camera place, station, person), by column role."""
    spec = _spec(row.source_id, row.table)
    for col in [(spec.get("person") or {}).get("name")] + list(spec.get("text") or []):
        if col and row.row.get(col) not in (None, "") and (
                col == (spec.get("person") or {}).get("name") or _kind(col, spec) not in (None, "make", "model", "colour")):
            return str(row.row[col])
    return None


def _target(rows: List[Any], sid: str, edge: Dict[str, Any], value: Any) -> Optional[Any]:
    return next((x for x in rows if x.source_id == sid and x.table == edge.get("to")
                 and value is not None and str(x.row.get(edge.get("to_col"))) == str(value)), None)


def record_facts(r: Any, rows: List[Any]) -> Dict[str, Any]:
    """What one context row says, for a plain line: printed id, names of the records it points to,
    its date (decoded with the graph's time format), and its remaining descriptive values."""
    spec, row = _spec(r.source_id, r.table), dict(r.row or {})
    hit = _anchor(r.source_id, r.table, row)
    used = {hit[0]} if hit else set()
    ident = hit[1] if hit else display_id(r.source_id, r.table, r.pk)
    linked = [n for e in _edges(r.source_id, r.table)
              for t in [_target(rows, r.source_id, e, row.get(e.get("col")))] if t for n in [_name_of(t)] if n]
    when, tcol = None, spec.get("time") or {}
    if tcol.get("col") and row.get(tcol["col"]) not in (None, ""):
        try:
            from mediator.doc_conflicts import to_date  # noqa: PLC0415
            when = to_date(row[tcol["col"]], tcol.get("fmt", "iso_date"))
        except Exception:
            when = None
    person = spec.get("person") or {}
    private = {person.get(k) for k in ("mobile", "pincode") if person.get(k)}   # contact details stay out of the line
    values = [str(row[c]) for c in spec.get("text") or [] if c not in used | private and row.get(c) not in (None, "")
              and not isinstance(row[c], (bool, int, float))]   # 0/1 flags and numbers read as noise
    return {"id": ident, "linked": linked, "date": when, "values": values}


def identity(ctx: Any) -> Dict[str, str]:
    """make / model / colour / owner from the registration row in the context (empty when it is not there)."""
    rows = list(getattr(ctx, "rows", None) or [])
    reg = next((r for r in rows if r.source_id == "REG" and _spec("REG", r.table).get("plate")), None)
    if reg is None:
        return {}
    spec, out = _spec("REG", reg.table), {}
    for col in spec.get("text") or []:
        kind = _kind(col, spec)
        if kind in ("make", "model", "colour") and reg.row.get(col) not in (None, ""):
            out.setdefault(kind, str(reg.row[col]))
    owner = _owner(reg, rows, getattr(ctx, "persons", None) or [])
    if owner:
        out["owner"] = owner
    return out


def _owner(reg: Any, rows: List[Any], persons: List[Any]) -> Optional[str]:
    """The owner's name: the owner row if it was shipped, else the person cluster holding it, else a person row
    the context reached from that owner across the person bridge (same person by record linkage)."""
    for e in _edges(reg.source_id, reg.table):
        name_col = (_spec(reg.source_id, e.get("to", "")).get("person") or {}).get("name")
        key = reg.row.get(e.get("col"))
        if not name_col or key is None:
            continue
        hit = _target(rows, reg.source_id, e, key)
        if hit is not None and hit.row.get(name_col):
            return str(hit.row[name_col])
        for p in persons:
            if any((m[0], m[1], str(m[2])) == (reg.source_id, e["to"], str(key)) for m in p.members):
                if (p.canonical or {}).get("name"):
                    return str(p.canonical["name"])
        mark = f"{e['to']}#{key}"
        for r in rows:
            path = list(r.path or [])
            if r.via_bridge == "person" and mark in path and path.index(mark) + 2 == len(path) - 1:
                pname = (_spec(r.source_id, r.table).get("person") or {}).get("name")
                if pname and r.row.get(pname):
                    return str(r.row[pname])
    return None


def agencies_answered(res: Dict[str, Any]) -> int:
    ctx = res.get("context")
    n = len({s.source_id for s in getattr(ctx, "stats", None) or [] if s.status == "OK"})
    return n + (1 if res.get("docs") is not None else 0)   # the document store answers as an agency of its own
# CONTEXT-END
