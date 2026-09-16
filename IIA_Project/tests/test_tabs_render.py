"""Task 1.3: the remaining tab bodies as their own modules (`app/tabs/plan_trace.render_tab`,
`app/tabs/matcher_tab.py`, `app/tabs/catalog_tab.py`, `app/tabs/reports.py`), plus `app/app.py`
itself reduced to setup + tab shells.

Each tab test drives `render()` (or `render_tab()`) with `AppTest.from_function` against a tmp
`meta.db` and, where the tab needs one, a real tmp wrapper built from that source's own
`schema.sql` -- nothing here is mocked at the HTTP boundary. `AppTest.from_file("app/app.py")`
then drives the whole page once, the way the demo actually runs.
"""
from __future__ import annotations

import socket
import sqlite3
import time
from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from mediator import catalog, report
from sources.server_manager import WrapperServerThread
from sources.wrapper_template import create_app

ROOT = Path(__file__).resolve().parents[1]
PLATE = "DL01AB1234"

REG_SEED = [
    "INSERT INTO OWNERS (owner_id, full_name, address_line, city) "
    "VALUES (1, 'Asha Menon', '12 MG Road', 'Delhi')",
    "INSERT INTO VEHICLE_REGISTRATION (registration_id, registration_no, owner_id, make, model, "
    "colour, fuel_type, registered_on, reg_status, rto_code) "
    f"VALUES (1, '{PLATE}', 1, 'Maruti Suzuki', 'Swift', 'White', 'PETROL', '2021-03-04', "
    "'ACTIVE', 'DL01')",
]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _schema_statements(source_dir: str) -> list[str]:
    raw = (ROOT / "sources" / source_dir / "schema.sql").read_text(encoding="utf-8")
    raw = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("--"))
    return [s.strip() for s in raw.split(";") if s.strip()]


def make_db(tmp_path: Path, source_dir: str, seed: list[str]) -> str:
    db_path = tmp_path / f"{source_dir}.db"
    con = sqlite3.connect(db_path)
    for stmt in _schema_statements(source_dir) + seed:
        con.execute(stmt)
    con.commit()
    con.close()
    return f"sqlite:///{db_path.as_posix()}"


def serve(app) -> tuple[str, WrapperServerThread]:
    port = free_port()
    server = WrapperServerThread(app=app, host="127.0.0.1", port=port)
    server.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{base}/health", timeout=0.5).status_code == 200:
                return base, server
        except httpx.HTTPError:
            time.sleep(0.05)
    server.stop()
    raise RuntimeError("wrapper did not come up")


@pytest.fixture()
def reg_cluster(tmp_path, monkeypatch) -> str:
    """One real REG wrapper, registered alone in a tmp catalog. Also redirects
    `mediator.report`'s copy of `META_DB_PATH` -- it imported the name by value, so patching
    `catalog.META_DB_PATH` alone would not reach it."""
    reg_base, reg_server = serve(create_app(
        make_db(tmp_path, "reg", REG_SEED), source_id="REG", dbms="sqlite",
        tables=["VEHICLE_REGISTRATION", "OWNERS"]))

    meta_path = str(tmp_path / "meta.db")
    monkeypatch.setattr(catalog, "META_DB_PATH", meta_path)
    monkeypatch.setattr(report, "META_DB_PATH", meta_path)
    catalog.register_source(
        "REG", "Regional Transport Office (REG)", "SQLite", reg_base, "registration_no",
        "OFFICIAL", 0.95,
        ["plate_number", "owner_name", "vehicle_make", "vehicle_model", "vehicle_colour",
         "registration_date", "registration_status"])
    with sqlite3.connect(catalog.META_DB_PATH) as meta:
        meta.execute("DELETE FROM SOURCE_CATALOG WHERE source_id != 'REG'")

    try:
        yield reg_base
    finally:
        reg_server.stop()


def run(page) -> AppTest:
    at = AppTest.from_function(page, default_timeout=120).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def headings(at: AppTest) -> list[str]:
    """Every real section heading on the page (Task A3's `components.section`)."""
    return [m.value for m in at.markdown if 'class="vz-h"' in m.value]


def has_heading(at: AppTest, title: str) -> bool:
    return any(title in h for h in headings(at))


# --------------------------------------------------------------------- Plan Trace

def _plan_trace_page() -> None:
    from app.tabs.plan_trace import render_tab
    render_tab()


def test_plan_trace_without_latest_result_shows_the_info_text():
    at = run(_plan_trace_page)
    assert any("Execute a query" in i.value for i in at.info)


def test_plan_trace_reads_latest_result_from_session_state():
    def page() -> None:
        import streamlit as st
        from app.tabs.plan_trace import render_tab
        st.session_state["latest_result"] = {
            "profile": {"plate_number": "DL01AB1234"},
            "plan_trace": {
                "canonical_plate": "DL01AB1234",
                "raw_plate": "DL01AB1234",
                "requested_attrs": ["all"],
                "sources_contacted": ["REG", "INS"],
                "source_count": 2,
                "total_elapsed_ms": 42,
                "sources_detail": {
                    "REG": {"status": "OK", "elapsed_ms": 12, "row_count": 1},
                    "INS": {"status": "TIMEOUT", "elapsed_ms": 1500, "row_count": 0},
                },
                "sqls": {"REG": "SELECT 1", "INS": "SELECT 2"},
            },
        }
        render_tab()

    at = run(page)
    assert not any("Execute a query" in i.value for i in at.info)
    chip_markdown = [m.value for m in at.markdown if "fm-chip" in m.value]
    assert any("REG" in c for c in chip_markdown)
    assert any("INS" in c for c in chip_markdown)
    codes = [c.value for c in at.code]
    assert "SELECT 1" in codes and "SELECT 2" in codes


