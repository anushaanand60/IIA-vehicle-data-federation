# Phase 3 build contract (shared by every workstream)

Read with `docs/superpowers/plans/2026-11-11-phase3-context-integration.md`. This file fixes names so workstreams
can build in parallel. Change it only by agreement; `data/context_graph.json` is its machine-readable twin.

## House rules
- Existing tables and columns are NOT altered (phase 1-2 code, mappings and ~1,000 tests depend on them).
  New tables are added; logical FKs onto old tables are declared in `data/context_graph.json`.
- Every edit to an existing file is marked: `# CONTEXT-ADDED (Rn: reason)` … `# CONTEXT-END`, or
  `# CONTEXT-REPLACED (Rn: reason):` followed by the original lines commented, then the new lines.
  A new file says `CONTEXT-ADDED (Rn)` in its module docstring. Rn = rubric point (R1-R4).
- No git commits, stashes, checkouts or resets by workstreams; the integrator commits.
- Do not run tests that start the local cluster (fixture `local_cluster`, ports 8001-8006) while other
  workstreams may be running; run only your own unit tests. The integrator runs the full suite.
- The product (UI text, docs) never mentions lecture slides or course weeks.
- Type hints, small modules, plain explanations a student can defend.

## Sources, ports, engines
| id | port | deployed engine | local file |
|---|---|---|---|
| REG | 8001 | PostgreSQL | sources/reg/reg.db |
| INS | 8002 | MySQL 8 | sources/ins/ins.db |
| THEFT | 8003 | SQLite | sources/theft/theft.db |
| CAM | 8004 | PostgreSQL | sources/cam/cam.db |
| PUC | 8005 | SQLite | sources/puc/puc.db |
| DOCS | 8006 | SQLite | sources/docs/docs.db |

## Existing tables (unchanged)
- REG: `OWNERS(owner_id PK, full_name, address_line, city)`, `VEHICLE_REGISTRATION(registration_id PK, registration_no, owner_id→OWNERS, make, model, colour, fuel_type, registered_on DATE, reg_status, rto_code)`. Plate stored `DL01AB1234`.
- INS: `INSURERS(insurer_id PK, insurer_name)`, `POLICY_RECORDS(policy_id PK, vehicle_reg, insurer_id→INSURERS, policy_type, policy_start, policy_until, is_active, premium_inr)`. Plate stored `DL-01-AB-1234`; dates `DD/MM/YYYY` text.
- THEFT: `CRIME_RECORDS(incident_id PK, vehicle_number, fir_no, reported_date INT epoch, incident_type, stolen_flag Y/N, recovered_flag Y/N, case_status, police_station)`. Plate stored `dl 01 ab 1234`.
- CAM: `CAMERAS(camera_id PK, location_name, lat, lon)`, `PLATE_CAPTURES(capture_id PK, plate_id, camera_id→CAMERAS, captured_at ISO text, observed_make, observed_model, observed_colour, ocr_confidence)`. Plate = OCR read.
- PUC: `POLLUTION_CERT(cert_no PK, regn_number, valid_upto DATE, tested_at, emission_norm)`.

## New tables (R1). Every source ends with 4-5 tables. Formats stay agency-specific on purpose.
REG (5)
- `VEHICLE_IDENTITY(registration_id INTEGER PK REFERENCES VEHICLE_REGISTRATION, chassis_no VARCHAR(17) UNIQUE, engine_no VARCHAR(14), mfg_year INTEGER)`
- `OWNERSHIP_HISTORY(transfer_id INTEGER PK, registration_id → VEHICLE_REGISTRATION, prev_owner_id → OWNERS, new_owner_id → OWNERS, transfer_date DATE, transfer_type VARCHAR(12))`  -- SALE / INHERITANCE / AUCTION
- `OWNER_CONTACTS(owner_id INTEGER PK REFERENCES OWNERS, mobile VARCHAR(10), pincode CHAR(6))`

