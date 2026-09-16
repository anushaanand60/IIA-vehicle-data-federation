"""Task 3.2 (Decomposer portability): SQL that runs unchanged on SQLite/PostgreSQL/MySQL, and
camera plates that still match after an OCR misread (see docs/superpowers/plans/2026-09-16-overhaul.md).

Ported from C:\\Users\\siddh_ygv5bws\\OneDrive\\Desktop\\iia-combined\\IIA_Project\\tests\\test_decomposer_hardening.py,
adapted to this repo's file layout and extended with the explicit-column-list requirement.
"""
import re
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mediator.decomposer import decompose_query
from sources.wrapper_template import create_app, guard_sql

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def validated_mappings() -> None:
    from tests.fixtures import inject_test_mappings
    inject_test_mappings()


def decompose(source_id: str, plate: str) -> str:
    return decompose_query(source_id, plate)


# --- LEFT JOIN ------------------------------------------------------------------------------

def test_lookup_tables_are_left_joined_so_a_missing_owner_never_hides_a_vehicle():
    assert "LEFT JOIN OWNERS ON" in decompose("REG", "DL01AB1234")
    assert "LEFT JOIN INSURERS ON" in decompose("INS", "DL01AB1234")
    assert "LEFT JOIN CAMERAS ON" in decompose("CAM", "DL01AB1234")


def test_no_source_sql_uses_inner_join():
    for source_id in ("REG", "INS", "THEFT", "CAM"):
        sql = decompose(source_id, "DL01AB1234")
        assert "INNER JOIN" not in sql.upper()


# --- portable ordering ------------------------------------------------------------------------

def test_ins_sql_has_no_order_by_because_policy_until_is_ddmmyyyy_text():
    sql = decompose("INS", "DL01AB1234")
    assert "ORDER BY" not in sql.upper()
    assert "||" not in sql
    assert "substr(" not in sql.lower()


def test_no_source_sql_ever_concatenates_a_sortable_date_with_pipes():
    for source_id in ("REG", "INS", "THEFT", "CAM"):
        sql = decompose(source_id, "DL01AB1234")
        assert "||" not in sql
        assert "substr(" not in sql.lower()


def test_theft_and_cam_still_push_down_latest_by_on_a_plain_sortable_column():
    sql_theft = decompose("THEFT", "DL01AB1234")
    assert "ORDER BY CRIME_RECORDS.reported_date DESC LIMIT 1" in sql_theft

    sql_cam = decompose("CAM", "DL01AB1234")
    assert "ORDER BY PLATE_CAPTURES.captured_at DESC LIMIT 1" in sql_cam


# --- plate predicate / OCR fold -----------------------------------------------------------------

def test_authoritative_sources_keep_exact_plate_matching_no_ocr_fold():
    for source_id in ("REG", "INS", "THEFT"):
        sql = decompose(source_id, "DL01AB1234")
        assert "UPPER(REPLACE(REPLACE(" in sql
        assert "'O'" not in sql and "'I'" not in sql


def test_observational_source_folds_o_0_and_i_1_on_both_sides():
    sql = decompose("CAM", "DL01AB1234")
    assert "REPLACE(REPLACE(" in sql
    assert "'O', '0'" in sql and "'I', '1'" in sql
    assert "'DL01AB1234'" in sql  # canonical plate already has no O/I to fold


@pytest.fixture()
def cam_client(tmp_path) -> TestClient:
    db = tmp_path / "cam.db"
    with closing(sqlite3.connect(db)) as con:
        con.executescript((ROOT / "sources" / "cam" / "schema.sql").read_text(encoding="utf-8"))
        con.execute("INSERT INTO CAMERAS VALUES ('CAM005', 'Old Delhi Signal', 28.6562, 77.2410)")
        con.executemany(
            "INSERT INTO PLATE_CAPTURES VALUES (?, ?, 'CAM005', ?, 'Tata', 'Nexon', 'Grey', 0.9)",
            [(1, "MH12IJ7788", "2026-09-01T07:10:00"),   # read correctly
             (2, "MH121J7788", "2026-09-04T07:10:00"),   # latest sighting, letter I read as digit 1
             (3, "MH12IJ7789", "2026-09-05T07:10:00")])  # a different vehicle
        con.commit()
    return TestClient(create_app(f"sqlite:///{db.as_posix()}", source_id="CAM", dbms="sqlite",
                                 tables=["CAMERAS", "PLATE_CAPTURES"]))


def test_camera_sub_query_finds_the_latest_sighting_despite_an_o_0_or_i_1_misread(cam_client):
    sql = decompose("CAM", "MH12IJ7788")
    guard_sql(sql, ["CAMERAS", "PLATE_CAPTURES"])
    rows = cam_client.post("/query", json={"sql": sql}).json()["rows"]
    assert [r["plate_id"] for r in rows] == ["MH121J7788"]


# --- explicit column list, no SELECT * -----------------------------------------------------------

def test_no_source_sql_uses_select_star():
    for source_id in ("REG", "INS", "THEFT", "CAM"):
        sql = decompose(source_id, "DL01AB1234")
        assert not re.match(r"SELECT\s+\*", sql, re.I)
        assert re.match(r"SELECT\s+\S", sql, re.I)


