"""CONTEXT-ADDED (R2/R3): the measurements behind the Context Evaluation page, as plain functions.

`scripts/evaluate_context.py` drives the backend (discover / topk / doc_eval) and calls these to turn
results into numbers; the page only reads the JSON it writes. Everything here is pure: no source is
contacted, so every metric is unit-tested on hand-built rows.

What is measured, and why each is defensible:
  * data moved - rows, bytes, round trips, latency per source, read off the counting client, never estimated;
  * exactness - our bounded top-n vs a central oracle: the closure baseline (B0) ships everything, the
    mediator re-ranks it with the same scores, and that top-n is what a warehouse holding all data would pick
    (ties with the oracle's n-th score count as found).
    This is the headline because it does not depend on our own relevance labels;
  * precision@n / nDCG@n - against grades taken from the generator's planted scenarios (secondary: the
    generator wrote both the data and the labels, so these are partly circular, and the page says so);
  * rank stability - Kendall tau of the oracle top-n when every edge weight is scaled by a random factor
    in [1-s, 1+s]. A row's path affinity is the product of its edge weights, so a perturbed edge rescales
    exactly the rows whose path crosses it (relevance terms are left alone).
"""
from __future__ import annotations

import json
import math
import random
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

Key = Tuple[str, str, str]   # (source_id, table, pk as text): one row, identified the same way everywhere

# Fixed display order for the four approaches; the page colours them in this order too.
MODES: List[Tuple[str, str]] = [
    ("bounded", "Ours: ranked inside each database, bounded follow-ups"),
    ("closure", "B0: every match + full FK closure"),
    ("topn", "B1: each database's top n, one round, no bounds"),
    ("mediator", "B2: mediator walks each FK edge with one query per hop"),
]


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field from a dataclass or a dict (results may come back from JSON)."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def canonical_plate(text: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())


def row_key(row: Any) -> Key:
    return (str(_get(row, "source_id", "")), str(_get(row, "table", "")), str(_get(row, "pk", "")))


def ranked(rows: Iterable[Any]) -> List[Any]:
    """Best first by score; ties broken by key so the order is reproducible."""
    return sorted(rows, key=lambda r: (-float(_get(r, "score", 0.0) or 0.0), row_key(r)))


def by_source(rows: Iterable[Any]) -> Dict[str, List[Any]]:
    out: Dict[str, List[Any]] = {}
    for row in rows:
        out.setdefault(str(_get(row, "source_id", "")), []).append(row)
    return out


# ------------------------------------------------------------------ exactness vs the central oracle

def _groups(rows: Iterable[Any], per_source: bool) -> Dict[str, List[Any]]:
    return by_source(rows) if per_source else {"*": list(rows)}


def overlap_at_n(our_rows: Sequence[Any], closure_rows: Sequence[Any], n: int,
                 per_source: bool = False) -> Optional[float]:
    """(Our top-n rows the oracle would also accept) / |oracle top-n|. None when the oracle is empty.

    Global by default, because discover() answers with the global best n rows (ContextResult.rows).
    per_source=True compares each database's own top n (mean over databases) - for a single wrapper.
    Tie-aware: a row whose oracle score equals the oracle's n-th score is as correct as the row the
    oracle happened to keep (many rows tie, e.g. every challan of one camera); without this the
    measure would reward copying the oracle's arbitrary tie-break.
    """
    eps = 1e-9
    shares = []
    ours_by = _groups(our_rows, per_source)
    for sid, rows in _groups(closure_rows, per_source).items():
        top = ranked(rows)[:n]
        if not top:
            continue
        cut = float(_get(top[-1], "score", 0) or 0)
        oracle_score = {row_key(r): float(_get(r, "score", 0) or 0) for r in rows}
        ours = ranked(ours_by.get(sid, []))[:n]
        hits = sum(1 for r in ours if oracle_score.get(row_key(r), -1.0) >= cut - eps)
        shares.append(min(hits, len(top)) / len(top))
    return round(sum(shares) / len(shares), 4) if shares else None


# ------------------------------------------------------------------ judged relevance (secondary)

