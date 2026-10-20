"""The Data Cleansing page (`app/tabs/cleansing_tab.py`, rubric 2) under Streamlit's AppTest.

Runs against the session `local_cluster` (meta.db is a tmp copy, so CLEANSING_LOG and WATCHLIST rows
written here never reach the real catalogue). Every write a test makes to CAM is undone or deleted
before it ends; the rule file is redirected to a tmp copy for the teach test.
"""
from __future__ import annotations

import re
import shutil

import pytest
from streamlit.testing.v1 import AppTest

from mediator import catalog, cleansing


def _page() -> None:
    from app.tabs.cleansing_tab import render
    render()


def _ok(at: AppTest) -> AppTest:
    assert not at.exception, [e.value for e in at.exception]
    return at


def _run() -> AppTest:
    return _ok(AppTest.from_function(_page, default_timeout=300).run())


def _report(at: AppTest) -> dict:
    return at.session_state["dc_scan"]["report"]


def _kpi(at: AppTest, label: str) -> str:
    html = " ".join(str(m.value) for m in at.markdown if "cv-kpi" in str(m.value))
    match = re.search(r'<div class="n">([^<]*)</div><div class="l">' + re.escape(label), html)
    assert match, html
    return match.group(1)


def _item(at: AppTest, rid) -> dict | None:
    return next((i for i in _report(at)["items"] if str(i["record_id"]) == str(rid)), None)


def test_scan_renders_with_zero_unhandled(local_cluster):
    at = _run()
    report = _report(at)
    assert report["status"] == "OK" and report["records_scanned"] > 0
    # PHASE2-REPLACED (simplified GUI, 19 Oct 2026: six KPI tiles with shorter labels; advanced tools in one collapsed expander):
    # assert _kpi(at, "Unhandled (must be 0)") == "0"
    # assert int(_kpi(at, "Anomalies before")) == sum(report["anomalies_before"].values())
    # text = " ".join(str(m.value) for m in at.markdown)
    # assert "The rule tree" in text and "Examiner toolbox" in text and "R11" in text
    assert _kpi(at, "Unhandled") == "0"
    assert int(_kpi(at, "Anomalies found")) == sum(report["anomalies_before"].values())
    text = " ".join(str(m.value) for m in at.markdown)
    assert "The rule tree" in text and "Examiner toolbox" in text and "R11" in text and "How to use" in text
    advanced = next(e for e in at.expander if e.label.startswith("Advanced"))
    assert not advanced.proto.expanded  # rule tree, teach, bulk apply and validation start collapsed
    watch = next(e for e in at.expander if e.label == "Watch the data change")
    assert not watch.proto.expanded  # open only right after a write


def test_resolve_then_undo_round_trip(local_cluster):
    at = _run()
    flagged = next(i for i in _report(at)["items"] if "flagged" in i["dispositions"])
    rid, plate = str(flagged["record_id"]), flagged["record"]["plate_number"]
    at.selectbox(key="dc_capture").set_value(rid).run()
    at.radio(key="dc_res_decision").set_value("confirm_clone").run()
    at.text_input(key="dc_res_note").set_value("make and colour match the clone report").run()
    _ok(at.button(key="dc_resolve").click().run())
    item = _item(at, rid)
    assert "resolved" in item["dispositions"] and "flagged" not in item["dispositions"]
    assert item["resolution"]["decision"] == "confirm_clone"
    assert catalog.watchlist_get(cleansing.norm_plate(plate)) is not None
    log_id = item["resolution"]["log_id"]
    _ok(at.button(key=f"dc_undo_{log_id}").click().run())
    item = _item(at, rid)
    assert "flagged" in item["dispositions"] and item["resolution"] is None


def test_toolbox_insert_shows_the_anomaly_and_delete_removes_it(local_cluster):
    at = _run()
    at.text_input(key="dc_tb_plate").set_value("DL01AB0377").run()
    at.text_input(key="dc_tb_make").set_value("Tata").run()
    at.text_input(key="dc_tb_model").set_value("Nexon").run()
    at.text_input(key="dc_tb_colour").set_value("Blu").run()
    _ok(at.button(key="dc_tb_insert").click().run())
    new_id = at.session_state["dc_capture"]
    try:
        item = _item(at, new_id)
        assert item and "COLOUR_NOT_IN_LEXICON" in [a["code"] for a in item["anomalies"]]
        # PHASE2-REPLACED (examiner pass: the shared toolbox names the record it inserted):
        # assert any(s.value.startswith("Inserted a capture") for s in at.success)
        assert any(s.value.startswith(f"Inserted camera record #{new_id}") for s in at.success)
    finally:
        at.text_input(key="dc_tb_del_id").set_value(str(new_id)).run()
        _ok(at.button(key="dc_tb_delete").click().run())
    assert _item(at, new_id) is None


def test_teach_refusal_is_shown_verbatim_and_the_tree_is_unchanged(local_cluster, tmp_path, monkeypatch):
    rules = tmp_path / "rules.json"
    shutil.copy(cleansing.RULES_PATH, rules)
    monkeypatch.setattr(cleansing, "RULES_PATH", rules)
    before = rules.read_text(encoding="utf-8")
    at = _run()
    at.text_input(key="dc_t_cfield").set_value("value").run()
    at.text_input(key="dc_t_cvalue").set_value("NOT-THIS-CASE").run()
    at.text_input(key="dc_t_because").set_value("should be refused").run()
    _ok(at.button(key="dc_t_add").click().run())
    assert any("not satisfied by the case" in e.value for e in at.error)
    assert rules.read_text(encoding="utf-8") == before
