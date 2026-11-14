"""CONTEXT-ADDED (R2): one small HTTP client every phase-3 module uses to reach the source wrappers.

Base URLs come from the live source catalog (the same one phase 1-2 use), falling back to localhost ports,
so the three-laptop setup needs no extra configuration. Every call is counted (rows, bytes, round trips),
which is how the "data moved" numbers on the website are measured rather than estimated.
Failures never raise: they come back as SourceStats.status (phase-1 rule: a dead source degrades, never crashes).

Tests inject a fake: anything with the same three methods (query / post / stats) works.
"""
from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional

import httpx

from mediator.context_types import SourceStats

DEFAULT_PORTS = {"REG": 8001, "INS": 8002, "THEFT": 8003, "CAM": 8004, "PUC": 8005, "DOCS": 8006}
TIMEOUT_S = 6.0      # context queries are heavier than phase-1 lookups; still bounded so a hang cannot stall the page


def base_urls() -> Dict[str, str]:
    urls = {sid: f"http://127.0.0.1:{port}" for sid, port in DEFAULT_PORTS.items()}
    try:
        from mediator.catalog import get_source_catalog
        for sid, meta in get_source_catalog().items():
            if meta.get("base_url"):
                urls[sid] = str(meta["base_url"]).rstrip("/")
    except Exception:  # catalog missing on a fresh clone: localhost defaults are right there
        pass
    return urls


# CONTEXT-ADDED (R2: one shared, thread-safe connection pool for every SourceClient in this process)
_CLIENT: Optional[httpx.Client] = None


def _http() -> httpx.Client:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.Client(trust_env=False, limits=httpx.Limits(max_connections=64, max_keepalive_connections=32))
    return _CLIENT
# CONTEXT-END


class SourceClient:
    """Counts what crosses the network per source. One instance per user query."""

    def __init__(self, urls: Optional[Dict[str, str]] = None, timeout_s: float = TIMEOUT_S) -> None:
        self.urls = urls or base_urls()
        self.timeout_s = timeout_s
        self._stats: Dict[str, SourceStats] = {}

    def stats(self) -> List[SourceStats]:
        return list(self._stats.values())

    def stat(self, source_id: str) -> SourceStats:
        return self._stats.setdefault(source_id, SourceStats(source_id=source_id))

    def post(self, source_id: str, path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """POST JSON to a wrapper endpoint. Returns the body, or None (status recorded) on any failure."""
        st = self.stat(source_id)
        url = self.urls.get(source_id)
        if not url:
            st.status, st.error = "DOWN", "no base URL"
            return None
        t0 = time.perf_counter()
        try:
            # CONTEXT-REPLACED (R2: httpx.post() builds a new client -- and on Windows a new SSL context -- per call,
            # ~250 ms each; one pooled client per process costs ~6 ms. It pools connections only, never responses):
            # resp = httpx.post(f"{url}{path}", json=payload, timeout=self.timeout_s)
            resp = _http().post(f"{url}{path}", json=payload, timeout=self.timeout_s)
            # CONTEXT-END
        except httpx.TimeoutException:
            st.status, st.error = "TIMEOUT", f"no answer in {self.timeout_s:.0f} s"
            return None
        except httpx.HTTPError as exc:
            st.status, st.error = "DOWN", type(exc).__name__
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
        except json.JSONDecodeError:
            st.status, st.error = "ERROR", "unparseable JSON"
            return None
        st.rows_shipped += len(body.get("rows", []) or [])
        st.sql_statements += 1
        return body

    def query(self, source_id: str, sql: str) -> List[Dict[str, Any]]:
        """Plain read-only SQL through the phase-1 /query guard. Empty list on failure (status recorded)."""
        body = self.post(source_id, "/query", {"sql": sql})
        return list(body.get("rows", [])) if body else []
