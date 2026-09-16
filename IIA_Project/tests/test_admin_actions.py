"""Task 0.2: the full add/update/delete action menu per source.

Each source gets a tmp SQLite wrapper built straight from its own sources/<id>/schema.sql, then
every action in ADMIN_ACTIONS[source] is exercised over HTTP (/admin/mutate) and verified by
reading the data back over /query -- the same seam the mediator/GUI will use.
"""
from __future__ import annotations

import re
import sqlite3
import socket
import time
from pathlib import Path

import httpx
import pytest

from sources.server_manager import WrapperServerThread
from sources.wrapper_template import ADMIN_ACTIONS, create_app

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _schema_statements(source_id_dir: str) -> list[str]:
    raw = (ROOT / "sources" / source_id_dir / "schema.sql").read_text(encoding="utf-8")
    # Strip the leading `-- comment` line(s); split on statement-ending semicolons.
    raw = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("--"))
    return [s.strip() for s in raw.split(";") if s.strip()]


def make_db(tmp_path: Path, source_id_dir: str, seed: list[str]) -> str:
    db_path = tmp_path / f"{source_id_dir}.db"
    con = sqlite3.connect(db_path)
    for stmt in _schema_statements(source_id_dir) + seed:
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


def mutate(base: str, action: str, plate: str, params: dict | None = None) -> httpx.Response:
    return httpx.post(f"{base}/admin/mutate",
                      json={"action": action, "plate": plate, "params": params or {}}, timeout=10.0)


def query(base: str, sql: str) -> list[dict]:
    r = httpx.post(f"{base}/query", json={"sql": sql}, timeout=10.0)
    assert r.status_code == 200, r.text
    return r.json()["rows"]


# ==================================================================== REG =====================

@pytest.fixture()
def reg_wrapper(tmp_path):
    url = make_db(tmp_path, "reg", [
        "INSERT INTO OWNERS (owner_id, full_name, address_line, city) "
        "VALUES (1, 'Ramesh Sharma', '12 Barakhamba Road', 'New Delhi')",
        "INSERT INTO VEHICLE_REGISTRATION (registration_id, registration_no, owner_id, make, "
        "model, colour, fuel_type, registered_on, reg_status, rto_code) "
        "VALUES (1, 'DL01AB1234', 1, 'Hyundai', 'Creta', 'White', 'PETROL', "
        "'2023-01-15', 'ACTIVE', 'DL01')",
    ])
    base, server = serve(create_app(url, source_id="REG", dbms="sqlite",
                                    tables=["VEHICLE_REGISTRATION", "OWNERS"]))
    try:
        yield base
    finally:
        server.stop()


def test_reg_register_creates_owner_and_vehicle(reg_wrapper):
    r = mutate(reg_wrapper, "register", "KA05MN9999", {"owner": "Test Owner", "make": "Kia"})
    assert r.status_code == 200, r.text
    assert r.json()["rows_affected"] == 2
    rows = query(reg_wrapper, "SELECT * FROM VEHICLE_REGISTRATION WHERE registration_no = 'KA05MN9999'")
    assert len(rows) == 1 and rows[0]["make"] == "Kia" and rows[0]["reg_status"] == "ACTIVE"
    owners = query(reg_wrapper, "SELECT * FROM OWNERS WHERE full_name = 'Test Owner'")
    assert len(owners) == 1


def test_reg_set_status(reg_wrapper):
    r = mutate(reg_wrapper, "set_status", "DL01AB1234", {"status": "suspended"})
    assert r.status_code == 200, r.text
    assert r.json()["rows_affected"] == 1
    rows = query(reg_wrapper, "SELECT reg_status FROM VEHICLE_REGISTRATION WHERE registration_no = 'DL01AB1234'")
    assert rows[0]["reg_status"] == "SUSPENDED"


def test_reg_set_status_rejects_unknown_value(reg_wrapper):
    r = mutate(reg_wrapper, "set_status", "DL01AB1234", {"status": "BANNED"})
    assert r.status_code == 400


def test_reg_unregister_removes_vehicle_and_orphaned_owner(reg_wrapper):
    r = mutate(reg_wrapper, "unregister", "DL01AB1234")
    assert r.status_code == 200, r.text
    assert r.json()["rows_affected"] == 2  # 1 vehicle + 1 orphaned owner
    assert query(reg_wrapper, "SELECT * FROM VEHICLE_REGISTRATION WHERE registration_no = 'DL01AB1234'") == []
    assert query(reg_wrapper, "SELECT * FROM OWNERS WHERE owner_id = 1") == []


# ==================================================================== INS =====================

