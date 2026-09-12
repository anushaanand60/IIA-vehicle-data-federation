"""
Parallel Federated Query Executor.
Dispatches sub-queries in parallel to source wrappers with per-source timeouts.
Translates failures into DOWN / TIMEOUT states without raising exceptions.
"""

import time
import httpx
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Any, List
from mediator.catalog import get_source_catalog
from mediator.decomposer import decompose_query

def execute_single_source(source_id: str, base_url: str, sql: str, timeout_ms: int) -> Dict[str, Any]:
    timeout_sec = max(0.5, timeout_ms / 1000.0)
    endpoint = f"{base_url.rstrip('/')}/query"

    t_start = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout_sec) as client:
            resp = client.post(endpoint, json={"sql": sql})
            elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)

            if resp.status_code == 200:
                data = resp.json()
                return {
                    "source_id": source_id,
                    "status": "OK",
                    "rows": data.get("rows", []),
                    "row_count": data.get("row_count", 0),
                    "sql": sql,
                    "fetched_at": data.get("fetched_at", datetime.now(timezone.utc).isoformat()),
                    "elapsed_ms": elapsed_ms
                }
            else:
                return {
                    "source_id": source_id,
                    "status": "ERROR",
                    "rows": [],
                    "row_count": 0,
                    "sql": sql,
                    "error": f"HTTP {resp.status_code}: {resp.text}",
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "elapsed_ms": elapsed_ms
                }
    except httpx.TimeoutException:
        elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
        return {
            "source_id": source_id,
            "status": "TIMEOUT",
            "rows": [],
            "row_count": 0,
            "sql": sql,
            "error": f"Timeout after {timeout_ms}ms",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": elapsed_ms
        }
    except Exception as e:
        elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
        return {
            "source_id": source_id,
            "status": "DOWN",
            "rows": [],
            "row_count": 0,
            "sql": sql,
            "error": str(e),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": elapsed_ms
        }

def execute_federated_plan(sources: List[str], canonical_plate: str) -> Dict[str, Any]:
    catalog = get_source_catalog()
    tasks = []
    generated_sqls = {}

    for s_id in sources:
        if s_id not in catalog:
            continue
        s_meta = catalog[s_id]
        sql = decompose_query(s_id, canonical_plate)
        generated_sqls[s_id] = sql
        tasks.append((s_id, s_meta.get("base_url", "http://127.0.0.1:8001"), sql, s_meta.get("timeout_ms", 1500)))

    results = {}
    t_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, len(tasks))) as executor:
        future_to_source = {
            executor.submit(execute_single_source, s_id, url, sql, timeout): s_id
            for s_id, url, sql, timeout in tasks
        }
        for future in as_completed(future_to_source):
            s_id = future_to_source[future]
            try:
                results[s_id] = future.result()
            except Exception as e:
                results[s_id] = {
                    "source_id": s_id,
                    "status": "DOWN",
                    "rows": [],
                    "row_count": 0,
                    "sql": generated_sqls.get(s_id, ""),
                    "error": str(e),
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "elapsed_ms": 0.0
                }

    total_elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
    return {
        "sources_executed": results,
        "total_elapsed_ms": total_elapsed_ms,
        "sqls": generated_sqls
    }

def check_all_sources_health() -> Dict[str, Dict[str, Any]]:
    """Quick health probe across all cataloged sources."""
    catalog = get_source_catalog()
    health_results = {}

    def probe(s_id: str, url: str) -> Dict[str, Any]:
        t0 = time.perf_counter()
        try:
            with httpx.Client(timeout=1.0) as client:
                r = client.get(f"{url.rstrip('/')}/health")
                ms = round((time.perf_counter() - t0) * 1000, 2)
                if r.status_code == 200:
                    d = r.json()
                    d["latency_ms"] = ms
                    return d
                return {"source_id": s_id, "up": False, "latency_ms": ms}
        except Exception:
            ms = round((time.perf_counter() - t0) * 1000, 2)
            return {"source_id": s_id, "up": False, "latency_ms": ms}

    with ThreadPoolExecutor(max_workers=len(catalog)) as executor:
        futs = {executor.submit(probe, s_id, meta["base_url"]): s_id for s_id, meta in catalog.items()}
        for fut in as_completed(futs):
            s_id = futs[fut]
            health_results[s_id] = fut.result()

    return health_results
