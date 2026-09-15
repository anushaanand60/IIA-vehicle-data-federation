# Transport-layer contracts

The seams between the transport layer and everyone else. The code is the authority —
`mediator/contract.py` — and this page explains it. Any change to either is announced to the
team first and logged in §7. "PDF §x" refers to the team design
`Project_A_Design_Uninsured_Vehicle_Integration_1 (1).pdf`.

```
 matcher ──writes──▶ meta.db or mappings.json ──registry_loader──▶ Registry[SourceSpec]
                                                                     │
 GUI ──plate, attrs──▶ executor.execute(registry, plate, attrs) ──HTTP──▶ wrapper ×4 ──SQL──▶ DB ×4
                          │
                          └──▶ FederationResponse ──▶ transforms / integrator / decide / report
                                                  └──▶ plan_trace tab
```

---

## 1. Wrapper HTTP API

Identical on every source. Built by `sources/wrapper_template.py`; only the database URL and the
table whitelist differ.

### `GET /health`

```json
200 {"source_id": "INS", "dbms": "mysql", "up": true, "ts": "2026-09-12T10:41:07.114+00:00"}
503 {"source_id": "INS", "up": false, "error": "No module named 'pymysql'"}
```

### `GET /schema`

```json
200 {"source_id": "REG", "tables": [
      {"table": "VEHICLE_REGISTRATION", "columns": [
        {"name": "owner_id", "type": "INTEGER", "nullable": false, "pk": false, "fk": "OWNERS.owner_id",
         "samples": [1, 2, 3, "..."]}]}]}
503 {"error": "database unreachable: ..."}
```

Only whitelisted tables appear. `samples` holds up to 20 distinct non-null values in the source's own
format — the matcher's instance signal (PDF §6). `fk` is `"table.column"` or null.

### `POST /query`

```json
request  {"sql": "SELECT vehicle_number, stolen_flag FROM CRIME_RECORDS WHERE vehicle_number = 'hr 26 ef 4455'"}
200      {"rows": [{"vehicle_number": "hr 26 ef 4455", "stolen_flag": "Y"}], "row_count": 1,
          "fetched_at": "2026-09-12T10:41:07.230+00:00", "elapsed_ms": 4}
```

| Code | Meaning | Executor status |
|---|---|---|
| 200 | answered — **zero rows is still 200** | `OK` |
| 400 | the guard rejected the SQL, the body was malformed, or the database rejected the SQL. All **our bug**. | `ERROR` |
| 503 | wrapper up, its database unreachable | `DOWN` |
| 504 | the database cancelled the statement after 3 s | `TIMEOUT` |

The 504 code, and 400 for SQL the database rejects, extend CLAUDE.md §5.1. After a database error the
wrapper probes with `SELECT 1`: if that works the database is fine and the query was wrong.

### The guard

1. Non-empty, at most 10 000 characters, no NUL byte.
2. No `;` — one statement only. No comments (`--`, `/*`, `#`).
3. Starts with `SELECT` and contains exactly one `SELECT`: no subqueries, `UNION`, `WITH`.
4. No DML/DDL or dangerous keyword as a whole word: `INSERT UPDATE DELETE DROP CREATE ALTER TRUNCATE
   INTO SET UNION TABLE PRAGMA ATTACH COPY LOAD …`, and file or sleep functions (`pg_sleep`,
   `load_file`, `pg_read_file`, `dblink`, `query_to_xml`, …). **`REPLACE` and `UPPER` are allowed** —
   PDF §8.1 uses them as string functions, and the MySQL `REPLACE` statement cannot start with SELECT.
5. At least one table, and every table after `FROM` / `JOIN` is whitelisted. A `FROM` item is a plain
   `table [AS alias]`.
6. `LIMIT 200` is appended unless the query ends in `LIMIT n`; the wrapper stops at 200 rows regardless.

The guard catches mediator bugs; it is not a parser. The **security boundary** is below it: every
connection is read-only (`PRAGMA query_only`, `default_transaction_read_only`, `SESSION TRANSACTION
READ ONLY`), plus a SELECT-only database account per laptop (`NETWORK.md`).

### Wire encoding

| Database value | On the wire |
|---|---|
| text, integer, float, boolean, NULL | unchanged |
| DATE / TIMESTAMP / TIMESTAMPTZ / TIME | `str()` of the driver value: `"2019-03-14"`, `"2026-08-30 21:14:05+05:30"` |
| DECIMAL / NUMERIC | float |
| BLOB / BYTEA | lowercase hex |

No value is reformatted beyond this. `policy_until` stays `"12/07/2026"`; `reported_date` stays an integer.

