# Project A — Identification of Uninsured Vehicles
## Federated (Mediator/GAV) Integration System

### 0. Executive Pitch
> Four autonomous government/observational databases — Registration (REG), Insurance (INS), Theft/Crime (THEFT), and Road Camera (CAM) — were designed in isolation and know nothing about each other. We build a mediator layer that:
> 1. Discovers how their schemas correspond using a hybrid schema-matching algorithm ($0.40 \cdot N + 0.20 \cdot C + 0.40 \cdot I$).
> 2. Operationalizes those correspondences as Global-As-View (GAV) mappings held in a metadata registry (`meta.db`), not hardcoded.
> 3. Plans each global query so that only the sources that can answer it are contacted (query-driven source selection).
> 4. Decomposes the global query into source-specific SQL executed live over the network with pushdown predicates.
> 5. Integrates answers with full provenance and source-trust, detects conflicts, and resolves them deterministically.
> 6. Produces a defensible decision — including refusing to decide (`UNDETERMINED`) when an essential source is unreachable — and files an official audit report to the Ministry of Transportation.
> The data never leaves the sources; every query sees fresh data.

---

### 1. Innovative Aspects
1. **Metadata-Driven Integration**: Schema correspondences are discovered by a hybrid matcher, validated by a human, and stored in `MAPPING_REGISTRY` + `SOURCE_CATALOG`. Adding a 5th source (e.g. PUC) requires only metadata registration; zero engine code changes.
2. **Provenance-Carrying, Trust-Aware Integration with Refusal**: Every integrated attribute carries `{source, authority, trust, fetched_at}`. If an essential source is offline, the mediator returns `UNDETERMINED` with `LOW` confidence instead of guessing.
3. **Query-Driven Source Selection**: The query planner checks attribute coverage in the catalog and contacts only relevant databases (e.g., UC1 contacts 2 sources; UC2 contacts 4 sources) executing sub-queries in parallel.

---

### 2. Autonomous Source Databases & Heterogeneity
- **REG (Regional Transport Office)**: Port 8001 (`OWNERS`, `VEHICLE_REGISTRATION`). Canonical plate format `DL01AB1234`.
- **INS (Insurance Provider)**: Port 8002 (`INSURERS`, `POLICY_RECORDS`). Hyphenated plate `DL-01-AB-1234`, date as text `DD/MM/YYYY`.
- **THEFT (Police Crime Records)**: Port 8003 (`CRIME_RECORDS`). Spaced lowercase plate `dl 01 ab 1234`, date as Unix epoch seconds.
- **CAM (Road Camera Network)**: Port 8004 (`CAMERAS`, `PLATE_CAPTURES`). Observational noisy sensor with timestamps.
- **PUC (Pollution Authority - UC6)**: Port 8005 (`POLLUTION_CERT`). Extensible 5th agency.

---

### 3. Global Virtual Schema (`VEHICLE_PROFILE`)
- **Key**: `plate_number` (canonical upper alphanumeric).
- **Registration**: `owner_name`, `vehicle_make`, `vehicle_model`, `vehicle_colour`, `registration_date`, `registration_status`.
- **Insurance**: `insurer_name`, `policy_type`, `insurance_start`, `insurance_expiry`, `insurance_status` (derived: `VALID` | `EXPIRED` | `NONE` | `UNKNOWN`).
- **Theft**: `stolen_status` (derived: `STOLEN` | `RECOVERED` | `NOT_REPORTED` | `UNKNOWN`), `last_incident_date`, `case_status`.
- **Sighting**: `last_seen_location`, `last_seen_time`, `observed_make`, `observed_model`, `observed_colour`.
- **Metadata**: `provenance`, `conflicts`, `source_availability`, `decision`, `confidence`, `reasons`.

---

### 4. GAV Mapping Rules (Formal Specification)
$$\text{Vehicle}(p, \text{make}, \text{model}, \text{colour}, \text{regdate}, \text{regstatus}, \text{owner}) \supseteq \text{REG.VEHICLE\_REGISTRATION}(\_, rn, oid, make, model, colour, \_, regdate, regstatus, \_) \bowtie \text{REG.OWNERS}(oid, owner, \_, \_) \text{ WHERE } p = \text{norm\_plate}(rn)$$

$$\text{Insurance}(p, \text{insurer}, \text{ptype}, \text{start}, \text{expiry}, \text{act}) \supseteq \text{INS.POLICY\_RECORDS}(\_, vr, iid, ptype, ps, pu, act, \_) \bowtie \text{INS.INSURERS}(iid, insurer) \text{ WHERE } p = \text{norm\_plate}(vr)$$
*(Aggregated by $\max(\text{expiry})$ per $p$)*

$$\text{Theft}(p, \text{stolen}, \text{recovered}, \text{incident\_date}, \text{case\_status}) \supseteq \text{THEFT.CRIME\_RECORDS}(\_, vn, \_, rd, \text{'THEFT'}, s, r, cs, \_) \text{ WHERE } p = \text{norm\_plate}(vn)$$
*(Aggregated by $\max(rd)$ per $p$)*

$$\text{Sighting}(p, \text{location}, \text{seen\_at}, \text{o\_make}, \text{o\_model}, \text{o\_colour}) \supseteq \text{CAM.PLATE\_CAPTURES}(\_, pl, cid, ts, \text{o\_make}, \text{o\_model}, \text{o\_colour}, \_) \bowtie \text{CAM.CAMERAS}(cid, \text{location}, \_, \_) \text{ WHERE } p = \text{norm\_plate}(pl)$$
*(Aggregated by $\max(ts)$ per $p$)*

$$\text{VEHICLE\_PROFILE} = \text{Vehicle} \ \tilde{\bowtie}_{p} \ \text{Insurance} \ \tilde{\bowtie}_{p} \ \text{Theft} \ \tilde{\bowtie}_{p} \ \text{Sighting}$$

---

### 5. Decision Engine Ordered Rules
1. `THEFT` or `INS` unreachable when needed $\to$ `UNDETERMINED` (confidence: `LOW`).
2. `stolen_status = STOLEN` and `case_status = OPEN` $\to$ `STOLEN — ALERT POLICE` (confidence: `HIGH`).
3. Plate seen by CAM but absent in REG $\to$ `UNREGISTERED / SUSPICIOUS` (confidence: `MEDIUM`).
4. REG make/model/colour $\ne$ CAM observed ($\ge 2$ mismatches) $\to$ `SUSPICIOUS — POSSIBLE CLONED PLATE` (confidence: `MEDIUM`).
5. No policy or `insurance_expiry < today` $\to$ `UNINSURED — REPORT` (confidence: `HIGH`).
6. `registration_status != ACTIVE` $\to$ `REGISTRATION INVALID — REPORT` (confidence: `HIGH`).
7. Otherwise $\to$ `CLEAR` (confidence: `HIGH`).

---

### 6. Frequently Asked Questions (TA / Professor Q&A)
- **Why GAV not LAV?** We have a known, stable set of sources. Query reformulation is simplified to view unfolding. Extensibility is maintained through the metadata registry.
- **When two sources disagree, what do you believe?** Official authority beats observational sensor data (`OFFICIAL` > `OBSERVATIONAL`). Among equals, recency. Disagreements are never erased; they are preserved in `conflicts[]` and trigger plate-cloning alerts.
- **What happens when a source fails?** The system returns `UNDETERMINED` with `LOW` confidence. It refuses to guess.
