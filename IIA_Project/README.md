# Federated Mediator: Uninsured Vehicle Identification System

A federated database mediator implementing Global-As-View (GAV) schema integration over four (soon
five) autonomous, heterogeneous government and observational source databases (Registration,
Insurance, Police Crime, Road Camera, and an extensible Pollution/PUC authority) to detect
uninsured and non-compliant vehicles — with live writes on every source, an unknown-plate
onboarding workflow, plate-photo OCR, and a watchlist/risk layer, all still read-only from the
mediator's own query path.

---

## Key Features

1. **Autonomous Source Wrappers**: FastAPI micro-adapters on ports 8001–8005 hiding DBMS dialects,
   schemas, and connection details. Each wrapper exposes `GET /health`, `GET /schema`,
   `POST /query` (read-only, guarded, single `SELECT` only).
2. **Live writes on every source, through the wrapper's own admin endpoints** — never through the
   mediator. `POST /admin/mutate` runs a fixed, named menu of actions (`register`, `renew`,
   `expire`, `add_policy`, `steal`, `clear`, `shred`, `sight`, `issue`, `revoke`, …, one set per
   source, listed live at `GET /admin/actions`); `POST /admin/sql` is a guarded free-SQL console
   (single INSERT/UPDATE/DELETE, whitelisted tables, no DDL). Both are **on by default** on every
   wrapper — set `<SOURCE_ID>_ADMIN=off` on a laptop to turn that one wrapper back into a read-only
   server. `/query`'s connection is never the one admin writes use, so the mediator's read-only
   guarantee is untouched by any of this.
3. **SQL console (GUI tab)**: run read queries or (when admin is on) writes against any source's
   real database, in that source's own SQL dialect, with query history, before/after row counts,
   and a one-click "re-run in Investigate" once a plate literal is recognised in the statement.
4. **Source Editor (sidebar)**: a form built live from each wrapper's own `GET /admin/actions` —
   pick a source, an action, a plate, fill in the action's own parameters, Apply. No action list is
   hard-coded in the GUI; a laptop with admin off shows an honest message instead of a dead form.
5. **Unknown-plate onboarding wizard**: type any plate that exists in none of the sources and the
   Investigate tab offers a 4-step wizard — Register (REG), Insure (INS, optional), issue a PUC
   certificate (optional), Re-evaluate — each step posting to that agency's own `/admin/mutate` and
   showing the SQL/detail it executed. Backed by a new decision, `UNKNOWN VEHICLE — NOT REGISTERED`.
6. **Hybrid Schema Matcher**: discovers correspondences dynamically
   ($0.40 \cdot N + 0.20 \cdot C + 0.40 \cdot I$, θ = 0.55) against each source's real, live
   `/schema` response, with a similarity heatmap. Verified precision/recall: **1.00/1.00** on
   REG, INS, THEFT, CAM and PUC; **1.00/1.00 overall (23 of 23 correspondences, 0 false positives)**
   (`python scripts/evaluate_matcher.py`).
7. **Metadata-Driven GAV Registry**: `MAPPING_REGISTRY` + `SOURCE_CATALOG` in `meta.db`. Adding a
   new source (PUC, live, UC6) requires zero engine code changes — only metadata registration.
8. **Query Planner & Parallel Decomposer, driven entirely by metadata**: `mediator/planner.py`
   names no source id anywhere in its code (a test greps for one); it selects sources from
   `SOURCE_CATALOG.covers` ∩ the requested attributes (plus what a *derived* attribute like
   `insurance_status` is derived from), and asks every `identity_authority` source when the request
   `needs_identity_check`. `mediator/decomposer.py` emits `LEFT JOIN`s (a missing lookup row never
   hides the vehicle), an explicit column list (never `SELECT *`), OCR-confusion folding only for
   `OBSERVATIONAL` sources, and portable `ORDER BY … LIMIT 1` pushdown that is skipped for any
   `DD/MM/YYYY`-encoded column (the integrator picks the latest row in Python there instead).
