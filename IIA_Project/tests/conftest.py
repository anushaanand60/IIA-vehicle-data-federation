"""Session-scoped local source cluster, shared by every test that needs the mock wrappers running.

Why this exists
----------------
`tests/test_ground_truth.py` (fixture `team_cluster`) and `tests/test_e2e_groundtruth.py`
(`setUpClass`) each used to call `sources.server_manager.get_cluster()` -- a *process-wide
singleton* -- and independently `start_all()` / `stop_all()` it. Two modules doing that in
whatever order pytest happens to collect and run them race each other: one module's `stop_all()`
can tear a port down while the other module's test is still mid-query against it. That produced
the intermittent `DL01AB1234` -> UNDETERMINED failure observed roughly once per few full test-suite
runs. A single session-scoped fixture removes the race by starting the cluster exactly once and
stopping it exactly once, after every dependent test has finished.

Why this also removes the `configure_cluster.py --local` prerequisite
-----------------------------------------------------------------------
`mediator/meta.db` (`mediator.catalog.META_DB_PATH`) is the same file the real four-laptop demo
uses, and it may legitimately hold a developer's hotspot/LAN `base_url`s (set via
`scripts/configure_cluster.py --set ...`). Tests must run against `127.0.0.1`, but a `pytest` run
must never silently overwrite that real file -- nobody wants their laptop IPs wiped by a test run.

So this fixture never touches the real `meta.db`. It copies it into a tmp directory (this also
carries forward the `MAPPING_REGISTRY` rows that prior matcher/dev runs have already seeded there --
see the note on `tests/fixtures.py::inject_test_mappings` below), rewrites only the
`SOURCE_CATALOG.base_url` column *in that copy* to `http://127.0.0.1:800x`, and points
`mediator.catalog.META_DB_PATH` at the copy for the rest of the pytest session. `mediator.report`
imports `META_DB_PATH` *by value* (`from mediator.catalog import META_DB_PATH`), so it has its own
module-level name and must be redirected separately -- the same gotcha documented in
`tests/test_report_bundle.py` and `tests/test_tabs_render.py`.

One known exception: `tests/fixtures.py::inject_test_mappings` is not touched by this fixture (it
is not in scope here) and still writes into the *real* `mediator/meta.db`, not this copy. That is
harmless: its inserts are `INSERT OR IGNORE` against mapping rows that are already present in the
real file (and therefore already present in the copy taken from it) from earlier matcher/seeding
work, so calling it again after this fixture is active is a no-op against rows the copy already has.

Net effect: `python -m pytest -q` is green without running `scripts/configure_cluster.py --local`
first, and the real catalog a developer configured for the physical demo is left untouched.
"""
from __future__ import annotations

import shutil
import socket
import time
from pathlib import Path

import httpx
import pytest

# Conventional ports (mirrors sources/server_manager.py::SOURCE_CONFIGS and
# scripts/configure_cluster.py::DEFAULT_PORTS).
_PORTS = {"REG": 8001, "INS": 8002, "THEFT": 8003, "CAM": 8004, "PUC": 8005}


def _wait_for_health(ports: "dict[str, int]", timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    pending = set(ports.values())
    while pending and time.monotonic() < deadline:
        for port in list(pending):
            try:
                if httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5).status_code == 200:
                    pending.discard(port)
            except httpx.HTTPError:
                pass
        if pending:
            time.sleep(0.1)
    if pending:
        raise RuntimeError(f"source(s) on port(s) {sorted(pending)} never answered /health")


def _wait_for_free(ports: "dict[str, int]", timeout_s: float = 10.0) -> None:
    deadline = time.monotonic() + timeout_s
    for port in ports.values():
        while time.monotonic() < deadline:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(0.2)
                try:
                    sock.connect(("127.0.0.1", port))
                except (socket.timeout, ConnectionRefusedError, OSError):
                    break  # nobody answered: the port is free
            time.sleep(0.1)


@pytest.fixture(scope="session")
def local_cluster(tmp_path_factory: pytest.TempPathFactory):
    """Start REG/INS/THEFT/CAM/PUC once for the whole test session, against a tmp copy of the
    catalog pinned to 127.0.0.1. See module docstring for the full rationale."""
    import mediator.catalog as catalog
    import mediator.report as report
    from sources.server_manager import get_cluster

    real_meta = Path(catalog.META_DB_PATH)
    tmp_meta = tmp_path_factory.mktemp("local_cluster_meta") / "meta.db"
    if real_meta.exists():
        shutil.copy(real_meta, tmp_meta)

    original_catalog_path = catalog.META_DB_PATH
    original_report_path = report.META_DB_PATH
    catalog.META_DB_PATH = str(tmp_meta)
    report.META_DB_PATH = str(tmp_meta)
    catalog.init_meta_db()

    existing = catalog.get_source_catalog()
    for source_id, port in _PORTS.items():
        meta = existing.get(source_id)
        if meta is None:  # e.g. PUC, only registered live in the UC6 demo
            continue
        catalog.register_source(
            source_id=source_id,
            display_name=meta["display_name"],
            dbms=meta["dbms"],
            base_url=f"http://127.0.0.1:{port}",
            identifier_attr=meta["identifier_attr"],
            authority=meta["authority"],
            trust_score=meta["trust_score"],
            covers=meta.get("covers", []),
            timeout_ms=meta.get("timeout_ms", 1500),
        )

    cluster = get_cluster()
    cluster.start_all(include_puc=True)
    try:
        _wait_for_health(_PORTS)
        yield cluster
    finally:
        cluster.stop_all()
        _wait_for_free(_PORTS)
        catalog.META_DB_PATH = original_catalog_path
        report.META_DB_PATH = original_report_path