INS (5)
- `POLICY_HOLDERS(holder_id INTEGER PK, holder_name VARCHAR(80), mobile VARCHAR(10), address VARCHAR(160), pincode CHAR(6))`  -- own identity, NOT REG's owner
- `POLICY_PARTIES(policy_id INTEGER PK REFERENCES POLICY_RECORDS, holder_id → POLICY_HOLDERS, chassis_no VARCHAR(17))`
- `CLAIMS(claim_id INTEGER PK, policy_id → POLICY_RECORDS, loss_date VARCHAR(10) DD/MM/YYYY, intimation_date VARCHAR(10) DD/MM/YYYY, loss_type VARCHAR(12), amount_claimed DECIMAL(10,2), claim_status VARCHAR(12), surveyor_name VARCHAR(60))`  -- loss_type THEFT/OWN_DAMAGE/THIRD_PARTY; status OPEN/SETTLED/REJECTED/INVESTIGATING

THEFT (5)
- `STATIONS(station_name VARCHAR(60) PK, district VARCHAR(40), zone VARCHAR(20))`  -- logical FK: CRIME_RECORDS.police_station → STATIONS.station_name
- `STOLEN_PROPERTY(incident_id INTEGER PK REFERENCES CRIME_RECORDS, chassis_no VARCHAR(17), engine_no VARCHAR(14), recovered_on INTEGER NULL)`  -- epoch, like reported_date
- `PERSONS(person_id INTEGER PK, name VARCHAR(80), mobile VARCHAR(10), address VARCHAR(160))`
- `INCIDENT_PERSONS(incident_id → CRIME_RECORDS, person_id → PERSONS, role VARCHAR(12), PRIMARY KEY(incident_id, person_id, role))`  -- COMPLAINANT/ACCUSED/WITNESS

CAM (5)
- `ZONES(zone_id INTEGER PK, zone_name VARCHAR(40), city VARCHAR(40))`
- `CAMERA_ZONES(camera_id VARCHAR(8) PK REFERENCES CAMERAS, zone_id → ZONES, road VARCHAR(60))`
- `CHALLANS(challan_id INTEGER PK, capture_id → PLATE_CAPTURES, violation VARCHAR(40), fine_inr INTEGER, challan_status VARCHAR(10), issued_at TEXT ISO)`  -- PENDING/PAID/COURT

PUC (4)
- `TEST_CENTRES(centre_id INTEGER PK, centre_name VARCHAR(60), city VARCHAR(40), lat NUMERIC(9,6), lon NUMERIC(9,6))`
- `ANALYSERS(analyser_id INTEGER PK, centre_id → TEST_CENTRES, calibrated_on DATE)`
- `CERT_TESTS(test_id INTEGER PK, cert_no → POLLUTION_CERT, analyser_id → ANALYSERS, test_ts TEXT ISO, co_pct NUMERIC(5,2), hc_ppm INTEGER, result VARCHAR(4))`  -- PASS/FAIL; the vehicle must be physically present for a test

DOCS (new autonomous source, unstructured)
- `DOCUMENTS(doc_id VARCHAR(12) PK, doc_type VARCHAR(20), title VARCHAR(160), body TEXT, issuer VARCHAR(60), issued_on DATE)`  -- FIR_NARRATIVE / SURVEYOR_REPORT / ACCIDENT_REPORT / NEWS / RTO_NOTICE / WITNESS_STATEMENT
- Gold links doc→rows live OUTSIDE the source in `data/phase3_gold.json` (evaluation only).

## Cross-source bridges (how context travels between databases)
- `chassis`: REG.VEHICLE_IDENTITY.chassis_no, INS.POLICY_PARTIES.chassis_no, THEFT.STOLEN_PROPERTY.chassis_no — exact, confidence 1.0.
- `plate`: REG.VEHICLE_REGISTRATION.registration_no, INS.POLICY_RECORDS.vehicle_reg, THEFT.CRIME_RECORDS.vehicle_number, CAM.PLATE_CAPTURES.plate_id, PUC.POLLUTION_CERT.regn_number — canonicalised (uppercase alphanumerics); CAM is noisy, confidence = phase-2 match probability.
- `person`: REG.OWNERS(+OWNER_CONTACTS), INS.POLICY_HOLDERS, THEFT.PERSONS — fuzzy (name, mobile, pincode/address), confidence = Fellegi-Sunter posterior.

