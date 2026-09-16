"""Task 0.1: admin writes are ON by default so a plain SQLite wrapper (or any laptop that only ever
set <SOURCE_ID>_DB_URL) accepts writes on /admin/sql without a separate <SOURCE_ID>_ADMIN_URL.
<SOURCE_ID>_ADMIN=off is the only way back to the original read-only wrapper.
"""
from __future__ import annotations

import socket
import sqlite3
import time
from pathlib import Path

import httpx
import pytest

from sources.server_manager import WrapperServerThread
from sources.wrapper_template import create_app, from_env

TABLES = ["CRIME_RECORDS"]
SCHEMA = """
    CREATE TABLE CRIME_RECORDS (incident_id INTEGER PRIMARY KEY, vehicle_number TEXT,
                                case_status TEXT);
    INSERT INTO CRIME_RECORDS VALUES (1, 'hr 26 ef 4455', 'OPEN');
"""


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


# ============================ create_app: default admin engine ============================

def test_default_sqlite_wrapper_accepts_admin_sql_and_query_sees_it(tmp_path):
    url = make_db(tmp_path)
    base, server = serve(create_app(url, source_id="TST", dbms="sqlite", tables=TABLES))
    try:
        r = httpx.post(f"{base}/admin/sql", timeout=10.0, json={
            "sql": "INSERT INTO CRIME_RECORDS (incident_id, vehicle_number, case_status) "
                   "VALUES (2, 'dl 05 cd 9876', 'OPEN')"})
        assert r.status_code == 200, r.text
        assert r.json()["rows_affected"] == 1
        rows = httpx.post(f"{base}/query", json={"sql": "SELECT * FROM CRIME_RECORDS"},
                          timeout=10.0).json()["rows"]
        assert [row["incident_id"] for row in rows] == [1, 2]
    finally:
        server.stop()


def test_admin_enabled_false_answers_404(tmp_path):
    url = make_db(tmp_path)
    base, server = serve(create_app(url, source_id="TST", dbms="sqlite", tables=TABLES,
                                    admin_enabled=False))
    try:
        r = httpx.post(f"{base}/admin/sql", json={"sql": "DELETE FROM CRIME_RECORDS"}, timeout=10.0)
        assert r.status_code == 404
        assert "TST_ADMIN_URL" in r.json()["error"] and "TST_ADMIN=on" in r.json()["error"]
        r = httpx.post(f"{base}/admin/mutate", json={"action": "steal", "plate": "HR26EF4455"},
                       timeout=10.0)
        assert r.status_code == 404
    finally:
        server.stop()


def test_query_stays_read_only_with_default_admin_engine(tmp_path):
    # Regression: the new writable admin engine must never leak into /query's engine.
    url = make_db(tmp_path)
    base, server = serve(create_app(url, source_id="TST", dbms="sqlite", tables=TABLES))
    try:
        r = httpx.post(f"{base}/query", json={"sql": "DELETE FROM CRIME_RECORDS"}, timeout=10.0)
        assert r.status_code == 400
        left = httpx.post(f"{base}/query", json={"sql": "SELECT * FROM CRIME_RECORDS"}, timeout=10.0)
        assert left.json()["row_count"] == 1
    finally:
        server.stop()


# ================================ from_env: <ID>_ADMIN toggle ================================

def test_from_env_with_only_db_url_set_defaults_admin_on(tmp_path, monkeypatch):
    url = make_db(tmp_path)
    monkeypatch.setenv("TST_DB_URL", url)
    monkeypatch.delenv("TST_ADMIN", raising=False)
    monkeypatch.delenv("TST_ADMIN_URL", raising=False)
    base, server = serve(from_env("TST", default_url=url, default_dbms="SQLite",
                                  default_tables=TABLES))
    try:
        r = httpx.post(f"{base}/admin/sql", json={
            "sql": "UPDATE CRIME_RECORDS SET case_status='CLOSED' WHERE incident_id=1"},
            timeout=10.0)
        assert r.status_code == 200, r.text
    finally:
        server.stop()


def test_from_env_with_admin_off_answers_404(tmp_path, monkeypatch):
    url = make_db(tmp_path)
    monkeypatch.setenv("TST_DB_URL", url)
    monkeypatch.setenv("TST_ADMIN", "off")
    monkeypatch.delenv("TST_ADMIN_URL", raising=False)
    base, server = serve(from_env("TST", default_url=url, default_dbms="SQLite",
                                  default_tables=TABLES))
    try:
        r = httpx.post(f"{base}/admin/sql", json={"sql": "DELETE FROM CRIME_RECORDS"}, timeout=10.0)
        assert r.status_code == 404
    finally:
        server.stop()
    monkeypatch.delenv("TST_ADMIN", raising=False)
    monkeypatch.delenv("TST_DB_URL", raising=False)


def test_query_still_refuses_delete_on_a_from_env_wrapper(tmp_path, monkeypatch):
    url = make_db(tmp_path)
    monkeypatch.setenv("TST_DB_URL", url)
    monkeypatch.delenv("TST_ADMIN", raising=False)
    monkeypatch.delenv("TST_ADMIN_URL", raising=False)
    base, server = serve(from_env("TST", default_url=url, default_dbms="SQLite",
                                  default_tables=TABLES))
    try:
        r = httpx.post(f"{base}/query", json={"sql": "DELETE FROM CRIME_RECORDS"}, timeout=10.0)
        assert r.status_code == 400
    finally:
        server.stop()
    monkeypatch.delenv("TST_DB_URL", raising=False)
