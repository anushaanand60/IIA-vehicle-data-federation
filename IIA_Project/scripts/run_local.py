"""One-command local rehearsal: the four mock sources plus the GUI, all on this machine.

    python scripts/run_local.py                  # mocks on :8001-8004, GUI on :8501
    python scripts/run_local.py --with-puc       # adds the UC6 pollution authority on :8005
    python scripts/run_local.py --no-gui         # sources only (drive them with mediator.executor)
    python scripts/run_local.py --only REG,CAM   # a partial federation, for the failure demo

Same wrappers, registry, executor and GUI as the four-laptop deployment; only the database URLs
(SQLite files instead of PostgreSQL/MySQL over the LAN) differ. Ctrl+C stops everything.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sources._mock.run_all import PORTS, SOURCE_IDS, running  # noqa: E402

GUI_PORT = 8501
MOCK = ROOT / "sources" / "_mock"
# app/app.py is the team's shell and does not exist yet; until it does, the trace tab runs standalone.
GUI_CANDIDATES = (ROOT / "app" / "app.py", ROOT / "app" / "tabs" / "plan_trace.py")


def gui_entry() -> Path | None:
    return next((p for p in GUI_CANDIDATES if p.exists()), None)


def start_gui(entry: Path, registry: Path | None) -> subprocess.Popen:
    env = {**os.environ,
           "PYTHONPATH": os.pathsep.join(p for p in (str(ROOT), os.environ.get("PYTHONPATH")) if p)}
    if registry is not None:
        env["IIA_REGISTRY"] = str(registry)
    return subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(entry),
         "--server.port", str(GUI_PORT), "--server.headless", "true"], cwd=ROOT, env=env)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", default=",".join(SOURCE_IDS), help="comma-separated source ids")
    parser.add_argument("--with-puc", action="store_true", help="also serve PUC on :8005 (UC6)")
    parser.add_argument("--no-gui", action="store_true", help="skip Streamlit")
    args = parser.parse_args()

    ids = [s.strip().upper() for s in args.only.split(",") if s.strip()] + (["PUC"] if args.with_puc else [])
    if unknown := [s for s in ids if s not in PORTS]:
        parser.error(f"unknown source id(s): {unknown}")
    registry = MOCK / ("mock_mappings_uc6.json" if args.with_puc else "mock_mappings.json")

    gui: subprocess.Popen | None = None
    with running(dict.fromkeys(ids)) as procs:
        for source_id in procs:
            print(f"  {source_id:<6} http://127.0.0.1:{PORTS[source_id]}   pid {procs[source_id].pid}")
        print(f"  registry {registry.relative_to(ROOT)}")
        if not args.no_gui and (entry := gui_entry()):
            gui = start_gui(entry, registry if args.with_puc else None)
            print(f"  GUI    http://127.0.0.1:{GUI_PORT}   ({entry.relative_to(ROOT)}, pid {gui.pid})")
        print(f"\n  python -m mediator.executor DL05CD9876   # IIA_REGISTRY={registry.name}")
        print("  kill a source pid to watch it go DOWN; Ctrl+C stops everything")
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("stopping")
        finally:
            if gui is not None:
                gui.terminate()


if __name__ == "__main__":
    main()