@pytest.fixture()
def ins_wrapper(tmp_path):
    url = make_db(tmp_path, "ins", [
        "INSERT INTO INSURERS (insurer_id, insurer_name) VALUES (1, 'ACME Insurance')",
        "INSERT INTO POLICY_RECORDS (policy_id, vehicle_reg, insurer_id, policy_type, "
        "policy_start, policy_until, is_active, premium_inr) "
        "VALUES (1, 'DL05CD9876', 1, 'THIRD_PARTY', '01/01/2025', '10/06/2026', 0, 4500.00)",
    ])
    base, server = serve(create_app(url, source_id="INS", dbms="sqlite",
                                    tables=["POLICY_RECORDS", "INSURERS"]))
    try:
        yield base
    finally:
        server.stop()


def test_ins_renew(ins_wrapper):
    r = mutate(ins_wrapper, "renew", "DL05CD9876", {"until": "31/12/2027"})
    assert r.status_code == 200, r.text
    assert r.json()["rows_affected"] == 1
    rows = query(ins_wrapper, "SELECT policy_until, is_active FROM POLICY_RECORDS WHERE vehicle_reg = 'DL05CD9876'")
    assert rows[0]["policy_until"] == "31/12/2027" and rows[0]["is_active"] == 1


def test_ins_expire(ins_wrapper):
    r = mutate(ins_wrapper, "expire", "DL05CD9876", {"until": "10/01/2026"})
    assert r.status_code == 200, r.text
    rows = query(ins_wrapper, "SELECT is_active FROM POLICY_RECORDS WHERE vehicle_reg = 'DL05CD9876'")
    assert rows[0]["is_active"] == 0


def test_ins_add_policy_inserts_a_second_row(ins_wrapper):
    r = mutate(ins_wrapper, "add_policy", "DL05CD9876",
              {"policy_type": "COMPREHENSIVE", "until": "31/12/2099"})
    assert r.status_code == 200, r.text
    assert r.json()["rows_affected"] == 1
    rows = query(ins_wrapper, "SELECT * FROM POLICY_RECORDS WHERE vehicle_reg = 'DL05CD9876'")
    assert len(rows) == 2
    new_row = next(x for x in rows if x["policy_id"] != 1)
    assert new_row["is_active"] == 1  # 31/12/2099 is in the future


def test_ins_add_policy_past_date_is_inactive(ins_wrapper):
    r = mutate(ins_wrapper, "add_policy", "DL05CD9876", {"until": "01/01/2020"})
    assert r.status_code == 200, r.text
    rows = query(ins_wrapper, "SELECT * FROM POLICY_RECORDS WHERE vehicle_reg = 'DL05CD9876' "
                              "AND policy_id != 1")
    assert rows[0]["is_active"] == 0


def test_ins_delete_policies(ins_wrapper):
    r = mutate(ins_wrapper, "delete_policies", "DL05CD9876")
    assert r.status_code == 200, r.text
    assert r.json()["rows_affected"] == 1
    assert query(ins_wrapper, "SELECT * FROM POLICY_RECORDS WHERE vehicle_reg = 'DL05CD9876'") == []


# ==================================================================== THEFT ===================

@pytest.fixture()
def theft_wrapper(tmp_path):
    url = make_db(tmp_path, "theft", [])
    base, server = serve(create_app(url, source_id="THEFT", dbms="sqlite", tables=["CRIME_RECORDS"]))
    try:
        yield base
    finally:
        server.stop()


def test_theft_steal_then_clear(theft_wrapper):
    r = mutate(theft_wrapper, "steal", "HR26EF4455", {"fir_no": "FIR00999/2026"})
    assert r.status_code == 200, r.text
    assert r.json()["rows_affected"] == 1
    rows = query(theft_wrapper, "SELECT * FROM CRIME_RECORDS WHERE "
                                "UPPER(REPLACE(REPLACE(vehicle_number,'-',''),' ','')) = 'HR26EF4455'")
    assert len(rows) == 1 and rows[0]["case_status"] == "OPEN" and rows[0]["stolen_flag"] == "Y"

    r2 = mutate(theft_wrapper, "clear", "HR26EF4455")
    assert r2.status_code == 200, r2.text
    assert r2.json()["rows_affected"] == 1
    assert query(theft_wrapper, "SELECT * FROM CRIME_RECORDS") == []


def test_theft_shred(theft_wrapper):
    r = mutate(theft_wrapper, "shred", "DL01AB1234")
    assert r.status_code == 200, r.text
    rows = query(theft_wrapper, "SELECT * FROM CRIME_RECORDS")
    assert len(rows) == 1
    row = rows[0]
    assert row["incident_type"] == "SHREDDING"
    assert row["stolen_flag"] == "N" and row["recovered_flag"] == "N"
    assert row["case_status"] == "CLOSED"


def test_theft_delete_incidents(theft_wrapper):
    mutate(theft_wrapper, "steal", "DL01AB1234")
    mutate(theft_wrapper, "shred", "DL01AB1234")
    assert len(query(theft_wrapper, "SELECT * FROM CRIME_RECORDS")) == 2
    r = mutate(theft_wrapper, "delete_incidents", "DL01AB1234")
    assert r.status_code == 200, r.text
    assert r.json()["rows_affected"] == 2
    assert query(theft_wrapper, "SELECT * FROM CRIME_RECORDS") == []


