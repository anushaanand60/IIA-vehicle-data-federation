"""Self-healing local sources (workstream G, item 1).

The bug: the app started its cluster once per server and start_all() skipped every port that was busy
at that moment, so wrappers never came back after a test run released 8001-8005. These tests hold a
free high port with a dummy socket, release it, and check ensure_local_sources() reclaims it. They
use their own SourceCluster on high ports, never 8001-8005, and stop every server they start.
"""
from __future__ import annotations

import socket
import time

import httpx
import pytest

from sources import server_manager as sm


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_free(port: int, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while sm.is_port_in_use(port) and time.monotonic() < deadline:
        time.sleep(0.05)


@pytest.fixture()
def cluster():
    c = sm.SourceCluster()
    yield c
    ports = [srv.port for srv in c.servers.values()]
    c.stop_all()
    for port in ports:
        _wait_free(port)


def test_port_held_then_released_is_reclaimed(cluster):
    port = _free_port()
    catalog = {"REG": {"base_url": f"http://127.0.0.1:{port}"}}
    holder = socket.socket()
    holder.bind(("127.0.0.1", port))
    holder.listen(1)
    try:  # what start_all() met: the port busy at start-up, so nothing was started
        assert sm.ensure_local_sources(catalog, cluster=cluster) == {"REG": "running"}
        assert "REG" not in cluster.servers
    finally:
        holder.close()
    _wait_free(port)
    assert sm.ensure_local_sources(catalog, cluster=cluster) == {"REG": "started"}
    r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=3, trust_env=False)
    assert r.status_code == 200 and r.json()["source_id"] == "REG"
    assert sm.ensure_local_sources(catalog, cluster=cluster) == {"REG": "running"}  # idempotent


def test_dead_thread_is_pruned_and_restarted(cluster):
    port = _free_port()
    catalog = {"THEFT": {"base_url": f"http://localhost:{port}"}}
    assert sm.ensure_local_sources(catalog, cluster=cluster)["THEFT"] == "started"
    cluster.servers["THEFT"].stop()  # dies without leaving cluster.servers: the old blocker
    cluster.servers["THEFT"].thread.join(5)
    _wait_free(port)
    assert sm.ensure_local_sources(catalog, cluster=cluster)["THEFT"] == "started"
    assert cluster.servers["THEFT"].thread.is_alive()


def test_remote_unknown_and_puc_filter(cluster):
    catalog = {"INS": {"base_url": "http://192.168.1.40:8002"}, "XYZ": {"base_url": f"http://127.0.0.1:{_free_port()}"},
               "PUC": {"base_url": "http://10.0.0.9:8005"}}
    out = sm.ensure_local_sources(catalog, include_puc=False, cluster=cluster)
    assert out["INS"] == "remote" and out["XYZ"].startswith("failed:") and "PUC" not in out
    assert cluster.servers == {}


def test_suspended_source_is_not_healed(cluster):
    port = _free_port()
    catalog = {"CAM": {"base_url": f"http://127.0.0.1:{port}"}}
    assert sm.ensure_local_sources(catalog, cluster=cluster)["CAM"] == "started"
    assert cluster.suspend("CAM") is True
    _wait_free(port)
    assert sm.ensure_local_sources(catalog, cluster=cluster)["CAM"].startswith("stopped")
    cluster.resume("CAM")
    assert sm.ensure_local_sources(catalog, cluster=cluster)["CAM"] == "started"
    assert cluster.suspend("NOT_OWNED") is False and "NOT_OWNED" not in cluster.held