---

## 2. Registry

### 2.1 Where it comes from

`load_registry()` reads `IIA_REGISTRY` if set, otherwise the first that exists of `mediator/meta.db`
(PDF §7.1, opened read-only), `mediator/mappings.json` (CLAUDE.md §5.2), and
`sources/_mock/mock_mappings.json`. The CLI prints which one it used.

`meta.db` rows map onto the same model:

| `SOURCE_CATALOG` / `MAPPING_REGISTRY` | `SourceSpec` |
|---|---|
| `source_id, display_name, dbms, base_url, timeout_ms` | same |
| `identifier_attr` | `key_predicate.column` |
| `authority` `OFFICIAL` / `OBSERVATIONAL` | `authority` `authoritative` / `observational` |
| `trust_score` | `trust` |
| `covers` (JSON or comma text) | `covers` |
| mapping rows | `attribute_map` (`transform_fn` → `transform`, `match_score` → `score`) |
| `join_path` `A.x=B.y` | `joins` |
| `aggregate` `latest_by:col` | `aggregate` `{strategy: latest, global_attr, pushdown: false}` |
| optional `GLOBAL_SCHEMA(global_attr, derived_from)` | `Registry.global_attributes` / `derived` |

### 2.2 `SourceSpec`

| Field | Type | Notes |
|---|---|---|
| `source_id` | `str` | uppercase, unique |
| `display_name`, `dbms` | `str` | labels |
| `base_url` | `str` | `http://host:port`, no trailing slash |
| `authority` | `"authoritative" \| "observational"` | |
| `trust` | `float` 0–1 | |
| `timeout_ms` | `int` > 0 | default 3000 |
| `covers` | `list[str]` | each needs a mapping |
| `table` | identifier | the base table; the plate column lives here |
| `joins` | `list[{table, left, right}]` | `left`/`right` are `TABLE.column` |
| `identity_authority` | `bool` | asked on every narrow query to confirm the plate exists (UC1) |
| `key_predicate` | object | §2.3 |
| `query_template` | `str` | a single `SELECT` with holes, §2.5 |
| `aggregate` | `{strategy: all \| latest, global_attr, pushdown}` | §2.4 |
| `attribute_map` | `list[{source_table, source_attr, global_attr, transform, score}]` | several columns may map to one attribute (e.g. THEFT `stolen_status` ← `incident_type`, `stolen_flag`, `recovered_flag`) |
| `extra` | `dict` | unrecognised registry keys, preserved |

### 2.3 `key_predicate`

```json
{"column": "vehicle_reg", "match": "exact", "render": {"groups": [2, 2, 2, 4], "separator": "-", "case": "upper"}}

{"column": "plate_id", "match": "in_variants",
 "variant_map": {"0": ["O"], "O": ["0"], "1": ["I"], "I": ["1"]}, "max_variants": 64}
```

`render` spells the canonical plate the source's way. `in_variants` expands the rendered plate through
the source's OCR error model, breadth-first by number of substitutions, exact spelling first, capped.

### 2.4 `aggregate` and pushdown

`latest` names the attribute that decides "latest". With `pushdown: true` the executor appends
`ORDER BY <column> DESC`; only set it when the column sorts correctly as stored (epoch integers,
timestamps). DD/MM/YYYY text must stay `false`: the latest is picked downstream after parsing. Rows are
never dropped.

### 2.5 `query_template` holes

| Hole | Expands to |
|---|---|
| `{columns}` | the key column, then each mapped column whose attribute was requested; `TABLE.column` once a join is present; a clashing name becomes `TABLE.column AS TABLE__column` |
| `{table}` | `table` |
| `{joins}` | ` LEFT JOIN T ON a = b` for each join a requested column needs, else empty |
| `{predicate}` | `col = 'rendered'` or `col IN ('v1', …)` |
| `{order_by}` | ` ORDER BY col DESC` for a pushed-down `latest`, else empty |
| `{plate}` | the rendered plate, single quotes doubled |
| `{limit}` | empty (the wrapper appends the limit) |

Default: `SELECT {columns} FROM {table}{joins} WHERE {predicate}{order_by}`. An unknown hole, or a
template without `{joins}` when a joined column is requested, makes that one source `ERROR`.

### 2.6 Vocabulary: stored, derived, extended

- **Stored:** the 19 agreed names (`GLOBAL_ATTRIBUTES`). A registry may add names — JSON
  `"global_schema": {"attributes": ["puc_expiry"]}` or a `GLOBAL_SCHEMA` row — but never remove these.
- **Derived:** computed downstream, never fetched. Default `insurance_status` ← `insurance_expiry`
  (PDF §5). A registry may declare more under `global_schema.derived`.
