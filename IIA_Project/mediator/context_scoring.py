"""CONTEXT-ADDED (R2): the mediator's copy of the context scoring rules, read from data/context_graph.json.

The sources score rows in SQL (sources/context_sql.py). The mediator needs the *same* rules in Python for
three things: (1) R-bar_t, the best relevance any row of source t could reach for this query, which turns a
bridge state's affinity into a sound upper bound; (2) the B2 baseline, which scores rows itself; (3) the
central oracle the tests compare against. Keep the constants in step with context_sql.REL_DEFAULTS.

score = A x R  with  A = product of edge weights x gamma per hop x bridge confidences (path affinity)
                     R = clip01(base + keyword share + time-window hit + decision boosts)   (row relevance)
"""
from __future__ import annotations

import functools
import itertools
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sources.context_sql import REL_DEFAULTS, edge_weight

GRAPH_PATH = Path(__file__).resolve().parents[1] / "data" / "context_graph.json"


def load_graph(path: Optional[str] = None) -> Dict[str, Any]:
    p = Path(path or os.environ.get("CONTEXT_GRAPH") or GRAPH_PATH)
    return json.loads(p.read_text(encoding="utf-8"))


def params_of(graph: Dict[str, Any]) -> Dict[str, Any]:
    return {**REL_DEFAULTS, **graph.get("params", {})}


def canon(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isalnum()).upper()


@functools.lru_cache(maxsize=4096)
def _variants(p: str, cap: int, max_subs: int) -> Tuple[str, ...]:
    from mediator.plate_resolve import CONFUSABLE
    out, spots = {p}, [i for i, ch in enumerate(p) if CONFUSABLE.get(ch)]
    for k in range(1, max_subs + 1):  # ANPR rarely misreads more than two glyphs of one plate
        for pos in itertools.combinations(spots, k):
            for repl in itertools.product(*(CONFUSABLE[p[i]] for i in pos)):
                v = list(p)
                for i, ch in zip(pos, repl):
                    v[i] = ch
                out.add("".join(v))
    subs = lambda v: sum(a != b for a, b in zip(v, p))  # noqa: E731
    return tuple(sorted(out, key=lambda v: (subs(v), v))[:cap])


def ocr_variants(plate: str, cap: int = 24, max_subs: int = 2) -> List[str]:
    """Spellings an ANPR camera could have produced for `plate` (phase-2 confusable pairs), fewest edits first.

    Trade-off: shipping <= cap variants to the noisy source keeps the equality seed in-DB; a fuzzy
    in-DB match would need dialect-specific edit-distance UDFs.
    """
    return list(_variants(canon(plate), cap, max_subs))


@functools.lru_cache(maxsize=65536)
def plate_confidence(a: str, b: str) -> float:
    """Phase-2 OCR-confusion similarity as a cheap proxy for the learned match probability."""
    if canon(a) == canon(b):
        return 1.0
    from mediator.similarity import ocr_edit_similarity
    return max(0.0, min(1.0, ocr_edit_similarity(canon(a), canon(b))))


def parse_day(value: Any, fmt: str) -> Optional[date]:
    if value is None or value == "":
        return None
    try:
        if fmt in ("iso_date", "iso_ts"):
            return date.fromisoformat(str(value)[:10])
        if fmt == "ddmmyyyy":
            return datetime.strptime(str(value), "%d/%m/%Y").date()
        if fmt == "epoch":
            return datetime.fromtimestamp(int(float(value)), tz=timezone.utc).date()
    except (ValueError, OverflowError, OSError):
        return None
    return None


