"""CONTEXT-ADDED (R2): federated, bounded, multi-round context discovery across the agencies.

Round 1 asks every source's POST /context in parallel (each DBMS ranks its own FK neighbourhood and returns
its top n + an exact bound u_s on anything it kept back + bridge-column hints). Bridge values then become
frontier states (a bind join across databases); the mediator expands states best-bound-first and stops,
in the spirit of Fagin's threshold algorithm, when

    tau_n >= max( max_s u_s , max_f A_f x c_f x R-bar_t )        (score = affinity A x relevance R)

tau_n = n-th best global score so far. Every unexpanded state can only produce rows below its bound, so the
answer is the exact top n of the theta-closure (theta and max_hops are the stated approximation knobs).
Modes: "bounded" (ours), "topn" (B1: one round, no follow-ups), "closure" (B0: full FK closure everywhere
plus every bridge value followed once), "mediator" (B2: mediator walks FK edges with /query, see
mediator/context_baseline.py). A source that is down never raises: complete=False and u_s = 1.
Note: a call that returns its top n always leaves u_s <= tau_n (the global n-th is at least that call's n-th),
so the "deepen a source" branch only fires when a source had to cap its bridge hints; it is kept for that case.
"""
from __future__ import annotations

import math
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from mediator.context_bridges import FrontierState, shipping_estimate, spawn_states
from mediator.context_people import resolve_pending
from mediator.context_probe import probe_baseline  # CONTEXT-ADDED (R2: B0 size without shipping B0)
from mediator.context_scoring import (canon, load_graph, max_relevance, noisy_plate_sources, ocr_variants_conf,
                                      params_of, seed_factor)
from mediator.context_types import ContextResult, ContextRow, ParsedQuery

HINTS_PER_N = 5
CLOSURE_CHUNK = 1000   # CONTEXT-ADDED (R2): seeds per B0 follow-up request; ~1 s of in-DB work, under the 3 s timeout
PLATE_RE = re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{0,3}\d{1,4}$")


def to_parsed(query: Any) -> ParsedQuery:
    if isinstance(query, ParsedQuery):
        return query
    text = str(query or "").strip()
    if PLATE_RE.match(canon(text)) and len(text.split()) <= 4:
        return ParsedQuery(text=text, plates=[canon(text)])
    try:
        from mediator.query_understanding import parse
        return parse(text)
    except Exception:  # parser missing or failed: the words themselves are the keywords
        return ParsedQuery(text=text, keywords=[w for w in re.findall(r"\w+", text.lower()) if len(w) > 2],
                           notes=["query_understanding unavailable: words used as keywords"])


def _keywords(pq: ParsedQuery) -> List[str]:
    extra = [str(p.value) for p in pq.predicates if p.op in ("=", "~") and isinstance(p.value, str) and len(p.value) > 2]
    return sorted({k.lower() for k in list(pq.keywords) + extra if k})


