"""Workstream G item 2: the scenario runner behind the GUI's "Test scenarios" page.

The live tests start their OWN wrappers on free high ports against a tmp copy of the catalog (and a
fresh cluster object in place of the process-wide one), so they never depend on, or disturb, whatever
holds 8001-8005 -- a running website, another test session -- and never touch the real meta.db.
"""
from __future__ import annotations

import json
import shutil
import socket

import pytest

from mediator import scenarios


def _free_ports(n: int) -> list:
    socks = [socket.socket() for _ in range(n)]
    try:
        for s in socks:
            s.bind(("127.0.0.1", 0))
        return [s.getsockname()[1] for s in socks]
    finally:
        for s in socks:
            s.close()


@pytest.fixture(scope="module")
def own_cluster(tmp_path_factory):
    import mediator.catalog as catalog
    import mediator.report as report
    from sources import server_manager as sm
    with pytest.MonkeyPatch.context() as mp:
        meta = tmp_path_factory.mktemp("scenario_meta") / "meta.db"
        shutil.copy(catalog.META_DB_PATH, meta)
        mp.setattr(catalog, "META_DB_PATH", str(meta))
        mp.setattr(report, "META_DB_PATH", str(meta))
        cluster = sm.SourceCluster()
        mp.setattr(sm, "_global_cluster", cluster)  # get_cluster() is ours for this module only
        sources = catalog.get_source_catalog()
        for (sid, m), port in zip(sources.items(), _free_ports(len(sources))):
            catalog.register_source(source_id=sid, display_name=m["display_name"], dbms=m["dbms"],
                                    base_url=f"http://127.0.0.1:{port}", identifier_attr=m["identifier_attr"],
                                    authority=m["authority"], trust_score=m["trust_score"],
                                    covers=m.get("covers", []), timeout_ms=m.get("timeout_ms", 1500))
        status = sm.ensure_local_sources(catalog.get_source_catalog(), cluster=cluster)
        assert all(v == "started" for v in status.values()), status
        try:
            yield cluster
        finally:
            cluster.stop_all()


# ------------------------------------------------------------------ offline

def test_every_scenario_has_metadata_and_a_function():
    assert set(scenarios.SCENARIOS) == set(scenarios._FUNCS) and len(scenarios.SCENARIOS) == 16
    for meta in scenarios.SCENARIOS.values():
        assert set(meta) == {"title", "rubric", "what", "why"} and meta["rubric"] in ("1", "2", "1+2")


def test_char_diff():
    assert scenarios.char_diff("DLO1AB1234", "DL01AB1234") == [
        {"op": "replace", "position": 3, "read": "O", "plate": "0"}]
    assert [d["op"] for d in scenarios.char_diff("HR2GEF44551", "HR26EF4455")] == ["replace", "delete"]


def test_a_scenario_that_fails_half_way_still_restores(monkeypatch):
    undone = []

    def broken(run):
        run.on_restore("undo the fake write", lambda: undone.append(1) or "undone")
        raise RuntimeError("boom")
    monkeypatch.setitem(scenarios._FUNCS, "ocr_zero_o", broken)
    r = scenarios.run_scenario("ocr_zero_o", registry=object())
    assert r.passed is False and r.restored is True and undone == [1]
    assert "boom" in r.actual and [s.title for s in r.steps][-2:] == ["Error", "Restore: undo the fake write"]


def test_a_failed_restore_is_reported_honestly(monkeypatch):
    def bad_restore(run):
        def fail():
            raise RuntimeError("source gone")
        run.on_restore("delete the row", fail)
        return "x", "y", True
    monkeypatch.setitem(scenarios._FUNCS, "ocr_zero_o", bad_restore)
    r = scenarios.run_scenario("ocr_zero_o", registry=object())
    assert r.restored is False and r.steps[-1].ok is False


def test_rdr_refusal_runs_offline_on_a_temp_copy():
    r = scenarios.run_scenario("rdr_teach_refusal", registry=object())
    assert r.passed is True and r.data["rules_file_unchanged"] is True
    json.dumps(r.to_dict())  # the GUI gets plain JSON


# ------------------------------------------------------------------ live, on our own wrappers

@pytest.mark.parametrize("key", ["ocr_zero_o", "ocr_b8", "unregistered_plate", "reverse_search"])
def test_matching_scenarios_pass(own_cluster, key):
    r = scenarios.run_scenario(key)
    assert r.passed is True, [(s.title, s.detail) for s in r.steps]
    assert r.restored is False  # read-only scenarios change nothing


def test_ocr_falls_back_to_the_recorded_read(own_cluster, monkeypatch):
    import scripts.match_plate as mp

    def no_ocr(_):
        raise RuntimeError("easyocr missing")
    monkeypatch.setattr(mp, "run_ocr", no_ocr)
    r = scenarios.run_scenario("two_errors_rotated")
    assert r.passed is True and r.data["read"] == "HR2GEF44551" and "benchmark CSV" in r.data["ocr"]


@pytest.mark.parametrize("key", ["ambiguous_twins", "context_breaks_tie", "examiner_insert_dirty",
                                 "examiner_modify_conflict", "examiner_delete", "misread_plate_cleansed"])
def test_writing_scenarios_pass_and_restore(own_cluster, key):
    from mediator import cleansing
    before = len(cleansing.Context.build().records)
    r = scenarios.run_scenario(key)
    assert r.passed is True, [(s.title, s.detail) for s in r.steps]
    assert r.restored is True
    ctx = cleansing.Context.build()
    assert len(ctx.records) == before and "DLO1AB1234" not in ctx.profiles


def test_source_down_refusal_restarts_ins(own_cluster):
    r = scenarios.run_scenario("source_down_refusal")
    assert r.passed is True and r.restored is True, [(s.title, s.detail) for s in r.steps]
    assert r.data["verdict"] == "HOLD" and "INS" in own_cluster.servers and not own_cluster.held


def test_batch_stress_small(own_cluster):
    r = scenarios.run_scenario("batch_stress", batch_n=25)
    s = r.data["summary"]
    assert s["n"] == 25 and 0 <= s["accept_precision"] <= 1 and s["p95_ms"] > 0
    assert r.passed == (s["accept_precision"] >= 0.98 and s["p95_ms"] < 200)
