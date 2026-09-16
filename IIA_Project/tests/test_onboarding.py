"""Task 0.5: the unknown-plate onboarding wizard, end to end over real HTTP.

Two throwaway wrappers (REG and INS, built from those sources' own schema.sql) are registered in
a tmp catalog, then the wizard is driven with AppTest: clicking Register must make the vehicle
readable on REG's own /query, clicking Add policy must make the policy readable on INS. Nothing
is mocked -- what is asserted is that the GUI's writes land in the agency's database.
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
from mediator.decide import UNKNOWN_VEHICLE
from sources.server_manager import WrapperServerThread
from sources.wrapper_template import create_app

from app.tabs.onboarding import is_unknown, render_unknown_plate

ROOT = Path(__file__).resolve().parents[1]
PLATE = "KA05MN9999"


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


def query(base: str, sql: str) -> list[dict]:
    r = httpx.post(f"{base}/query", json={"sql": sql}, timeout=10.0)
    assert r.status_code == 200, r.text
    return r.json()["rows"]


@pytest.fixture()
def cluster(tmp_path, monkeypatch) -> dict[str, str]:
    """A tmp REG + INS pair, empty of this plate, registered in a tmp meta.db catalog."""
    reg_base, reg_server = serve(create_app(
        make_db(tmp_path, "reg", []), source_id="REG", dbms="sqlite",
        tables=["VEHICLE_REGISTRATION", "OWNERS"]))
    ins_base, ins_server = serve(create_app(
        make_db(tmp_path, "ins", ["INSERT INTO INSURERS (insurer_id, insurer_name) "
                                  "VALUES (1, 'Bharti AXA')"]),
        source_id="INS", dbms="sqlite", tables=["POLICY_RECORDS", "INSURERS"]))
    monkeypatch.setattr(catalog, "META_DB_PATH", str(tmp_path / "meta.db"))
    catalog.register_source("REG", "Regional Transport Office (REG)", "SQLite", reg_base,
                            "registration_no", "OFFICIAL", 0.95, ["plate_number"])
    catalog.register_source("INS", "Insurance Provider (INS)", "SQLite", ins_base,
                            "vehicle_reg", "OFFICIAL", 0.90, ["plate_number"])
    try:
        yield {"REG": reg_base, "INS": ins_base}
    finally:
        reg_server.stop()
        ins_server.stop()


UNKNOWN_PROFILE = {"plate_number": PLATE, "decision": UNKNOWN_VEHICLE, "confidence": "MEDIUM",
                   "registration_status": None, "source_availability": {"REG": "OK"}}


def _page() -> None:
    # AppTest re-executes this body as its own script, so it must import what it uses.
    from app.tabs.onboarding import render_unknown_plate as render
    from mediator.decide import UNKNOWN_VEHICLE as unknown
    render("KA05MN9999", {"plate_number": "KA05MN9999", "decision": unknown})


def run_wizard() -> AppTest:
    at = AppTest.from_function(_page, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


# ---------------------------------------- is_unknown -----------------------------------------

def test_is_unknown_fires_only_on_the_unknown_decision():
    assert is_unknown(UNKNOWN_PROFILE)
    assert not is_unknown({"decision": "CLEAR"})
    assert not is_unknown({})


# ---------------------------------------- the wizard -----------------------------------------

def test_wizard_renders_all_four_steps(cluster):
    at = run_wizard()
    keys = {b.key for b in at.button}
    assert {"ob_register", "ob_insure", "ob_requery"} <= keys
    assert "ob_puc" not in keys  # no PUC source in this cluster: step 3 is skipped, not broken


def test_register_step_writes_the_vehicle_into_regs_own_database(cluster):
    at = run_wizard()
    at.text_input(key="ob_make").set_value("Kia").run()
    at.button(key="ob_register").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.success and "REG.register" in at.success[0].value

    rows = query(cluster["REG"], f"SELECT * FROM VEHICLE_REGISTRATION "
                                 f"WHERE registration_no = '{PLATE}'")
    assert len(rows) == 1
    assert (rows[0]["make"], rows[0]["reg_status"], rows[0]["rto_code"]) == ("Kia", "ACTIVE", "KA05")


def test_insure_step_writes_the_policy_into_ins_own_database(cluster):
    at = run_wizard()
    at.text_input(key="ob_until").set_value("31/12/2099").run()
    at.button(key="ob_insure").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.success and "INS.add_policy" in at.success[0].value

    rows = query(cluster["INS"], f"SELECT * FROM POLICY_RECORDS WHERE vehicle_reg = '{PLATE}'")
    assert len(rows) == 1
    assert (rows[0]["policy_until"], rows[0]["is_active"]) == ("31/12/2099", 1)


def test_requery_step_hands_the_plate_back_to_investigate(cluster):
    at = run_wizard()
    at.button(key="ob_requery").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["selected_plate"] == PLATE
    assert at.session_state["ob_done"] is True


def test_a_dead_source_is_reported_not_raised(cluster, tmp_path):
    # Point REG at a port nothing is listening on: the wizard must degrade to a message.
    catalog.register_source("REG", "Regional Transport Office (REG)", "SQLite",
                            f"http://127.0.0.1:{free_port()}", "registration_no", "OFFICIAL",
                            0.95, ["plate_number"])
    at = run_wizard()
    at.button(key="ob_register").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.error and "DOWN" in at.error[0].value


def test_a_wrapper_with_writes_disabled_names_the_env_switch(tmp_path, monkeypatch):
    base, server = serve(create_app(make_db(tmp_path, "reg", []), source_id="REG", dbms="sqlite",
                                    tables=["VEHICLE_REGISTRATION", "OWNERS"],
                                    admin_enabled=False))
    monkeypatch.setattr(catalog, "META_DB_PATH", str(tmp_path / "meta.db"))
    catalog.register_source("REG", "Regional Transport Office (REG)", "SQLite", base,
                            "registration_no", "OFFICIAL", 0.95, ["plate_number"])
    try:
        at = run_wizard()
        at.button(key="ob_register").click().run()
        assert not at.exception, [e.value for e in at.exception]
        assert at.error and "REG_ADMIN=off" in at.error[0].value
    finally:
        server.stop()