9. **Plate photo → OCR → lookup**: upload or paste a plate photo; EasyOCR reads it, a
   position-aware confusion table (O↔0, I↔1, B↔8, S↔5, Z↔2, G↔6, applied against the Indian plate
   pattern) proposes "did you mean" candidates, and a click runs the query. No easyocr installed →
   a clean install hint, never a crash.
10. **Watchlist, alerts and an explainable risk score**: `mediator/watchlist.py` (add/remove/check
    a plate) logs an alert with camera evidence whenever a watched plate is queried, and separately
    raises a "hotlist hit" for any STOLEN/SCRAPPED/SHREDDED verdict even if nobody marked it.
    `mediator/risk.py` scores every profile 0–100 from the decision plus five additive-only
    modifiers (conflicts, near-expiry policy, stale/no sighting, a down core source, low mean
    source trust) — no ML, no probability, every point in the score is a named `RiskFactor` and
    they sum to the total. Mirrors the UK MIB's Operation Tutelage marker and India's IIB/ANPR
    uninsured-vehicle flags (`docs/FIELD_RESEARCH.md`).
11. **Grace-period / escalation ladder for lapsed insurance**: 1–15 days lapsed →
    `UNINSURED — ADVISORY` (MEDIUM); 16–30 days → `UNINSURED — WARNING` (HIGH); more than 30 days,
    or no policy at all → `UNINSURED — REPORT` (HIGH). Mirrors the UK's Continuous Insurance
    Enforcement (advisory → penalty → impound).
12. **Citizen self-check (data-minimised)**: a separate tab that answers only "is my own vehicle
    registered / insured / PUC-valid / reported stolen" — never the owner's name, never a camera
    location or a raw row — and requests only the five attributes it needs, so the planner
    (feature 8) genuinely contacts fewer sources (CAM is never asked) and the tab says so on
    screen.
13. **Conflict Resolution & Decision Engine**: 8+ ordered, deterministic rules (unreachable source
    ⇒ `UNDETERMINED`; scrapped/shredded; stolen; unregistered-but-seen; cloned plate; the insurance
    ladder; registration invalid; unknown vehicle; clear), each carrying plain-English reasons that
    name only sources actually asked and answered — a source never asked or never answered makes no
    claim, never a fabricated "not reported".
14. **Ministry evidence-bundle PDF**: every filed report carries, beyond the decision, the
    per-attribute provenance table (source, authority, trust, fetched-at), the exact SQL sent to
    each source, camera evidence when CAM answered, the rule that fired, the mediator's own git
    commit hash, and any watchlist alerts — everything a Ministry official would need to defend the
    decision, not just the verdict.
15. **One-command laptop bring-up**: `python scripts/serve.py <SRC> --db-url "…"` writes that
    laptop's settings to a gitignored `sources/<id>/laptop.env`, starts the wrapper, and prints the
    exact `configure_cluster.py --set` line for laptop 1. Later runs are just
    `python scripts/serve.py <SRC>`.
16. **Streamlit Interactive GUI, 8 tabs**: Investigate, Plan Trace, Matcher & Heatmap, Catalog &
    Registry, Ministry Reports, SQL Console, Watchlist & Alerts, Citizen Self-Check — plus a
    Source Editor sidebar. `app/app.py` is ~120 lines; every tab body lives in its own
    `app/tabs/*.py` module built from shared `app/components.py` render helpers and a single
    `app/theme.py` design-token stylesheet (AirSentinel-derived palette, explicit colour + icon on
    every status/decision, never colour alone).
17. **Ground truth at scale**: `python scripts/evaluate_ground_truth.py --start-wrappers` grades
    the mediator's live decisions against an independently computed oracle for every generated
    plate — **621/621 = 100.0%**, across all 10 decision/confidence classes including the two new
    insurance-ladder tiers.
18. **Query audit log**: every query the mediator answers appends one row to `QUERY_LOG`
    (plate, requested attributes, sources asked, statuses, decision, confidence, elapsed time) —
    decisions and traces only, never a copy of source rows, so it is auditable state, not a cache
    that would contradict the freshness thesis. Browsable from the Ministry Reports tab, filterable
    by plate.