# ==================================================================== CAM =====================

@pytest.fixture()
def cam_wrapper(tmp_path):
    url = make_db(tmp_path, "cam", [
        "INSERT INTO CAMERAS (camera_id, location_name, lat, lon) "
        "VALUES ('CAM001', 'MG Road', 28.6, 77.2)",
    ])
    base, server = serve(create_app(url, source_id="CAM", dbms="sqlite",
                                    tables=["PLATE_CAPTURES", "CAMERAS"]))
    try:
        yield base
    finally:
        server.stop()


def test_cam_sight(cam_wrapper):
    r = mutate(cam_wrapper, "sight", "DL01AB1234",
              {"make": "Kia", "model": "Seltos", "colour": "Blue", "ocr_confidence": 0.87})
    assert r.status_code == 200, r.text
    assert r.json()["rows_affected"] == 1
    rows = query(cam_wrapper, "SELECT * FROM PLATE_CAPTURES WHERE plate_id = 'DL01AB1234'")
    assert len(rows) == 1
    assert rows[0]["observed_make"] == "Kia" and rows[0]["camera_id"] == "CAM001"
    assert abs(rows[0]["ocr_confidence"] - 0.87) < 1e-6


def test_cam_delete_sightings(cam_wrapper):
    mutate(cam_wrapper, "sight", "DL01AB1234")
    mutate(cam_wrapper, "sight", "DL01AB1234")
    assert len(query(cam_wrapper, "SELECT * FROM PLATE_CAPTURES")) == 2
    r = mutate(cam_wrapper, "delete_sightings", "DL01AB1234")
    assert r.status_code == 200, r.text
    assert r.json()["rows_affected"] == 2
    assert query(cam_wrapper, "SELECT * FROM PLATE_CAPTURES") == []


# ==================================================================== PUC =====================

@pytest.fixture()
def puc_wrapper(tmp_path):
    url = make_db(tmp_path, "puc", [])
    base, server = serve(create_app(url, source_id="PUC", dbms="sqlite", tables=["POLLUTION_CERT"]))
    try:
        yield base
    finally:
        server.stop()


def test_puc_issue_then_revoke(puc_wrapper):
    r = mutate(puc_wrapper, "issue", "DL01AB1234", {"emission_norm": "BS-VI"})
    assert r.status_code == 200, r.text
    assert r.json()["rows_affected"] == 1
    rows = query(puc_wrapper, "SELECT * FROM POLLUTION_CERT WHERE regn_number = 'DL01AB1234'")
    assert len(rows) == 1 and rows[0]["emission_norm"] == "BS-VI"

    r2 = mutate(puc_wrapper, "revoke", "DL01AB1234")
    assert r2.status_code == 200, r2.text
    assert r2.json()["rows_affected"] == 1
    assert query(puc_wrapper, "SELECT * FROM POLLUTION_CERT") == []


# ==================================================================== generic =================

def test_unknown_action_is_400(reg_wrapper):
    r = mutate(reg_wrapper, "does_not_exist", "DL01AB1234")
    assert r.status_code == 400
    assert "does_not_exist" in r.json()["error"]


@pytest.mark.parametrize("source_id, tables", [
    ("REG", ["VEHICLE_REGISTRATION", "OWNERS"]),
    ("INS", ["POLICY_RECORDS", "INSURERS"]),
    ("THEFT", ["CRIME_RECORDS"]),
    ("CAM", ["PLATE_CAPTURES", "CAMERAS"]),
    ("PUC", ["POLLUTION_CERT"]),
])
def test_admin_actions_endpoint_lists_every_action_with_params(tmp_path, source_id, tables):
    dir_name = source_id.lower()
    url = make_db(tmp_path, dir_name, [])
    base, server = serve(create_app(url, source_id=source_id, dbms="sqlite", tables=tables))
    try:
        r = httpx.get(f"{base}/admin/actions", timeout=10.0)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["source_id"] == source_id
        assert body["enabled"] is True
        assert set(body["actions"]) == set(ADMIN_ACTIONS[source_id])
        for name, meta in body["actions"].items():
            assert "params" in meta and "help" in meta
            for p in meta["params"]:
                assert set(p) == {"name", "default", "help"}
    finally:
        server.stop()


def test_admin_actions_endpoint_200_even_when_disabled(tmp_path):
    url = make_db(tmp_path, "reg", [])
    base, server = serve(create_app(url, source_id="REG", dbms="sqlite",
                                    tables=["VEHICLE_REGISTRATION", "OWNERS"], admin_enabled=False))
    try:
        r = httpx.get(f"{base}/admin/actions", timeout=10.0)
        assert r.status_code == 200, r.text
        assert r.json()["enabled"] is False
        assert set(r.json()["actions"]) == set(ADMIN_ACTIONS["REG"])
    finally:
        server.stop()
