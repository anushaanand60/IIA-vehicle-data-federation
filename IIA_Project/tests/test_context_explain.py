"""CONTEXT-ADDED (R2: admission proof and leads not followed): context_explain on hand-made results and on discover().

Pure parts use hand-built ContextResults; the discovery parts use the in-process SQLite sources of
tests/context_fixture.py (no ports), the same world the oracle test uses.
"""
from __future__ import annotations

import json
import math

import pytest

from mediator.context_bridges import FrontierState
from mediator.context_discovery import discover
from mediator.context_explain import (cutoff, explain_row, hop_weights, note_leads, pursue, split_path, top_leads)
from mediator.context_scoring import load_graph
from mediator.context_types import ContextResult, ContextRow, ParsedQuery, SourceStats
from tests.context_fixture import STORY, InProcessClient, build

GRAPH = load_graph()
EW = {"REG": [{"from": "VEHICLE_REGISTRATION", "to": "OWNERS", "col": "owner_id", "fwd": 1.0, "rev": 0.7},
              {"from": "OWNERSHIP_HISTORY", "to": "OWNERS", "col": "prev_owner_id", "fwd": 1.0, "rev": 0.9},
              {"from": "OWNERSHIP_HISTORY", "to": "OWNERS", "col": "new_owner_id", "fwd": 1.0, "rev": 0.6}]}


def _ctx() -> ContextResult:
    rows = [ContextRow("INS", "POLICY_RECORDS", 562, {}, 0.80, ["POLICY_RECORDS#562"], 0, affinity=1.0),
            ContextRow("REG", "OWNERSHIP_HISTORY", 53, {}, 0.24, ["VEHICLE_REGISTRATION#602", "OWNERS#602",
                       "OWNERSHIP_HISTORY#53"], 2, affinity=0.384, edge_weights=[0.8, 0.48]),
            ContextRow("INS", "POLICY_HOLDERS", 552, {}, 0.20, ["VEHICLE_REGISTRATION#602", "OWNERS#602",
                       "person->INS", "POLICY_HOLDERS#552"], 2, via_bridge="person", bridge_confidence=0.9,
                       affinity=0.72, edge_weights=[0.8])]
    stats = [SourceStats("REG", 3, 2048, 1, bound=0.0), SourceStats("INS", 2, 1024, 2, bound=0.11)]
    return ContextResult(query=ParsedQuery(text=STORY, plates=[STORY]), rows=rows, stats=stats,
                         trace=[{"round": 1, "tau_n": 0.2, "final": True}])


def test_split_path_gives_every_node_its_source_across_a_crossing():
    parts = split_path(["VEHICLE_REGISTRATION#602", "OWNERS#602", "person->INS", "POLICY_HOLDERS#552"], "INS",
                       {"VEHICLE_REGISTRATION": "REG", "OWNERS": "REG"})
    assert [p.get("source") for p in parts] == ["REG", "REG", None, "INS"] and parts[2] == {"bridge": "person", "to": "INS"}


def test_hop_weights_include_the_hop_discount_and_pick_the_parallel_edge_that_was_walked():
    path = ["VEHICLE_REGISTRATION#602", "OWNERS#602", "OWNERSHIP_HISTORY#53"]
    g = {**GRAPH, "params": {**GRAPH["params"], "gamma_hops": 0.8}}
    assert hop_weights(path, "REG", EW, g, target=0.8 * 0.48) == [0.8, 0.48]     # new_owner_id edge (0.6 x 0.8)
    assert hop_weights(path, "REG", EW, g, target=0.8 * 0.72) == [0.8, 0.72]     # prev_owner_id edge (0.9 x 0.8)
    assert hop_weights(path, "REG", {}, g) == []                                 # unknown weights: nothing guessed


def test_explain_row_states_the_whole_admission_proof():
    ctx = _ctx()
    e = explain_row(ctx.rows[1], ctx, GRAPH)
    assert e["path"].count("→") == 2 and "(weight 0.48)" in e["path"]
    assert e["affinity"].startswith("Affinity 0.38 = 0.80 × 0.48")
    assert e["relevance"] == "Relevance 0.62 = score 0.24 ÷ affinity 0.38"
    assert e["admitted"] == "Admitted because its score 0.24 ≥ the cut-off 0.20 (the 3rd best score)"
    assert "held nothing back" in e["guarantee"]
    direct = explain_row(ctx.rows[0], ctx, GRAPH)
    assert direct["path"].endswith("found directly from what you asked") and "nothing is discounted" in direct["affinity"]
    bridged = explain_row(ctx.rows[2], ctx, GRAPH)
    assert "same person found in INS (90% sure)" in bridged["path"] and "0.90" in bridged["affinity"]
    assert explain_row(ctx.rows[0], ctx, GRAPH)["guarantee"] == "Nothing INS held back could score above 0.11."
    assert math.isclose(bridged["numbers"]["carried"], 1.0)


