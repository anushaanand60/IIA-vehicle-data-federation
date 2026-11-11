"""CONTEXT-ADDED (R2/R3): the evaluation metrics and the experiment runner, on hand-built rows and fakes.

No source, no port: the runner is driven with a fake discover() and a fake counting client.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from mediator import context_eval as ce
from mediator.context_eval_run import Backend, Runner, normalise_doc_eval
from mediator.context_types import ContextResult, ContextRow, ParsedQuery, SourceStats, TopKResult


def row(sid: str, table: str, pk: int, score: float, path: List[str] | None = None, **kw: Any) -> ContextRow:
    return ContextRow(source_id=sid, table=table, pk=pk, row=kw.pop("row", {}), score=score,
                      path=path or [f"{table}#{pk}"], hops=len(path or []), **kw)


# ------------------------------------------------------------------ metrics

def test_overlap_global_and_per_source():
    closure = [row("REG", "T", i, 1 - i / 10) for i in range(6)] + [row("INS", "P", i, 0.5 - i / 20) for i in range(4)]
    ours = [row("REG", "T", 0, 0.9), row("REG", "T", 5, 0.8), row("INS", "P", 0, 0.5), row("INS", "P", 1, 0.4)]
    # Per source: REG oracle top-2 = {0,1}, ours {0,5} -> 1/2; INS {0,1} vs {0,1} -> 1; mean 0.75.
    assert ce.overlap_at_n(ours, closure, 2, per_source=True) == 0.75
    # Global: oracle top-2 = REG 0 and 1 (cut 0.9); ours' best two are REG 0 (hit) and REG 5 (oracle 0.5, miss).
    assert ce.overlap_at_n(ours, closure, 2) == 0.5
    assert ce.overlap_at_n(ours, [], 2) is None


def test_overlap_counts_a_tie_with_the_oracle_cut_as_correct():
    closure = [row("CAM", "CHALLANS", i, 0.05) for i in range(20)] + [row("CAM", "CAMERAS", 0, 0.3)]
    ours = [row("CAM", "CAMERAS", 0, 0.3)] + [row("CAM", "CHALLANS", i, 0.05) for i in range(11, 20)]
    assert ce.overlap_at_n(ours, closure, 10) == 1.0


def test_precision_and_ndcg():
    keys = [("REG", "T", "1"), ("REG", "T", "2"), ("REG", "T", "3")]
    grades = {("REG", "T", "1"): 2, ("REG", "T", "3"): 1}
    assert ce.precision_at_n(keys, grades, 2) == 0.5
    assert ce.ndcg_at_n(keys[:1] + keys[2:], grades, 2) == 1.0
    assert 0 < ce.ndcg_at_n(list(reversed(keys)), grades, 3) < 1
    assert ce.ndcg_at_n(keys, {}, 3) is None


def test_kendall_tau_extremes():
    a = [("S", "T", str(i)) for i in range(5)]
    assert ce.kendall_tau(a, a) == 1.0
    assert ce.kendall_tau(a, list(reversed(a))) == -1.0


def test_stability_without_noise_is_perfect_and_counts_edges():
    rows = [row("REG", "OWNERS", i, 1 - i / 10, path=["VEHICLE_REGISTRATION#1", f"OWNERS#{i}"]) for i in range(5)]
    rows.append(row("REG", "VEHICLE_IDENTITY", 9, 0.95, path=["VEHICLE_REGISTRATION#1", "VEHICLE_IDENTITY#9"],
                    via_bridge="chassis"))
    calm = ce.stability(rows, n=3, spread=0.0, trials=3)
    assert calm["tau_mean"] == 1.0 and calm["overlap_mean"] == 1.0
    assert calm["edges_perturbed"] == 3   # two FK edge types + the chassis bridge
    noisy = ce.stability(rows, n=3, spread=0.5, trials=20)
    assert -1.0 <= noisy["tau_min"] <= noisy["tau_mean"] <= 1.0


def test_grades_use_scenario_evidence_and_the_plate_column_from_the_graph():
    graph = {"sources": {"INS": {"tables": {"POLICY_RECORDS": {"plate": "vehicle_reg"}}}}}
    rows = [row("INS", "POLICY_RECORDS", 1, 0.9, row={"vehicle_reg": "DL-05-CD-9876"}),
            row("INS", "POLICY_RECORDS", 2, 0.8, row={"vehicle_reg": "DL-05-CD-9877"})]
    scen = [{"type": "QUICK_CLAIM", "plate": "dl05cd9876", "evidence": [["INS", "CLAIMS", 7], {"source": "THEFT",
                                                                                            "table": "X", "pk": 1}]}]
    g = ce.grades_for("DL05CD9876", rows, scen, graph)
    assert g == {("INS", "POLICY_RECORDS", "1"): 1, ("INS", "CLAIMS", "7"): 2, ("THEFT", "X", "1"): 2}


def test_movement_and_pruning_read_the_counters():
    res = ContextResult(query=ParsedQuery(text="x"), rows=[], stats=[
        SourceStats("REG", rows_shipped=10, bytes_shipped=2048, round_trips=2, latency_ms=5),
        SourceStats("INS", rows_shipped=5, bytes_shipped=1024, round_trips=1, latency_ms=3, status="DOWN")],
        complete=False, baseline_rows=300, elapsed_ms=12)
    mv = ce.movement(res)
    assert (mv["rows"], mv["bytes"], mv["round_trips"], mv["complete"]) == (15, 3072, 3, False)
    assert mv["per_source"]["INS"]["status"] == "DOWN"
    assert ce.pruned_share([{"pruned": 3, "expanded": 1}, {"pruned": 2, "expanded": 0}]) == (5, 6)
    assert ce.pruned_share(None) is None


def test_doc_eval_shapes_are_normalised():
    a = normalise_doc_eval({"query_only": {"nDCG@10": 0.4, "MRR": 0.5}, "context": {"nDCG@10": 0.7, "MRR": 0.8}})
    b = normalise_doc_eval({"modes": [{"mode": "context", "ndcg@10": 0.7, "mrr": 0.8}]})
    assert a[1] == {"mode": "context", "ndcg_at_10": 0.7, "mrr": 0.8}
    assert b == [a[1]]


# ------------------------------------------------------------------ runner with fakes

class FakeClient:
    def __init__(self) -> None:
        self._s: Dict[str, SourceStats] = {}

    def stat(self, sid: str) -> SourceStats:
        return self._s.setdefault(sid, SourceStats(sid))

    def stats(self) -> List[SourceStats]:
        return list(self._s.values())

    def post(self, sid: str, path: str, payload: Dict[str, Any]) -> Dict[str, Any] | None:
        return None

    def query(self, sid: str, sql: str) -> List[Dict[str, Any]]:
        return [{"k": 42, "c": 9}]  # 9 vehicles hang off this hub


def fake_discover(query: Any, n: int = 10, theta: float = 0.05, mode: str = "bounded", client: Any = None) -> Any:
    if mode == "mediator":
        raise ValueError("mode not implemented")
    every = [row(sid, "T", i, 1 - i / 50) for sid in ("REG", "INS") for i in range(30)]
    rows = every if mode == "closure" else [r for r in every if int(r.pk) < n + (1 if mode == "topn" else 0)
                                            and not (mode == "topn" and int(r.pk) == 0)]
    for sid in ("REG", "INS"):
        st = client.stat(sid)
        st.rows_shipped += sum(1 for r in rows if r.source_id == sid)
        st.round_trips += 1 if mode != "bounded" else 2
    res = ContextResult(query=ParsedQuery(text=str(query)), rows=rows, stats=client.stats(), baseline_rows=60)
    if mode == "bounded":
        res.trace = [{"round": 1, "tau_n": 0.8, "max_frontier_bound": 0.9, "expanded": 2, "pruned": 1},
                     {"round": 2, "tau_n": 0.82, "max_frontier_bound": 0.7, "expanded": 0, "pruned": 3}]
    return res


def fake_topk(query: Any, k: int = 10, client: Any = None) -> TopKResult:
    client.stat("REG").round_trips += 3
    return TopKResult(query=query, vehicles=[], sorted_accesses=12, random_accesses=4, rows_shipped=30, baseline_rows=900)


def test_runner_measures_every_mode_and_skips_a_missing_one():
    r = Runner(Backend(discover=fake_discover, topk=fake_topk), graph={}, scenarios=[], n=10, log=lambda _: None)
    got = r.measure("x1", "P1", "story", "DL01AB1234", FakeClient,
                    lambda mode, client: r.discover("DL01AB1234", mode, client), plate="DL01AB1234", with_stability=True)
    modes = [m["mode"] for m in got["movement"]]
    assert modes == ["bounded", "closure", "topn"]
    assert any("mediator" in note for note in r.notes)
    exact = {e["mode"]: e["overlap_at_n"] for e in got["exactness"]}
    assert exact["bounded"] == 1.0 and exact["closure"] == 1.0 and exact["topn"] == pytest.approx(0.8)
    assert got["movement"][0]["pruned"] == 4 and got["movement"][0]["considered"] == 6
    assert got["traces"] and got["stability"][0]["tau_mean"] is not None


def test_runner_topk_records_naive_baseline_and_sensitivity_grid():
    def board(k: int = 10, client: Any = None, method: str = "ta") -> TopKResult:
        return fake_topk(ParsedQuery(text="fleet"), k, client)

    r = Runner(Backend(discover=fake_discover, topk=fake_topk, leaderboard=board), graph={}, scenarios=[],
               log=lambda _: None)
    tk = r.topk(["stolen white Creta"], FakeClient)
    # topk has no method= switch -> TA only (noted); the leaderboard takes one, but follows the same list.
    assert {(t["task"], t["method"]) for t in tk} == {("free text", "ta"), ("free text", "naive"),
                                                      ("fleet leaderboard", "ta"), ("fleet leaderboard", "naive")}
    assert next(t for t in tk if t["method"] == "ta")["round_trips"] == 3
    sens = r.sensitivity(["DL01AB1234"], FakeClient)
    assert len(sens) == 9 and all(s["overlap_at_n"] is not None for s in sens)


def test_runner_hub_value_uses_the_fk_from_the_graph():
    graph = {"sources": {"REG": {"tables": {"VEHICLE_REGISTRATION": {"plate": "registration_no"}, "OWNERS": {}},
                                 "edges": [{"from": "VEHICLE_REGISTRATION", "col": "owner_id", "to": "OWNERS"}]}}}
    seen: List[str] = []

    class Spy(FakeClient):
        def query(self, sid: str, sql: str) -> List[Dict[str, Any]]:
            seen.append(sql)
            return super().query(sid, sql)

    r = Runner(Backend(), graph=graph, scenarios=[], log=lambda _: None)
    assert r.hub_value(Spy(), "REG", "OWNERS") == 42
    assert "GROUP BY owner_id" in seen[0] and "FROM VEHICLE_REGISTRATION" in seen[0]
    assert r.hub_value(Spy(), "REG", "NOPE") is None
