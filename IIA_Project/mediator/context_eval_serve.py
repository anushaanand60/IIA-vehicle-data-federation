"""CONTEXT-ADDED (R2): serve one generator scale (x1 / x10 / x100) to the evaluation without opening a port.

Each source's SQLite file is wrapped by the SAME factory the live agencies use
(`sources.wrapper_template.create_app`), and called in-process through FastAPI's TestClient. The client
below has the three methods of `mediator.source_client.SourceClient` (query / post / stats) and counts
rows, bytes and round trips the same way, so discover() cannot tell the difference.

Trade-off, stated: in-process calls skip the network, so latency here understates what a LAN adds, and
the extra cost of shipping more rows (B0) is under-counted rather than over-counted. Rows and bytes are
exact either way. In exchange the evaluation needs no free ports (ports 8001-8006 belong to the demo
cluster) and runs headless on one laptop at every scale.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from mediator.context_types import SourceStats

ROOT = Path(__file__).resolve().parents[1]
GRAPH_PATH = ROOT / "data" / "context_graph.json"
SCALE_DIR = ROOT / "data" / "scale"
EVAL_TIMEOUT_S = 30.0     # generous: a slow B0 at x100 is a finding, not a crash
EVAL_ROW_LIMIT = 1_000_000  # the baseline must not be silently capped by the demo's LIMIT 200


def load_graph() -> Dict[str, Any]:
    return json.loads(GRAPH_PATH.read_text(encoding="utf-8")) if GRAPH_PATH.exists() else {"sources": {}}


def _source_of(stem: str, known: List[str]) -> Optional[str]:
    """'reg' / 'REG' / 'reg_x10' -> 'REG'."""
    head = stem.split("_")[0].split(".")[0].upper()
    return head if head in known else None


def scale_files(graph: Dict[str, Any]) -> Dict[str, Dict[str, Path]]:
    """{'x1': {sid: db}, 'x10': {...}, ...}. x1 is the demo data in sources/<sid>/<sid>.db."""
    known = list(graph.get("sources", {})) + ["DOCS"]
    scales: Dict[str, Dict[str, Path]] = {}
    base = {sid: ROOT / "sources" / sid.lower() / f"{sid.lower()}.db" for sid in known}
    scales["x1"] = {sid: p for sid, p in base.items() if p.exists()}
    if SCALE_DIR.exists():
        for folder in sorted(SCALE_DIR.iterdir(), key=lambda p: (len(p.name), p.name)):
            if folder.is_dir() and folder.name.startswith("x") and folder.name != "x1":
                files = {sid: p for p in folder.glob("*.db") if (sid := _source_of(p.stem, known))}
                if files:
                    # A source the scaler did not multiply (e.g. DOCS) is served at x1 so the set stays whole.
                    scales[folder.name] = {**scales["x1"], **files}
    return scales


def build_apps(files: Dict[str, Path], graph: Dict[str, Any]) -> Dict[str, Any]:
    """One wrapper app per source, whitelisting exactly the tables the context graph declares."""
    from sources.wrapper_template import create_app  # noqa: PLC0415 - heavy import, only when serving

    apps: Dict[str, Any] = {}
    for sid, path in files.items():
        tables = list(graph.get("sources", {}).get(sid, {}).get("tables", {})) or ["DOCUMENTS"]
        apps[sid] = create_app(f"sqlite:///{path.as_posix()}", source_id=sid, dbms="SQLite", tables=tables,
                               statement_timeout_s=EVAL_TIMEOUT_S, row_limit=EVAL_ROW_LIMIT,
                               admin_enabled=False)
        if sid == "DOCS":
            try:  # the documents source adds its own ranked /search route to the same factory app
                from sources.docs.wrapper import add_search_route  # noqa: PLC0415
                add_search_route(apps[sid])
            except ImportError:
                pass
    return apps


class InProcessClient:
    """SourceClient look-alike over in-process wrapper apps. One instance per measured query."""

    def __init__(self, apps: Dict[str, Any]) -> None:
        from fastapi.testclient import TestClient  # noqa: PLC0415

        self._clients = {sid: TestClient(app) for sid, app in apps.items()}
        self._stats: Dict[str, SourceStats] = {}
        self._lock = threading.Lock()   # TestClient is not built for concurrent callers; serialise them

    def stats(self) -> List[SourceStats]:
        return list(self._stats.values())

    def stat(self, source_id: str) -> SourceStats:
        return self._stats.setdefault(source_id, SourceStats(source_id=source_id))

    def post(self, source_id: str, path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        st = self.stat(source_id)
        client = self._clients.get(source_id)
        if client is None:
            st.status, st.error = "DOWN", "not served at this scale"
            return None
        t0 = time.perf_counter()
        try:
            with self._lock:
                resp = client.post(path, json=payload)
        except Exception as exc:  # a crashing route is an ERROR status, exactly as over HTTP
            st.status, st.error = "ERROR", type(exc).__name__
            return None
        finally:
            st.latency_ms += (time.perf_counter() - t0) * 1000
            st.round_trips += 1
        st.bytes_shipped += len(resp.content)
        if resp.status_code != 200:
            st.status, st.error = "ERROR", resp.text[:300]
            return None
        try:
            body = resp.json()
        except ValueError:
            st.status, st.error = "ERROR", "unparseable JSON"
            return None
        st.rows_shipped += len(body.get("rows", []) or [])
        st.sql_statements += 1
        return body

    def query(self, source_id: str, sql: str) -> List[Dict[str, Any]]:
        body = self.post(source_id, "/query", {"sql": sql})
        return list(body.get("rows", [])) if body else []
