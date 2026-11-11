"""CONTEXT-ADDED (R2): discover() -- bounded multi-round federation vs its baselines and a central oracle.

Sources are real wrappers over tiny SQLite files, reached through an in-process client (no ports).
The oracle reads every database directly, builds one global graph (FK edges both ways + bridge edges) and
computes the best-affinity score of every row centrally; bounded discover() must return the same top n.
"""
from __future__ import annotations

import copy
import heapq
import random
import sqlite3
from typing import Any, Dict, List, Tuple

import pytest

from mediator.context_discovery import discover, to_parsed
from mediator.context_scoring import (canon, edge_weights_from_rows, load_graph, noisy_plate_sources, ocr_variants,
                                      params_of, pk_key, plate_confidence, relevance)
from mediator.context_types import ContextResult, ParsedQuery
from tests.context_fixture import STORY, InProcessClient, build, generate, write_graph

GRAPH = load_graph()


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("disc")
    return build(tmp, seed=7, vehicles=24)


def run(world, mode="bounded", down=(), **kw) -> Tuple[ContextResult, InProcessClient]:
    client = InProcessClient(world, down=set(down))
    return discover(kw.pop("query", STORY), mode=mode, client=client, **kw), client


def shipped(client) -> int:
    return sum(s.rows_shipped for s in client.stats())


def test_plate_query_returns_n_rows_best_first_and_stops_by_the_bound(world):
    res, _ = run(world, n=8)
    assert len(res.rows) == 8 and res.complete
    assert [r.score for r in res.rows] == sorted((r.score for r in res.rows), reverse=True)
    final = res.trace[-1]
    assert final["final"] and final["stopped"] == "bound"
    assert final["tau_n"] >= final["max_frontier_bound"] and final["tau_n"] >= final["max_source_bound"]
    assert {r.source_id for r in res.rows} >= {"REG", "INS", "THEFT"}


def test_trace_records_the_threshold_convergence(world):
    res, _ = run(world, n=8)
    rounds = [t for t in res.trace if not t.get("final")]
    assert rounds and all({"round", "tau_n", "max_frontier_bound", "expanded", "pruned"} <= set(t) for t in rounds)
    taus = [t["tau_n"] for t in res.trace]
    assert taus == sorted(taus)                                          # tau_n never decreases
    assert any("shipping" in t for t in rounds)
    ship = next(t["shipping"] for t in rounds if "shipping" in t)
    assert all({"list_bytes", "bloom_bytes_est", "shipped"} <= set(v) for v in ship.values())


def test_bounded_moves_less_data_than_every_baseline(world):
    bounded, cb = run(world, n=8)
    closure, cc = run(world, mode="closure", n=8)
    b2, cm = run(world, mode="mediator", n=8)
    topn, ct = run(world, mode="topn", n=8)
    assert shipped(cb) < shipped(cc) and shipped(cb) < shipped(cm)
    assert sum(s.round_trips for s in cb.stats()) < sum(s.round_trips for s in cm.stats())
    assert closure.baseline_rows == shipped(cc) and len(closure.rows) > len(bounded.rows)
    assert topn.rounds == 1 and len(ct.log) == 5                        # one /context per source, nothing else
    # what the bounded answer holds is the best the full closure knows about, row for row
    best = {(r.source_id, r.table, str(r.pk)): r.score for r in closure.rows}
    for r in bounded.rows:
        assert r.score <= best.get((r.source_id, r.table, str(r.pk)), r.score) + 1e-6


def test_a_down_source_degrades_never_raises(world):
    res, client = run(world, down={"INS"}, n=8)
    assert res.complete is False and res.rows
    st = {s.source_id: s for s in res.stats}
    assert st["INS"].status == "DOWN" and st["INS"].bound == 1.0
    assert all(st[s].status == "OK" for s in ("REG", "THEFT", "CAM", "PUC"))
    assert not any(r.source_id == "INS" for r in res.rows)
    every, _ = run(world, down={"REG", "INS", "THEFT", "CAM", "PUC"}, n=8)
    assert every.rows == [] and every.complete is False


def test_person_ring_is_resolved_and_the_namesake_kept_apart(world):
    res, client = run(world, n=10)
    ring = [c for c in res.persons if {m[0] for m in c.members} == {"REG", "INS", "THEFT"}]
    assert ring and ring[0].canonical["mobile"] == "9811111111"
    namesake = max(m["owner_id"] for m in generate(7, 24)["REG"]["OWNERS"])         # Ravi Kumar, other mobile
    assert all(("REG", "OWNERS", namesake) not in c.members for c in res.persons)
    person_sql = [e["payload"]["sql"] for e in client.log if e["path"] == "/query"]
    assert person_sql and all(" WHERE " in q for q in person_sql)                    # keys pushed down, no full tables


def test_bridge_rows_carry_their_cross_source_path(world):
    res, _ = run(world, n=25, theta=0.02)
    bridged = [r for r in res.rows if r.via_bridge]
    assert bridged
    for r in bridged:
        assert any("->" in step for step in r.path) and 0 < r.bridge_confidence <= 1


def test_noisy_camera_reads_match_with_reduced_confidence(world):
    res, _ = run(world, n=40, theta=0.01)
    cam = [r for r in res.rows if r.source_id == "CAM" and r.table == "PLATE_CAPTURES"]
    reads = {r.row["plate_id"]: r.bridge_confidence for r in cam}
    assert reads.get(STORY) == 1.0 and 0.9 < reads.get("DLO5CD9876", 0) < 1.0


