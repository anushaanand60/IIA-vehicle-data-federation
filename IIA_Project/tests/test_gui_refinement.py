"""Workstream G (GUI refinement): How it works, the Plate Match separation views, Test scenarios, the
Watch-the-data-change and cleansing-validation panels, and the sidebar source status.

Backend modules this GUI codes against (`mediator.scenarios`, `mediator.change_audit`,
`mediator.cleansing_validation`) are replaced by small fakes here, so these tests pin the GUI's own
behaviour and do not depend on the backend's timing; the pages that read live sources run on the
session `local_cluster`.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]


def _ok(at: AppTest) -> AppTest:
    assert not at.exception, [e.value for e in at.exception]
    return at


def _text(at: AppTest) -> str:
    return " ".join([str(m.value) for m in at.markdown] + [str(x.value) for x in at.info]
                    + [str(x.value) for x in at.warning] + [str(x.value) for x in at.error]
                    + [str(x.value) for x in at.caption])


def _headings(at: AppTest) -> list[str]:
    return [m.value for m in at.markdown if 'class="cv-h"' in m.value]


# ------------------------------------------------------------------ separation diff (pure)

def _cost(a: str, b: str) -> float:
    return 0.1 if {a, b} == {"0", "O"} else 1.0


def test_align_marks_a_confusable_swap_and_prices_it():
    from app.tabs.plate_separation import align
    cells = align("DL01AB1234", "DLO1AB1234", _cost)
    assert [c["op"] for c in cells] == ["match", "match", "confusable"] + ["match"] * 7
    assert cells[2] == {"op": "confusable", "truth": "0", "read": "O", "cost": 0.1}


def test_align_separates_substitution_insertion_and_deletion():
    from app.tabs.plate_separation import align
    assert [c["op"] for c in align("DL01AB1234", "DL01AB1734", _cost)].count("substitute") == 1
    ins = align("DL01AB1234", "XDL01AB1234", _cost)
    assert ins[0] == {"op": "insert", "truth": "", "read": "X", "cost": 1.0}
    dele = align("DL01AB1234", "DL01AB123", _cost)
    assert dele[-1]["op"] == "delete" and dele[-1]["truth"] == "4"
    assert [c["op"] for c in align("DL01AB1234", "", _cost)] == ["delete"] * 10


def test_align_total_cost_is_the_weighted_edit_distance():
    from app.tabs.plate_separation import align
    from mediator.similarity import weighted_edit_distance
    for truth, read in [("DL05CD9876", "DL05CD9B76"), ("HR26EF4455", "HR26F4A55"), ("UP16GH1122", "UPL6GHI12Z2")]:
        assert sum(c["cost"] for c in align(truth, read, _cost)) == pytest.approx(
            weighted_edit_distance(truth, read, _cost))


def test_align_uses_the_learned_costs_by_default_and_labels_every_cell():
    from app.tabs.plate_separation import OPS, align, diff_html, summary
    from mediator.confusion import learned_confusion_cost
    cells = align("DL01AB1234", "DLO1AB1Z34")
    swap = next(c for c in cells if c["truth"] == "0")
    assert swap["cost"] == pytest.approx(learned_confusion_cost("0", "O"))
    html = diff_html(cells, "Real plate", "Raw OCR")
    assert "swap" in html and 'role="img"' in html and set(c["op"] for c in cells) <= set(OPS)
    assert summary(align("DL01AB1234", "DL01AB1234")).startswith("identical")


# ------------------------------------------------------------------ How it works

def test_how_it_works_facts_come_from_the_evaluation_document():
    from app.tabs.how_it_works import facts
    f = facts((ROOT / "docs" / "MATCHING_EVALUATION.md").read_text(encoding="utf-8"))
    assert f["winner"] == "logreg" and f["single"] == "learned_ocr_similarity"
    assert f["gain_variant"] == "no camera context"  # one feature group, not the combined string-only row
    assert float(f["top1"]) > float(f["single_top1"]) and f["scan"] == "605.0"


def test_how_it_works_renders_diagrams_methods_and_glossary():
    def page() -> None:
        from app.tabs.how_it_works import render
        render()
    at = _ok(AppTest.from_function(page, default_timeout=60).run())
    heads = " ".join(_headings(at))
    for title in ("Matching pipeline", "Cleansing loop", "The combination we use", "Every method", "Glossary"):
        assert title in heads, heads
    text = _text(at)
    assert "learned_ocr_similarity" in text and "no camera context" in text
    assert "p. 47-51" in text and "slides 24-29" in text and "Cornerstone case" in text
    assert len(at.get("graphviz_chart")) == 2


# ------------------------------------------------------------------ Test scenarios

def _fake_scenarios(passed: bool = True) -> types.ModuleType:
    mod = types.ModuleType("mediator.scenarios")
    mod.SCENARIOS = {
        "ocr_zero_o": {"title": "OCR reads 0 as O", "rubric": "1", "what": "match DLO1AB1234",
                       "why": "the commonest OCR error"},
        "batch_stress": {"title": "Batch stress", "rubric": "1", "what": "200 reads",
                         "why": "scale"},
        "examiner_delete": {"title": "Examiner deletes a record", "rubric": "2",
                            "what": "delete then restore", "why": "no stale cache"},
    }

    def run_scenario(key, *, registry=None):
        return {"key": key, "title": mod.SCENARIOS[key]["title"], "rubric": mod.SCENARIOS[key]["rubric"],
                "expected": "DL01AB1234 ACCEPT", "actual": "DL01AB1234 ACCEPT" if passed else "REVIEW",
                "passed": passed, "elapsed_ms": 42, "restored": key == "examiner_delete" or None,
                "steps": [{"title": "matched", "detail": "p=0.99", "ok": True},
                          {"title": "note", "detail": "info only", "ok": None}],
                "data": {"accuracy": 0.95, "latencies_ms": [3.0, 4.0, 5.5, 4.2],
                         "rows": [{"read": "DLO1AB1234", "top": "DL01AB1234"}]}}
    mod.run_scenario = run_scenario
    mod.run_all = lambda keys=None: [run_scenario(k) for k in (keys or mod.SCENARIOS)]
    return mod


def _install(monkeypatch, mod) -> None:
    import mediator
    monkeypatch.setitem(sys.modules, "mediator.scenarios", mod)
    if mod is None:
        monkeypatch.delattr(mediator, "scenarios", raising=False)
    else:
        monkeypatch.setattr(mediator, "scenarios", mod, raising=False)


def _scenarios_page() -> None:
    from app.tabs.scenarios_tab import render
    render()


def test_scenarios_page_lists_by_rubric_and_runs_one(monkeypatch):
    _install(monkeypatch, _fake_scenarios())
    at = _ok(AppTest.from_function(_scenarios_page, default_timeout=60).run())
    heads = " ".join(_headings(at))
    assert "Rubric 1: approximate plate matching" in heads and "Rubric 2: cleansing camera records" in heads
    assert "NOT RUN" in _text(at) and "restores it" in _text(at)
    _ok(at.button(key="sc2_run_ocr_zero_o").click().run())
    res = at.session_state["sc2_results"]["ocr_zero_o"]
    assert res["passed"] and res["elapsed_ms"] == 42
    text = _text(at)
    assert "PASS" in text and "DL01AB1234 ACCEPT" in text and "[ok] matched" in text and "[info] note" in text


def test_scenarios_run_all_keeps_results_and_shows_failures(monkeypatch):
    _install(monkeypatch, _fake_scenarios(passed=False))
    at = _ok(AppTest.from_function(_scenarios_page, default_timeout=60).run())
    _ok(at.button(key="sc2_run_all").click().run())
    assert set(at.session_state["sc2_results"]) == {"ocr_zero_o", "batch_stress", "examiner_delete"}
    _ok(at.run())  # a plain rerun keeps them
    assert len(at.session_state["sc2_results"]) == 3 and "FAIL" in _text(at)
    assert "data restored" in _text(at)
    assert at.get("vega_lite_chart"), "batch latencies should draw a histogram"


def test_scenarios_page_survives_a_missing_backend(monkeypatch):
    _install(monkeypatch, None)
    at = _ok(AppTest.from_function(_scenarios_page, default_timeout=60).run())
    assert "Not available yet" in _text(at)


def test_scenarios_page_renders_the_real_catalogue():
    """The real mediator.scenarios catalogue groups into the three rubric headings (nothing is run)."""
    scenarios = pytest.importorskip("mediator.scenarios")
    if not hasattr(scenarios, "run_scenario"):
        pytest.skip("mediator.scenarios has no run_scenario yet (backend workstream in progress)")
    at = _ok(AppTest.from_function(_scenarios_page, default_timeout=60).run())
    heads = " ".join(_headings(at))
    assert "Rubric 1: approximate plate matching" in heads and "Rubrics 1 and 2 together" in heads
    assert "Batch stress test" in _text(at)


def test_a_scenario_that_raises_is_a_fail_not_a_crash(monkeypatch):
    mod = _fake_scenarios()
    mod.run_scenario = lambda key, **kw: (_ for _ in ()).throw(RuntimeError("INS wrapper gone"))
    _install(monkeypatch, mod)
    at = _ok(AppTest.from_function(_scenarios_page, default_timeout=60).run())
    _ok(at.button(key="sc2_run_batch_stress").click().run())
    assert at.session_state["sc2_results"]["batch_stress"]["passed"] is False
    assert "INS wrapper gone" in _text(at)


# ------------------------------------------------------------------ Watch the data change

def test_diff_rows_flags_only_changed_columns():
    from app.tabs.change_watch import diff_rows
    rows = diff_rows({"id": 1, "plate_id": "DL05CD9B76", "colour": "Silver"},
                     {"id": 1, "plate_id": "DL05CD9876", "colour": "Silver"})
    assert [r["column"] for r in rows if r["changed"]] == ["plate_id"]
    assert all(r["changed"] for r in diff_rows({"id": 1}, None))


def test_watch_panel_shows_before_after_and_sql(monkeypatch):
    import mediator
    store = {"rows": {"916": {"capture_id": 916, "plate_id": "DL05CD9B76", "colour": "Silver"}}}
    fake = types.ModuleType("mediator.change_audit")
    fake.capture_row = lambda cid: store["rows"].get(str(cid))
    fake.registration_row = lambda plate: {"registration_no": plate, "make": "Maruti Suzuki"}
    fake.last_admin_statements = lambda limit=5: [{"source": "CAM", "sql": "UPDATE CAPTURES SET plate_id = 'DL05CD9876'"}]
    monkeypatch.setitem(sys.modules, "mediator.change_audit", fake)
    monkeypatch.setattr(mediator, "change_audit", fake, raising=False)

    def page() -> None:
        import streamlit as st
        from app.tabs import change_watch
        if st.session_state.get("go"):
            change_watch.note_write("Applied 1 change(s) to capture 916", 916)
            st.session_state["go"] = False
        change_watch.render("916")

    at = _ok(AppTest.from_function(page, default_timeout=60).run())
    assert "no write yet" in _text(at) and "UPDATE CAPTURES" in _text(at)
    store["rows"]["916"] = {"capture_id": 916, "plate_id": "DL05CD9876", "colour": "Silver"}
    at.session_state["go"] = True
    _ok(at.run())
    text = _text(at)
    assert "CHANGED" in text and "DL05CD9B76" in text and "DL05CD9876" in text and "1 column(s) changed" in text


def test_watch_panel_without_backend_says_not_available(monkeypatch):
    import mediator
    monkeypatch.setitem(sys.modules, "mediator.change_audit", None)
    monkeypatch.delattr(mediator, "change_audit", raising=False)

    def page() -> None:
        from app.tabs import change_watch
        change_watch.render("1")
    at = _ok(AppTest.from_function(page, default_timeout=60).run())
    assert "Not available yet" in _text(at)


# ------------------------------------------------------------------ cleansing validation

VALIDATION = {
    "seed": 7, "overall": {"records": 300, "detection_recall": 0.97, "correction_accuracy": 0.91},
    "per_type": {"plate_confusion": {"detection_recall": 0.99, "detection_precision": 1.0,
                                     "correction_accuracy": 0.95, "quarantine_rate": 0.02, "wrong_corrections": 1},
                 "colour_variant": {"detection_recall": 1.0, "detection_precision": 0.98,
                                    "correction_accuracy": 1.0, "quarantine_rate": 0.0, "wrong_corrections": 0}},
    "examples": [{"type": "plate_confusion", "original": "DL05CD9876", "noisy": "DL05CD9B76", "detected": True,
                  "proposed": "DL05CD9876", "rule_path": ["R0", "R5"], "correct": True}],
}


def test_validation_panel_renders_the_saved_file(tmp_path, monkeypatch):
    from app.tabs import cleansing_validation_panel as panel_mod
    saved = tmp_path / "cleansing_validation.json"
    saved.write_text(json.dumps(VALIDATION), encoding="utf-8")
    monkeypatch.setattr(panel_mod, "SAVED", saved)

    def page() -> None:
        from app.tabs import cleansing_validation_panel
        cleansing_validation_panel.render()
    at = _ok(AppTest.from_function(page, default_timeout=60).run())
    text = _text(at)
    assert "plate_confusion" in text and "DL05CD9B76" in text and "detection recall" in text and "seed 7" in text
    assert at.get("vega_lite_chart")
    df = panel_mod.chart_frame(VALIDATION["per_type"])
    assert set(df["metric"]) == {"detection recall", "correction accuracy"} and len(df) == 4


def test_validation_panel_without_file_or_backend(tmp_path, monkeypatch):
    from app.tabs import cleansing_validation_panel as panel_mod
    monkeypatch.setattr(panel_mod, "SAVED", tmp_path / "missing.json")

    def page() -> None:
        from app.tabs import cleansing_validation_panel
        cleansing_validation_panel.render()
    at = _ok(AppTest.from_function(page, default_timeout=60).run())
    assert "Not available yet" in _text(at)


# ------------------------------------------------------------------ sidebar source status

def test_source_status_words():
    from app.tabs.source_status import describe
    assert describe("running") == ("running", "ok")
    assert describe("started")[0] == "restarted just now"
    assert describe("remote")[0] == "on another laptop"
    assert describe("failed: port 8002 busy") == ("failed — port 8002 busy", "bad")
    assert describe("stopped: held down on purpose")[1] == "warn"


def test_source_status_restart_clears_the_status():
    def page() -> None:
        import streamlit as st
        from app.tabs.source_status import render_sidebar
        st.session_state.setdefault("source_status", {"REG": "running", "INS": "failed: refused"})
        with st.sidebar:
            render_sidebar()
    at = _ok(AppTest.from_function(page, default_timeout=60).run())
    html = " ".join(m.value for m in at.sidebar.markdown)
    assert "INS: failed" in html and "REG: running" in html
    assert any("INS: failed — refused" in c.value for c in at.sidebar.caption)
    _ok(at.sidebar.button(key="ss_restart").click().run())


# ------------------------------------------------------------------ Plate Match separation (live)

def _plate_page() -> None:
    from app.tabs.plate_match_lab import render
    render()


def test_plate_match_separation_for_a_benchmark_image(local_cluster):
    at = _ok(AppTest.from_function(_plate_page, default_timeout=300).run())
    at.radio(key="pm_mode").set_value("Benchmark image").run()
    at.selectbox(key="pm_deg").set_value("rotate").run()
    _ok(at.button(key="pm_run").click().run())
    res = at.session_state["pm_result"]
    assert res["truth"] and res["target"] == res["truth"]
    heads = " ".join(_headings(at))
    assert "Separation" in heads and "How far apart" in heads and "every damage level" in heads
    assert len(res["grid"]["rows"]) == 22
    text = _text(at)
    assert "Real plate" in text and "Probability gap" in text and "What the filter does" in text


def test_plate_match_max_edits_one_collapses_the_funnel(local_cluster):
    at = _ok(AppTest.from_function(_plate_page, default_timeout=300).run())
    at.text_input(key="pm_typed").set_value("DL05CD9B76").run()
    at.selectbox(key="pm_edits").set_value(1).run()
    _ok(at.button(key="pm_run").click().run())
    res = at.session_state["pm_result"]
    assert res["max_edits"] == 1 and res["stages"][0]["candidates"] > 100
    assert res["stages"][-1]["candidates"] <= 5
    assert res["cands"][0].plate == "DL05CD9876"
    assert "matcher's winner DL05CD9876" in _text(at)
