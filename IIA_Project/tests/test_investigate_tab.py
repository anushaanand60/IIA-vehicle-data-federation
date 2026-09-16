"""Task 1.2: the Investigate tab as a component module (app/tabs/investigate.py).

Nothing is mocked. Two throwaway wrappers (REG and INS, built from those sources' own schema.sql)
are served on free ports and registered, together with a deliberately dead THEFT, in a tmp
meta.db catalog with real GAV mappings. The tab is then driven with AppTest, so what is asserted
is the behaviour a demo would see: a decision banner, one status chip per source that was asked,
a muted "not asked" chip for the catalogued source the planner left out, demo buttons that hand a
plate back through `selected_plate`, and the onboarding wizard for a plate no source holds.

Gotcha: `AppTest.from_function` re-executes the function's *source* inside a fresh module, so the
page body must import everything it uses -- module-level names from this file are not visible
there.
"""
from __future__ import annotations

import socket
import sqlite3
import time
from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from mediator import catalog
from sources.server_manager import WrapperServerThread
from sources.wrapper_template import create_app

ROOT = Path(__file__).resolve().parents[1]
PLATE = "DL01AB1234"
UNKNOWN_PLATE = "KA05MN9999"
UC1 = "UC1: Insurance Verification Only (INS + REG)"

REG_SEED = [
    "INSERT INTO OWNERS (owner_id, full_name, address_line, city) "
    "VALUES (1, 'Asha Menon', '12 MG Road', 'Delhi')",
    "INSERT INTO VEHICLE_REGISTRATION (registration_id, registration_no, owner_id, make, model, "
    "colour, fuel_type, registered_on, reg_status, rto_code) "
    f"VALUES (1, '{PLATE}', 1, 'Maruti Suzuki', 'Swift', 'White', 'PETROL', '2021-03-04', "
    "'ACTIVE', 'DL01')",
]
INS_SEED = [
    "INSERT INTO INSURERS (insurer_id, insurer_name) VALUES (1, 'Bharti AXA')",
    "INSERT INTO POLICY_RECORDS (policy_id, vehicle_reg, insurer_id, policy_type, policy_start, "
    "policy_until, is_active, premium_inr) "
    "VALUES (1, 'DL-01-AB-1234', 1, 'COMPREHENSIVE', '15/01/2026', '14/01/2027', 1, 8400.00)",
]

