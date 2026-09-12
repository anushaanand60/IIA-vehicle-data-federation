"""
Server Manager to run all source FastAPI wrappers concurrently.
Supports in-process background threading for single-machine demo/tests
as well as standalone execution per node.
"""

import socket
import uvicorn
import threading
import time
from typing import Dict, List, Optional
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

_global_cluster: Optional[SourceCluster] = None

def get_cluster() -> SourceCluster:
    global _global_cluster
    if _global_cluster is None:
        _global_cluster = SourceCluster()
    return _global_cluster

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
