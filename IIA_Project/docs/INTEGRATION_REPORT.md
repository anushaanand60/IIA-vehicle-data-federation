# Integration Audit — transport layer

**Status: design known, code artefacts still pending.** Updated 2026-09-12 after the team design
`Project_A_Design_Uninsured_Vehicle_Integration_1 (1).pdf` ("the PDF") was added to the repo root.
Section references like "PDF §3.1" point into it.

---

## 1. What the audit found

**First pass (2026-09-12, morning).** The repository held only `CLAUDE.md`. No schemas, data generator,
matcher or registry existed, so the transport layer was built against provisional schemas I invented
inside `sources/_mock/`.

**Second pass (same day).** The PDF specifies what the first pass had to guess: the four source schemas
(§3), the data slices (§4), the mediated schema (§5), the matcher (§6), the registry and catalog in
`meta.db` (§7), the pipeline (§8), deployment (§9), GUI (§10) and team split (§12). The mocks, the mock
registry, the wrappers' default whitelists, the loader and the executor now follow it.

Still missing as code, and owned by teammates: `sources/*/schema.sql`, `data/generate.py` and
`ground_truth.csv`, `mediator/matcher.py`, `mediator/meta.db` (or `mappings.json`), and everything
downstream of the executor.

---

## 2. Source reality per PDF §3 — what the mocks implement

| | REG · PostgreSQL · :8001 | INS · MySQL · :8002 | THEFT · SQLite · :8003 | CAM · PostgreSQL · :8004 |
|---|---|---|---|---|
| Tables | `VEHICLE_REGISTRATION`, `OWNERS` | `POLICY_RECORDS`, `INSURERS` | `CRIME_RECORDS` | `PLATE_CAPTURES`, `CAMERAS` |
| Plate column | `registration_no` | `vehicle_reg` | `vehicle_number` | `plate_id` |
| Plate format | `DL01AB1234` | `DL-01-AB-1234` | `dl 01 ab 1234` | OCR, ~5% misread O↔0 / I↔1: `DLOIAB1234`, `MH121J7788` |
| Dates | `registered_on DATE` (ISO) | `policy_start`, `policy_until` DD/MM/YYYY **text** | `reported_date` epoch seconds | `captured_at TIMESTAMPTZ` → `2026-08-30 21:14:05+05:30` on the wire |
| Flags / status | `reg_status` ACTIVE / SUSPENDED / CANCELLED | `is_active` 1/0; `policy_type` THIRD_PARTY / COMPREHENSIVE | `stolen_flag`, `recovered_flag` Y/N; `case_status` OPEN/CLOSED; `incident_type` THEFT / SHREDDING / HIT_AND_RUN | — |
| Rows per plate | one | many (renewals) | many (incidents) | many (sightings) |
| Join needed for | `owner_name` → `VEHICLE_REGISTRATION.owner_id = OWNERS.owner_id` | `insurer_name` → `POLICY_RECORDS.insurer_id = INSURERS.insurer_id` | — | `last_seen_location` → `PLATE_CAPTURES.camera_id = CAMERAS.camera_id` |
| Columns only this agency has (left unmapped) | `fuel_type`, `rto_code` | `premium_inr` | `fir_no`, `police_station` | `ocr_confidence`, `lat`, `lon` |
| Default database | `regdb` | `insdb` | `data/theft.db` | `camdb` |

**PUC** (UC6, PDF §8.3, :8005): `POLLUTION_CERT(cert_no, regn_number, valid_upto)`. The PDF names only
the columns; the formats (`DL 01 AB 1234`, ISO dates) are mine.

**Mock data** (`sources/_mock/seed.py`, deterministic): 39 registered vehicles plus one camera-only
plate; the six story vehicles; stolen-then-recovered, shredded and hit-and-run incidents; the dirty rows
of PDF §4 (a NULL make, `Hyundia`, a duplicated policy). This is for transport tests. The PDF's
~600-vehicle generator and `ground_truth.csv` remain the teammates' deliverable.

---

## 3. Divergences, and the adapter for each

Every adapter lives in a transport-owned file. No teammate file was edited.

### 3.1 Where the registry lives
CLAUDE.md §5.2 says JSON `mediator/mappings.json`; PDF §7.1 says SQLite `meta.db`. **Adapter:**
`load_registry` reads both, and `default_registry_path` tries `IIA_REGISTRY`, then `mediator/meta.db`,
then `mappings.json`, then the mock. `meta.db` is opened read-only, and a missing file is never created.

### 3.2 The catalog's own vocabulary
| PDF §7.1 | Loader turns it into |
|---|---|
| `authority` `OFFICIAL` / `OBSERVATIONAL` | `authoritative` / `observational` |
| `trust_score` | `trust` |
| `identifier_attr` | `key_predicate.column` |
| `covers[]` stored as JSON text or comma text | a list |
| `transform_fn`, `match_score` | `transform`, `score` |
| `join_path` on a mapping row | a declared join (§3.3) |
| `aggregate` `latest_by:<column>` | `{strategy: latest, global_attr: <mapped attribute>}` (§3.4) |
| `last_health`, `last_seen_at`, anything else | kept in `extra` |

### 3.3 Lookup tables need joins
The PDF normalises owners, insurers and camera locations into their own tables. **Adapter:**
`SourceSpec.joins` (`{table, left, right}`), filled from `join_path`. The executor adds
`LEFT JOIN … ON …` **only when a requested column needs that table**, qualifies every column once
there is a join, and aliases a column-name clash as `TABLE__column` so no value is lost. LEFT, so a
missing lookup row never hides a base row. The contract rejects a mapping from a table that is neither
the base table nor joined.