## Planted scenarios (generator writes `data/phase3_scenarios.json` with ids, plates, expected indicators)
Keep the phase 1-2 story vehicles (DL01AB1234, DL05CD9876, DL09KL3321, HR26EF4455, UP16GH1122, MH12IJ7788, KA05MN9999) behaving as before. Add:
- ALIBI: a vehicle reported stolen (open FIR, stolen interval) that has a PUC CERT_TEST and a CAM capture + challan inside the interval.
- QUICK_CLAIM: theft claim with loss_date ≤ 15 days after policy_start and intimation before the FIR.
- TRANSFER_BEFORE_LOSS: OWNERSHIP_HISTORY transfer ≤ 30 days before a theft claim/FIR.
- PERSON_RING: one mobile shared by an REG owner, an INS holder (different spelling) and a THEFT accused across ≥ 3 vehicles.
- CLONE: plate seen by two cameras far apart within minutes, observed make ≠ registered.
- HARD NEGATIVES: plates one character apart, same owner name different person (different mobile).

## Shared Python types
`mediator/context_types.py` (written by the integrator) — every workstream imports from it, never redefines.

## Interfaces between workstreams (exact names; implement these, call these)
Shared: `mediator/context_types.py` (dataclasses), `mediator/source_client.py` (`SourceClient` with
`.query(source_id, sql) -> list[dict]`, `.post(source_id, path, payload) -> dict|None`, `.stats()`; counts rows/bytes/round trips;
tests inject a fake object with the same methods), `data/context_graph.json` (FK graph + bridges + scoring columns).

WS-A (schemas, data) produces: new tables in `sources/*/schema.sql`; CSVs at repo root named `<src>_<table lower>.csv`
(e.g. `reg_vehicle_identity.csv`, `docs_documents.csv` written later by WS-D); `scripts/load_source.py` SOURCES entries for all
new tables incl. DOCS; wrapper whitelists; `scripts/phase3_generate.py`; `data/phase3_scenarios.json`;
`scripts/phase3_scale.py --factor 10 --out data/scale/x10/` producing one SQLite file per source (same schemas) for evaluation.

WS-B (context) produces:
- wrapper route `POST /context` body `{"seeds": {"plate": [...], "chassis": [...], "person_ids": {"TABLE": [...]}, "pks": {"TABLE": [...]}},
  "n": 10, "theta": 0.05, "window": ["YYYY-MM-DD","YYYY-MM-DD"] | null, "keywords": [...], "mode": "bounded"|"closure"}`
  → `{"rows": [{"table","pk","row","score","path","hops"}], "bound": float, "row_count", "elapsed_ms", "sql"}`
- `mediator/context_discovery.discover(query: ParsedQuery | str, n=10, theta=0.05, mode="bounded"|"topn"|"closure", client=None, max_rounds=4) -> ContextResult`
  ("closure" = baseline B0: every match + full FK closure; "topn" = baseline B1: top-n per source, one round, no bounds)
- `mediator/person_resolution.resolve_persons(records: list[dict]) -> list[PersonCluster]`; records are
  `{"source_id","table","pk","name","mobile","address","pincode"}`; `fit_em(...)` returns learned m/u.

WS-C (query, top-k, analytics) produces:
- `mediator/llm.py`: `available() -> bool`, `generate_json(prompt, model="qwen2.5:3b", timeout_s=20) -> dict|None`,
  `generate_text(prompt, model=..., timeout_s=...) -> str|None` (Ollama at http://127.0.0.1:11434, disk cache `data/llm_cache.json`).
- `mediator/query_understanding.parse(text: str, use_llm: bool = True) -> ParsedQuery`
- `mediator/entity_topk.topk(query: ParsedQuery, k=10, client=None) -> TopKResult`
- `mediator/analytics.indicators_for(plate: str, context: ContextResult | None = None, client=None) -> list[Indicator]`
- `mediator/analytics.leaderboard(k=10, client=None) -> list[RankedVehicle]`

WS-D (documents) produces: `sources/docs/{__init__.py,schema.sql,wrapper.py}` (port 8006, `/search` route),
`scripts/phase3_docs_generate.py` → `docs_documents.csv` + `data/phase3_gold.json`,
`mediator/doc_retrieval.retrieve(context: ContextResult, k=5, mode="query_only"|"context"|"context+anchors", client=None) -> DocResult`,
`mediator/grounded_summary.summarise(context: ContextResult, docs: DocResult, use_llm=True) -> list[dict]` (`{"sentence","citations"}`).

WS-E (evaluation, UI) produces: `mediator/context_eval.py`, `scripts/evaluate_context.py` → `data/context_eval.json`,
`app/tabs/context_explorer.py` and `app/tabs/context_evaluation.py` (each `render() -> None`), nav entries.