def precision_at_n(keys: Sequence[Key], grades: Dict[Key, int], n: int) -> Optional[float]:
    if not grades:
        return None
    top = list(keys)[:n]
    return round(sum(1 for k in top if grades.get(k, 0) > 0) / max(len(top), 1), 4) if top else 0.0


def ndcg_at_n(keys: Sequence[Key], grades: Dict[Key, int], n: int) -> Optional[float]:
    if not grades:
        return None
    gain = lambda g, i: (2 ** g - 1) / math.log2(i + 2)  # noqa: E731 - standard exponential gain
    dcg = sum(gain(grades.get(k, 0), i) for i, k in enumerate(list(keys)[:n]))
    ideal = sum(gain(g, i) for i, g in enumerate(sorted(grades.values(), reverse=True)[:n]))
    return round(dcg / ideal, 4) if ideal > 0 else None


def load_scenarios(path: Path) -> List[Dict[str, Any]]:
    """The generator's planted scenarios, tolerant of a list or a {"scenarios": [...]} wrapper."""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("scenarios", data) if isinstance(data, dict) else data
    return [s for s in items if isinstance(s, dict)] if isinstance(items, list) else []


def scenario_plates(scenario: Dict[str, Any]) -> List[str]:
    raw = scenario.get("plates") or ([scenario["plate"]] if scenario.get("plate") else [])
    return [canonical_plate(p) for p in raw if p]


def evidence_keys(scenario: Dict[str, Any]) -> List[Key]:
    """Evidence rows as (source, table, pk), whether written as lists or dicts."""
    out: List[Key] = []
    for ev in scenario.get("evidence") or []:
        if isinstance(ev, (list, tuple)) and len(ev) >= 3:
            out.append((str(ev[0]), str(ev[1]), str(ev[2])))
        elif isinstance(ev, dict):
            out.append((str(ev.get("source_id") or ev.get("source", "")), str(ev.get("table", "")),
                        str(ev.get("pk", ""))))
    return out


def plate_columns(graph: Dict[str, Any]) -> Dict[Tuple[str, str], str]:
    """(source, table) -> its plate column, from data/context_graph.json (never hard-coded here)."""
    return {(sid, t): meta["plate"] for sid, src in graph.get("sources", {}).items()
            for t, meta in src.get("tables", {}).items() if meta.get("plate")}


def grades_for(plate: str, rows: Iterable[Any], scenarios: List[Dict[str, Any]],
               graph: Dict[str, Any]) -> Dict[Key, int]:
    """Grade 2: a row the generator planted as scenario evidence for this plate. Grade 1: a row that
    carries the plate itself. Everything else 0. Judged over every row any mode returned (pooling)."""
    plate = canonical_plate(plate)
    grades: Dict[Key, int] = {}
    cols = plate_columns(graph)
    for row in rows:
        col = cols.get((str(_get(row, "source_id")), str(_get(row, "table"))))
        if col and canonical_plate((_get(row, "row") or {}).get(col)) == plate:
            grades[row_key(row)] = 1
    for sc in scenarios:
        if plate in scenario_plates(sc):
            for key in evidence_keys(sc):
                grades[key] = 2
    return grades


# ------------------------------------------------------------------ rank stability under weight noise

def kendall_tau(order_a: Sequence[Key], order_b: Sequence[Key]) -> Optional[float]:
    """Tau over the items of order_a, ranked by both orders (items missing from b rank last, tied)."""
    if len(order_a) < 2:
        return None
    pos_b = {k: i for i, k in enumerate(order_b)}
    last = len(order_b)
    rb = [pos_b.get(k, last) for k in order_a]
    concordant = discordant = 0
    for i in range(len(rb)):
        for j in range(i + 1, len(rb)):
            if rb[i] < rb[j]:
                concordant += 1
            elif rb[i] > rb[j]:
                discordant += 1
    pairs = concordant + discordant
    return round((concordant - discordant) / pairs, 4) if pairs else 1.0


