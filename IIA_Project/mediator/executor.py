"""Executor: plate in, raw rows out (CLAUDE.md §5.3, §7 step 7; design PDF §8.1 steps 1-4).

Selects sources, builds each source's SQL from its registry entry, calls the wrappers in parallel,
and classifies every outcome. It knows no source by name; everything comes from the registry.

    python -m mediator.executor DL05CD9876 [--attrs insurer_name,insurance_expiry] [--json]
"""
from __future__ import annotations

import argparse
import itertools
import json
import ssl
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import datetime, timezone
from typing import Any, NamedTuple

import httpx
from pydantic import ValidationError

from mediator.contract import (GLOBAL_ATTRIBUTES, AttributeMapping, FederationResponse, JoinSpec, PlanTrace,
                               QueryResponse, Registry, RenderSpec, SourceResult, SourceSpec, Status, canonical_plate)
from mediator.registry_loader import default_registry_path, load_registry

KEY_ATTR = "plate_number"
CONNECT_TIMEOUT_S = 0.5  # a live wrapper accepts TCP in milliseconds; waiting longer only delays a DOWN verdict
GRACE_S = 0.25           # backstop on top of the slowest source's own timeout
SqlBuilder = Callable[[SourceSpec, str, "list[str] | None"], str]


class Endpoint(NamedTuple):
    """All the transport layer needs about a source. Keeps dispatch usable from both public APIs."""
    source_id: str
    base_url: str
    timeout_ms: int


def select_sources(registry: Registry, requested: list[str] | None) -> tuple[list[SourceSpec], dict[str, str]]:
    if requested is None:
        return list(registry.sources), {}
    # Every source holds the plate, so it cannot drive selection; asking for it alone means "who knows it?"
    wanted = set(registry.resolve(requested)) - {KEY_ATTR}
    if not wanted:
        return list(registry.sources), {}
    selected, skipped = [], {}
    for spec in registry.sources:
        if wanted & set(spec.covers) or spec.identity_authority:  # the identity authority confirms the plate exists
            selected.append(spec)
        else:
            skipped[spec.source_id] = f"covers none of the requested attributes ({', '.join(sorted(wanted))})"
    return selected, skipped


def render_plate(plate: str, render: RenderSpec) -> str:
    parts, pos = [], 0
    for size in render.groups:
        if pos >= len(plate):
            break
        parts.append(plate[pos:pos + size])
        pos += size
    if pos < len(plate):
        parts.append(plate[pos:])  # longer than the groups: keep the tail rather than drop characters
    text = render.separator.join(parts)
    return text.lower() if render.case == "lower" else text.upper()


def plate_variants(plate: str, variant_map: dict[str, list[str]], max_variants: int) -> list[str]:
    """Spellings the source's own error model could produce, fewest substitutions first, capped."""
    spots = [(i, variant_map[ch]) for i, ch in enumerate(plate) if variant_map.get(ch)]
    out = [plate]
    for k in range(1, len(spots) + 1):
        for combo in itertools.combinations(spots, k):
            for picks in itertools.product(*(options for _, options in combo)):
                chars = list(plate)
                for (i, _), ch in zip(combo, picks):
                    chars[i] = ch
                out.append("".join(chars))
                if len(out) >= max_variants:
                    return list(dict.fromkeys(out))
    return list(dict.fromkeys(out))


