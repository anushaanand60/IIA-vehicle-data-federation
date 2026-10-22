"""PHASE3-ADDED (C6): every Apply and Resolve on the Data Cleansing page shows up in the numbers.

Replays what an examiner does: insert a dirty capture with the toolbox, Apply its fix, Resolve a clone, and
checks that the operator tiles move, the moved tiles carry their change, and the Last change box says so.
Runs against the session `local_cluster`; every write is undone or deleted before the test ends.
"""
from __future__ import annotations

import re

from streamlit.testing.v1 import AppTest

from mediator import cleansing


def _page() -> None:
    from app.tabs.cleansing_tab import render
    render()


def _ok(at: AppTest) -> AppTest:
    assert not at.exception, [e.value for e in at.exception]
    return at


def _tiles(at: AppTest) -> dict:
    html = " ".join(str(m.value) for m in at.markdown if "cv-kpi" in str(m.value))
    return dict((label, value) for value, label in re.findall(r'class="n">([^<]*)</div><div class="l">([^<]*)<', html))


def _n(value: str) -> int:
    return int(value.split(" ")[0])


def _box(at: AppTest) -> str:
    return " ".join(str(s.value) for s in at.success)


def test_apply_and_resolve_move_the_numbers(local_cluster):
    at = _ok(AppTest.from_function(_page, default_timeout=300).run())
    start = _tiles(at)

    _ok(at.button(key="dc_tb_insert").click().run())          # DL01AB1Z34 seen as DL01AB1234's own car
    new_id = at.session_state["dc_capture"]
    assert f"Inserted camera record #{new_id}" in _box(at)
    assert "Anomalies found" in at.session_state["dc_last"]["changes"]
    applied_log = None
    try:
        _ok(at.button(key="dc_apply").click().run())
        last = at.session_state["dc_last"]
        assert last["message"].startswith(f"Applied 1 change(s) to capture {new_id}") and last["left_list"]
        fixed = _tiles(at)["Records fixed (applied)"]
        assert _n(fixed) == _n(start["Records fixed (applied)"]) + 1 and "(+1)" in fixed
        assert "Records fixed (applied)" in _box(at) and "left the list" in _box(at)
        applied_log = next(e["log_id"] for e in cleansing.log_entries(20)
                           if str(e["record_id"]) == str(new_id) and e["action"] == "set" and not e["undone_at"])

        flagged = next(i for i in at.session_state["dc_scan"]["report"]["items"] if "flagged" in i["dispositions"])
        at.selectbox(key="dc_capture").set_value(str(flagged["record_id"])).run()
        at.radio(key="dc_res_decision").set_value("confirm_clone").run()
        _ok(at.button(key="dc_resolve").click().run())
        decided = _tiles(at)["Records decided by a person"]
        assert _n(decided) == _n(start["Records decided by a person"]) + 1 and "(+1)" in decided
        assert "It's a cloned plate" in _box(at)
        res_log = next(e["log_id"] for e in cleansing.log_entries(20)
                       if str(e["record_id"]) == str(flagged["record_id"]) and e["action"] == "resolve")
        _ok(at.button(key=f"dc_undo_{res_log}").click().run())
        assert _n(_tiles(at)["Records decided by a person"]) == _n(start["Records decided by a person"])
    finally:
        if applied_log is not None:
            cleansing.undo(applied_log, actor="test")
        ctx = cleansing.Context.build()
        cleansing._admin(ctx.cam, f"DELETE FROM {ctx.cam.table} WHERE {ctx.pk} = {int(new_id)}")
