"""SQL Console (GUI tab 6): the professor types SQL, one chosen source executes it.

Reads go through the existing read-only POST /query; writes go through the opt-in POST /admin/sql,
which only exists when that laptop set <SOURCE_ID>_ADMIN_URL. Both are exercised over real HTTP
against a throwaway wrapper on a free port, so what is asserted is the wire behaviour, not a mock.
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
from sources.wrapper_template import QueryRejected, create_app, write_guard_sql

TABLES = ["CRIME_RECORDS"]
SCHEMA = """
    CREATE TABLE CRIME_RECORDS (incident_id INTEGER PRIMARY KEY, vehicle_number TEXT,
                                case_status TEXT);
    CREATE TABLE SECRET_NOTES (note_id INTEGER PRIMARY KEY, body TEXT);
    INSERT INTO CRIME_RECORDS VALUES (1, 'hr 26 ef 4455', 'OPEN');
"""


# =============================== unit: the write guard ===============================

def rejected(sql: str) -> str:
    with pytest.raises(QueryRejected) as exc:
        write_guard_sql(sql, TABLES)
    return str(exc.value)


def test_update_of_a_whitelisted_table_is_accepted():
    sql = "UPDATE CRIME_RECORDS SET case_status='CLOSED' WHERE incident_id=1"
    assert write_guard_sql(sql, TABLES) == sql


def test_insert_and_delete_of_a_whitelisted_table_are_accepted():
    for sql in ("INSERT INTO CRIME_RECORDS (incident_id) VALUES (9)",
                "delete from crime_records where incident_id = 9"):
        assert write_guard_sql(sql, TABLES) == sql


def test_ddl_is_rejected():
    # Refused twice over: DROP is not one of the three allowed first keywords, and it is on the
    # forbidden list wherever it appears (e.g. after a whitelisted UPDATE).
    assert rejected("DROP TABLE CRIME_RECORDS")
    assert "DROP" in rejected("UPDATE CRIME_RECORDS SET case_status = DROP").upper()


def test_write_against_a_table_outside_the_whitelist_is_rejected():
    assert "whitelist" in rejected("DELETE FROM OTHER_TABLE")


def test_a_read_is_not_a_write_and_belongs_on_slash_query():
    assert "INSERT" in rejected("SELECT 1")


def test_a_second_statement_smuggled_after_the_write_is_rejected():
    assert ";" in rejected("UPDATE X SET a=1; DROP TABLE X")


def test_comments_and_empty_input_are_rejected():
    assert "comment" in rejected("DELETE FROM CRIME_RECORDS -- everything")
    assert "empty" in rejected("   ")


def test_a_subselect_from_a_non_whitelisted_table_is_rejected():
    assert "whitelist" in rejected("INSERT INTO CRIME_RECORDS SELECT * FROM SECRET_NOTES")


# ============================ integration: POST /admin/sql ============================

def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_db(tmp_path: Path) -> str:
    con = sqlite3.connect(tmp_path / "tst.db")
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return f"sqlite:///{(tmp_path / 'tst.db').as_posix()}"


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
def writable(tmp_path) -> str:
    url = make_db(tmp_path)
    base, server = serve(create_app(url, source_id="TST", dbms="sqlite", tables=TABLES, admin_url=url))
    yield base
    server.stop()


@pytest.fixture()
def read_only(tmp_path) -> str:
    base, server = serve(create_app(make_db(tmp_path), source_id="TST", dbms="sqlite", tables=TABLES))
    yield base
    server.stop()


def test_insert_through_admin_sql_is_visible_to_the_next_read(writable):
    r = httpx.post(f"{writable}/admin/sql", timeout=10.0, json={
        "sql": "INSERT INTO CRIME_RECORDS (incident_id, vehicle_number, case_status) "
               "VALUES (2, 'dl 05 cd 9876', 'OPEN')"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["source_id"], body["rows_affected"]) == ("TST", 1) and body["executed_at"]
    rows = httpx.post(f"{writable}/query", json={"sql": "SELECT * FROM CRIME_RECORDS"}, timeout=10.0).json()["rows"]
    assert [r["incident_id"] for r in rows] == [1, 2]


def test_guard_rejection_is_400(writable):
    r = httpx.post(f"{writable}/admin/sql", json={"sql": "DROP TABLE CRIME_RECORDS"}, timeout=10.0)
    assert r.status_code == 400 and "INSERT, UPDATE or DELETE" in r.json()["error"]


def test_sql_the_database_rejects_is_400_with_its_message(writable):
    r = httpx.post(f"{writable}/admin/sql", json={"sql": "UPDATE CRIME_RECORDS SET nope = 1"}, timeout=10.0)
    assert r.status_code == 400 and "nope" in r.json()["error"]


def test_a_source_that_never_opted_in_answers_404(read_only):
    r = httpx.post(f"{read_only}/admin/sql", json={"sql": "DELETE FROM CRIME_RECORDS"}, timeout=10.0)
    assert r.status_code == 404 and "TST_ADMIN_URL" in r.json()["error"]


def test_query_is_still_read_only_on_a_wrapper_with_an_admin_url(writable):
    r = httpx.post(f"{writable}/query", json={"sql": "DELETE FROM CRIME_RECORDS"}, timeout=10.0)
    assert r.status_code == 400 and httpx.post(
        f"{writable}/query", json={"sql": "SELECT * FROM CRIME_RECORDS"}, timeout=10.0).json()["row_count"] == 1


# ================================ the tab, end to end ================================

@pytest.fixture()
def catalogued(writable, tmp_path, monkeypatch) -> str:
    monkeypatch.setattr(catalog, "META_DB_PATH", str(tmp_path / "meta.db"))
    catalog.register_source("TST", "Test Source (TST)", "SQLite", writable,
                            "vehicle_number", "OFFICIAL", 0.9, ["plate_number"])
    return writable


def _page() -> None:
    from app.tabs.sql_console import render
    render()


def run_tab(sql: str, button_key: str) -> AppTest:
    at = AppTest.from_function(_page, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    at.selectbox(key="sqlc_source").select("TST").run()
    at.text_area(key="sqlc_sql").set_value(sql).run()
    at.button(key=button_key).click().run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_select_runs_against_the_chosen_source_and_shows_its_rows(catalogued):
    at = run_tab("SELECT * FROM CRIME_RECORDS", "sqlc_run")
    assert len(at.dataframe) >= 1
    frame = at.dataframe[0].value
    assert list(frame["vehicle_number"]) == ["hr 26 ef 4455"]


def test_write_runs_against_the_chosen_source_and_reports_rows_affected(catalogued):
    at = run_tab("DELETE FROM CRIME_RECORDS WHERE incident_id=1", "sqlc_write")
    assert "1 row" in at.success[0].value
    left = httpx.post(f"{catalogued}/query", json={"sql": "SELECT * FROM CRIME_RECORDS"}, timeout=10.0)
    assert left.json()["row_count"] == 0


def test_a_rejected_write_is_shown_not_raised(catalogued):
    at = run_tab("DROP TABLE CRIME_RECORDS", "sqlc_write")
    assert "Rejected by the wrapper guard" in at.error[0].value


def test_a_source_that_is_down_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "META_DB_PATH", str(tmp_path / "meta.db"))
    catalog.register_source("TST", "Test Source (TST)", "SQLite", f"http://127.0.0.1:{free_port()}",
                            "vehicle_number", "OFFICIAL", 0.9, ["plate_number"])
    at = run_tab("SELECT * FROM CRIME_RECORDS", "sqlc_run")
    assert any("DOWN" in e.value or "TIMEOUT" in e.value for e in at.error)