def build_sql(spec: SourceSpec, plate: str, requested: list[str] | None) -> str:
    """`requested` holds stored attributes (derived ones already resolved); None means the full profile."""
    kp, base = spec.key_predicate, spec.table
    wanted = [m for m in spec.attribute_map if requested is None or m.global_attr in requested]
    order = _order_mapping(spec)
    joins = _needed_joins(spec, {m.source_table.lower() for m in [*wanted, *([order] if order else [])]})
    if joins and "{joins}" not in spec.query_template:
        raise ValueError(f"query_template has no {{joins}} placeholder, but columns from "
                         f"{', '.join(j.table for j in joins)} were requested")

    def col(table: str, name: str) -> str:
        return f"{table}.{name}" if joins else name  # qualify only once a second table is in play

    rendered = render_plate(plate, kp.render)
    key = col(base, kp.column)
    if kp.match == "in_variants":
        predicate = f"{key} IN ({', '.join(map(_literal, plate_variants(rendered, kp.variant_map, kp.max_variants)))})"
    else:
        predicate = f"{key} = {_literal(rendered)}"
    holes = {
        "columns": _select_list([(base, kp.column), *((m.source_table, m.source_attr) for m in wanted)], col),
        "table": base,
        "joins": "".join(f" LEFT JOIN {j.table} ON {j.left} = {j.right}" for j in joins),
        "predicate": predicate,
        "order_by": f" ORDER BY {col(order.source_table, order.source_attr)} DESC" if order else "",
        "limit": "",
        "plate": rendered.replace("'", "''"),
    }
    try:
        return spec.query_template.format_map(holes)
    except (KeyError, IndexError) as exc:
        raise ValueError(f"query_template has an unknown placeholder: {exc}") from None


def classify_http(status_code: int) -> Status:
    """A 503 means the wrapper is alive but its database is not, so the source is DOWN, not ERROR."""
    return {200: "OK", 503: "DOWN", 504: "TIMEOUT"}.get(status_code, "ERROR")


def execute(registry: Registry, plate_raw: str, requested_attrs: list[str] | None = None,
            *, sql_builder: SqlBuilder = build_sql) -> FederationResponse:
    """Raises only for caller errors (unusable plate, unknown attribute). Source failures become statuses."""
    started = time.perf_counter()
    plate = canonical_plate(plate_raw)
    selected, skipped = select_sources(registry, requested_attrs)
    stored = None if requested_attrs is None else registry.resolve(requested_attrs)
    results: dict[str, SourceResult] = {}
    calls: dict[str, tuple[Endpoint, str]] = {}
    for spec in selected:
        try:
            endpoint = Endpoint(spec.source_id, spec.base_url, spec.timeout_ms)
            calls[spec.source_id] = (endpoint, sql_builder(spec, plate, stored))
        except Exception as exc:  # a registry entry we cannot turn into SQL is our bug, reported per source
            results[spec.source_id] = SourceResult(source_id=spec.source_id, status="ERROR", elapsed_ms=0,
                                                   error=f"could not build SQL: {exc}")
    if calls:
        results.update(_call_all(calls))
    trace = PlanTrace(
        plate_raw=plate_raw, plate_normalized=plate,
        requested_attrs=registry.vocabulary if requested_attrs is None else list(requested_attrs),
        sources_selected=[s.source_id for s in selected], sources_skipped=skipped,
        total_elapsed_ms=int((time.perf_counter() - started) * 1000))
    return FederationResponse(results=[results[s.source_id] for s in selected], trace=trace)


def _order_mapping(spec: SourceSpec) -> AttributeMapping | None:
    agg = spec.aggregate  # ORDER BY only when the registry says the column sorts correctly as stored
    if agg.strategy != "latest" or not agg.pushdown:
        return None
    return next(m for m in spec.attribute_map if m.global_attr == agg.global_attr)


def _needed_joins(spec: SourceSpec, tables: set[str]) -> list[JoinSpec]:
    """Joins whose table supplies a needed column, plus the joins those depend on, in declared order."""
    base, needed, chosen = spec.table.lower(), tables - {spec.table.lower()}, []
    while grown := [j for j in spec.joins if j.table.lower() in needed and j not in chosen]:
        chosen += grown
        needed |= {side.split(".")[0].lower() for j in grown for side in (j.left, j.right)} - {base}
    return [j for j in spec.joins if j in chosen]


def _select_list(pairs: list[tuple[str, str]], col: Callable[[str, str], str]) -> str:
    seen_pairs: set[tuple[str, str]] = set()
    seen_names: set[str] = set()
    out = []
    for table, name in pairs:
        if (table.lower(), name.lower()) in seen_pairs:
            continue
        seen_pairs.add((table.lower(), name.lower()))
        expr = col(table, name)
        if name.lower() in seen_names:  # same column name from a second table: alias it so its value survives
            expr = f"{expr} AS {table}__{name}"
        seen_names.add(name.lower())
        out.append(expr)
    return ", ".join(out)


