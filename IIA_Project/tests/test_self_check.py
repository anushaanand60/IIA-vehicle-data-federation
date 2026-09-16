"""Task 2.4: the Citizen Self-Check tab (`app/tabs/self_check.py`).

Mirrors the pattern in `tests/test_investigate_tab.py` / `tests/test_tabs_render.py`: real
throwaway REG + INS + THEFT wrappers (built from those sources' own `schema.sql`), a registered
CAM row that is never queried, all inside a tmp `meta.db` catalog with real GAV mappings, driven
through `AppTest.from_function`.

What is asserted is exactly the citizen-facing contract: registration/insurance/PUC/stolen status
render for the requested plate, the owner's name and any camera location never appear on the page,
and the data-minimisation caption shows that fewer sources were asked than are catalogued (CAM is
never one of them, because none of the self-check attributes route to it).
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

PLATE_CLEAN = "DL01AB1234"
PLATE_NO_POLICY = "DL09KL3321"
UNKNOWN_PLATE = "KA05MN9999"

REG_SEED = [
    "INSERT INTO OWNERS (owner_id, full_name, address_line, city) "
    "VALUES (1, 'Asha Menon', '12 MG Road', 'Delhi')",
    "INSERT INTO VEHICLE_REGISTRATION (registration_id, registration_no, owner_id, make, model, "
    "colour, fuel_type, registered_on, reg_status, rto_code) "
    f"VALUES (1, '{PLATE_CLEAN}', 1, 'Maruti Suzuki', 'Swift', 'White', 'PETROL', '2021-03-04', "
    "'ACTIVE', 'DL01')",
    "INSERT INTO VEHICLE_REGISTRATION (registration_id, registration_no, owner_id, make, model, "
    "colour, fuel_type, registered_on, reg_status, rto_code) "
    f"VALUES (2, '{PLATE_NO_POLICY}', 1, 'Hyundai', 'i20', 'Grey', 'PETROL', '2020-06-01', "
    "'ACTIVE', 'DL09')",
]
INS_SEED = [
    "INSERT INTO INSURERS (insurer_id, insurer_name) VALUES (1, 'Bharti AXA')",
    "INSERT INTO POLICY_RECORDS (policy_id, vehicle_reg, insurer_id, policy_type, policy_start, "
    "policy_until, is_active, premium_inr) "
    "VALUES (1, 'DL-01-AB-1234', 1, 'COMPREHENSIVE', '15/01/2026', '14/01/2027', 1, 8400.00)",
]

REG_MAPPINGS = [
    ("VEHICLE_REGISTRATION", "registration_no", "plate_number", "norm_plate", None, None),
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
    """REG, INS, THEFT answering for real; CAM registered but never reachable/queried -- the
    self-check attributes never route to it, so this proves the "fewer sources asked" claim
    without needing a fourth live wrapper."""
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
        ["plate_number", "owner_name", "registration_status"])
    catalog.register_source(
        "INS", "Insurance Provider (INS)", "SQLite", ins_base, "vehicle_reg", "OFFICIAL", 0.90,
        ["plate_number", "insurer_name", "insurance_start", "insurance_expiry"])
    catalog.register_source(
        "THEFT", "Police Crime Records (THEFT)", "SQLite", theft_base,
        "vehicle_number", "OFFICIAL", 0.90,
        ["plate_number", "stolen_status", "case_status", "last_incident_date"])
    # CAM covers only camera attributes, none of which self-check ever requests, and its base_url
    # is never dialled -- it exists purely so the catalog has a fourth source to *not* ask.
    catalog.register_source(
        "CAM", "Road Camera Network (CAM)", "PostgreSQL", "http://127.0.0.1:1", "plate_ocr",
        "OBSERVATIONAL", 0.60,
        ["plate_number", "last_seen_location", "last_seen_time", "observed_make",
         "observed_model", "observed_colour"])

    with sqlite3.connect(catalog.META_DB_PATH) as meta:
        meta.execute("DELETE FROM SOURCE_CATALOG WHERE source_id NOT IN ('REG','INS','THEFT','CAM')")
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
    from app.tabs.self_check import render
    render()


def run_tab() -> AppTest:
    at = AppTest.from_function(_page, default_timeout=120).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _page_text(at: AppTest) -> str:
    parts = [str(m.value) for m in at.markdown]
    parts += [f"{m.label} {m.value}" for m in at.metric]
    parts += [str(i.value) for i in at.info]
    parts += [str(w.value) for w in at.warning]
    return " ".join(parts)


# ----------------------------------------------------------- (1) known, insured plate

def test_known_insured_plate_shows_valid_and_active(cluster):
    at = run_tab()
    at.text_input(key="sc_plate").set_value(PLATE_CLEAN).run()
    at.button(key="sc_check").click().run()
    assert not at.exception, [e.value for e in at.exception]

    text = _page_text(at)
    assert "ACTIVE" in text
    assert "VALID" in text
    assert "Asha Menon" not in text  # owner name is never shown
    assert "location" not in text.lower()  # nor any camera location wording


# ----------------------------------------------------------- (2) plate with no policy

def test_plate_with_no_policy_shows_no_policy_on_record(cluster):
    at = run_tab()
    at.text_input(key="sc_plate").set_value(PLATE_NO_POLICY).run()
    at.button(key="sc_check").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert "no policy on record" in _page_text(at)


# ----------------------------------------------------------- (3) data minimisation caption

def test_sources_asked_caption_shows_fewer_than_catalogued(cluster):
    at = run_tab()
    at.text_input(key="sc_plate").set_value(PLATE_CLEAN).run()
    at.button(key="sc_check").click().run()
    assert not at.exception, [e.value for e in at.exception]

    captions = [c.value for c in at.caption]
    minimisation = [c for c in captions if "contacted" in c]
    assert minimisation, captions
    assert "contacted 3 of 4 sources" in minimisation[0]
    assert "CAM" in minimisation[0]  # named among the sources not asked
    chip_markdown = [m.value for m in at.markdown if "fm-chip" in m.value]
    assert any("NOT ASKED" in c and "CAM" in c for c in chip_markdown)


# ----------------------------------------------------------- (4) unknown plate

def test_unknown_plate_shows_not_registered(cluster):
    at = run_tab()
    at.text_input(key="sc_plate").set_value(UNKNOWN_PLATE).run()
    at.button(key="sc_check").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert "NOT REGISTERED" in _page_text(at)


# ----------------------------------------------------------- (5) whole-app wiring

def test_app_file_renders_every_tab_with_no_exceptions():
    at = AppTest.from_file(str(ROOT / "app" / "app.py"), default_timeout=180).run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.sidebar.radio(key="nav").options) == 10  # the sidebar menu (Task D2)


# ----------------------------------------------------------- (6) citizen dispute loop

DISPUTED_CASE = {
    "case_id": 7, "plate_read": "DL05CD9B76", "plate_resolved": "DL05CD9876",
    "captured_at": "2026-09-04T11:00:00", "location": "Sector 29 Crossing",
    "lat": 28.4601, "lon": 77.0648, "status": "CANCELLED", "verdict": "REJECT",
    "reason": "record changed since issue: insurance_expiry was none now 2027-01-01",
    "amount_inr": None, "evidence": {"profile": {"owner_name": "Asha Menon"}},
}


def test_dispute_block_masks_the_plate_and_reports_the_outcome(cluster, monkeypatch):
    """A citizen sees the outcome of their own dispute and nothing else: no owner name, no camera
    coordinates, and their own plate masked in the middle."""
    from app.tabs import self_check as tab

    monkeypatch.setattr(tab.challan_guard, "dispute",
                        lambda cid, reason, actor="citizen": DISPUTED_CASE)
    at = run_tab()
    at.number_input(key="sc_case_id").set_value(7).run()
    at.text_area(key="sc_reason").set_value("I renewed before that date").run()
    at.button(key="sc_dispute").click().run()
    assert not at.exception, [e.value for e in at.exception]

    text = _page_text(at)
    assert "CANCELLED" in text
    assert "insurance_expiry" in text
    assert "DL05CD9876" not in text and "DL05CD9B76" not in text  # masked
    assert "DL05" in text and "76" in text                        # but recognisably theirs
    assert "Asha Menon" not in text
    assert "28.4601" not in text


def test_a_dispute_on_a_case_that_was_never_issued_is_explained_not_crashed(cluster, monkeypatch):
    from app.tabs import self_check as tab

    def refuse(case_id, reason, actor="citizen"):
        raise ValueError("case 7 is CANDIDATE; only an issued challan can be disputed")

    monkeypatch.setattr(tab.challan_guard, "dispute", refuse)
    at = run_tab()
    at.number_input(key="sc_case_id").set_value(7).run()
    at.text_area(key="sc_reason").set_value("not mine").run()
    at.button(key="sc_dispute").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert "only an issued challan" in _page_text(at)


def test_masking_keeps_the_ends_and_hides_the_middle():
    from app.tabs.self_check import mask_plate

    assert mask_plate("DL05CD9876") == "DL05••••76"
    assert mask_plate(None) == "—"