def _edges(row: Any) -> List[str]:
    """Edges a row's score went through: consecutive tables on its path, plus its bridge if any."""
    tables = [str(p).split("#")[0].split(":")[-1].split(".")[-1] for p in (_get(row, "path") or [])]
    edges = [f"{a}->{b}" for a, b in zip(tables, tables[1:])]
    if _get(row, "via_bridge"):
        edges.append(f"bridge:{_get(row, 'via_bridge')}")
    return edges


def stability(rows: Sequence[Any], n: int, spread: float = 0.5, trials: int = 20, seed: int = 7,
              per_source: bool = False) -> Dict[str, Optional[float]]:
    """Scale each edge weight by U(1-spread, 1+spread); compare the top-n before and after (global by default)."""
    rng = random.Random(seed)
    edges = sorted({e for r in rows for e in _edges(r)})
    groups = _groups(rows, per_source)
    taus: List[float] = []
    overlaps: List[float] = []
    for _ in range(trials):
        factor = {e: rng.uniform(1 - spread, 1 + spread) for e in edges}
        for src_rows in groups.values():
            before = [row_key(r) for r in ranked(src_rows)]
            scored = [(-(float(_get(r, "score", 0) or 0) * math.prod(factor[e] for e in _edges(r))), row_key(r))
                      for r in src_rows]
            after = [k for _, k in sorted(scored)]
            tau = kendall_tau(before[:n], after)
            if tau is not None:
                taus.append(tau)
            if before[:n]:
                overlaps.append(len(set(before[:n]) & set(after[:n])) / len(before[:n]))
    mean = lambda xs: round(sum(xs) / len(xs), 4) if xs else None  # noqa: E731
    return {"tau_mean": mean(taus), "tau_min": round(min(taus), 4) if taus else None,
            "overlap_mean": mean(overlaps), "edges_perturbed": len(edges), "trials": trials, "spread": spread}


# ------------------------------------------------------------------ data moved

def movement(result: Any, stats: Optional[Sequence[Any]] = None) -> Dict[str, Any]:
    """Rows / bytes / round trips / latency, total and per source, from a ContextResult (or its client)."""
    stats = list(stats if stats is not None else (_get(result, "stats") or []))
    per = {str(_get(s, "source_id")): {"rows": int(_get(s, "rows_shipped", 0) or 0),
                                       "bytes": int(_get(s, "bytes_shipped", 0) or 0),
                                       "round_trips": int(_get(s, "round_trips", 0) or 0),
                                       "latency_ms": round(float(_get(s, "latency_ms", 0) or 0), 1),
                                       "status": _get(s, "status", "OK")} for s in stats}
    total = lambda f: sum(v[f] for v in per.values())  # noqa: E731
    return {"rows": total("rows"), "bytes": total("bytes"), "round_trips": total("round_trips"),
            "latency_ms": round(float(_get(result, "elapsed_ms", 0) or 0) or total("latency_ms"), 1),
            "complete": bool(_get(result, "complete", True)), "baseline_rows": _get(result, "baseline_rows"),
            "per_source": per}


def pruned_share(trace: Optional[Sequence[Dict[str, Any]]]) -> Optional[Tuple[int, int]]:
    """(follow-ups pruned by the bound, follow-ups considered) from a discover trace, if one is attached."""
    steps = [t for t in trace or [] if not t.get("final")]   # the closing summary entry repeats the last round
    if not steps:
        return None
    pruned = sum(int(t.get("pruned", 0) or 0) for t in steps)
    expanded = sum(int(t.get("expanded", 0) or 0) for t in steps)
    return (pruned, pruned + expanded) if pruned + expanded else None


def summarise(records: List[Dict[str, Any]], group: Sequence[str], fields: Sequence[str]) -> List[Dict[str, Any]]:
    """Mean of `fields` per `group` key tuple, skipping None; keeps first-seen group order."""
    buckets: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
    for rec in records:
        buckets.setdefault(tuple(rec.get(g) for g in group), []).append(rec)
    out = []
    for key, recs in buckets.items():
        row: Dict[str, Any] = dict(zip(group, key))
        for f in fields:
            vals = [r[f] for r in recs if isinstance(r.get(f), (int, float))]
            row[f] = round(sum(vals) / len(vals), 4) if vals else None
        row["queries"] = len(recs)
        out.append(row)
    return out