class _Run:
    """State of one discover() call: rows found, values sent, open bounds, frontier, trace."""

    def __init__(self, graph: Dict[str, Any], client: Any, pq: ParsedQuery, n: int, theta: float, mode: str,
                 base: Dict[str, Any]) -> None:
        self.graph, self.client, self.pq, self.n, self.theta, self.mode, self.base = graph, client, pq, n, theta, mode, base
        self.sources = list(graph["sources"])
        self.prm = prm = params_of(graph)
        self.rbar = {s: max_relevance(graph["sources"][s], base["keywords"], base["window"], prm) for s in self.sources}
        self.ew: Dict[str, List[Dict[str, Any]]] = {}      # edge weights each source measured and reported
        self._factor: Dict[Tuple[str, str, Optional[str]], float] = {}  # CONTEXT-ADDED (R2: perf) memo for factor()
        self.rows: Dict[Tuple[str, str, str], ContextRow] = {}
        self.sent: Dict[Tuple[str, str, str], float] = {}
        self.meta: Dict[Tuple[str, str], FrontierState] = {}
        self.open: Dict[str, List[float]] = {}
        self.n_sent: Dict[str, int] = {}
        self.dead: set = set()
        self.frontier: List[FrontierState] = []
        self.pending: Dict[Tuple[str, str, str], Tuple[float, List[str], int]] = {}
        self.resolved: set = set()
        self.persons: list = []
        self.hints: Dict[str, int] = {}
        self.trace: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ calls
    def call(self, requests: Dict[str, Any]) -> None:
        # CONTEXT-REPLACED (R2: a source may get several requests in one round -- closure seeds are chunked):
        # futures = {sid: pool.submit(self.client.post, sid, "/context", _strip(body)) ...}
        batches = {sid: (r if isinstance(r, list) else [r]) for sid, r in requests.items()}

        def per_source(sid: str) -> List[Optional[Dict[str, Any]]]:  # sequential within a source, parallel across
            return [self.client.post(sid, "/context", _strip(body)) for body in batches[sid]]

        with ThreadPoolExecutor(max_workers=max(1, len(batches))) as pool:
            futures = {sid: pool.submit(per_source, sid) for sid in batches}
        for sid, fut in futures.items():
            try:
                bodies = fut.result()
            except Exception:  # a misbehaving client must not crash the query either
                bodies = [None]
            for body, req in zip(bodies, batches[sid]):
                if body is None:
                    self.dead.add(sid)
                else:
                    self.absorb(sid, body, req)
        # CONTEXT-END

    def factor(self, t: str, kind: str, table: Optional[str] = None) -> float:
        # CONTEXT-ADDED (R2: perf -- memoised; it was recomputed for every one of ~10^5 states in closure mode)
        key = (t, kind, table)
        if key not in self._factor:
            src = self.graph["sources"].get(t, {})
            seed_tables = [table] if table else [name for name, s in src.get("tables", {}).items() if s.get(kind)]
            self._factor[key] = seed_factor(src, seed_tables, self.base["keywords"], self.base["window"], self.prm,
                                            self.ew.get(t))
        return self._factor[key]
        # CONTEXT-END

    def absorb(self, sid: str, body: Dict[str, Any], req: Dict[str, Any]) -> None:
        if body.get("edge_weights") is not None:
            self.ew[sid] = body["edge_weights"]
            self._factor = {k: v for k, v in self._factor.items() if k[0] != sid}  # CONTEXT-ADDED (R2: perf) new weights
        for r in body.get("rows", []):
            st = self.meta.get((sid, r.get("seed") or ""))
            path = (st.origin_path if st else []) + list(r.get("path", []))
            hops = (st.origin_hops + 1 if st and st.bridge else 0) + int(r.get("hops", 0))
            key = (sid, r["table"], str(r.get("pk_key", r["pk"])))
            row = ContextRow(sid, r["table"], r["pk"], r.get("row", {}), float(r["score"]), path, hops,
                             st.bridge if st else None, st.confidence if st else 1.0)
            if key not in self.rows or row.score > self.rows[key].score:
                self.rows[key] = row
            if b := r.get("bridge"):
                self.spawn(sid, b["attr"], b["value"], float(r.get("affinity", r["score"])), path, hops)
        for h in body.get("hints", []):
            st = self.meta.get((sid, h.get("seed") or ""))
            self.spawn(sid, h["attr"], h["value"], float(h["affinity"]),
                       (st.origin_path if st else []) + [f"{h['table']}#{h['pk']}"], int(h.get("hops", 0)))
        self.hints[sid] = self.hints.get(sid, 0) + len(body.get("hints", []))
        need = max(float(body.get("bound_cut") or 0.0), float(body.get("bound_hints") or 0.0) * max(self.rbar.values()))
        self.open[sid] = [need] if req.get("_all") else self.open.get(sid, []) + [need]

    def spawn(self, sid: str, attr: str, value: Any, aff: float, path: List[str], hops: int) -> None:
        if attr == "person":
            key = (sid, self._person_table(sid), str(value))
            if key not in self.pending or self.pending[key][0] < aff:
                self.pending[key] = (aff, path, hops)
            return
        self.frontier += spawn_states(self.graph, sid, attr, value, aff, self.factor, self.dead, path, hops)

    def _person_table(self, sid: str) -> str:
        return next((m[1] for m in self.graph.get("bridges", {}).get("person", {}).get("members", []) if m[0] == sid), "")

    def request(self, sid: str, states: List[FrontierState], n: int, all_seeds: bool) -> Dict[str, Any]:
        seeds: Dict[str, Any] = {"plate": [], "chassis": [], "pks": {}, "weights": {}}
        chosen = {k: w for k, w in self.sent.items() if k[0] == sid} if all_seeds else {}
        for st in states:
            if st.weight > chosen.get(st.key, -1.0):
                chosen[st.key] = st.weight
                self.meta[(sid, self._seed_key(st))] = st
        for (_, kind, value), w in chosen.items():
            self.sent[(sid, kind, value)] = max(w, self.sent.get((sid, kind, value), 0.0))
            if kind == "pk":
                table, pk = value.split("#", 1)
                seeds["pks"].setdefault(table, []).append(pk)
            else:
                seeds[kind].append(value)
            seeds["weights"][self._seed_key(FrontierState(sid, kind, value, w, 0, "", 1))] = round(w, 6)
        self.n_sent[sid] = n
        # a hint with affinity a can only lead to scores <= a x max R-bar: below tau_n / max R-bar it is useless
        hint_min = math.floor(self.tau() / max(1e-9, max(self.rbar.values())) * 1e6) / 1e6 if self.mode != "closure" else 0.0
        # hints beyond 5n are cut at the source; it reports the best cut affinity (u_hint), and the deepen rule
        # re-asks with a larger cap if that could still matter -- fewer bytes, same exact answer
        return {**self.base, "seeds": seeds, "n": n, "_all": all_seeds, "hint_min": hint_min, "max_hints": HINTS_PER_N * n,
                "_ship": shipping_estimate(sorted({v for (_, _, v) in chosen}))}

    @staticmethod
    def _seed_key(st: FrontierState) -> str:
        return f"pk:{st.value}" if st.kind == "pk" else f"{st.kind}:{st.value}"

    # ------------------------------------------------------------------ bookkeeping
    def tau(self) -> float:
        scores = sorted((r.score for r in self.rows.values()), reverse=True)
        return scores[self.n - 1] if len(scores) >= self.n else 0.0

    def live(self) -> List[FrontierState]:
        return [f for f in self.frontier if f.target not in self.dead and f.weight > self.sent.get(f.key, 0.0) + 1e-12
                and (self.mode == "closure" or f.weight >= self.theta)]


