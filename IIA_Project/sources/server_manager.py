"""
Server Manager to run all source FastAPI wrappers concurrently.
Supports in-process background threading for single-machine demo/tests
as well as standalone execution per node.
"""

import socket
import uvicorn
import threading
import time
# PHASE2-REPLACED (Set added for the cluster's held set):
# from typing import Dict, List, Optional
from typing import Dict, List, Optional, Set
from urllib.parse import urlparse
from sources.reg.wrapper import app as reg_app
from sources.ins.wrapper import app as ins_app
from sources.theft.wrapper import app as theft_app
from sources.cam.wrapper import app as cam_app
from sources.puc.wrapper import app as puc_app

SOURCE_CONFIGS = {
    "REG": {"app": reg_app, "port": 8001},
    "INS": {"app": ins_app, "port": 8002},
    "THEFT": {"app": theft_app, "port": 8003},
    "CAM": {"app": cam_app, "port": 8004},
    "PUC": {"app": puc_app, "port": 8005},
}

def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        try:
            s.connect((host, port))
            return True
        except (socket.timeout, ConnectionRefusedError, OSError):
            return False

class WrapperServerThread:
    def __init__(self, app, host: str, port: int):
        self.host = host
        self.port = port
        config = uvicorn.Config(app=app, host=host, port=port, log_level="error", loop="asyncio")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.server.should_exit = True

class SourceCluster:
    def __init__(self, host: str = "127.0.0.1"):
        self.host = host
        self.servers: Dict[str, WrapperServerThread] = {}
        # Sources stopped on purpose (a scenario taking INS down): the healer must not restart them.
        self.held: Set[str] = set()

    def start_all(self, include_puc: bool = False):
        sources = ["REG", "INS", "THEFT", "CAM"]
        if include_puc:
            sources.append("PUC")

        for s_id in sources:
            if s_id not in self.servers:
                cfg = SOURCE_CONFIGS[s_id]
                port = cfg["port"]
                if is_port_in_use(port, self.host):
                    continue
                try:
                    srv = WrapperServerThread(app=cfg["app"], host=self.host, port=port)
                    srv.start()
                    self.servers[s_id] = srv
                except Exception as e:
                    print(f"Warning: Failed to bind {s_id} on port {port}: {e}")
        time.sleep(0.5)

    def start_source(self, s_id: str):
        if s_id in SOURCE_CONFIGS and s_id not in self.servers:
            cfg = SOURCE_CONFIGS[s_id]
            port = cfg["port"]
            if is_port_in_use(port, self.host):
                return
            try:
                srv = WrapperServerThread(app=cfg["app"], host=self.host, port=port)
                srv.start()
                self.servers[s_id] = srv
                time.sleep(0.3)
            except Exception as e:
                print(f"Warning: Failed to bind {s_id} on port {port}: {e}")

    def stop_source(self, s_id: str):
        if s_id in self.servers:
            self.servers[s_id].stop()
            del self.servers[s_id]
            time.sleep(0.3)

    def stop_all(self):
        for srv in self.servers.values():
            srv.stop()
        self.servers.clear()

    def prune_dead(self) -> List[str]:
        """Forget server threads that have exited, so a later start can bind the port again."""
        dead = [s for s, srv in self.servers.items() if not srv.thread.is_alive()]
        for s in dead:
            del self.servers[s]
        return dead

    def suspend(self, s_id: str) -> bool:
        """Stop a source on purpose and keep ensure_local_sources from restarting it until resume().
        Returns whether this cluster owned it (a wrapper another process runs is never stopped)."""
        owned = s_id in self.servers
        if owned:
            self.held.add(s_id)
            self.stop_source(s_id)
        return owned

    def resume(self, s_id: str) -> None:
        self.held.discard(s_id)

_global_cluster: Optional[SourceCluster] = None

def get_cluster() -> SourceCluster:
    global _global_cluster
    if _global_cluster is None:
        _global_cluster = SourceCluster()
    return _global_cluster


# ------------------------------------------------------------------ self-healing (phase 2, workstream G)
# The Streamlit app starts the cluster once per server (@st.cache_resource), and start_all() skips any
# port already taken at that instant. If a test run held 8001-8005 when the site started, the site came
# up with no wrappers and showed every source DOWN after the test run exited. ensure_local_sources() is
# called on every rerun instead, so a freed port is reclaimed on the next one.

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_heal_lock = threading.Lock()  # Streamlit reruns sessions on several threads; one healer at a time


def local_target(base_url: str) -> Optional[tuple]:
    """(host, port) when base_url points at this machine, else None (another laptop: never touched)."""
    parsed = urlparse(str(base_url or ""))
    host = (parsed.hostname or "").lower()
    if host not in LOCAL_HOSTS:
        return None
    return ("127.0.0.1" if host == "localhost" else host), parsed.port or 80


def _ensure_one(cluster: SourceCluster, s_id: str, base_url: str, wait_s: float = 3.0) -> str:
    target = local_target(base_url)
    if target is None:
        return "remote"
    host, port = target
    if s_id in cluster.held:
        return "stopped: held down on purpose (a test scenario is running)"
    # Trade-off: an open port counts as running without a /health call, so a rerun costs one 0.2 s
    # TCP probe per source; the sidebar's own 5 s-cached /health badge still shows real health.
    if is_port_in_use(port, host):
        return "running"
    cfg = SOURCE_CONFIGS.get(s_id)
    if cfg is None:
        return f"failed: no in-process wrapper for {s_id}"
    cluster.prune_dead()
    old = cluster.servers.pop(s_id, None)
    if old is not None:  # thread alive but its port is silent (stopping, or bound elsewhere): replace it
        old.stop()
    try:
        srv = WrapperServerThread(app=cfg["app"], host=host, port=port)
        srv.start()
    except Exception as exc:  # a failed start is a status, never a crash
        return f"failed: {exc}"
    cluster.servers[s_id] = srv
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if is_port_in_use(port, host):
            return "started"
        if not srv.thread.is_alive():  # uvicorn ends its thread when the bind fails
            cluster.servers.pop(s_id, None)
            return f"failed: port {port} could not be bound"
        time.sleep(0.05)
    return f"failed: no answer on port {port} after {wait_s:.0f} s"


def ensure_local_sources(catalog: Dict[str, Dict], include_puc: bool = True,
                         cluster: Optional[SourceCluster] = None) -> Dict[str, str]:
    """(Re)start every catalog source that should run on this machine and is not answering.

    Returns source_id -> running | started | remote | stopped: <why> | failed: <reason>. A source
    whose base_url names another host is `remote` and is never started here (source autonomy).
    """
    cluster = cluster or get_cluster()
    out: Dict[str, str] = {}
    with _heal_lock:
        for s_id, meta in catalog.items():
            if s_id == "PUC" and not include_puc:
                continue
            out[s_id] = _ensure_one(cluster, s_id, (meta or {}).get("base_url", ""))
    return out



def heal_local_sources(catalog_fn, cluster: Optional[SourceCluster] = None) -> Dict[str, str]:
    """ensure_local_sources for the GUI: reads the catalog itself and never raises (a broken catalog
    read becomes a `failed:` status, so the healer cannot take the page down with it)."""
    try:
        return ensure_local_sources(catalog_fn(), include_puc=True, cluster=cluster)
    except Exception as exc:
        return {"cluster": f"failed: {exc}"}


if __name__ == "__main__":
    cluster = get_cluster()
    cluster.start_all(include_puc=True)
    print("All 5 source wrappers (REG, INS, THEFT, CAM, PUC) started on ports 8001-8005.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        cluster.stop_all()
        print("Stopped all wrappers.")