def _cmp(left: Any, op: str, right: Any) -> bool:
    if left is None:
        return False
    try:
        a, b = float(left), float(right)
    except (TypeError, ValueError):
        a, b = str(left), str(right)
    return {"=": a == b, "!=": a != b, "<>": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


def relevance(spec: Dict[str, Any], row: Dict[str, Any], keywords: Sequence[str],
              window: Optional[Sequence[str]], params: Dict[str, Any]) -> float:
    """R(row): same formula as context_sql.relevance_expr."""
    low = {k.lower(): v for k, v in row.items()}
    r = float(params["rel_base"])
    kws = [k.lower() for k in keywords if k]
    if kws and spec.get("text"):
        blob = " ".join("" if low.get(c.lower()) is None else str(low.get(c.lower())) for c in spec["text"]).lower()
        r += params["rel_keyword"] * sum(k in blob for k in kws) / len(kws)
    tm = spec.get("time")
    if window and tm:
        d = parse_day(low.get(tm["col"].lower()), tm["fmt"])
        if d and date.fromisoformat(window[0]) <= d <= date.fromisoformat(window[1]):
            r += params["rel_time"]
    for dec in spec.get("decision", []):
        if _cmp(low.get(dec["col"].lower()), dec["op"], dec["value"]):
            r += float(dec["boost"])
    return max(0.0, min(1.0, r))


def table_max_relevance(spec: Dict[str, Any], keywords: Sequence[str], window: Optional[Sequence[str]],
                        params: Dict[str, Any]) -> float:
    r = float(params["rel_base"])
    r += params["rel_keyword"] if keywords and spec.get("text") else 0.0
    r += params["rel_time"] if window and spec.get("time") else 0.0
    r += sum(max(0.0, float(d["boost"])) for d in spec.get("decision", []))
    return max(0.0, min(1.0, r))


def max_relevance(src: Dict[str, Any], keywords: Sequence[str], window: Optional[Sequence[str]],
                  params: Dict[str, Any]) -> float:
    """R-bar_t: the best relevance any row of this source could reach for this query (metadata only)."""
    return max((table_max_relevance(s, keywords, window, params) for s in src.get("tables", {}).values()), default=0.0)


def seed_factor(src: Dict[str, Any], seed_tables: Sequence[str], keywords: Sequence[str],
                window: Optional[Sequence[str]], params: Dict[str, Any],
                edge_weights: Optional[Sequence[Dict[str, Any]]] = None) -> float:
    """Best score per unit of seed weight a state landing on `seed_tables` can produce (sound, tighter than R-bar_t).

    A seed row scores <= w x R-bar(seed table); anything one or more hops away has affinity
    <= w x gamma x W1 (best first-hop edge weight out of the seed table) and relevance <= R-bar_t.
    Weights come from the source's own last /context answer (measured live); unknown -> 1."""
    tables, gamma = src.get("tables", {}), float(params.get("gamma_hops", 1.0))
    w1 = 0.0
    for t in seed_tables:
        if edge_weights is None:
            w1 = 1.0
            break
        w1 = max([w1] + [e["fwd"] for e in edge_weights if e["from"] == t] + [e["rev"] for e in edge_weights if e["to"] == t])
    seed_r = max((table_max_relevance(tables[t], keywords, window, params) for t in seed_tables if t in tables), default=0.0)
    return max(seed_r, gamma * w1 * max_relevance(src, keywords, window, params))


def bridge_members(graph: Dict[str, Any]) -> Dict[str, List[Tuple[str, str, str]]]:
    return {name: [tuple(m) for m in b.get("members", [])] for name, b in graph.get("bridges", {}).items()}


def noisy_plate_sources(graph: Dict[str, Any]) -> List[str]:
    """Sources whose plate column is an OCR read. Read from the graph when declared; CAM by contract otherwise."""
    return list(graph.get("bridges", {}).get("plate", {}).get("noisy", ["CAM"]))


def edge_weights_from_rows(graph: Dict[str, Any], sid: str, rows: Dict[str, List[Dict[str, Any]]]
                           ) -> List[Tuple[float, float]]:
    """(forward, reverse) weight per edge from full table contents -- the oracle's / B2's version of fanout_sql."""
    w0 = float(graph.get("params", {}).get("w0", 1.0))
    out = []
    for e in graph["sources"][sid].get("edges", []):
        col = [r.get(e["col"]) for r in rows.get(e["from"], []) if r.get(e["col"]) is not None]
        tcol = [r.get(e["to_col"]) for r in rows.get(e["to"], []) if r.get(e["to_col"]) is not None]
        rev = len(col) / max(1, len(set(col)))
        fwd = len(tcol) / max(1, len(set(tcol)))
        out.append((edge_weight(e.get("w"), fwd, w0), edge_weight(e.get("w"), rev, w0)))
    return out


def pk_key(spec: Dict[str, Any], row: Dict[str, Any]) -> str:
    low = {k.lower(): v for k, v in row.items()}
    cols = spec["pk"] if isinstance(spec["pk"], list) else [spec["pk"]]
    return "|".join(str(low.get(c.lower())) for c in cols)


def first_hop_weight(weights: Iterable[Tuple[float, float]]) -> float:
    return max((max(w) for w in weights), default=0.0)
