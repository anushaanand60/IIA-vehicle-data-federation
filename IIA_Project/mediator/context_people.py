"""CONTEXT-ADDED (R2): the person bridge inside discover() -- resolve people only when they can still matter.

A person row (REG owner, INS holder, THEFT person) reached with affinity A can lead, through another agency,
to rows scoring at most A x 1 x R-bar_t. Only persons whose bound beats tau_n are resolved: their records are
read by pk, their blocking keys are pushed to the other agencies as /query filters (candidates only), and the
Fellegi-Sunter posterior p of each cross-agency pair becomes the bridge confidence of a new frontier state.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from mediator.context_bridges import FrontierState, blocking_keys, fetch_person_records, person_sources
from mediator.person_resolution import THRESHOLD, pair_posteriors, resolve_persons


def resolve_pending(run: Any, tau: float, everything: bool = False) -> None:
    psrc = person_sources(run.graph)
    best_other = {s: max([run.factor(t, "pk", psrc[t]["table"]) for t in psrc if t != s and t not in run.dead],
                         default=0.0) for s in psrc}
    todo = {k: v for k, v in run.pending.items()
            if k not in run.resolved and k[0] not in run.dead and (everything or v[0] * best_other.get(k[0], 0) > tau)}
    if not todo:
        return
    origin: List[Dict[str, Any]] = []
    for sid in sorted({k[0] for k in todo}):
        origin += fetch_person_records(run.client, run.graph, sid, pks=[k[2] for k in todo if k[0] == sid])
    cands: List[Dict[str, Any]] = []
    for t in sorted(psrc):
        others = [r for r in origin if r["source_id"] != t]
        if t not in run.dead and others:
            cands += fetch_person_records(run.client, run.graph, t, **blocking_keys(others))
    records, seen = [], set()
    for r in origin + cands:
        k = (r["source_id"], r["table"], str(r["pk"]))
        if k not in seen:
            seen.add(k)
            records.append(r)
    probs, params = pair_posteriors(records)
    for (i, j), p in probs.items():
        for a, b in ((i, j), (j, i)):
            ra, rb = records[a], records[b]
            okey: Tuple[str, str, str] = (ra["source_id"], ra["table"], str(ra["pk"]))
            if okey in todo and rb["source_id"] != ra["source_id"] and p >= THRESHOLD:
                aff, path, hops = todo[okey]
                w = aff * p
                run.frontier.append(FrontierState(rb["source_id"], "pk", f"{rb['table']}#{rb['pk']}", w,
                                                  w * run.factor(rb["source_id"], "pk", rb["table"]), "person", p,
                                                  list(path) + [f"person->{rb['source_id']}"], hops))
    run.resolved |= set(todo)
    known = {tuple(map(str, m)) for c in run.persons for m in c.members}
    for c in resolve_persons(records, params=params):
        touches_context = any((m[0], m[1], str(m[2])) in todo for m in c.members)
        if len(c.members) > 1 and touches_context and not any(tuple(map(str, m)) in known for m in c.members):
            run.persons.append(c)
