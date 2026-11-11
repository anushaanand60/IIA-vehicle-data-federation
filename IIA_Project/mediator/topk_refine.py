"""CONTEXT-ADDED (R2: entity top-k): exact top-k when a predicate spans two agencies ("tested for PUC after theft").

No single source can score "a PUC test inside the stolen interval": PUC does not know the theft date. So each
source scores the part it can judge alone (THEFT: stolen; PUC: has a test) and that TA total is an UPPER BOUND on
the true score. The expensive cross-source check (analytics.indicators_for, restricted to the agencies the
indicator needs) is then run lazily, best upper bound first - filter and refine:

    verified(v) = TA total(v) - weight of every precondition whose cross-source check failed
    stop when the k-th best verified score >= max(best unverified upper bound, TA threshold for unseen vehicles)

If the k-th verified score is still below the TA threshold, TA reads its lists deeper and the loop continues.
A budget caps the number of vehicles checked; if it is reached the result says so instead of pretending.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from mediator import entity_topk as E
from mediator import topk_sql as S
from mediator.context_types import RankedVehicle

VERIFY_BUDGET = 60     # vehicles checked at most (each check is a handful of bind-join queries)


class _Counted:
    """Client proxy so the verification traffic is reported with the top-k traffic, not hidden."""

    def __init__(self, run: E._Run, client: Any) -> None:
        self.run, self.client = run, client

    def query(self, sid: str, sql: str) -> List[Dict[str, Any]]:
        return self.run._query(sid, sql)

    def post(self, sid: str, path: str, payload: Dict[str, Any]) -> Any:
        return self.client.post(sid, path, payload)

    def stats(self) -> Any:
        return self.client.stats()


class Refiner:
    def __init__(self, codes: List[str], client: Any, budget: int = VERIFY_BUDGET) -> None:
        self.codes, self.client, self.budget = codes, client, budget
        self.scores: Dict[str, float] = {}
        self.notes: Dict[str, List[str]] = {}
        self.budget_hit = False

    def _penalty(self, run: E._Run, plate: str, confirmed: set) -> float:
        """Weight (identity-discounted) of every matched precondition whose indicator was not confirmed."""
        total = 0.0
        for sid, (labels, conf, _) in run.why.get(plate, {}).items():
            for f in run.lists[sid].feats:
                code = f.label.replace("precondition of ", "") if f.label.startswith("precondition of ") else None
                if code and f.label in labels and code not in confirmed:
                    total += f.weight * conf
        return total

    def verify(self, run: E._Run, plate: str) -> None:
        from mediator.analytics import indicators_for   # analytics imports entity_topk: import late
        found = {i.code: i for i in indicators_for(plate, client=_Counted(run, self.client), codes=self.codes)}
        self.scores[plate] = max(0.0, run.total(plate) - self._penalty(run, plate, set(found)))
        self.notes[plate] = [f"verified {c}: {found[c].explanation}" if c in found
                             else f"{c} not confirmed across sources (precondition only, weight removed)"
                             for c in self.codes]

    def __call__(self, run: E._Run, k: int, threshold: float) -> bool:
        while True:
            verified = sorted(self.scores.values(), reverse=True)
            kth = verified[k - 1] if len(verified) >= k else float("-inf")
            todo = sorted(((p, run.total(p)) for p in run.partial if p not in self.scores and run.total(p) > 0),
                          key=lambda x: (-x[1], x[0]))
            best_unverified = todo[0][1] if todo else 0.0
            if kth >= max(threshold, best_unverified):
                return True
            if not todo or best_unverified < kth:      # nothing seen can win: read deeper (threshold > kth)
                return False
            if len(self.scores) >= self.budget:
                self.budget_hit = True
                return True
            self.verify(run, todo[0][0])


def refined_topk(feats: Dict[str, List[S.Feature]], codes: List[str], k: int, client: Any,
                 weight_total: float) -> Tuple[E._Run, int, List[RankedVehicle]]:
    run = E._Run(client=client, lists=E._lists(feats))
    refiner = Refiner(codes, client)
    depth = E.run_ta(run, k, stop=refiner) if run.lists else 0
    if run.lists and not refiner.budget_hit:
        refiner(run, k, 0.0)          # lists exhausted: finish the checks the last round still needed
    vehicles = E.ranked(run, k, weight_total, scores=refiner.scores)
    for v in vehicles:
        v.explanation += refiner.notes.get(v.plate, [])
        if refiner.budget_hit:
            v.explanation.append(f"verification budget of {refiner.budget} vehicles reached: ranking may be incomplete")
    return run, depth, vehicles