- Mapping to an undeclared name fails loudly, naming the source and the attribute.

---

## 3. Executor API

```python
from mediator.executor import execute
from mediator.registry_loader import load_registry

response = execute(load_registry(), "dl 01 ab 1234", requested_attrs=["insurance_status"])
```

`execute(registry, plate_raw, requested_attrs=None, *, sql_builder=build_sql) -> FederationResponse`

- **Plate.** `canonical_plate()` keeps uppercase letters and digits. It does not undo OCR errors. A
  plate with no letters or digits raises `ValueError`.
- **Selection** (PDF §8.1 step 2). `None` asks every source. Otherwise requested attributes are
  resolved (derived → inputs), `plate_number` is set aside (every source holds it), and a source is
  asked if it covers what remains — or if it is the `identity_authority`. A request for
  `plate_number` alone asks everyone. An unknown attribute raises `ValueError`. Skipped sources get a
  reason.
- **SQL.** `sql_builder(spec, canonical_plate, stored_attrs)` per source; `stored_attrs` is `None` for
  a full profile, otherwise the resolved stored attributes. The default `build_sql` reads only that
  source's registry entry. A teammate decomposer can be passed in (INTEGRATION_REPORT B5).
- **Calls.** One thread per source, all at once. TCP connect within `min(0.5 s, timeout_ms)`; full reply
  within `timeout_ms`; a backstop of the slowest `timeout_ms` + 0.25 s.
- **Raises** only for the two caller errors above. **No retries, no caching, no writes.**

---

## 4. What the executor hands downstream

```python
class SourceResult(BaseModel):
    source_id: str
    status: Literal["OK", "TIMEOUT", "DOWN", "ERROR"]
    rows: list[dict]          # RAW: source column names (no table prefix), source value formats
    row_count: int
    sql_sent: str | None      # exactly what was POSTed (without the wrapper's LIMIT)
    fetched_at: datetime | None
    elapsed_ms: int
    error: str | None

class PlanTrace(BaseModel):
    plate_raw: str
    plate_normalized: str
    requested_attrs: list[str]           # as requested; the full vocabulary when the caller passed None
    sources_selected: list[str]
    sources_skipped: dict[str, str]      # source_id -> reason
    total_elapsed_ms: int

class FederationResponse(BaseModel):
    results: list[SourceResult]          # one per selected source, registry order
    trace: PlanTrace
```

### Status classification

| What happened | `status` |
|---|---|
| 200 with a valid body, any number of rows | `OK` |
| no TCP connection within 0.5 s; connection refused, unreachable or reset; wrapper replied 503 | `DOWN` |
| connected but no full reply within `timeout_ms`; wrapper replied 504; pending at the backstop | `TIMEOUT` |
| any other non-200; a 200 that is not valid JSON or breaks `QueryResponse`; SQL could not be built | `ERROR` |

A slow *connect* counts as `DOWN`: a live wrapper accepts TCP within milliseconds even when its
database is slow. Windows also retries a refused SYN for ~2.7 s, which would blow the 2 s budget.

### Guarantees

- `row_count == len(rows)`.
- `OK` ⇒ `error is None`; zero rows with `OK` means the source answered and has nothing.
- not `OK` ⇒ `rows == []` and a non-empty `error`. Treat it as **undetermined** for that source's attributes.
- A narrow request's identity-authority row holds only the key column: it proves the plate exists.
- Rows are never renamed, reformatted, deduplicated or truncated beyond the 200-row cap.

Worked examples: `tests/fixtures/source_results.json`.

---

## 5. Health for the GUI

Wrapper `/health` may be cached for up to 5 s for status badges only. Nothing that feeds a decision
is cached.

## 6. Change policy

Changing a field, type, status or rule in `mediator/contract.py` is a breaking change: announce it,
log it in §7, regenerate the fixture (`python tests/fixtures/make_source_results.py`), keep `pytest -q` green.

## 7. Changelog

| Date | Change |
|---|---|
| 2026-09-12 | v1. Initial contract. Extends CLAUDE.md §5.1 with HTTP 504 and 400 for SQL the database rejects. |
| 2026-09-12 | v2. Aligned with the design PDF. Added `joins`, `identity_authority`, `Aggregate.pushdown`, derived attributes, a registry-extensible vocabulary and `meta.db` loading; the guard allows `REPLACE`. **Breaking:** `latest` no longer implies ORDER BY unless `pushdown` is true; unknown attribute names are rejected by `Registry`, not by a lone `SourceSpec`; mock schemas and fixture column names follow PDF §3. |
