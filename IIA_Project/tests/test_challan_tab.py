"""The Challan Guard tab (`app/tabs/challan_guard.py`) under Streamlit's AppTest.

Runs against the session-scoped local cluster, so pressing Verify really does query four wrappers.
`tests/conftest.py` has already redirected `catalog.META_DB_PATH` at a tmp copy of meta.db, so the
cases this test files never reach the real registry.
"""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="module")
def queued(local_cluster):
    """An empty queue, the validated mappings, and the five demo candidates seeded."""
    from scripts.seed_challan_cases import reset, seed
    from scripts.seed_mappings import seed as seed_mappings

    seed_mappings()
    reset()
    seed()
    yield
    reset()


def _page() -> None:
    # AppTest re-executes this body as its own script, so it must import what it uses.
    from app.tabs.challan_guard import render
    render()


def run_tab() -> AppTest:
    at = AppTest.from_function(_page, default_timeout=180).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _page_text(at: AppTest) -> str:
    parts = [str(m.value) for m in at.markdown]
    parts += [f"{m.label} {m.value}" for m in at.metric]
    parts += [str(i.value) for i in at.info]
    parts += [str(w.value) for w in at.warning]
    parts += [str(s.value) for s in at.success]
    parts += [str(c.value) for c in at.caption]
    return " ".join(parts)


def test_the_tab_renders_the_queue_without_exceptions(queued):
    at = run_tab()
    text = _page_text(at)
    assert "DL05CD9B76" in text  # the misread candidate is on screen
    assert at.selectbox(key="cg_case").value is not None


def test_kpis_count_the_queue(queued):
    """The KPIs are one `components.kpi_row` grid rather than four `st.metric` cards, so the
    labels are read off that block."""
    at = run_tab()
    kpis = " ".join(str(m.value) for m in at.markdown if "cv-kpi" in str(m.value))
    assert all(label in kpis for label in ("Candidates", "Held", "Issued", "Rejected")), kpis
    assert "prevent" in kpis.lower()


def test_verifying_the_selected_case_shows_the_identity_step(queued):
    at = run_tab()
    at.button(key="cg_verify").click().run()
    assert not at.exception, [e.value for e in at.exception]
    text = _page_text(at)
    assert "Identity" in text
    assert "Insurance at sighting time" in text or "Clone signal" in text


def test_the_seed_button_is_idempotent(queued):
    at = run_tab()
    before = len(at.selectbox(key="cg_case").options)
    at.button(key="cg_seed").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.selectbox(key="cg_case").options) == before


def test_a_new_candidate_can_be_filed_from_a_sighting(queued):
    at = run_tab()
    before = len(at.selectbox(key="cg_case").options)
    at.text_input(key="cg_new_plate").set_value("DL07ZZ4444").run()
    at.button(key="cg_new_submit").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.selectbox(key="cg_case").options) == before + 1


def test_every_widget_key_is_namespaced(queued):
    """The tab shares a page with eight others; an unprefixed key would collide."""
    at = run_tab()
    keys = [w.key for group in (at.button, at.selectbox, at.text_input) for w in group if w.key]
    assert keys and all(k.startswith("cg_") for k in keys), keys


def test_the_selected_case_survives_verify(queued):
    """Verify changes a case's status; the selectbox must not snap back to case #1 when it does."""
    from mediator import challan_guard

    third = challan_guard.queue(limit=200)[2]
    at = run_tab()
    at.selectbox(key="cg_case").set_value(third["case_id"]).run()
    at.button(key="cg_verify").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.selectbox(key="cg_case").value == third["case_id"]
    assert f"Case #{third['case_id']} — read as {third['plate_read']}" in _page_text(at)
    assert challan_guard.get(third["case_id"])["status"] != "CANDIDATE"
    assert any(f"Case #{third['case_id']} verified" in str(t.value) for t in at.toast), \
        [t.value for t in at.toast]


def test_a_case_id_that_no_longer_exists_falls_back_to_the_first_case(queued):
    """After `--reset` the remembered case_id is gone; the tab shows case #1 instead of raising."""
    from mediator import challan_guard
    from scripts.seed_challan_cases import reset, seed

    extra = challan_guard.new_candidate(plate_read="DL07ZZ5555", camera_id="CAM001",
                                        location="NH8 Toll Plaza", lat=28.4595, lon=77.0266,
                                        captured_at="2026-09-04T12:00:00")
    at = run_tab()
    at.selectbox(key="cg_case").set_value(extra).run()
    reset()
    seed()  # every case_id the session remembered is gone
    first = challan_guard.queue(limit=200)[0]
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.selectbox(key="cg_case").value == first["case_id"]


def test_verify_all_runs_the_whole_queue_from_one_click(queued):
    """The demo needs every KPI to move from one button, with the outcome said out loud."""
    import re

    from mediator import challan_guard
    from scripts.seed_challan_cases import reset, seed

    reset()
    seed()
    at = run_tab()
    at.button(key="cg_verify_all").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert challan_guard.queue(status="CANDIDATE") == []
    assert any("12 verified: 6 issued, 5 rejected, 1 held" in str(t.value) for t in at.toast), \
        [t.value for t in at.toast]
    kpis = next(str(m.value) for m in at.markdown if "cv-kpi" in str(m.value))
    figures = dict((label, int(n)) for n, label in
                   re.findall(r'class="n">(\d+)</div><div class="l">([^<]+)<', kpis))
    assert figures == {"Candidates": 0, "Held": 1, "Issued": 6, "Rejected": 5,
                       "Wrongful fines prevented": 5}, figures
    caption = " ".join(str(c.value) for c in at.caption)
    assert "Wrongful fines prevented" in caption and "cancelled" in caption
