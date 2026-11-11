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
