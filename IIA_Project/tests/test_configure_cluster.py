"""`scripts/configure_cluster.py --probe` (Task C2).

A probe answers two different questions about a laptop, and the demo needs both: can the mediator
*read* it (`/health`), and does that agency still accept its own operator's *writes*
(`GET /admin/actions`, which reports `enabled: false` once the laptop sets `<SOURCE>_ADMIN=off`).
The second is what makes the cross-laptop write story checkable from the mediator machine before
anyone tries it live, so it is asserted here against three real wrappers: one with admin open, one
with admin shut, one not listening at all.
"""
from __future__ import annotations

import socket
import sqlite3
import time
from pathlib import Path

import httpx
import pytest

from mediator import catalog
from scripts import configure_cluster
from sources.server_manager import WrapperServerThread
from sources.wrapper_template import create_app

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _schema_statements(source_dir: str) -> list[str]:
    raw = (ROOT / "sources" / source_dir / "schema.sql").read_text(encoding="utf-8")
    raw = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("--"))
    return [s.strip() for s in raw.split(";") if s.strip()]


def make_db(tmp_path: Path, source_dir: str) -> str:
    db_path = tmp_path / f"{source_dir}.db"
    con = sqlite3.connect(db_path)
    for stmt in _schema_statements(source_dir):
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
def three_laptops(tmp_path, monkeypatch) -> None:
    """REG up with admin on, INS up with admin off, THEFT catalogued at a port nobody is bound to."""
    reg_base, reg_server = serve(create_app(
        make_db(tmp_path, "reg"), source_id="REG", dbms="sqlite",
        tables=["VEHICLE_REGISTRATION", "OWNERS"]))
    ins_base, ins_server = serve(create_app(
        make_db(tmp_path, "ins"), source_id="INS", dbms="sqlite",
        tables=["POLICY_RECORDS", "INSURERS"], admin_enabled=False))
    dead_base = f"http://127.0.0.1:{free_port()}"  # bound only long enough to reserve a free number

    monkeypatch.setattr(catalog, "META_DB_PATH", str(tmp_path / "meta.db"))
    for source_id, base_url in (("REG", reg_base), ("INS", ins_base), ("THEFT", dead_base)):
        catalog.register_source(source_id, source_id, "SQLite", base_url, "plate", "OFFICIAL",
                                0.9, ["plate_number"])
    with sqlite3.connect(catalog.META_DB_PATH) as meta:
        meta.execute("DELETE FROM SOURCE_CATALOG WHERE source_id NOT IN ('REG','INS','THEFT')")
    try:
        yield
    finally:
        reg_server.stop()
        ins_server.stop()


def _line(out: str, source_id: str) -> str:
    matches = [l for l in out.splitlines() if f" {source_id} " in l or l.split()[1:2] == [source_id]]
    assert matches, f"{source_id} missing from probe output:\n{out}"
    return matches[0]


def test_probe_reports_admin_state_for_every_source(three_laptops, capsys):
    rc = configure_cluster.probe()
    out = capsys.readouterr().out
    assert "admin: on" in _line(out, "REG")
    assert "admin: off" in _line(out, "INS")
    assert "admin: unreachable" in _line(out, "THEFT")
    assert rc == 1  # one source is down, so the probe exits non-zero


def test_probe_still_reports_reachability(three_laptops, capsys):
    configure_cluster.probe()
    out = capsys.readouterr().out
    assert _line(out, "REG").strip().startswith("UP")
    assert _line(out, "THEFT").strip().startswith("DOWN")
    assert "2/3 source(s) reachable" in out


def test_admin_state_of_an_unbound_port_is_unreachable():
    assert configure_cluster.admin_state(f"http://127.0.0.1:{free_port()}") == "unreachable"
