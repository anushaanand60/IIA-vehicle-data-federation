"""CONTEXT-ADDED (R2): how many rows the B0 baseline WOULD ship, measured without shipping them.

discover(..., with_baseline=True) asks each source for a count-only closure (POST /context with
mode="closure", count_only=true): the same recursive CTE runs, but only one integer and the closure's distinct
bridge values come back. Those values are then sent once to the other sources as count-only closures, exactly
as B0 follows every bridge value once. The probe's own traffic is recorded separately and removed from the
query's SourceStats, so the "data moved" numbers of the bounded answer stay honest.

Choice: counting in the DBMS costs one extra recursive query per source per step but moves integers and
bridge values instead of thousands of rows; it is opt-in because it roughly doubles in-DB work. Person
follow-ups (probabilistic, need resolution) are not counted, so the figure is a slight under-estimate of B0 --
conservative for our "less data moved" claim.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

from mediator.context_scoring import canon, noisy_plate_sources, ocr_variants_conf
from sources.context_sql import CLOSURE_CAP


def _post_all(client: Any, reqs: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    with ThreadPoolExecutor(max_workers=max(1, len(reqs))) as pool:
        futs = {sid: pool.submit(client.post, sid, "/context", body) for sid, body in reqs.items()}
    return {sid: f.result() for sid, f in futs.items()}


def _chunks(seeds: Dict[str, List[str]]) -> List[Dict[str, List[str]]]:
    """Same chunking as B0's follow-ups (sorted keys, CLOSURE_CHUNK per request), so the count matches B0."""
    from mediator.context_discovery import CLOSURE_CHUNK
    keys = sorted((k, v) for k, vs in seeds.items() for v in vs)
    out = []
    for i in range(0, len(keys), CLOSURE_CHUNK):
        part: Dict[str, List[str]] = {}
        for k, v in keys[i:i + CLOSURE_CHUNK]:
            part.setdefault(k, []).append(v)
        out.append(part)
    return out


def _post_lists(client: Any, reqs: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Any]]:
    with ThreadPoolExecutor(max_workers=max(1, len(reqs))) as pool:
        futs = {sid: pool.submit(lambda s=sid: [client.post(s, "/context", b) for b in reqs[s]]) for sid in reqs}
    return {sid: f.result() for sid, f in futs.items()}


def probe_baseline(run: Any, first: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    client = run.client
    saved = {st.source_id: dict(vars(st)) for st in client.stats()}
    base = {"mode": "closure", "count_only": True, "keywords": run.base["keywords"], "window": run.base["window"],
            "max_hops": run.base.get("max_hops")}
    live = [sid for sid in first if sid not in run.dead]
    step1 = _post_all(client, {sid: {k: v for k, v in {**base, "seeds": first[sid]["seeds"]}.items() if v is not None}
                               for sid in live})
    rows = sum(min(CLOSURE_CAP, int(b.get("closure_rows", 0))) for b in step1.values() if b)
    noisy, reqs = set(noisy_plate_sources(run.graph)), {}
    for t in live:
        sent = {(k, v) for (s, k, v) in run.sent if s == t}  # B0 never re-sends a value it already sent there
        seeds: Dict[str, List[str]] = {"plate": [], "chassis": []}
        for s, body in step1.items():
            if s == t or not body:
                continue
            for attr in ("plate", "chassis"):
                for v in body.get("bridge_values", {}).get(attr, []):
                    vals = [x for x, _ in ocr_variants_conf(v)] if attr == "plate" and (s in noisy or t in noisy) else [canon(v)]
                    seeds[attr] += [x for x in vals if (attr, x) not in sent]
        seeds = {k: sorted(set(v)) for k, v in seeds.items() if v}
        if seeds:
            reqs[t] = [{k: v for k, v in {**base, "seeds": chunk}.items() if v is not None} for chunk in _chunks(seeds)]
    step2 = _post_lists(client, reqs) if reqs else {}
    rows += sum(min(CLOSURE_CAP, int(b.get("closure_rows", 0))) for bs in step2.values() for b in bs if b)
    probe = {"rows": rows, "round_trips": 0, "bytes": 0,
             "complete": all(step1.values()) and all(b for bs in step2.values() for b in bs)}
    for st in client.stats():  # remove the probe's own traffic from the query's measured numbers
        before = saved.get(st.source_id)
        probe["round_trips"] += st.round_trips - (before or {}).get("round_trips", 0)
        probe["bytes"] += st.bytes_shipped - (before or {}).get("bytes_shipped", 0)
        if before is not None:
            vars(st).update(before)
    return probe
