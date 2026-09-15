"""Contract test against the four real laptops (CLAUDE.md §7 step 10). Opt in with: pytest -q -m live

This is the only test that proves end-to-end integration. It uses the teammates' real registry,
never the mock. Configure with:
    IIA_REGISTRY     path to the real registry (default: mediator/mappings.json)
    IIA_LIVE_PLATE   a plate present in all four real sources (default: HR26EF4455)
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from mediator.contract import Registry
from mediator.executor import execute
from mediator.registry_loader import MOCK_REGISTRY, REAL_REGISTRY, load_registry

pytestmark = pytest.mark.live
PLATE = os.environ.get("IIA_LIVE_PLATE", "HR26EF4455")


@pytest.fixture(scope="module")
def real_registry() -> Registry:
    path = Path(os.environ.get("IIA_REGISTRY", REAL_REGISTRY))
    if not path.exists():
        pytest.skip(f"real registry {path} not found yet (docs/INTEGRATION_REPORT.md B1)")
    if path.resolve() == MOCK_REGISTRY.resolve():
        pytest.fail("the live contract test must run against the real registry, not the mock")
    return load_registry(path)


def _get(url: str, timeout_ms: int) -> httpx.Response | str:
    try:
        return httpx.get(url, timeout=timeout_ms / 1000, trust_env=False)
    except httpx.HTTPError as exc:
        return f"{type(exc).__name__}: {exc}"


def test_every_real_wrapper_is_healthy(real_registry):
    problems = {}
    for spec in real_registry.sources:
        r = _get(f"{spec.base_url}/health", spec.timeout_ms)
        if isinstance(r, str) or r.status_code != 200:
            problems[spec.source_id] = r if isinstance(r, str) else f"HTTP {r.status_code}: {r.text[:200]}"
    assert not problems, problems


def test_registry_names_columns_that_really_exist(real_registry):
    problems = {}
    for spec in real_registry.sources:
        r = _get(f"{spec.base_url}/schema", spec.timeout_ms)
        if isinstance(r, str) or r.status_code != 200:
            problems[spec.source_id] = r if isinstance(r, str) else f"HTTP {r.status_code}"
            continue
        tables = {t["table"]: {c["name"] for c in t["columns"]} for t in r.json()["tables"]}
        needed = {spec.key_predicate.column} | {m.source_attr for m in spec.attribute_map if m.source_table == spec.table}
        if spec.table not in tables:
            problems[spec.source_id] = f"table {spec.table!r} not published; wrapper exposes {sorted(tables)}"
        elif missing := needed - tables[spec.table]:
            problems[spec.source_id] = f"columns missing from {spec.table}: {sorted(missing)}"
    assert not problems, problems


def test_each_source_returns_ok_with_rows_for_a_known_plate(real_registry):
    resp = execute(real_registry, PLATE)
    assert resp.trace.sources_selected == [s.source_id for s in real_registry.sources]
    problems = {r.source_id: f"{r.status}, {r.row_count} rows, error={r.error!r}, sql={r.sql_sent!r}"
                for r in resp.results if r.status != "OK" or r.row_count == 0}
    assert not problems, problems
