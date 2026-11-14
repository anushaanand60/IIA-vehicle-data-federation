"""CONTEXT-ADDED (R2: verdict robustness): which agencies each warning sign actually rests on (source knock-out).

For every agency S, the warning signs are recomputed on the rows already in memory with S's rows removed
(analytics.indicators_from_rows, the same function the page uses, with no client, so nothing is fetched).
A sign that disappears without S *depends* on S; an agency whose removal changes nothing could have been down
without changing the verdict. Per sign, the smallest set of agencies that still produces it is found by trying
subsets smallest first (at most 2^k recomputations, k = agencies with rows <= 6: milliseconds, no traffic).
Pure: no network, nothing stored. `now` is fixed once so every recomputation reads the clock identically.
"""
from __future__ import annotations

from datetime import datetime
from itertools import combinations
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Set

from mediator import analytics
from mediator import evidence as D

Rows = Dict[Any, List[Dict[str, Any]]]


def _codes(plate: str, rows: Rows, keep: FrozenSet[str], now: datetime, memo: Dict[FrozenSet[str], Set[str]]
           ) -> Set[str]:
    if keep not in memo:
        sub = {k: v for k, v in rows.items() if k[0] in keep}
        memo[keep] = {i.code for i in analytics.indicators_from_rows(plate, sub, now)}  # client=None: no fetch
    return memo[keep]


def knock_out(plate: str, ctx: Any, now: Optional[datetime] = None, asked: Sequence[str] = ()) -> Dict[str, Any]:
    """{"indicators", "asked", "falls_without": {S: [codes]}, "minimal": {code: [S...]}, "depends", "holds_without"}."""
    plate = "".join(ch for ch in str(plate or "").upper() if ch.isalnum())
    now = now or datetime.now()
    rows = D.from_context(ctx) if ctx is not None else {}
    with_rows = sorted({k[0] for k, v in rows.items() if v})
    order = list(dict.fromkeys(list(asked) + [s.source_id for s in getattr(ctx, "stats", []) or []] + with_rows))
    memo: Dict[FrozenSet[str], Set[str]] = {}
    full = _codes(plate, rows, frozenset(with_rows), now, memo)
    falls = {s: sorted(full - _codes(plate, rows, frozenset(with_rows) - {s}, now, memo)) for s in order}
    minimal: Dict[str, List[str]] = {}
    for code in sorted(full):
        for size in range(1, len(with_rows) + 1):
            hit = next((c for c in combinations(with_rows, size) if code in _codes(plate, rows, frozenset(c), now, memo)),
                       None)
            if hit:
                minimal[code] = list(hit)
                break
    return {"indicators": sorted(full), "asked": order, "falls_without": falls, "minimal": minimal,
            "depends": [s for s in order if falls[s]], "holds_without": [s for s in order if not falls[s]]}


def _and(names: Sequence[str]) -> str:
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def summary_line(ko: Dict[str, Any]) -> str:
    """'Holds without CAM, DOCS · Depends on THEFT and PUC' or the clear-verdict sentence."""
    if not ko["indicators"]:
        return (f"Clear verdict uses all {len(ko['asked'])} agencies; "
                "losing any one would make it a lower bound")
    parts = []
    if ko["holds_without"]:
        parts.append("Holds without " + ", ".join(ko["holds_without"]))
    if ko["depends"]:
        parts.append("Depends on " + _and(ko["depends"]))
    return " · ".join(parts)
