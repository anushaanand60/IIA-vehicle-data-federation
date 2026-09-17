# Heterogeneity Checklist

This table maps directly to the course lecture on *"Why Schema Matching & Data Integration is Hard"*:

| Conflict Type (Slide Term) | Where Introduced in Project | How Handled by Mediator |
| :--- | :--- | :--- |
| **Naming / Synonyms** | `registration_no` (REG) = `vehicle_reg` (INS) = `vehicle_number` (THEFT) = `plate_id` (CAM) | Hybrid Matcher (lexical + thesaurus expansion) maps all to `plate_number` in `MAPPING_REGISTRY`. |
| **Representation Conflict** | Dates: `registered_on DATE`, ISO `YYYY-MM-DD` (REG) vs `policy_start`/`policy_until VARCHAR(10)`, `DD/MM/YYYY` text (INS) vs `reported_date INTEGER`, Unix epoch seconds (THEFT) vs `captured_at TEXT`, ISO timestamp `YYYY-MM-DDTHH:MM:SS` (CAM) | Pure transforms `parse_ddmmyyyy` (INS) and `epoch_to_date` (THEFT) convert to ISO; REG and CAM are already ISO and pass through with the `none` transform. |
| **Value Format on Join Key** | `DL01AB1234` (REG) vs `DL-01-AB-1234` (INS) vs `dl 01 ab 1234` (THEFT) | `norm_plate` strips punctuation/spaces and uppercases; decomposer pushes down `UPPER(REPLACE(REPLACE(...)))` in SQL. |
| **Encoding of Booleans / Status** | `is_active INTEGER` `1/0` (INS) vs `stolen_flag`/`recovered_flag TEXT` `'Y'/'N'` (THEFT) vs `reg_status VARCHAR(10)` `'ACTIVE'/'SUSPENDED'/'CANCELLED'` (REG) | `status_map` on REG `reg_status` and THEFT `case_status`; the integrator applies `yn_to_bool` to the THEFT flags. `tinyint_to_bool` exists in `mediator/transforms.py` but no mapping uses it: `is_active` is carried with `none` and drives no decision. |
| **Granularity / Structure** | Owner normalized into separate `OWNERS` table in REG vs flat representation elsewhere | GAV mapping rule encodes SQL join path `VEHICLE_REGISTRATION.owner_id = OWNERS.owner_id`. |
| **Derived Attributes** | `insurance_status` is not stored in INS; must be derived dynamically from `policy_until` vs today | Integrator derives status (`VALID` vs `EXPIRED`) comparing policy expiration against reference date. |
| **Temporal / Cardinality** | Many policies & many camera captures per vehicle | Latest-wins aggregation rules: `latest_by:policy_until`, `latest_by:captured_at`, `latest_by:reported_date`. |
| **Authoritative vs Observational** | REG, INS, THEFT are official authorities; CAM is a noisy physical sensor | Conflict resolution prefers `OFFICIAL` over `OBSERVATIONAL`. Mismatches between REG and CAM raise plate-cloning flags. |
| **DBMS Heterogeneity** | PostgreSQL, MySQL, SQLite | Autonomous FastAPI wrappers encapsulate DBMS dialect and connection details behind a unified REST contract. |
