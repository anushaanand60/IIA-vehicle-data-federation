"""CONTEXT-ADDED (R2: entity top-k): the best k VEHICLES for a free-text query, distributed over the sources.

Each source ranks vehicles by its own predicates inside its own DBMS (mediator/topk_sql.py) and is read in two
ways: sorted access (`ORDER BY score DESC LIMIT d OFFSET o`, paged deeper only when needed) and random access
(`WHERE plate IN (...)`, one batched query per source). Two classic algorithms run on top:

  * TA  (Fagin's threshold algorithm): read every list to depth 10, 20, 40...; complete each newly seen vehicle
    by random access; stop once the k-th best total >= threshold = sum of the last scores seen under sorted access.
  * TPUT (three-phase uniform threshold): phase 1 top-k of each list -> tau1 = k-th best partial sum; phase 2
    every list ships all vehicles with partial >= tau1/m (one range query each); vehicles whose upper bound
    cannot reach the k-th lower bound are pruned; phase 3 random access completes the survivors. Fixed number
    of round trips (3), at the price of shipping more rows than TA on skewed data.

Identity: REG/INS/THEFT/PUC store exact plates (confidence 1.0). CAM stores OCR reads, so a camera partial is
discounted by ocr_confidence x OCR-aware similarity between the read and the canonical plate. Only object scores
are discounted; the threshold stays undiscounted (raw partials), which keeps it a valid upper bound.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from mediator import topk_sql as S
from mediator.context_types import ParsedQuery, RankedVehicle, TopKResult
from mediator.plate_ocr import normalise_candidates
from mediator.similarity import ocr_edit_similarity

DEPTH0 = 10          # first sorted-access depth; doubled each TA round
BATCH = 40           # plates per random-access query: keeps the IN list well under the guard's 10k chars
PAGE = 200           # the wrapper's own row cap


@dataclass
class _List:
    sid: str
    feats: List[S.Feature]
    noisy: bool
    offset: int = 0
    last: float = float("inf")   # raw score of the last row read under sorted access
    exhausted: bool = False


@dataclass
class _Run:
    client: Any
    lists: Dict[str, _List]
    partial: Dict[str, Dict[str, float]] = field(default_factory=dict)       # plate -> sid -> discounted partial
    known: Dict[str, set] = field(default_factory=dict)                      # plate -> sids whose partial is final
    why: Dict[str, Dict[str, Tuple[List[str], float, str]]] = field(default_factory=dict)  # labels, idconf, read
    sorted_accesses: int = 0
    random_accesses: int = 0
    rows_shipped: int = 0
    round_trips: int = 0

    def _query(self, sid: str, sql: str) -> List[Dict[str, Any]]:
        rows = self.client.query(sid, sql) or []
        self.round_trips += 1
        self.rows_shipped += len(rows)
        return rows

    def _ingest(self, lst: _List, row: Dict[str, Any], target: Optional[str] = None) -> Tuple[str, float]:
        read = str(row.get("plate") or "")
        flags = [int(float(row.get(f"f{i}") or 0)) for i in range(len(lst.feats))]
        raw = sum(f.weight * b for f, b in zip(lst.feats, flags))
        plate, conf = read, 1.0
        if lst.noisy:
            plate = target or (normalise_candidates(read) or [read])[0]
            conf = float(row.get("idc") or 1.0) * ocr_edit_similarity(read, plate)
        score = raw * conf
        if score >= self.partial.setdefault(plate, {}).get(lst.sid, 0.0) and raw > 0:
            self.partial[plate][lst.sid] = score
            labels = [f.label for f, b in zip(lst.feats, flags) if b]
            self.why.setdefault(plate, {})[lst.sid] = (labels, round(conf, 3), read)
        return plate, raw

    def sorted_access(self, lst: _List, upto: int) -> None:
        want = upto - lst.offset
        if lst.exhausted or want <= 0:
            return
        rows = self._query(lst.sid, S.score_sql(lst.sid, lst.feats, limit=want, offset=lst.offset))
        self.sorted_accesses += len(rows)
        lst.offset += len(rows)
        for row in rows:
            plate, raw = self._ingest(lst, row)
            lst.last = raw
            if not lst.noisy:
                self.known.setdefault(plate, set()).add(lst.sid)
        if len(rows) < want:
            lst.exhausted, lst.last = True, 0.0

    def range_access(self, lst: _List, tau: float) -> None:
        """TPUT phase 2: everything with raw partial >= tau that sorted access has not shipped yet."""
        while not lst.exhausted:
            rows = self._query(lst.sid, S.score_sql(lst.sid, lst.feats, limit=PAGE, offset=lst.offset,
                                                     min_score=tau or None))
            self.sorted_accesses += len(rows)
            lst.offset += len(rows)
            for row in rows:
                plate, lst.last = self._ingest(lst, row)
                if not lst.noisy:
                    self.known.setdefault(plate, set()).add(lst.sid)
            if len(rows) < PAGE:
                lst.exhausted = True
        lst.last = min(lst.last, tau) if tau else 0.0

    def random_access(self, plates: Sequence[str]) -> None:
        for lst in self.lists.values():
            todo = [p for p in plates if lst.sid not in self.known.get(p, set())]
            for i in range(0, len(todo), BATCH):
                batch = todo[i:i + BATCH]
                self.random_accesses += len(batch)
                for row in self._query(lst.sid, S.score_sql(lst.sid, lst.feats, plates=batch)):
                    read = str(row.get("plate") or "")
                    hits = [p for p in batch if S.fold(p) == S.fold(read)] if lst.noisy else [read]
                    for p in hits:
                        self._ingest(lst, row, target=p if lst.noisy else None)
                for p in batch:
                    self.known.setdefault(p, set()).add(lst.sid)

    def total(self, plate: str) -> float:
        return sum(self.partial.get(plate, {}).values())

    def best(self, k: int) -> List[Tuple[str, float]]:
        ranked = sorted(((p, self.total(p)) for p in self.partial), key=lambda x: (-x[1], x[0]))
        return [(p, s) for p, s in ranked if s > 0][:k]


def _lists(feats: Dict[str, List[S.Feature]]) -> Dict[str, _List]:
    return {sid: _List(sid, fs, noisy=S.identity_column(sid) is not None) for sid, fs in feats.items() if fs}


def ta_stop(run: _Run, k: int, threshold: float) -> bool:
    top = run.best(k)
    return len(top) == k and top[-1][1] >= threshold


def run_ta(run: _Run, k: int, stop: Callable[[_Run, int, float], bool] = ta_stop) -> int:
    """`stop` may be replaced (topk_refine) to check an expensive cross-source condition lazily."""
    depth = DEPTH0
    while True:
        for lst in run.lists.values():
            run.sorted_access(lst, depth)
        run.random_access([p for p in run.partial if run.known.get(p, set()) != set(run.lists)])
        threshold = sum(0.0 if l.exhausted else l.last for l in run.lists.values())
        if stop(run, k, threshold) or all(l.exhausted for l in run.lists.values()):
            return depth
        depth *= 2


def run_tput(run: _Run, k: int) -> int:
    for lst in run.lists.values():                                   # phase 1
        run.sorted_access(lst, k)
    lower = sorted((run.total(p) for p in run.partial), reverse=True)
    tau1 = lower[k - 1] if len(lower) >= k else 0.0
    m = len(run.lists)
    for lst in run.lists.values():                                   # phase 2
        run.range_access(lst, tau1 / m)
    lower = sorted((run.total(p) for p in run.partial), reverse=True)
    tau2 = lower[k - 1] if len(lower) >= k else 0.0

    def upper(p: str) -> float:   # unseen in a list => below that list's phase-2 cut (raw, undiscounted)
        seen = run.partial.get(p, {})
        return sum(seen.get(l.sid, 0.0) if l.sid in run.known.get(p, set()) else max(seen.get(l.sid, 0.0), l.last)
                   for l in run.lists.values())
    survivors = [p for p in run.partial if upper(p) >= tau2]
    run.random_access(survivors)                                     # phase 3
    return max((l.offset for l in run.lists.values()), default=0)


def execute(feats: Dict[str, List[S.Feature]], k: int, client: Any, method: str = "ta",
            plates: Optional[Sequence[str]] = None) -> Tuple[_Run, int]:
    run = _Run(client=client, lists=_lists(feats))
    if not run.lists:
        return run, 0
    if plates:                       # the user named the vehicles: random access only, nothing to search for
        for p in plates:
            run.partial.setdefault(p, {})
        run.random_access(list(plates))
        return run, 0
    return run, (run_tput(run, k) if method == "tput" else run_ta(run, k))


def ranked(run: _Run, k: int, weight_total: float, keep_zero: Sequence[str] = (),
           scores: Optional[Dict[str, float]] = None) -> List[RankedVehicle]:
    """RankedVehicles with explanations; `scores` overrides the totals (refined scores)."""
    out: List[RankedVehicle] = []
    if scores is not None:
        top = sorted(((p, s) for p, s in scores.items() if s > 0), key=lambda x: (-x[1], x[0]))[:k]
    else:
        top = (sorted(((p, run.total(p)) for p in keep_zero), key=lambda x: (-x[1], x[0]))[:k] if keep_zero
               else run.best(k))
    for plate, score in top:
        why = run.why.get(plate, {})
        lines = []
        for sid, (labels, conf, read) in sorted(why.items()):
            ident = f" (camera read {read}, identity {conf:.2f})" if run.lists[sid].noisy else ""
            lines.append(f"{sid}: " + "; ".join(labels) + ident)
        out.append(RankedVehicle(plate=plate, score=round(score / weight_total, 4) if weight_total else 0.0,
                                 partial={s: round(v, 4) for s, v in run.partial.get(plate, {}).items()},
                                 identity_confidence=min((c for _, c, _ in why.values()), default=1.0),
                                 explanation=lines))
    return out


def baseline_rows(feats: Dict[str, List[S.Feature]], client: Any) -> int:
    """Rows the naive mediator would ship (every matching joined row, every source), measured with COUNT(*)."""
    total = 0
    for sid, fs in feats.items():
        rows = client.query(sid, S.count_sql(sid, fs)) or [{}]
        total += int(float(rows[0].get("n") or 0))
    return total


def topk(query: ParsedQuery, k: int = 10, client: Any = None, method: str = "ta",
         measure_baseline: bool = True, verify: bool = True) -> TopKResult:
    """Best k vehicles for a parsed query. method = "ta" | "tput". Never raises on a dead source."""
    t0 = time.perf_counter()
    if client is None:
        from mediator.source_client import SourceClient
        client = SourceClient()
    feats = S.features_for(query)
    weight_total = sum(f.weight for fs in feats.values() for f in fs)
    codes = sorted({str(p.value) for p in query.predicates if p.attr == "indicator"})
    if codes and verify and not query.plates:
        from mediator.topk_refine import refined_topk   # indicator queries: TA + lazy cross-source check
        run, depth, vehicles = refined_topk(feats, codes, k, client, weight_total)
        method = "ta+refine"
    else:
        run, depth = execute(feats, k, client, method, plates=query.plates)
        vehicles = ranked(run, k, weight_total, keep_zero=query.plates)
    result = TopKResult(query=query, vehicles=vehicles, sorted_accesses=run.sorted_accesses,
                        random_accesses=run.random_accesses, rows_shipped=run.rows_shipped,
                        baseline_rows=baseline_rows(feats, client) if measure_baseline else 0,
                        stopped_at_depth=depth, elapsed_ms=round((time.perf_counter() - t0) * 1000, 1))
    result.round_trips = run.round_trips    # type: ignore[attr-defined]  # requested as a TopKResult field
    result.method = method                  # type: ignore[attr-defined]
    return result
