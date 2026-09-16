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
1. **Metadata-Driven Integration**: Schema correspondences are discovered by a hybrid matcher, validated by a human, and stored in `MAPPING_REGISTRY` + `SOURCE_CATALOG`. Adding a 5th source (PUC) requires only metadata registration; zero engine code changes. This now extends to the planner itself: `mediator/planner.py` names no source id anywhere in its code — source selection is `SOURCE_CATALOG.covers` intersected with the requested attributes (following a derived attribute like `insurance_status` back to what it is derived from), plus every `identity_authority`-flagged source when the request `needs_identity_check`. A newly registered source is planned correctly with no code change, verified by a test that greps for a hard-coded source id.
2. **Provenance-Carrying, Trust-Aware Integration with Refusal**: Every integrated attribute carries `{source, authority, trust, fetched_at}`. If an essential source is offline, the mediator returns `UNDETERMINED` with `LOW` confidence instead of guessing — and, since the decision-honesty hardening pass, a source that was never *asked* also makes no claim: its derived status is `None`, not a fabricated absence, and the reasons list says explicitly which sources were "not checked in this query."
3. **Query-Driven Source Selection**: The query planner checks attribute coverage in the catalog and contacts only relevant databases (e.g., UC1 contacts 2 sources; UC2 contacts 4–5 sources; the citizen self-check contacts 3 and never CAM) executing sub-queries in parallel.
4. **Live agency-side writes through opt-in admin endpoints, with the mediator itself still strictly read-only**: every wrapper now serves `POST /admin/mutate` (a fixed, named menu of actions per source — register, renew, expire, steal, sight, issue, …) and `POST /admin/sql` (a guarded single INSERT/UPDATE/DELETE against a whitelisted table, no DDL), both on a **second, separate database connection** from the one `GET /query` uses. `/query`'s connection is read-only at the session level regardless of admin state; `/admin/*` is on by default and only `<SOURCE_ID>_ADMIN=off` turns a given wrapper's admin doors off. This is what makes "any record change on any laptop is visible in the mediator on the next query" a live, demonstrable claim rather than a slide bullet — and it is deliberately *not* the mediator writing: each agency updates its own database through its own endpoint, exactly as a real insurance company or transport office would.
5. **OCR-to-decision**: a plate photo is read by EasyOCR, repaired through a position-aware confusion table (O↔0, I↔1, B↔8, S↔5, Z↔2, G↔6 — applied only in the direction the Indian plate pattern `[A-Z]{2}\d{2}[A-Z]{1,3}\d{4}` allows at that position), and the repaired candidates feed the same query pipeline as a typed plate. This mirrors an ANPR camera's own job: read a plate, correct likely OCR noise, then look it up against the registration/insurance authorities — see `docs/FIELD_RESEARCH.md` fact 1 (India's Supreme Court mandate to link ANPR e-challans to VAHAN/IIB) and fact 4 (the e-challan mechanics: ANPR read → insurance-validity lookup → automatic flag).
6. **Watchlist and explainable risk score, mirroring real enforcement systems**: `mediator/watchlist.py` logs an alert whenever a watched plate is queried (carrying whatever camera evidence that query returned) and separately raises a "hotlist hit" for any STOLEN/SCRAPPED/SHREDDED verdict even with no operator marker — the production analogue is the UK Motor Insurers' Bureau's **Operation Tutelage/Scalis** (FIELD_RESEARCH fact 7): live ANPR sightings compared against the policy database, a persistent mismatch flags the vehicle to patrols. `mediator/risk.py` scores 0–100 deterministically from the decision plus five additive-only modifiers (conflicts, near-expiry policy, staleness, a down core source, low mean trust) — no ML, no probabilistic fusion, every point is a named, summed factor — mirroring India's IIB uninsured-vehicle ANPR flags (facts 1, 3) and the FASTag-vs-ANPR cloned-plate trials (fact 8, the same logic as our REG-vs-CAM mismatch rule).

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

Rules fire in this order, first match wins; a rule only ever reasons about a source it actually
asked and got an `OK` answer from — a source that was not asked, or answered `DOWN`/`TIMEOUT`/
`ERROR`, is treated identically as "cannot verify," never as an absence of record.

1. A required source (`INS`, `THEFT`, or `REG` when the request needs it) is `DOWN`/`TIMEOUT`/
   `ERROR` $\to$ `UNDETERMINED` (confidence: `LOW`), reason names the specific source.
