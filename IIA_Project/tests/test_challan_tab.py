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