def test_cutoff_falls_back_to_the_lowest_score_without_a_trace():
    ctx = _ctx()
    ctx.trace = []
    assert cutoff(ctx) == 0.20


def test_leads_keep_the_best_bound_per_value_drop_followed_ones_and_are_plain():
    st = lambda v, b, w=0.5: FrontierState("CAM", "plate", v, w, b, "plate", 1.0,  # noqa: E731
                                           ["VEHICLE_REGISTRATION#376", "plate->CAM"], 0)
    store: dict = {}
    note_leads(store, [st("UP16IJ0376", 0.10), st("UP16IJ0376", 0.19), st("DL01AB0001", 0.05)], 1)
    note_leads(store, [st("HR26AA0001", 0.07)] + [st(f"X{i}", 0.01) for i in range(30)], 2)
    leads = top_leads(store, {("CAM", "plate", "HR26AA0001"): 0.9}, GRAPH, k=20)   # HR26.. was followed later
    assert len(leads) == 1   # every state came from one record into one agency: one lead, the rest look-alikes
    assert leads[0] == {"target": "CAM", "attr": "plate", "value": "UP16IJ0376", "bound": 0.19,
                        "from": ("REG", "VEHICLE_REGISTRATION", "376"), "round": 1, "kind": "plate", "weight": 0.5,
                        "confidence": 1.0, "alike": 31}
    json.dumps(leads)  # plain types only


def test_leads_from_different_records_are_kept_apart_best_bound_first():
    store: dict = {}
    states = [FrontierState("INS", "plate", f"DL01AB{i:04d}", 0.2, 0.01 * i, "plate", 1.0,
                            [f"CRIME_RECORDS#{i}", "plate->INS"], 0) for i in range(1, 31)]
    note_leads(store, states, 1)
    leads = top_leads(store, {}, GRAPH, k=20)
    assert len(leads) == 20 and [l["bound"] for l in leads] == sorted((l["bound"] for l in leads), reverse=True)
    assert leads[0]["from"] == ("THEFT", "CRIME_RECORDS", "30") and leads[0]["alike"] == 0


# --------------------------------------------------------------------------- on discover()

@pytest.fixture(scope="module")
def world(tmp_path_factory):
    return build(tmp_path_factory.mktemp("explain"), seed=7, vehicles=24)


def test_discover_fills_affinity_edge_weights_and_pruned_leads(world):
    res = discover(STORY, n=8, client=InProcessClient(world))
    assert res.trace[-1]["stopped"] == "bound"
    for r in res.rows:
        assert r.affinity is not None and 0 < r.affinity <= 1 and r.score <= r.affinity + 1e-9  # relevance <= 1
        assert len(r.edge_weights) == sum(1 for a, b in zip(r.path, r.path[1:]) if "->" not in a + b)
        if not r.via_bridge and r.edge_weights:   # within one source the proof reproduces the reported affinity
            assert math.isclose(math.prod(r.edge_weights) * r.bridge_confidence, r.affinity, rel_tol=1e-4)
    tau = res.trace[-1]["tau_n"]
    assert res.pruned_leads and all(l["bound"] <= tau + 1e-9 for l in res.pruned_leads)
    assert len(res.pruned_leads) <= 20 and len({(l["target"], l["kind"], l["value"]) for l in res.pruned_leads}) == len(res.pruned_leads)


def test_pursuing_a_lead_confirms_its_bound(world):
    res = discover(STORY, n=8, client=InProcessClient(world))
    lead = res.pruned_leads[0]
    out = pursue(lead, res, client=InProcessClient(world))
    assert out["cutoff"] == res.trace[-1]["tau_n"] and not out["above"]
    assert all(r.score <= lead["bound"] + 1e-6 for r in out["rows"])
    have = {(r.source_id, r.table, str(r.pk)) for r in res.rows}
    assert all((r.source_id, r.table, str(r.pk)) not in have for r in out["rows"])