def _round1(run: _Run, pq: ParsedQuery, seeds: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    noisy, reqs = set(noisy_plate_sources(run.graph)), {}
    for sid in run.sources:
        states = []
        for p in pq.plates:
            pairs = list(ocr_variants_conf(p)) if sid in noisy else [(canon(p), 1.0)]
            states += [FrontierState(sid, "plate", v, c, c, "plate", c) for v, c in pairs]
        tables = run.graph["sources"][sid]["tables"]
        for group in ("pks", "person_ids"):
            for t, vals in ((seeds or {}).get(group) or {}).items():
                states += [FrontierState(sid, "pk", f"{t}#{v}", 1.0, 1.0, "", 1.0) for v in vals if t in tables]
        for kind in ("plate", "chassis"):
            states += [FrontierState(sid, kind, canon(v), 1.0, 1.0, "", 1.0) for v in (seeds or {}).get(kind) or []]
        reqs[sid] = run.request(sid, states, run.n, False)
        if not states:  # free text with no entity: the source seeds itself from keyword hits
            reqs[sid]["seeds"] = {}
    for st in list(run.meta.values()):
        st.bridge, st.origin_path = None, []  # type: ignore[assignment]  # query seeds are not bridges
    return reqs


def discover(query: Any, n: int = 10, theta: float = 0.05, mode: str = "bounded", client: Any = None,
             max_rounds: int = 4, *, seeds: Optional[Dict[str, Any]] = None, explain: bool = False,
             graph: Optional[Dict[str, Any]] = None, max_hops: Optional[int] = None,
             per_parent: Optional[int] = None, with_baseline: bool = False) -> ContextResult:
    t0 = time.perf_counter()
    pq, graph = to_parsed(query), graph or load_graph()
    if client is None:
        from mediator.source_client import SourceClient
        client = SourceClient()
    if mode == "mediator":
        from mediator.context_baseline import run_mediator_baseline
        return run_mediator_baseline(pq, graph, client, n=n, theta=theta, max_hops=max_hops, seeds=seeds)
    if mode not in ("bounded", "topn", "closure"):
        raise ValueError(f"unknown mode {mode!r}")
    base = {"theta": theta, "window": list(pq.time_window) if pq.time_window else None, "keywords": _keywords(pq),
            "mode": "closure" if mode == "closure" else "bounded", "max_hops": max_hops, "per_parent": per_parent,
            "explain": explain}
    run = _Run(graph, client, pq, n, theta, mode, base)
    first = _round1(run, pq, seeds)
    run.call(first)
    rounds, stopped = 1, "single round" if mode == "topn" else "bound"
    while mode != "topn":
        tau = 0.0 if mode == "closure" else run.tau()
        before = _trips(client)
        resolve_pending(run, tau, everything=(mode == "closure"))
        live = run.live()
        expand = live if mode == "closure" else [f for f in live if f.bound > tau]
        deepen = [] if mode == "closure" else [s for s, b in run.open.items() if s not in run.dead and max(b, default=0) > tau]
        entry = {"round": rounds, "tau_n": tau, "max_frontier_bound": max((f.bound for f in live), default=0.0),
                 "max_source_bound": max([max(b, default=0.0) for b in run.open.values()] + [1.0 if run.dead else 0.0]),
                 "expanded": len(expand), "pruned": len(live) - len(expand), "deepened": deepen,
                 "person_queries": _trips(client) - before, "rows_so_far": len(run.rows)}
        run.trace.append(entry)
        if (not expand and not deepen) or rounds >= max_rounds or (mode == "closure" and rounds >= 2):
            stopped = ("closure: every bridge followed once" if mode == "closure"
                       else "bound" if not expand and not deepen else "max_rounds")
            break
        reqs: Dict[str, Any] = {}
        for sid in sorted({f.target for f in expand} | set(deepen)):
            mine = [f for f in expand if f.target == sid]
            if mode == "closure":  # CONTEXT-ADDED (R2: chunk B0's thousands of seeds under the statement timeout)
                keys = sorted({f.key for f in mine})
                chunks = [set(keys[i:i + CLOSURE_CHUNK]) for i in range(0, len(keys), CLOSURE_CHUNK)]
                reqs[sid] = [run.request(sid, [f for f in mine if f.key in ch], n, False) for ch in chunks]
                continue  # CONTEXT-END
            reqs[sid] = run.request(sid, mine, run.n_sent.get(sid, n) * (2 if sid in deepen else 1), sid in deepen)
        entry["shipping"] = {sid: (r[0] if isinstance(r, list) else r)["_ship"] for sid, r in reqs.items()}
        run.frontier = []  # expanded now, or pruned for good: tau_n never decreases, so a pruned bound stays pruned
        run.call(reqs)
        rounds += 1
    probe = probe_baseline(run, first) if with_baseline and mode != "closure" else None  # CONTEXT-ADDED (R2)
    return _finish(run, client, rounds, stopped, t0, probe)


def _trips(client: Any) -> int:
    return sum(s.round_trips for s in client.stats())


def _strip(req: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in req.items() if not k.startswith("_") and v is not None}


def _finish(run: _Run, client: Any, rounds: int, stopped: str, t0: float,
            probe: Optional[Dict[str, Any]] = None) -> ContextResult:
    ordered = sorted(run.rows.values(), key=lambda r: (-r.score, r.source_id, r.table, str(r.pk)))
    stats = client.stats()
    for st in stats:
        st.bound = 1.0 if st.source_id in run.dead else max(run.open.get(st.source_id, []), default=0.0)
    shipped = sum(s.rows_shipped for s in stats)
    res = ContextResult(query=run.pq, rows=ordered if run.mode == "closure" else ordered[:run.n], stats=stats,
                        persons=run.persons, rounds=rounds, complete=not run.dead,
                        baseline_rows=shipped if run.mode == "closure" else (probe or {}).get("rows"),
                        elapsed_ms=(time.perf_counter() - t0) * 1000)
    tau = run.tau() if run.mode != "closure" else 0.0
    run.trace.append({"round": rounds, "tau_n": tau, "final": True, "stopped": stopped,
                      "max_frontier_bound": max((f.bound for f in run.live()), default=0.0),
                      "max_source_bound": max([max(b, default=0.0) for b in run.open.values()] + [1.0 if run.dead else 0.0]),
                      "expanded": 0, "pruned": 0, "rows_shipped": shipped,  # summary row: counts live in the rounds above
                      "hints_shipped": sum(run.hints.values())})
    # CONTEXT-REPLACED (R2: trace / hints_shipped are now ContextResult and SourceStats fields):
    # res.trace = run.trace  # type: ignore[attr-defined]
    # res.hints_shipped = dict(run.hints)  # type: ignore[attr-defined]
    if probe:
        run.trace[-1]["baseline_probe"] = probe
    res.trace, res.hints_shipped = run.trace, dict(run.hints)
    for st in stats:
        st.hints_shipped = run.hints.get(st.source_id, 0)
    # CONTEXT-END
    return res
