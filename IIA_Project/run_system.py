"""
One-command startup script for the entire Federated Mediator System.
Boots the 4 source FastAPI wrappers (plus 5th PUC source) and starts the Streamlit GUI.
"""

import sys
import os
import time
import subprocess

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT_DIR)

# Piped stdout on Windows is cp1252, which cannot encode an emoji: printing one killed start-up
# with UnicodeEncodeError before a single wrapper had booted. The banner is plain ASCII now, and
# stdout is told to replace anything it still cannot map rather than raise.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):  # already-wrapped or non-reconfigurable stream
        pass

from sources.server_manager import get_cluster

def main():
    print("==================================================================")
    print("Starting Federated Mediator System: Uninsured Vehicle Detection")
    print("==================================================================")

    # 1. Start source cluster
    cluster = get_cluster()
    print("Starting source database wrappers (REG :8001, INS :8002, THEFT :8003, CAM :8004, PUC :8005)...")
    cluster.start_all(include_puc=True)
    print("All source wrappers online.")

    # 2. Launch Streamlit app
    app_path = os.path.join(ROOT_DIR, "app", "app.py")
    cmd = [sys.executable, "-m", "streamlit", "run", app_path, "--server.port=8501", "--server.headless=true"]
    print(f"Launching Streamlit GUI at http://localhost:8501 ...")

    try:
        proc = subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\nShutting down system...")
    finally:
        cluster.stop_all()
        print("Shutdown complete.")

if __name__ == "__main__":
    main()