### 3.4 `latest_by` must not become a SQL sort on text dates
PDF §7.1 aggregates INS with `latest_by:policy_until`, but `policy_until` is DD/MM/YYYY text: sorted in
SQL it picks `19/07/2025` over `12/07/2026` (the mock reproduces this). PDF §8.1 applies aggregates
after transforms, downstream. **Adapter:** `Aggregate.pushdown`, default false. ORDER BY is pushed to
the source only where the registry opts in because the column sorts natively (THEFT `reported_date`,
CAM `captured_at`). Rows are never truncated either way.

### 3.5 Derived attributes
PDF §5 has `insurance_status`, derived from `insurance_expiry` and never stored; CLAUDE.md's list of 19
does not. **Adapter:** `DERIVED_ATTRIBUTES` in the contract. A request for it selects and projects its
input, so UC1 fetches `vehicle_reg, policy_until` and nothing else. A registry can declare more.

### 3.6 UC1 contacts two sources
PDF §1.1: UC1 touches "INS (+ REG to confirm vehicle exists)". **Adapter:** `identity_authority: true`
on REG. Any narrow request also asks REG, projecting only `registration_no`.

### 3.7 UC6 adds attributes nobody had
The 19 names were hard-coded, so PUC's `puc_expiry` was rejected: "no engine code changed" was false.
**Adapter:** the vocabulary lives on the `Registry`. JSON declares extras under `global_schema`;
`meta.db` may carry a `GLOBAL_SCHEMA(global_attr, derived_from)` table (B6). The agreed 19 can never be
dropped. Rehearse with `run_all.py --with-puc` and `sources/_mock/mock_mappings_uc6.json`.

### 3.8 Two ways to push the plate predicate down
PDF §8.1 normalises the column: `REPLACE(REPLACE(UPPER(vehicle_reg),'-',''),' ','') = 'DL01AB1234'`.
The executor renders the plate into the source's spelling instead: `vehicle_reg = 'DL-01-AB-1234'`,
which can use an index. **Adapter:** the guard now allows `REPLACE` (as a statement it can never start
with SELECT), so a decomposer written either way works.

### 3.9 OCR error model
PDF §3.4: O↔0 and I↔1, both directions. CAM's `variant_map` is `{"0":["O"],"O":["0"],"1":["I"],"I":["1"]}`;
`DL01AB1234` expands to 8 spellings. CLAUDE.md §9's example `DLO1AB1Z34` (Z for 2) is outside the
PDF's model, so the mocks follow the PDF.

### 3.10 JSON shape divergences (still absorbed)
Percentage trust and scores, `url`/`name` aliases, bare-string `key_predicate`, missing `timeout_ms` /
`covers` / `aggregate` / `table`, three container shapes, unmatched columns with `global_attr: null`.

---

## 4. Reconciliation checklist — when the code artefacts land

1. Diff each real `sources/*/schema.sql` against §2 and update `sources/_mock/schema_*.sql` to match,
   column order included.
2. Check each wrapper's `<ID>_TABLES` includes its lookup tables (`OWNERS`, `INSURERS`, `CAMERAS`).
   On MySQL under Linux or macOS, table names are case-sensitive: match the `CREATE TABLE` spelling.
3. `python scripts/validate_registry.py mediator/meta.db` — every attribute covered, joins listed.
4. Set `identity_authority` on REG and decide pushdown per aggregate (B7).
5. `python tests/fixtures/make_source_results.py`, then `pytest -q`.
6. `pytest -q -m live` on the four laptops.

---

## 5. Open questions for the team

- **B1 — code artefacts missing.** The PDF fixes the design; `schema.sql`, `generate.py`, the matcher and
  `meta.db` still do not exist.
- **B2 — resolved.** The OCR error model is O↔0, I↔1 (PDF §3.4).
- **B3 — one registry, not two.** `meta.db` (PDF) or `mappings.json` (CLAUDE.md)? The loader reads both;
  pick one so they cannot drift.
- **B4 — the plate is not a key.** `UP16GH1122` may be one plate on two cars. Joining on plate alone
  silently merges them. Downstream concern; flagged, not blocking.
- **B5 — SQL generation assigned twice.** PDF §8.1 and §12 give `decomposer.py` to P3; the executor
  has `build_sql` and accepts `execute(..., sql_builder=...)`, which receives *stored* attributes
  (derived ones already expanded). Confirm the decomposer's signature matches.
- **B6 — no home for new or derived attributes in `meta.db`.** PDF §7.1 has no global-schema table.
  Proposal: `GLOBAL_SCHEMA(global_attr TEXT, derived_from TEXT NULL)`, which the loader already reads.
- **B7 — no catalog columns for `identity_authority` or ORDER BY pushdown.** The loader reads an
  `identity_authority` column if SOURCE_CATALOG has one. Without it, UC1 asks only INS. Aggregates
  loaded from `meta.db` are never pushed down (the safe default).
- **B8 — the two briefs disagree.** CLAUDE.md has three people with the transport layer as one owner;
  the PDF has four (P3 owns matcher through integrator). The PDF header says "Tue 16 Sept" and
  "Thu 18 Sept", but 16 Sep 2026 is a Wednesday and 18 Sep a Friday; its own day plan says Wed 16.

---

## 6. Environment findings

- The enclosing git repository is rooted at the user's home directory, not this project.
- `pymysql` is not installed locally; only the real INS laptop needs it.
- No `.venv`; dependencies are installed globally (CLAUDE.md §10 assumes one).