2. Officially scrapped/shredded, per THEFT's incident record $\to$ `SCRAPPED — ALERT POLICE`
   (seen again since being shredded, confidence: `HIGH`) or `SCRAPPED — REGISTRATION VOID` (not seen
   since, confidence: `HIGH`).
3. `stolen_status = STOLEN` and `case_status = OPEN` $\to$ `STOLEN — ALERT POLICE` (confidence: `HIGH`).
4. Plate seen by CAM but absent in REG, and REG was asked **and** answered `OK` $\to$
   `UNREGISTERED / SUSPICIOUS` (confidence: `MEDIUM`).
5. REG make/model/colour $\ne$ CAM observed ($\ge 2$ mismatches) $\to$
   `SUSPICIOUS — POSSIBLE CLONED PLATE` (confidence: `MEDIUM`).
6. **UNKNOWN VEHICLE**: REG asked and `OK`, no REG row, no CAM sighting, no THEFT record and no
   INS policy row $\to$ `UNKNOWN VEHICLE — NOT REGISTERED` (confidence: `MEDIUM`) — absence is only
   evidence when every one of those sources actually answered; the Investigate tab offers the
   register/insure/re-evaluate onboarding wizard from here.
7. No policy $\to$ `UNINSURED — REPORT` (confidence: `HIGH`). `insurance_expiry < today` escalates
   by days lapsed instead (grace-period ladder, mirrors UK Continuous Insurance Enforcement's
   advisory $\to$ penalty $\to$ impound staging): 1–15 days $\to$ `UNINSURED — ADVISORY`
   (confidence: `MEDIUM`); 16–30 days $\to$ `UNINSURED — WARNING` (confidence: `HIGH`); $> 30$ days
   $\to$ `UNINSURED — REPORT` (confidence: `HIGH`).
8. `registration_status != ACTIVE` $\to$ `REGISTRATION INVALID — REPORT` (confidence: `HIGH`).
9. Otherwise $\to$ `CLEAR` (confidence: `HIGH`, but only when every core source (`REG`, `INS`,
   `THEFT`, `CAM`) was asked and `OK` **and** CAM actually saw the vehicle; `MEDIUM` otherwise) —
   its reasons claim only facts a source actually confirmed, and append
   "Sources not checked in this query: X, Y" for anything that was skipped by the planner, so a
   `CLEAR` from a two-source UC1 query never silently implies THEFT/CAM agreed with it.

---

### 6. Frequently Asked Questions (TA / Professor Q&A)
- **Why GAV not LAV?** We have a known, stable set of sources. Query reformulation is simplified to view unfolding. Extensibility is maintained through the metadata registry.
- **When two sources disagree, what do you believe?** Official authority beats observational sensor data (`OFFICIAL` > `OBSERVATIONAL`). Among equals, recency. Disagreements are never erased; they are preserved in `conflicts[]` and trigger plate-cloning alerts.
- **What happens when a source fails?** The system returns `UNDETERMINED` with `LOW` confidence. It refuses to guess.
- **How do you know the decisions are actually right, not just plausible-looking?** `mediator/ground_truth.py` computes an independent oracle decision for every generated vehicle straight from the source CSVs (not from the mediator's own code path), and `scripts/evaluate_ground_truth.py --start-wrappers` runs the live mediator against all of them: **621/621 = 100.0%** across all 10 generated decision/confidence classes, including both new insurance-ladder tiers. The schema matcher is graded the same way against a hand-made gold mapping (`data/gold_mapping.json`, `scripts/evaluate_matcher.py`): **1.00 precision/recall on all five sources — 23 of 23 correspondences, 0 false positives, 0 false negatives** (PUC's `valid_upto → puc_expiry` needed one domain boost in `mediator/matcher.py`, which is how a human-in-the-loop matcher is tuned).
- **Isn't the mediator writing now — doesn't that break "the mediator never writes to a source"?** No: the mediator's own query path (`GET /query`, everything `mediator/executor.py` calls) is still exactly as read-only as before — guarded to a single `SELECT`, and the database connection behind it is opened read-only at the session level, so even a guard bug cannot write. What changed is that each **wrapper** now also serves `POST /admin/mutate` and `POST /admin/sql` on a second, separate, writable database connection that the mediator's own code never touches — that is each agency updating its own database through its own opt-in endpoint, the same way a real insurance company's staff would use their own system, not the mediator reaching into someone else's data. `<SOURCE_ID>_ADMIN=off` removes those endpoints from a given laptop entirely (`404`) with zero effect on the mediator's read path.