---

## Quickstart Guide

### 1. Install and load every source, then run the whole system on one laptop

```powershell
pip install -r requirements.txt
python scripts/load_source.py REG
python scripts/load_source.py INS
python scripts/load_source.py THEFT
python scripts/load_source.py CAM
python scripts/load_source.py PUC
python scripts/seed_mappings.py
python run_system.py
```

`load_source.py <SRC>` builds that source's local SQLite copy from its own `schema.sql` and the
committed CSVs (`--url` points it at a real PostgreSQL/MySQL instance instead; `--verify` counts
the demo plates afterwards). `seed_mappings.py` writes the team's validated GAV mappings into
`meta.db` so the mediator can answer queries without first clicking through the Matcher tab.
`run_system.py` then boots all five wrappers (ports 8001–8005) and the Streamlit GUI at
**http://localhost:8501**.

### 2. Bring up one source on its own laptop (no env-var juggling)

```powershell
python scripts/serve.py INS --db-url "mysql+pymysql://iia:pw@127.0.0.1:3306/insdb"
python scripts/serve.py INS                       # later runs: re-loads sources/ins/laptop.env
python scripts/serve.py INS --check               # verify the demo plates, don't serve
python scripts/serve.py INS --readonly-admin      # disable /admin/* on this laptop only
```

Settings (`--db-url`, `--port`, `--readonly-admin`) persist to that source's gitignored
`sources/<id>/laptop.env` and reload on the next run. The command prints this laptop's own IP(s)
and the exact `python scripts/configure_cluster.py --set <ID>=<ip>` line to run on laptop 1.

### 3. Run the automated test suite

```powershell
python -m pytest -q
```

546 tests (3 deselected `@pytest.mark.live` contract tests, run separately below) cover the
transport layer, schema matching, planning, decomposition, integration, the decision ladder, the
admin write menu, OCR, watchlist/risk, the evidence bundle, and every GUI tab
(`streamlit.testing.v1.AppTest`, zero-exception full-app renders).

```powershell
python scripts/evaluate_ground_truth.py --start-wrappers   # 621/621 decisions correct
python scripts/evaluate_matcher.py                          # precision/recall vs the gold mapping
pytest -q -m live                                            # contract test against the real 4-5 laptops
```

---

## Pre-Loaded Demo Scenarios

| License Plate | Scenario Description | Expected Outcome |
| :--- | :--- | :--- |
| `DL01AB1234` | Valid registration, active comprehensive insurance, verified by camera | **`CLEAR`** (High Confidence) |
| `DL05CD9876` | Valid registration, expired insurance policy | **`UNINSURED — REPORT`** (High Confidence) |
| `DL09KL3321` | Registered, no policy row at all | **`UNINSURED — REPORT`** |
| `HR26EF4455` | Stolen vehicle with an open police case FIR | **`STOLEN — ALERT POLICE`** (High Confidence) |
| `UP16GH1122` | REG specifies Hyundai Creta (white), CAM sighted Hyundai Venue (silver) | **`SUSPICIOUS — POSSIBLE CLONED PLATE`** (Medium Confidence) |
| `MH12IJ7788` | Sighted by traffic camera, but has no REG record | **`UNREGISTERED / SUSPICIOUS`** (Medium Confidence) |
| `DL03SC5566` | Registered, then officially shredded, then seen again on camera | **`SCRAPPED — ALERT POLICE`** (High Confidence) |
| any plate in no source | Nothing on record anywhere asked | **`UNKNOWN VEHICLE — NOT REGISTERED`** — Investigate offers the onboarding wizard |

---

## Repository Structure

