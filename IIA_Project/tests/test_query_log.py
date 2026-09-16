"""Task 2.6: the query audit log (`QUERY_LOG` in `mediator/meta.db`) and its Reports tab section.

`mediator/core.py::run_global_query` appends one row per federated query -- what was asked, which
sources answered and how, and the verdict -- never a source's raw rows (CLAUDE.md's "no caching of
source data" invariant). The schema test below pins that down directly: the column set is exactly
the audit-trail columns, with nothing shaped like a row cache.

Function-scoped tests use their own empty tmp catalog (the `meta` fixture, the pattern
`tests/test_watchlist.py` established). The `run_global_query`/Reports-tab tests need real
wrappers answering, so they use the session-scoped `local_cluster` fixture from
`tests/conftest.py` instead of starting their own cluster.
"""
from __future__ import annotations

import sqlite3

import pytest
from streamlit.testing.v1 import AppTest

from mediator import catalog

PLATE = "DL01AB1234"
STOLEN_PLATE = "HR26EF4455"


@pytest.fixture()
def meta(tmp_path, monkeypatch):
    """An empty tmp catalog: QUERY_LOG created by init_meta_db, nothing logged yet."""
    monkeypatch.setattr(catalog, "META_DB_PATH", str(tmp_path / "meta.db"))
    catalog.init_meta_db()
    return str(tmp_path / "meta.db")


# --------------------------------------------------------------------------------- schema

def test_init_meta_db_creates_query_log(meta):
    with sqlite3.connect(meta) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "QUERY_LOG" in names


def test_query_log_schema_has_no_source_row_column(meta):
    """The whole point of the audit log: decisions and traces only, never a source's raw rows."""
    with sqlite3.connect(meta) as conn:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(QUERY_LOG)").fetchall()}
    assert columns == {
        "id", "ts", "plate", "requested_attrs", "sources_asked", "statuses",
        "decision", "confidence", "elapsed_ms",
    }
    forbidden = {"rows", "row_count", "source_rows", "raw_rows", "row_data", "results", "sources_detail"}
    assert not (columns & forbidden)


# --------------------------------------------------------------------- log_query / get_query_log

def test_log_query_round_trips(meta):
    catalog.log_query({
        "plate": PLATE,
        "requested_attrs": ["all"],
        "sources_asked": ["REG", "INS"],
        "statuses": {"REG": "OK", "INS": "TIMEOUT"},
        "decision": "CLEAR",
        "confidence": "MEDIUM",
        "elapsed_ms": 123.4,
    })
    rows = catalog.get_query_log()
    assert len(rows) == 1
    row = rows[0]
    assert row["plate"] == PLATE
    assert row["requested_attrs"] == ["all"]
    assert row["sources_asked"] == ["REG", "INS"]
    assert row["statuses"] == {"REG": "OK", "INS": "TIMEOUT"}
    assert row["decision"] == "CLEAR"
    assert row["confidence"] == "MEDIUM"
    assert row["elapsed_ms"] == 123.4
    assert row["ts"]


def _log(plate: str, **extra) -> None:
    base = {
        "plate": plate, "decision": "CLEAR", "confidence": "HIGH",
        "requested_attrs": ["all"], "sources_asked": ["REG"], "statuses": {"REG": "OK"},
        "elapsed_ms": 10,
    }
    base.update(extra)
    catalog.log_query(base)


def test_get_query_log_filters_by_plate(meta):
    _log(PLATE)
    _log(STOLEN_PLATE, decision="STOLEN — ALERT POLICE")
    only = catalog.get_query_log(plate=STOLEN_PLATE)
    assert [r["plate"] for r in only] == [STOLEN_PLATE]
    assert only[0]["decision"] == "STOLEN — ALERT POLICE"


def test_get_query_log_orders_newest_first_and_respects_limit(meta):
    for i in range(5):
        _log(PLATE, elapsed_ms=i)
    rows = catalog.get_query_log(limit=3)
    assert len(rows) == 3
    ids = [r["id"] for r in rows]
    assert ids == sorted(ids, reverse=True)


def test_get_query_log_is_empty_before_any_query(meta):
    assert catalog.get_query_log() == []
    assert catalog.get_query_log(plate=PLATE) == []


# ------------------------------------------------------------------------- core.py wires it in

def test_run_global_query_logs_a_row(local_cluster):
    from mediator.core import run_global_query

    before = len(catalog.get_query_log(plate=PLATE))
    result = run_global_query(PLATE, ["all"])
    profile = result["profile"]

    after = catalog.get_query_log(plate=PLATE)
    assert len(after) == before + 1
    row = after[0]  # newest first
    assert row["plate"] == PLATE
    assert row["decision"] == profile["decision"]
    assert row["confidence"] == profile["confidence"]
    assert row["statuses"] == profile["source_availability"]
    assert row["elapsed_ms"] is not None and row["elapsed_ms"] >= 0


def test_run_global_query_never_raises_when_the_query_log_is_broken(local_cluster, monkeypatch):
    """`mediator/core.py` wraps `catalog.log_query` in try/except: a broken meta.db must degrade to
    "no audit entry", never a failed query (the same rule Task 2.3 applies to risk/watchlist)."""
    from mediator import core

    def boom(_row):
        raise RuntimeError("meta.db is on fire")

    monkeypatch.setattr(catalog, "log_query", boom)
    result = core.run_global_query(PLATE, ["all"])
    assert result["profile"]["decision"]  # the decision itself is untouched


# --------------------------------------------------------------------------- GUI: Reports tab

def _reports_page() -> None:
    # AppTest re-executes this body as its own script, so it must import what it uses.
    from app.tabs.reports import render
    render()


def test_reports_tab_shows_the_audit_log_section(local_cluster):
    from mediator.core import run_global_query

    run_global_query(PLATE, ["all"])  # guarantee at least one row exists

    at = AppTest.from_function(_reports_page, default_timeout=120).run()
    assert not at.exception, [e.value for e in at.exception]
    # Task A3 turned the tab's pseudo-headings into `components.section`, so the heading and
    # its lead are one markdown block rather than a subheader plus a caption.
    page = " ".join(str(m.value) for m in at.markdown)
    assert "Query audit log" in page
    assert "no source rows are ever stored" in page.lower()

    tables = [df.value for df in at.dataframe if "plate" in df.value.columns]
    assert tables, "no audit dataframe rendered"
    assert PLATE in tables[-1]["plate"].tolist()
    assert "decision" in tables[-1].columns and "statuses" in tables[-1].columns


def test_reports_tab_audit_filter_narrows_to_one_plate(local_cluster):
    from mediator.core import run_global_query

    run_global_query(PLATE, ["all"])
    run_global_query(STOLEN_PLATE, ["all"])

    at = AppTest.from_function(_reports_page, default_timeout=120).run()
    at.text_input(key="rp_audit_plate").set_value(PLATE).run()
    assert not at.exception, [e.value for e in at.exception]

    tables = [df.value for df in at.dataframe if "plate" in df.value.columns]
    assert tables
    assert set(tables[-1]["plate"].tolist()) == {PLATE}
