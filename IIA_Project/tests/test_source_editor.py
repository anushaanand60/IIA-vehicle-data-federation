"""Source Editor sidebar (app/tabs/source_editor.py): the write form is built entirely from the
wrapper's own GET /admin/actions answer, never from GUI-side knowledge of what a source can do.

Uses a real HTTP wrapper on a free port, built with source_id="INS" so it carries the real INS
admin action menu (renew/expire/add_policy/delete_policies), but registered in the catalog under
an unrelated display id "TST" -- proving the sidebar only ever trusts what the wrapper reports at
/admin/actions, not the catalog key.
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

TABLES = ["POLICY_RECORDS", "INSURERS"]
SCHEMA = """
    CREATE TABLE POLICY_RECORDS (policy_id INTEGER PRIMARY KEY, vehicle_reg TEXT,
                                 insurer_id INTEGER, policy_type TEXT, policy_start TEXT,
                                 policy_until TEXT, is_active INTEGER);
    CREATE TABLE INSURERS (insurer_id INTEGER PRIMARY KEY, name TEXT);
    INSERT INTO INSURERS VALUES (1, 'Test Insurer');
    INSERT INTO POLICY_RECORDS VALUES (1, 'DL01AB1234', 1, 'COMPREHENSIVE', '01/01/2026',
                                       '31/12/2027', 1);
"""


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def make_db(tmp_path: Path) -> str:
    con = sqlite3.connect(tmp_path / "ins.db")
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    return f"sqlite:///{(tmp_path / 'ins.db').as_posix()}"


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
    base, server = serve(create_app(url, source_id="INS", dbms="sqlite", tables=TABLES, admin_url=url))
    yield base
    server.stop()


@pytest.fixture()
def disabled(tmp_path) -> str:
    base, server = serve(create_app(make_db(tmp_path), source_id="INS", dbms="sqlite", tables=TABLES,
                                    admin_enabled=False))
    yield base
    server.stop()


@pytest.fixture()
def catalogued(writable, tmp_path, monkeypatch) -> str:
    monkeypatch.setattr(catalog, "META_DB_PATH", str(tmp_path / "meta.db"))
    catalog.register_source("TST", "Test Source (TST)", "SQLite", writable,
                            "vehicle_reg", "OFFICIAL", 0.9, ["insurance_expiry"])
    return writable


def _page() -> None:
    from app.tabs.source_editor import render_sidebar
    render_sidebar()


def run_sidebar() -> AppTest:
    at = AppTest.from_function(_page, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


# ============================== (1) actions populated from the endpoint ==============================

def test_actions_are_populated_from_the_wrapper_endpoint(catalogued):
    at = run_sidebar()
    at.sidebar.selectbox(key="se_source").select("TST").run()
    action_options = at.sidebar.selectbox(key="se_action").options
    assert set(action_options) == {"renew", "expire", "add_policy", "delete_policies"}


# ==================== (2) select expire, apply -> row affected, is_active=0 ====================

def test_expire_action_applies_and_query_reflects_it(catalogued):
    at = run_sidebar()
    at.sidebar.selectbox(key="se_source").select("TST").run()
    at.sidebar.selectbox(key="se_action").select("expire").run()
    at.sidebar.text_input(key="se_plate").set_value("DL-01-AB-1234").run()
    at.sidebar.button(key="se_apply").click().run()
    assert not at.exception, [e.value for e in at.exception]

    success_values = [s.value for s in at.sidebar.success]
    assert any("row(s) affected" in v for v in success_values)
    assert at.session_state["selected_plate"] == "DL-01-AB-1234"

    rows = httpx.post(f"{catalogued}/query",
                      json={"sql": "SELECT is_active FROM POLICY_RECORDS WHERE vehicle_reg = 'DL01AB1234'"},
                      timeout=10.0).json()["rows"]
    assert rows[0]["is_active"] == 0


# ==================== (3) disabled wrapper -> info ====================

def test_admin_disabled_shows_info_message(disabled, tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "META_DB_PATH", str(tmp_path / "meta_disabled.db"))
    catalog.register_source("TST", "Test Source (TST)", "SQLite", disabled,
                            "vehicle_reg", "OFFICIAL", 0.9, ["insurance_expiry"])
    at = run_sidebar()
    at.sidebar.selectbox(key="se_source").select("TST").run()
    info_values = [i.value for i in at.sidebar.info]
    assert any("ADMIN=off" in v or "disabled" in v.lower() for v in info_values)
    assert "se_action" not in {s.key for s in at.sidebar.selectbox}


# ==================== (4) dead base_url -> error, no exception ====================

def test_dead_source_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "META_DB_PATH", str(tmp_path / "meta_dead.db"))
    catalog.register_source("TST", "Test Source (TST)", "SQLite", f"http://127.0.0.1:{free_port()}",
                            "vehicle_reg", "OFFICIAL", 0.9, ["insurance_expiry"])
    at = run_sidebar()
    at.sidebar.selectbox(key="se_source").select("TST").run()
    assert not at.exception, [e.value for e in at.exception]
    warning_values = [w.value for w in at.sidebar.warning]
    assert any("DOWN" in v for v in warning_values)