```
IIA_Project/
├── data/
│   ├── ground_truth.csv          # Independently computed oracle for every generated plate (621 rows)
│   ├── gold_mapping.json         # Hand-made gold correspondences for matcher precision/recall
│   └── make_ground_truth.py      # Regenerates ground_truth.csv from the source CSVs
├── sources/
│   ├── wrapper_template.py       # FastAPI adapter factory: /health, /schema, /query, /admin/*
│   ├── server_manager.py         # Multi-threaded server coordinator for local demo/tests
│   ├── reg/                      # Regional Transport Office (PostgreSQL / SQLite :8001)
│   ├── ins/                      # Insurance Authority (MySQL / SQLite :8002)
│   ├── theft/                    # Police Crime Records (SQLite :8003)
│   ├── cam/                      # Road Traffic Camera Network (PostgreSQL / SQLite :8004)
│   └── puc/                      # Pollution Certificate Agency (Extensibility UC6 :8005)
├── mediator/
│   ├── schema.py                 # VEHICLE_PROFILE global schema, derived_from / needs_identity_check metadata
│   ├── transforms.py             # Pure transformation functions (norm_plate, parse_dates, etc.)
│   ├── matcher.py                # Hybrid schema matching algorithm (N, C, I scoring)
│   ├── catalog.py                # Source catalog, GAV mapping registry, watchlist/alert/report tables in meta.db
│   ├── planner.py                # Metadata-driven query source selection (no source id in the code)
│   ├── decomposer.py             # LEFT JOIN SQL generation, explicit columns, OCR fold, portable pushdown
│   ├── executor.py               # Parallel federated execution with per-source timeouts
│   ├── integrator.py             # Outer join, provenance tracking, metadata-driven latest-wins, conflicts
│   ├── decide.py                 # Ordered, deterministic decision engine incl. the insurance ladder
│   ├── risk.py                   # Explainable 0-100 risk score from the decision + additive factors
│   ├── watchlist.py               # Watchlisted plates + alert log
│   ├── plate_ocr.py              # EasyOCR reading + position-aware plate-confusion repair
│   ├── ground_truth.py           # Oracle decision function mirroring decide.py, for grading
│   ├── report.py                 # REPORT_LOG persistence and the evidence-bundle PDF generator
│   └── core.py                   # Central mediator coordinator (run_global_query)
├── app/
│   ├── app.py                    # ~120-line shell: theme, masthead, sidebar, 8 tab shells
│   ├── theme.py                  # AirSentinel-derived design tokens and the one injected stylesheet
│   ├── components.py             # Shared render helpers (banner, chips, profile cards, risk gauge, …)
│   └── tabs/                     # investigate, plan_trace, matcher_tab, catalog_tab, reports,
│                                  # sql_console, watchlist, self_check, source_editor, onboarding, ocr_upload
├── scripts/
│   ├── serve.py                  # One-command single-laptop bring-up
│   ├── load_source.py            # Build/load one source's database from its schema.sql + CSVs
│   ├── seed_mappings.py          # Seed MAPPING_REGISTRY with the team's validated mappings
│   ├── configure_cluster.py      # Point the mediator laptop at the other laptops' IPs; --probe
│   ├── mutate_source.py          # CLI equivalent of every /admin/mutate action, any DBMS
│   ├── validate_registry.py      # Coverage-matrix sanity check of the registry
│   ├── evaluate_ground_truth.py  # Grade live decisions against data/ground_truth.csv
│   └── evaluate_matcher.py       # Precision/recall of the schema matcher vs data/gold_mapping.json
├── tests/                        # 546 active tests (transport, matcher, planner, decomposer,
│                                  # integrator, decide, risk, watchlist, OCR, report, all 8 GUI tabs)
├── docs/
│   ├── report.md                 # Full project technical report
│   ├── heterogeneity_table.md    # Heterogeneity checklist
│   ├── demo_script.md            # 10-minute rehearsal script
│   ├── LAPTOP_SETUP.md           # Machine-by-machine bring-up runbook
│   ├── NETWORK.md                # Topology, status semantics, latency budget
│   └── FIELD_RESEARCH.md         # Real-world systems the Phase 2 innovations mirror
├── STUDY_GUIDE.md                # Whole-project explainer for the viva
├── run_system.py                 # One-command system runner (all 5 wrappers + GUI)
└── README.md                     # This file
```
