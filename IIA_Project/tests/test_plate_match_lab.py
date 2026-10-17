"""The Plate Match page (`app/tabs/plate_match_lab.py`, rubric 1) under Streamlit's AppTest.

Runs against the session `local_cluster`, so the registry and the camera captures really are read
live through the REG and CAM wrappers.
"""
from __future__ import annotations

from streamlit.testing.v1 import AppTest


def _page() -> None:
    from app.tabs.plate_match_lab import render
    render()


def _run() -> AppTest:
    at = AppTest.from_function(_page, default_timeout=180).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _text(at: AppTest) -> str:
    return " ".join([str(m.value) for m in at.markdown] + [str(e.value) for e in at.error]
                    + [str(w.value) for w in at.warning])


def test_page_renders_and_explains_the_algorithm():
    at = _run()
    text = _text(at)
    assert "Read a plate" in text and "Why this algorithm" in text and "Matching%20Evaluation" in text


def test_typed_misread_ranks_the_story_vehicle_first_with_accept(local_cluster):
    at = _run()
    at.text_input(key="pm_typed").set_value("DL05CD9B76").run()
    at.button(key="pm_run").click().run()
    assert not at.exception, [e.value for e in at.exception]
    res = at.session_state["pm_result"]
    assert res["cands"][0].plate == "DL05CD9876" and res["cands"][0].decision == "ACCEPT"
    text = _text(at)
    assert "Blocking funnel" in text and "DL05CD9876 · ACCEPT" in text
    assert res["stages"][0]["candidates"] >= res["stages"][-1]["candidates"]  # the funnel only narrows


def test_benchmark_image_shows_its_raw_read_and_truth():
    at = _run()
    at.radio(key="pm_mode").set_value("Benchmark image").run()
    assert not at.exception, [e.value for e in at.exception]
    path = at.selectbox(key="pm_img").value
    truth = path.split("/")[-1].split("__")[0]           # images are named <true plate>__<degradation>
    kpis = " ".join(str(m.value) for m in at.markdown if "cv-kpi" in str(m.value))
    assert "True plate" in kpis and "Raw read" in kpis and truth in kpis


def test_reverse_lists_capture_916_for_the_story_vehicle(local_cluster):
    at = _run()
    at.text_input(key="pm_rev_plate").set_value("DL05CD9876").run()
    at.button(key="pm_rev_run").click().run()
    assert not at.exception, [e.value for e in at.exception]
    rows = at.session_state["pm_rev"]["rows"]
    assert 916 in [r["capture_id"] for r in rows]
    assert next(r for r in rows if r["capture_id"] == 916)["decision"] == "ACCEPT"
    assert "#916 · ACCEPT" in _text(at)