def test_string_queries_are_understood(monkeypatch):
    assert to_parsed("dl-05-cd-9876").plates == [STORY]
    import mediator.context_discovery as cd
    monkeypatch.setitem(__import__("sys").modules, "mediator.query_understanding", None)  # parser unavailable
    pq = cd.to_parsed("stolen white creta")
    assert pq.plates == [] and {"stolen", "white", "creta"} <= set(pq.keywords)


def test_free_text_query_without_a_plate_still_finds_context(world):
    res, _ = run(world, query=ParsedQuery(text="investigating claim", keywords=["investigating"]), n=5)
    assert res.rows and res.rows[0].table == "CLAIMS"


def test_unknown_mode_is_rejected(world):
    with pytest.raises(ValueError):
        run(world, mode="everything")


# --------------------------------------------------------------------------- central oracle

def _read_all(url: str) -> Dict[str, List[Dict[str, Any]]]:
    con = sqlite3.connect(url.replace("sqlite:///", ""))
    con.row_factory = sqlite3.Row
    names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    out = {n: [dict(r) for r in con.execute(f"SELECT * FROM {n}")] for n in names}
    con.close()
    return out


def oracle(urls, graph, plate, keywords, window, n, max_hops):
    """Global best-affinity search over all sources at once (what a central warehouse would compute)."""
    prm, gamma, noisy = params_of(graph), float(params_of(graph)["gamma_hops"]), set(noisy_plate_sources(graph))
    data = {sid: _read_all(urls[sid]) for sid in graph["sources"]}
    adj: Dict[tuple, List[tuple]] = {}
    rowof, bridge_of, by_value = {}, {}, {}
    for sid, src in graph["sources"].items():
        for t, spec in src["tables"].items():
            for r in data[sid][t]:
                node = (sid, t, pk_key(spec, r))
                rowof[node] = (spec, r)
        for e, (wf, wr) in zip(src["edges"], edge_weights_from_rows(graph, sid, data[sid])):
            idx = {}
            for r in data[sid][e["to"]]:
                idx.setdefault(r[e["to_col"]], []).append(r)
            for s in data[sid][e["from"]]:
                for o in idx.get(s[e["col"]], []) if s[e["col"]] is not None else []:
                    a, b = (sid, e["from"], pk_key(src["tables"][e["from"]], s)), (sid, e["to"], pk_key(src["tables"][e["to"]], o))
                    adj.setdefault(a, []).append((b, wf))
                    adj.setdefault(b, []).append((a, wr))
    for name, br in graph["bridges"].items():
        for sid, t, col in br["members"]:
            for r in data[sid][t]:
                node = (sid, t, pk_key(graph["sources"][sid]["tables"][t], r))
                bridge_of[node] = (name, canon(r[col]))
                by_value.setdefault((sid, name, canon(r[col])), []).append(node)

    def landings(origin_sid, attr, value):
        for t in {m[0] for m in graph["bridges"][attr]["members"]} - {origin_sid}:
            pairs = [(v, plate_confidence(value, v)) for v in ocr_variants(value)] \
                if attr == "plate" and (origin_sid in noisy or t in noisy) else [(value, 1.0)]
            for v, c in pairs:
                for node in by_value.get((t, attr, v), []):
                    yield node, c

    best: Dict[tuple, float] = {}
    heap = [(-c, node, 0) for node, c in landings(None, "plate", canon(plate))]
    seen: Dict[tuple, float] = {}
    while heap:
        neg, node, h = heapq.heappop(heap)
        aff = -neg
        if seen.get((node, h), -1) >= aff:
            continue
        seen[(node, h)] = aff
        best[node] = max(best.get(node, 0.0), aff)
        if h < max_hops:
            for nb, w in adj.get(node, []):
                if aff * w * gamma > seen.get((nb, h + 1), -1):
                    heapq.heappush(heap, (-(aff * w * gamma), nb, h + 1))
        if node in bridge_of:
            for nb, c in landings(node[0], *bridge_of[node]):
                if aff * c > seen.get((nb, 0), -1):
                    heapq.heappush(heap, (-(aff * c), nb, 0))
    scores = sorted((a * relevance(*rowof[nd], keywords, window, prm) for nd, a in best.items()), reverse=True)
    return scores[:n]


@pytest.mark.parametrize("case", range(8))
def test_bounded_equals_the_central_oracle(tmp_path, monkeypatch, case):
    rng = random.Random(1000 + case)
    graph = copy.deepcopy(GRAPH)
    graph["bridges"].pop("person")       # people resolve probabilistically; the oracle checks the exact bridges
    monkeypatch.setenv("CONTEXT_GRAPH", write_graph(tmp_path, graph))
    urls = build(tmp_path, seed=case, vehicles=rng.randint(10, 26))
    plates = [r["registration_no"] for r in generate(case, 1)["REG"]["VEHICLE_REGISTRATION"]] + [STORY]
    plate = rng.choice(plates)
    keywords = rng.choice([[], ["theft"], ["white", "pending"], ["open"]])
    window = rng.choice([None, ("2026-03-01", "2026-03-31"), ("2020-01-01", "2026-12-31")])
    n, theta, hops = rng.choice([3, 5, 8]), 0.01, int(graph["params"]["max_hops"])
    pq = ParsedQuery(text=plate, plates=[plate], keywords=keywords, time_window=window)
    res = discover(pq, n=n, theta=theta, client=InProcessClient(urls), graph=graph, max_rounds=30, per_parent=10**6)
    expect = oracle(urls, graph, plate, keywords, window, n, hops)
    assert expect[-1] >= theta                                    # theta can only matter below the n-th score
    assert res.trace[-1]["stopped"] == "bound"
    assert [round(r.score, 4) for r in res.rows] == [round(s, 4) for s in expect]