# ----------------------------------------------------------------------- Matcher

def _matcher_page() -> None:
    from app.tabs.matcher_tab import render
    render()


def test_matcher_tab_runs_against_a_live_wrapper(reg_cluster):
    at = run(_matcher_page)
    at.selectbox(key="mt_source").select("REG").run()
    assert not at.exception, [e.value for e in at.exception]
    at.button(key="mt_run").click().run()
    assert not at.exception, [e.value for e in at.exception]
    # A real REG schema always matches at least the plate/owner/make correspondences.
    assert has_heading(at, "Discovered correspondences"), headings(at)


# ----------------------------------------------------------------------- Catalog

def _catalog_page() -> None:
    from app.tabs.catalog_tab import render
    render()


def test_catalog_tab_lists_the_registered_source(reg_cluster):
    at = run(_catalog_page)
    tables = list(at.dataframe)
    assert tables, "no dataframe rendered"
    assert "REG" in tables[0].value.to_string()


def test_catalog_tab_register_new_source_form_is_present(reg_cluster):
    at = run(_catalog_page)
    assert at.text_input(key="ct_id").value == "PUC"
    assert {"ct_name", "ct_url", "ct_id_attr", "ct_covers"} <= {t.key for t in at.text_input}


# ----------------------------------------------------------------------- Reports

def _reports_page() -> None:
    from app.tabs.reports import render
    render()


def test_reports_tab_lists_none_gracefully(reg_cluster):
    catalog.init_meta_db()  # creates REPORT_LOG; nothing has been filed into it yet
    at = run(_reports_page)
    assert any("No audit reports have been filed" in i.value for i in at.info)


def test_reports_tab_lists_a_filed_report(reg_cluster):
    catalog.init_meta_db()
    report_id = report.file_ministry_report(
        {"plate_number": PLATE, "decision": "CLEAR", "confidence": "HIGH", "reasons": ["ok"]},
        {"sources_contacted": ["REG"]},
    )
    at = run(_reports_page)
    assert any(f"MOT-{report_id:06d}" in e.label for e in at.expander)


# ------------------------------------------------------------------------- app.py

# ------------------------------------------------- (A3) every tab leads with a real heading

def test_plan_trace_tab_leads_with_a_section_heading():
    def page() -> None:
        import streamlit as st
        from app.tabs.plan_trace import render_tab
        st.session_state["latest_result"] = {
            "profile": {"plate_number": "DL01AB1234"},
            "plan_trace": {"canonical_plate": "DL01AB1234", "raw_plate": "DL01AB1234",
                           "requested_attrs": ["all"], "sources_contacted": ["REG"],
                           "source_count": 1, "total_elapsed_ms": 9,
                           "sources_detail": {"REG": {"status": "OK", "elapsed_ms": 9,
                                                      "row_count": 1}},
                           "sqls": {"REG": "SELECT 1"}},
        }
        render_tab()

    at = run(page)
    assert has_heading(at, "Sources asked and skipped"), headings(at)
    assert has_heading(at, "SQL sent per source"), headings(at)


def test_matcher_tab_leads_with_a_section_heading(reg_cluster):
    at = run(_matcher_page)
    assert has_heading(at, "Hybrid schema matching"), headings(at)


def test_catalog_tab_leads_with_a_section_heading(reg_cluster):
    at = run(_catalog_page)
    assert has_heading(at, "Registered data sources"), headings(at)
    assert has_heading(at, "Register a new source"), headings(at)


def test_reports_tab_leads_with_a_section_heading(reg_cluster):
    catalog.init_meta_db()
    at = run(_reports_page)
    assert has_heading(at, "Ministry audit reports"), headings(at)
    assert has_heading(at, "Query audit log"), headings(at)


def test_no_tab_uses_a_markdown_pseudo_heading():
    """`st.subheader` / `###` / `#####` and captions-as-headings are all replaced by
    `components.section`, so a tab body may no longer contain them."""
    offenders = []
    for path in sorted((ROOT / "app" / "tabs").glob("*.py")):
        if path.name in ("self_check.py", "challan_guard.py"):
            continue  # another workstream owns these; Task C1 restyles them
        text = path.read_text(encoding="utf-8")
        if "st.subheader(" in text or 'st.markdown("#' in text or "st.markdown(f\"#" in text:
            offenders.append(path.name)
    assert offenders == [], offenders


def test_app_py_is_under_150_lines():
    lines = (ROOT / "app" / "app.py").read_text(encoding="utf-8").splitlines()
    assert len(lines) < 150, f"app/app.py is {len(lines)} lines"


def test_app_file_renders_every_tab_with_no_exceptions():
    at = AppTest.from_file(str(ROOT / "app" / "app.py"), default_timeout=180).run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.tabs) == 9, "Investigate, Challan Guard, Watchlist, Reports, Citizen Check, "                               "Plan Trace, Catalog, Matcher, SQL Console"


def test_app_masthead_states_what_the_system_does():
    at = AppTest.from_file(str(ROOT / "app" / "app.py"), default_timeout=180).run()
    page = " ".join(m.value for m in at.markdown)
    assert "Uninsured Vehicle Identification" in page
    assert "Nothing is copied" in page