# Built once: httpx otherwise reloads the CA bundle for every Client, ~250 ms per query on Windows.
# A shared verifying context keeps https base_urls safe; verify=False would be faster and wrong.
_TLS = ssl.create_default_context()


def _call_all(calls: dict[str, tuple[Endpoint, str]]) -> dict[str, SourceResult]:
    client = httpx.Client(trust_env=False, verify=_TLS)  # never route LAN wrappers through a system proxy
    pool = ThreadPoolExecutor(max_workers=len(calls))
    futures = {pool.submit(_call, client, ep, sql): source_id for source_id, (ep, sql) in calls.items()}
    deadline = max(ep.timeout_ms for ep, _ in calls.values()) / 1000 + GRACE_S
    done, _ = wait(futures, timeout=deadline)
    results = {}
    for future, source_id in futures.items():
        ep, sql = calls[source_id]
        results[source_id] = future.result() if future in done else SourceResult(
            source_id=source_id, status="TIMEOUT", sql_sent=sql, elapsed_ms=int(deadline * 1000),
            error=f"no response within {ep.timeout_ms} ms (hard deadline)")
    pool.shutdown(wait=False, cancel_futures=True)  # a hung call must not hold the caller hostage
    if len(done) == len(futures):
        client.close()
    return results


def _call(client: httpx.Client, ep: Endpoint, sql: str) -> SourceResult:
    started, budget = time.perf_counter(), ep.timeout_ms / 1000
    connect = min(budget, CONNECT_TIMEOUT_S)

    def result(status: Status, error: str | None = None, body: QueryResponse | None = None) -> SourceResult:
        rows = body.rows if body else []
        return SourceResult(source_id=ep.source_id, status=status, rows=rows, row_count=len(rows), sql_sent=sql,
                            fetched_at=body.fetched_at if body else None, error=error,
                            elapsed_ms=int((time.perf_counter() - started) * 1000))

    try:
        response = client.post(f"{ep.base_url}/query", json={"sql": sql}, timeout=httpx.Timeout(budget, connect=connect))
    except httpx.ConnectTimeout:
        return result("DOWN", f"no TCP connection to {ep.base_url} within {connect * 1000:.0f} ms")
    except httpx.TimeoutException:
        return result("TIMEOUT", f"no response within {ep.timeout_ms} ms")
    except httpx.TransportError as exc:  # refused, unreachable, reset mid-response
        return result("DOWN", f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # anything else is still a status, never an exception for the caller
        return result("ERROR", f"{type(exc).__name__}: {exc}")
    if response.status_code != 200:
        return result(classify_http(response.status_code), f"HTTP {response.status_code}: {_error_text(response)}")
    try:
        return result("OK", body=QueryResponse.model_validate_json(response.content))
    except ValidationError as exc:
        return result("ERROR", f"unparseable JSON or contract violation: {exc.errors()[0]['msg']}")


def _literal(value: str) -> str:
    # The plate is [A-Z0-9] by now; doubling quotes covers any separator the registry supplies.
    return "'" + value.replace("'", "''") + "'"


def _error_text(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error") or response.text[:200])
    except (ValueError, AttributeError):
        return response.text[:200]


def _print_human(resp: FederationResponse) -> None:
    t = resp.trace
    everything = set(t.requested_attrs) >= set(GLOBAL_ATTRIBUTES)
    print(f"plate    : {t.plate_normalized}   (entered as {t.plate_raw!r})   total {t.total_elapsed_ms} ms")
    print(f"requested: {'the full profile' if everything else ', '.join(t.requested_attrs)}")
    print(f"selected : {', '.join(t.sources_selected) or '-'}")
    for source_id, reason in t.sources_skipped.items():
        print(f"skipped  : {source_id}: {reason}")
    for r in resp.results:
        print(f"\n[{r.status:^7}] {r.source_id}   {r.row_count} row(s)   {r.elapsed_ms} ms")
        if r.sql_sent:
            print(f"  sql   {r.sql_sent}")
        if r.error:
            print(f"  error {r.error}")
        for row in r.rows:
            print(f"  row   {json.dumps(row)}")


# --- Dict-shaped API used by mediator.core and app/app.py ---------------------
# Their pipeline predates SourceResult. Rather than duplicate the transport layer, these two adapt
# it: source metadata still comes from SOURCE_CATALOG and SQL still comes from the decomposer, so
# no source-specific table, column or SQL fragment enters this module (CLAUDE.md §8).

LEGACY_TIMEOUT_MS = 1500
HEALTH_TIMEOUT_S = 1.0


def execute_federated_plan(sources: list[str], canonical_plate: str) -> dict[str, Any]:
    """Execute a plan built by mediator.planner. Unknown source ids are skipped, never raised."""
    # Imported here, not at module scope: execute() must stay usable without the catalog/decomposer.
    from mediator.catalog import get_source_catalog
    from mediator.decomposer import decompose_query

    catalog = get_source_catalog()
    calls: dict[str, tuple[Endpoint, str]] = {}
    sqls: dict[str, str] = {}
    for source_id in sources:
        meta = catalog.get(source_id)
        if meta is None:
            continue
        sqls[source_id] = sql = decompose_query(source_id, canonical_plate)
        calls[source_id] = (Endpoint(source_id, str(meta.get("base_url") or "").rstrip("/"),
                                     int(meta.get("timeout_ms") or LEGACY_TIMEOUT_MS)), sql)
    started = time.perf_counter()
    results = _call_all(calls) if calls else {}
    return {
        "sources_executed": {source_id: _as_dict(r) for source_id, r in results.items()},
        "total_elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "sqls": sqls,
    }


def check_all_sources_health() -> dict[str, dict[str, Any]]:
    """Probe /health per catalogued source. GUI badges only: never an input to a decision (CLAUDE.md §8)."""
    from mediator.catalog import get_source_catalog

    catalog = get_source_catalog()
    if not catalog:
        return {}

    def probe(source_id: str, base_url: str) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            with httpx.Client(trust_env=False, verify=_TLS, timeout=HEALTH_TIMEOUT_S) as client:
                response = client.get(f"{str(base_url).rstrip('/')}/health")
            body = response.json() if response.status_code == 200 else {"source_id": source_id, "up": False}
        except Exception as exc:  # a health probe that raises would take the whole GUI down with it
            body = {"source_id": source_id, "up": False, "error": f"{type(exc).__name__}: {exc}"}
        if not isinstance(body, dict):
            body = {"source_id": source_id, "up": False, "error": "health response was not a JSON object"}
        body["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
        return body

    with ThreadPoolExecutor(max_workers=len(catalog)) as pool:
        futures = {pool.submit(probe, sid, meta.get("base_url", "")): sid for sid, meta in catalog.items()}
        return {sid: future.result() for future, sid in futures.items()}


def _as_dict(r: SourceResult) -> dict[str, Any]:
    """SourceResult -> the key names mediator.integrator and app/app.py already read."""
    return {
        "source_id": r.source_id,
        "status": r.status,
        "rows": r.rows,
        "row_count": r.row_count,
        "sql": r.sql_sent or "",
        # Integrator stamps provenance from this, so never leave it absent.
        "fetched_at": (r.fetched_at or datetime.now(timezone.utc)).isoformat(),
        "elapsed_ms": r.elapsed_ms,
        "error": r.error,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Federated plate lookup: raw rows per source plus the plan trace.")
    parser.add_argument("plate")
    parser.add_argument("--attrs", help="comma-separated global attributes, derived ones included (default: all)")
    parser.add_argument("--registry", help="registry file (default: IIA_REGISTRY, mediator/meta.db, mappings.json, then the mock)")
    parser.add_argument("--json", action="store_true", help="print the FederationResponse as JSON")
    args = parser.parse_args(argv)
    path = args.registry or default_registry_path()
    attrs = [a.strip() for a in args.attrs.split(",") if a.strip()] if args.attrs else None
    response = execute(load_registry(path), args.plate, attrs)
    if args.json:
        print(response.model_dump_json(indent=2))
    else:
        print(f"registry : {path}")  # so a demo never silently runs on the wrong registry
        _print_human(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