def test_select_list_carries_every_mapped_column_the_integrator_reads():
    # integrator.py reads these source column names directly for latest-wins / derived status.
    sql_ins = decompose("INS", "DL01AB1234")
    assert "POLICY_RECORDS.policy_until" in sql_ins

    sql_theft = decompose("THEFT", "DL01AB1234")
    for col in ("stolen_flag", "recovered_flag", "case_status", "reported_date"):
        assert f"CRIME_RECORDS.{col}" in sql_theft

    sql_cam = decompose("CAM", "DL01AB1234")
    assert "PLATE_CAPTURES.captured_at" in sql_cam


# --- SQL actually executes on a real, schema.sql-built SQLite db and returns demo rows -----------

@pytest.fixture()
def reg_client(tmp_path) -> TestClient:
    db = tmp_path / "reg.db"
    with closing(sqlite3.connect(db)) as con:
        con.executescript((ROOT / "sources" / "reg" / "schema.sql").read_text(encoding="utf-8"))
        con.execute("INSERT INTO OWNERS VALUES (1, 'Asha Rao', 'MG Road', 'New Delhi')")
        con.execute("INSERT INTO VEHICLE_REGISTRATION VALUES "
                    "(1, 'DL01AB1234', 1, 'Maruti Suzuki', 'Swift', 'White', 'PETROL', "
                    "'2022-01-01', 'ACTIVE', 'DL01')")
        con.commit()
    return TestClient(create_app(f"sqlite:///{db.as_posix()}", source_id="REG", dbms="sqlite",
                                 tables=["OWNERS", "VEHICLE_REGISTRATION"]))


def test_reg_sql_executes_and_returns_the_demo_vehicle(reg_client):
    sql = decompose("REG", "DL01AB1234")
    guard_sql(sql, ["OWNERS", "VEHICLE_REGISTRATION"])
    rows = reg_client.post("/query", json={"sql": sql}).json()["rows"]
    assert len(rows) == 1
    assert rows[0]["registration_no"] == "DL01AB1234"
    assert rows[0]["full_name"] == "Asha Rao"


@pytest.fixture()
def ins_client(tmp_path) -> TestClient:
    db = tmp_path / "ins.db"
    with closing(sqlite3.connect(db)) as con:
        con.executescript((ROOT / "sources" / "ins" / "schema.sql").read_text(encoding="utf-8"))
        con.execute("INSERT INTO INSURERS VALUES (1, 'Bajaj Allianz')")
        con.execute("INSERT INTO POLICY_RECORDS VALUES "
                    "(1, 'DL01AB1234', 1, 'COMPREHENSIVE', '01/01/2026', '31/12/2027', 1, 15000.00)")
        con.commit()
    return TestClient(create_app(f"sqlite:///{db.as_posix()}", source_id="INS", dbms="sqlite",
                                 tables=["INSURERS", "POLICY_RECORDS"]))


def test_ins_sql_executes_and_returns_the_demo_policy(ins_client):
    sql = decompose("INS", "DL01AB1234")
    guard_sql(sql, ["INSURERS", "POLICY_RECORDS"])
    rows = ins_client.post("/query", json={"sql": sql}).json()["rows"]
    assert len(rows) == 1
    assert rows[0]["vehicle_reg"] == "DL01AB1234"
    assert rows[0]["insurer_name"] == "Bajaj Allianz"
    assert rows[0]["policy_until"] == "31/12/2027"


@pytest.fixture()
def theft_client(tmp_path) -> TestClient:
    db = tmp_path / "theft.db"
    with closing(sqlite3.connect(db)) as con:
        con.executescript((ROOT / "sources" / "theft" / "schema.sql").read_text(encoding="utf-8"))
        con.execute("INSERT INTO CRIME_RECORDS VALUES "
                    "(1, 'hr 26 ef 4455', 'FIR00123/2026', 1750000000, 'THEFT', 'Y', 'N', "
                    "'OPEN', 'Connaught Place PS')")
        con.commit()
    return TestClient(create_app(f"sqlite:///{db.as_posix()}", source_id="THEFT", dbms="sqlite",
                                 tables=["CRIME_RECORDS"]))


def test_theft_sql_executes_and_returns_the_demo_incident(theft_client):
    sql = decompose("THEFT", "HR26EF4455")
    guard_sql(sql, ["CRIME_RECORDS"])
    rows = theft_client.post("/query", json={"sql": sql}).json()["rows"]
    assert len(rows) == 1
    assert rows[0]["case_status"] == "OPEN"
    assert rows[0]["stolen_flag"] == "Y"


def test_cam_sql_executes_and_returns_the_demo_sighting(cam_client):
    sql = decompose("CAM", "MH12IJ7789")
    guard_sql(sql, ["CAMERAS", "PLATE_CAPTURES"])
    rows = cam_client.post("/query", json={"sql": sql}).json()["rows"]
    assert len(rows) == 1
    assert rows[0]["plate_id"] == "MH12IJ7789"
    assert rows[0]["observed_colour"] == "Grey"