REG_MAPPINGS = [
    ("VEHICLE_REGISTRATION", "registration_no", "plate_number", "norm_plate", None, None),
    ("VEHICLE_REGISTRATION", "make", "vehicle_make", "fix_make", None, None),
    ("VEHICLE_REGISTRATION", "model", "vehicle_model", "none", None, None),
    ("VEHICLE_REGISTRATION", "colour", "vehicle_colour", "title_case", None, None),
    ("VEHICLE_REGISTRATION", "registered_on", "registration_date", "none", None, None),
    ("VEHICLE_REGISTRATION", "reg_status", "registration_status", "none", None, None),
    ("OWNERS", "full_name", "owner_name", "none", None,
     "VEHICLE_REGISTRATION.owner_id=OWNERS.owner_id"),
]
THEFT_MAPPINGS = [
    ("CRIME_RECORDS", "vehicle_number", "plate_number", "norm_plate", None, None),
    ("CRIME_RECORDS", "reported_date", "last_incident_date", "epoch_to_date",
     "latest_by:reported_date", None),
    ("CRIME_RECORDS", "case_status", "case_status", "none", None, None),
    ("CRIME_RECORDS", "stolen_flag", "stolen_status", "yn_to_bool", None, None),
]
INS_MAPPINGS = [
    ("POLICY_RECORDS", "vehicle_reg", "plate_number", "norm_plate", None, None),
    ("POLICY_RECORDS", "policy_type", "policy_type", "none", None, None),
    ("POLICY_RECORDS", "policy_start", "insurance_start", "parse_ddmmyyyy", None, None),
    ("POLICY_RECORDS", "policy_until", "insurance_expiry", "parse_ddmmyyyy",
     "latest_by:policy_until", None),
    ("INSURERS", "insurer_name", "insurer_name", "none", None,
     "POLICY_RECORDS.insurer_id=INSURERS.insurer_id"),
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
def cluster(tmp_path, monkeypatch) -> dict[str, str]:
    """REG, INS and THEFT answering for real, in a tmp catalog with real GAV mappings."""
    reg_base, reg_server = serve(create_app(
        make_db(tmp_path, "reg", REG_SEED), source_id="REG", dbms="sqlite",
        tables=["VEHICLE_REGISTRATION", "OWNERS"]))
    ins_base, ins_server = serve(create_app(
        make_db(tmp_path, "ins", INS_SEED), source_id="INS", dbms="sqlite",
        tables=["POLICY_RECORDS", "INSURERS"]))
    theft_base, theft_server = serve(create_app(
        make_db(tmp_path, "theft", []), source_id="THEFT", dbms="sqlite",
        tables=["CRIME_RECORDS"]))

    monkeypatch.setattr(catalog, "META_DB_PATH", str(tmp_path / "meta.db"))
    catalog.register_source(
        "REG", "Regional Transport Office (REG)", "SQLite", reg_base, "registration_no",
        "OFFICIAL", 0.95,
        ["plate_number", "owner_name", "vehicle_make", "vehicle_model", "vehicle_colour",
         "registration_date", "registration_status"])
    catalog.register_source(
        "INS", "Insurance Provider (INS)", "SQLite", ins_base, "vehicle_reg", "OFFICIAL", 0.90,
        ["plate_number", "insurer_name", "policy_type", "insurance_start", "insurance_expiry"])
    # Holds no record of any plate here, which is exactly what the UC1 test needs: a catalogued
    # source the planner must be seen *not* to ask.
    catalog.register_source(
        "THEFT", "Police Crime Records (THEFT)", "SQLite", theft_base,
        "vehicle_number", "OFFICIAL", 0.90,
        ["plate_number", "stolen_status", "case_status", "last_incident_date"])

    # A fresh meta.db seeds the five demo sources at their default 127.0.0.1:800x ports, and
    # re-seeds whenever SOURCE_CATALOG is empty -- so prune *after* registering, never before.
    # This test asserts on exactly which sources were asked, so the catalog holds only these three.
    with sqlite3.connect(catalog.META_DB_PATH) as meta:
        meta.execute("DELETE FROM SOURCE_CATALOG WHERE source_id NOT IN ('REG','INS','THEFT')")
        meta.execute("DELETE FROM MAPPING_REGISTRY")

    for source_id, rows in (("REG", REG_MAPPINGS), ("INS", INS_MAPPINGS),
                            ("THEFT", THEFT_MAPPINGS)):
        for table, attr, global_attr, transform, aggregate, join_path in rows:
            catalog.save_mapping(source_id, table, attr, global_attr, transform, aggregate,
                                 join_path)
    try:
        yield {"REG": reg_base, "INS": ins_base, "THEFT": theft_base}
    finally:
        reg_server.stop()
        ins_server.stop()
        theft_server.stop()


def _page() -> None:
    # AppTest re-executes this body as its own script, so it must import what it uses.
    from app.tabs.investigate import render
    render()


def run_tab() -> AppTest:
    at = AppTest.from_function(_page, default_timeout=120).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def chips(at: AppTest) -> list[str]:
    return [m.value for m in at.markdown if "fm-chip" in m.value]


# ------------------------------------------------- (1) the decision banner renders

def test_default_plate_renders_a_decision_banner(cluster):
    at = run_tab()
    banners = [m.value for m in at.markdown if "fm-banner" in m.value]
    assert banners, "no decision banner was rendered"
    assert "Decision" in banners[0]
    assert "Confidence" in banners[0]
    assert at.session_state["latest_result"]["profile"]["plate_number"] == PLATE


# ------------------------------------- (2) UC1 asks two sources, and says so on screen

def test_uc1_scope_shows_two_answered_chips_and_marks_the_rest_not_asked(cluster):
    at = run_tab()
    at.selectbox(key="inv_scope").select(UC1).run()
    assert not at.exception, [e.value for e in at.exception]

    answered = [c for c in chips(at) if "NOT ASKED" not in c]
    assert len(answered) == 2, answered
    assert all(any(word in c for word in ("OK", "DOWN", "TIMEOUT", "ERROR")) for c in answered)
    assert {"REG", "INS"} == {c.split(" · ")[0].split(">")[-1] for c in answered}
    # THEFT is in the catalog but covers nothing UC1 asked for: shown, muted, never claimed.
    assert any("NOT ASKED" in c for c in chips(at))
    assert set(at.session_state["latest_result"]["plan_trace"]["sources_contacted"]) == {"REG",
                                                                                         "INS"}


def test_full_profile_scope_asks_every_catalogued_source(cluster):
    at = run_tab()
    answered = [c for c in chips(at) if "NOT ASKED" not in c]
    assert len(answered) == 3  # every catalogued source is asked for a full profile
    assert all("OK" in c for c in answered)


# ------------------------------------------------ (3) demo chips hand back a plate

def test_demo_button_sets_the_selected_plate(cluster):
    at = run_tab()
    assert at.session_state["selected_plate"] == PLATE
    at.button(key="inv_demo_DL05CD9876").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["selected_plate"] == "DL-05-cd-9876"
    assert at.session_state["inv_plate"] == "DL-05-cd-9876"


# ------------------------------------------------------------- (4) reruns are clean

def test_three_reruns_raise_nothing(cluster):
    at = run_tab()
    for _ in range(3):
        at.run()
        assert not at.exception, [e.value for e in at.exception]
    assert {"inv_file_report", "inv_watch"} <= {b.key for b in at.button}
    assert at.session_state["inv_plate"] == PLATE  # state survives the reruns unchanged


def test_watchlist_action_is_a_disabled_placeholder(cluster):
    at = run_tab()
    watch = at.button(key="inv_watch")
    assert watch.disabled is True


# --------------------------------------------- (5) an unknown plate offers onboarding

def test_unknown_plate_offers_the_onboarding_wizard(cluster):
    at = run_tab()
    at.text_input(key="inv_plate").set_value(UNKNOWN_PLATE).run()
    assert not at.exception, [e.value for e in at.exception]
    assert "ob_register" in {b.key for b in at.button}, "onboarding wizard was not rendered"
    assert any("onboard it live" in e.label for e in at.expander)
