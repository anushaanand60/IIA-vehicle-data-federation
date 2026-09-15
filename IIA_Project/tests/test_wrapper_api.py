"""Wrapper HTTP API (CLAUDE.md §5.1) against a throwaway SQLite database."""
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from sources.wrapper_template import create_app

WHITELIST = ["crime_records", "case_notes"]


@pytest.fixture()
def db(tmp_path) -> Path:
    path = tmp_path / "theft.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE crime_records (case_no TEXT PRIMARY KEY, regn_mark TEXT NOT NULL,
                                    incident_epoch INTEGER, stolen_flag TEXT);
        CREATE TABLE case_notes (note_id INTEGER PRIMARY KEY,
                                 case_no TEXT REFERENCES crime_records(case_no), body TEXT);
        CREATE TABLE officers (badge TEXT PRIMARY KEY, name TEXT);
        INSERT INTO officers VALUES ('B1', 'not for export');
    """)
    con.executemany("INSERT INTO crime_records VALUES (?, ?, ?, ?)",
                    [(f"C{i:04d}", f"dl 01 ab {i:04d}", 1756500000 + i, "Y" if i % 2 else "N") for i in range(300)])
    con.commit()
    con.close()
    return path


def client_for(url: str, **kw) -> TestClient:
    return TestClient(create_app(url, source_id="THEFT", dbms="sqlite", tables=WHITELIST, **kw))


@pytest.fixture()
def client(db) -> TestClient:
    return client_for(f"sqlite:///{db.as_posix()}")


@pytest.fixture()
def dead_client(tmp_path) -> TestClient:
    return client_for(f"sqlite:///{(tmp_path / 'no_such_dir' / 'x.db').as_posix()}")


# --- /health ---------------------------------------------------------------------

def test_health_reports_up(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert (body["source_id"], body["dbms"], body["up"]) == ("THEFT", "sqlite", True)
    assert body["ts"]


def test_health_reports_503_when_database_is_unreachable(dead_client):
    r = dead_client.get("/health")
    assert r.status_code == 503
    assert r.json()["up"] is False and r.json()["error"] and r.json()["source_id"] == "THEFT"


# --- /schema ---------------------------------------------------------------------

def test_schema_exposes_only_whitelisted_tables(client):
    # Keyed by table name: mediator/matcher.py iterates source_schema["tables"].items().
    assert set(client.get("/schema").json()["tables"]) == {"crime_records", "case_notes"}


def test_schema_describes_columns_keys_and_samples(client):
    body = client.get("/schema").json()
    crimes = body["tables"]["crime_records"]
    assert crimes["table"] == crimes["table_name"] == "crime_records"
    cols = {c["name"]: c for c in crimes["columns"]}
    assert list(cols) == ["case_no", "regn_mark", "incident_epoch", "stolen_flag"]
    assert cols["case_no"]["pk"] is True and cols["regn_mark"]["nullable"] is False
    assert 0 < len(cols["regn_mark"]["samples"]) <= 20
    assert "dl 01 ab 0000" in cols["regn_mark"]["samples"]  # raw source format, untouched
    notes = body["tables"]["case_notes"]
    assert next(c for c in notes["columns"] if c["name"] == "case_no")["fk"] == "crime_records.case_no"


def test_schema_publishes_both_spellings_the_matcher_and_the_contract_use(client):
    cols = {c["name"]: c for c in client.get("/schema").json()["tables"]["case_notes"]["columns"]}
    case_no = cols["case_no"]
    assert (case_no["pk"], case_no["is_pk"]) == (False, False)
    assert case_no["is_fk"] is True and case_no["fk_target"] == "crime_records"
    assert case_no["sample_values"] == case_no["samples"]


def test_schema_reports_503_when_database_is_unreachable(dead_client):
    assert dead_client.get("/schema").status_code == 503


# --- /query ----------------------------------------------------------------------

def test_query_returns_raw_rows(client):
    r = client.post("/query", json={"sql": "SELECT regn_mark, incident_epoch, stolen_flag FROM crime_records "
                                           "WHERE regn_mark = 'dl 01 ab 0007'"})
    assert r.status_code == 200
    body = r.json()
    assert body["rows"] == [{"regn_mark": "dl 01 ab 0007", "incident_epoch": 1756500007, "stolen_flag": "Y"}]
    assert body["row_count"] == 1 and body["fetched_at"] and body["elapsed_ms"] >= 0


def test_query_with_no_matching_rows_is_still_200(client):
    r = client.post("/query", json={"sql": "SELECT case_no FROM crime_records WHERE regn_mark = 'nope'"})
    assert r.status_code == 200 and r.json()["rows"] == [] and r.json()["row_count"] == 0


def test_query_is_capped_at_200_rows(client):
    assert client.post("/query", json={"sql": "SELECT case_no FROM crime_records"}).json()["row_count"] == 200


def test_guard_rejection_is_400_with_error(client):
    r = client.post("/query", json={"sql": "SELECT name FROM officers"})
    assert r.status_code == 400 and "whitelist" in r.json()["error"]


def test_malformed_body_is_400_not_422(client):
    r = client.post("/query", json={"query": "SELECT 1"})
    assert r.status_code == 400 and "error" in r.json()


def test_sql_the_database_rejects_is_400_because_it_is_our_bug(client):
    r = client.post("/query", json={"sql": "SELECT no_such_column FROM crime_records"})
    assert r.status_code == 400 and "no_such_column" in r.json()["error"]


def test_query_reports_503_when_database_is_unreachable(dead_client):
    r = dead_client.post("/query", json={"sql": "SELECT case_no FROM crime_records"})
    assert r.status_code == 503 and r.json()["error"]


def test_slow_statement_is_cancelled_with_504(db):
    slow = client_for(f"sqlite:///{db.as_posix()}", statement_timeout_s=0.2)
    started = time.monotonic()
    r = slow.post("/query", json={"sql": "SELECT count(*) FROM crime_records a, crime_records b, "
                                         "crime_records c, crime_records d"})
    assert r.status_code == 504 and "timeout" in r.json()["error"].lower()
    assert time.monotonic() - started < 3


def test_connection_itself_refuses_writes(client):
    # Defence in depth: even SQL that somehow slipped past the guard cannot modify the source.
    with pytest.raises(Exception):
        with client.app.state.engine.begin() as conn:
            conn.execute(text("DELETE FROM crime_records"))
