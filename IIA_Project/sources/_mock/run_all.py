"""Serve the mock sources through the real wrapper modules.

    python sources/_mock/run_all.py                  # REG, INS, THEFT, CAM on :8001-:8004; Ctrl+C stops them
    python sources/_mock/run_all.py --only REG,CAM   # a partial federation, for failure demos
    python sources/_mock/run_all.py --with-puc       # adds the fifth agency on :8005 for the UC6 demo;
                                                     # query it with IIA_REGISTRY=sources/_mock/mock_mappings_uc6.json

Each source is its own process, so killing one looks exactly like a laptop dropping off the
network. Only the database URL differs from the real deployment.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from sources._mock.seed import HERE, build_all  # noqa: E402

SOURCE_IDS = ("REG", "INS", "THEFT", "CAM")
PORTS = {"REG": 8001, "INS": 8002, "THEFT": 8003, "CAM": 8004, "PUC": 8005}
MODULES = {**{s: f"sources.{s.lower()}.wrapper" for s in SOURCE_IDS}, "PUC": "sources._mock.puc_wrapper"}


def start(source_id: str, db_path: Path, port: int) -> subprocess.Popen:
    env = {**os.environ,
           f"{source_id}_DB_URL": f"sqlite:///{db_path.as_posix()}",
           f"{source_id}_DBMS": "sqlite (mock)",
           "PYTHONPATH": os.pathsep.join(p for p in (str(ROOT), os.environ.get("PYTHONPATH")) if p)}
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", f"{MODULES[source_id]}:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=ROOT, env=env)


def wait_healthy(procs: dict[str, subprocess.Popen], ports: dict[str, int], timeout_s: float = 30.0) -> list[str]:
    """Return the sources that did not become healthy (e.g. port already in use)."""
    pending, deadline = set(procs), time.monotonic() + timeout_s
    while pending and time.monotonic() < deadline:
        for source_id in sorted(pending):
            if procs[source_id].poll() is not None:
                return sorted(pending)  # a wrapper exited during startup: fail fast
            try:
                if httpx.get(f"http://127.0.0.1:{ports[source_id]}/health", timeout=0.5).status_code == 200:
                    pending.discard(source_id)
            except httpx.HTTPError:
                pass
        time.sleep(0.1)
    return sorted(pending)


@contextmanager
def running(source_ids: Iterable[str] = SOURCE_IDS, ports: dict[str, int] | None = None,
            db_dir: str | Path | None = None) -> Iterator[dict[str, subprocess.Popen]]:
    """Build fresh mock databases, start one wrapper process per source, stop them all on exit."""
    ids = list(source_ids)
    ports = {source_id: (ports or PORTS)[source_id] for source_id in ids}
    dbs = build_all(db_dir or HERE)
    procs = {source_id: start(source_id, dbs[source_id], ports[source_id]) for source_id in ids}
    try:
        if failed := wait_healthy(procs, ports):
            raise RuntimeError(f"mock sources failed to start (port in use?): {failed}")
        yield procs
    finally:
        for proc in procs.values():
            proc.terminate()
        for proc in procs.values():
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", default=",".join(SOURCE_IDS), help="comma-separated source ids")
    parser.add_argument("--with-puc", action="store_true", help="also serve the UC6 pollution authority on :8005")
    args = parser.parse_args()
    ids = [s.strip().upper() for s in args.only.split(",") if s.strip()] + (["PUC"] if args.with_puc else [])
    if unknown := [s for s in ids if s not in PORTS]:
        parser.error(f"unknown source id(s): {unknown}")
    with running(dict.fromkeys(ids)) as procs:
        for source_id in procs:
            print(f"  {source_id:<6} http://127.0.0.1:{PORTS[source_id]}   pid {procs[source_id].pid}")
        print("mock federation up; kill a pid to simulate a source going down; Ctrl+C to stop")
        stopped: set[str] = set()
        try:
            while True:
                for source_id, proc in procs.items():
                    if source_id not in stopped and proc.poll() is not None:
                        stopped.add(source_id)
                        print(f"  {source_id} stopped (exit {proc.returncode}); the others keep serving")
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("stopping")


if __name__ == "__main__":
    main()
