# Study Guide — Uninsured Vehicle Federation

This is the whole project explained from first principles, written so you can defend every design
choice out loud to a professor. It is long on purpose — read it in sections alongside your lecture
notes, not in one sitting. Every fact here is pulled from the code that is actually running, not
from the design doc's intentions.

---

## 1. The one-sentence pitch (memorise this)

> Four autonomous government/observational databases — Registration, Insurance, Theft/Crime, Road
> Camera — were designed in isolation and know nothing about each other. We build a **mediator
> layer** that discovers how their schemas correspond, operationalises those correspondences as GAV
> mappings held in a metadata registry (not hard-coded), plans each global query so only the
> sources that can answer it are contacted, decomposes the query into source-specific SQL executed
> live over the network, integrates the answers with provenance and source-trust, detects
> conflicts, and produces a defensible decision — including refusing to decide when a source is
> unreachable — then files a report to the Ministry of Transportation. The data never leaves the
> sources; every query sees fresh data.

That paragraph is worth rubric item 1 by itself if you can say it without notes.

---

## 2. Federated vs. warehoused vs. virtualised — why federation, said properly

This is the question every integration project gets asked, so get the vocabulary exact.

**Three ways to integrate heterogeneous databases:**

| Approach | What it means | When it's right |
|---|---|---|
| **Materialized (data warehouse / ETL)** | Copy data from every source into one central store on a schedule (nightly, hourly). Queries run against the copy. | Historical analytics, reporting over stable data, when staleness is acceptable. |
| **Virtual / Federated (mediator)** | Nothing is copied. A mediator layer translates one global query into per-source queries, executed live, and merges the answers on the fly. | Freshness matters, sources must stay autonomous, low tolerance for duplicating sensitive data. |
| **Hybrid (materialized views with incremental maintenance)** | A middle ground: cached views that are kept in sync via triggers/CDC. | When you need warehouse-speed reads but can tolerate the sync-engineering cost. |

**We are Virtual/Federated — a mediator with GAV (Global-As-View) mappings.** Concretely:

- `mediator/executor.py` opens a live HTTP connection to all four sources **at query time**. There
  is no step where data from REG/INS/THEFT/CAM is copied anywhere.
- The only thing persisted centrally is `mediator/meta.db` — and that holds **metadata only**:
  which sources exist, what their columns mean, and a `REPORT_LOG` of past decisions. No vehicle
  data lives there.
- Every query against `DL05CD9876` re-fetches from all four databases. If the insurance company
  updates a row at 10:05, the very next query at 10:06 sees it — we proved this live tonight by
  renewing a policy on the insurance laptop and re-querying with no restart.

**Why not a warehouse (say this when asked):** the application needs (1) **freshness** — an
insurance policy renewed just now must show up on the next query, not tomorrow's ETL batch; (2)
**source autonomy** — four different real-world authorities (a transport office, an insurance
company, the police, a camera network) would never agree to hand a copy of their database to a
central system; (3) **provenance** — a decision that leads to a fine or a police alert must be
traceable to exactly which agency said what and when, which a merged warehouse row loses; (4) **low
tolerance for duplicating citizen data** — copying insurance and police records into a third
system is a privacy and security liability a real Ministry of Transportation project would never
accept.

**Why not incremental materialized views:** they would solve freshness reasonably well, but they
still require each source to either push changes (which assumes cooperation model beyond
"autonomous") or be polled and diffed (extra infrastructure, extra staleness window, extra failure
mode). Federation is the simpler, more defensible architecture for four sources that were "designed
in isolation" — the premise of the whole assignment.

**The one thing we deliberately trade away:** *performance predictability*. A federated query is
only as fast as its slowest live source, and a warehouse query is always fast because the data is
already local. We accept that cost — see §8 for exactly how we bound it.

---

## 3. The four sources — what's really in each one

Every schema below is copied from the actual `sources/*/schema.sql` files, not simplified.

### REG — Regional Transport Office (PostgreSQL)

```sql
OWNERS(owner_id PK, full_name, address_line, city)
VEHICLE_REGISTRATION(registration_id PK, registration_no, owner_id FK→OWNERS,
                      make, model, colour, fuel_type, registered_on DATE,
                      reg_status, rto_code)
```

Trust: **0.95** (highest — the government office of record). Authoritative.

### INS — Insurance Provider (MySQL)

```sql
INSURERS(insurer_id PK, insurer_name)
POLICY_RECORDS(policy_id PK, vehicle_reg, insurer_id FK→INSURERS, policy_type,
                policy_start TEXT, policy_until TEXT, is_active INTEGER, premium_inr)
```

Trust: **0.90**. Authoritative. **Multiple policies per vehicle are possible** — renewals create a
new row rather than updating the old one, so "latest wins" logic is required (see §5.4).

### THEFT — Police Crime Records (SQLite)

```sql
CRIME_RECORDS(incident_id PK, vehicle_number TEXT, fir_no, reported_date INTEGER,
              incident_type TEXT CHECK IN ('THEFT','SHREDDING','HIT_AND_RUN'),
              stolen_flag TEXT, recovered_flag TEXT, case_status TEXT, police_station)
```

Trust: **0.90**. Authoritative. `reported_date` is stored as a **Unix epoch integer** — the fourth
and strangest date encoding in the system.

### CAM — Road Camera Network (PostgreSQL)

```sql
CAMERAS(camera_id PK, location_name, lat, lon)
PLATE_CAPTURES(capture_id PK, plate_id, camera_id FK→CAMERAS, captured_at TEXT,
               observed_make, observed_model, observed_colour, ocr_confidence)
```

Trust: **0.60** — deliberately the lowest. **This is the one *observational* source; the other
three are authoritative.** A camera guesses a plate via OCR; an office records one. That
authority/trust distinction is load-bearing: it's exactly what lets the conflict-resolution logic
(§7) know to prefer REG's `make/model/colour` over CAM's when they disagree, without needing a
human in the loop.

### The heterogeneity table (this is your rubric-3 evidence)

| | REG | INS | THEFT | CAM |
|---|---|---|---|---|
| DBMS | PostgreSQL | MySQL | SQLite | PostgreSQL |
| Plate column | `registration_no` | `vehicle_reg` | `vehicle_number` | `plate_id` |
| Plate stored as | `DL01AB1234` | `DL-01-AB-1234` | `dl 01 ab 1234` | OCR noise, e.g. `DLO1AB1Z34` |
| Date format | ISO `2023-01-15` | `DD/MM/YYYY` text | Unix epoch int | ISO `2026-09-04T08:30:00` |
| Boolean encoding | `ACTIVE`/`SUSPENDED`/`CANCELLED` | `1`/`0` | `Y`/`N` | (none) |

That row of four different plate spellings and four different date encodings is the entire "assume
at least one column with the same value but a different name" requirement from your brief, made
concrete. It is why `norm_plate()` and the four date-transform functions in `mediator/transforms.py`
exist at all.

---

## 4. Synthetic data — what's actually in the tables and why

Generated by `reg.py` / `ins.py` / `theft.py` / `cam.py` / `puc.py`, with a **fixed seed
(2023519)** so every laptop that regenerates gets the same data — never do this per-laptop; the
CSVs are committed instead, precisely to avoid four laptops drifting apart.

**Scale:** 600 base vehicles + injected demo cases.

**Engineered overlaps and conflicts, on purpose:**

| What | Count | Why it exists |
|---|---|---|
| No insurance at all | 50 vehicles | Tests Rule 5 (`no policy` → UNINSURED) |
| Expired policy | 50 vehicles | Tests Rule 5 (`insurance_expiry < today`) |
| Renewed policy (two rows) | 10 vehicles | Tests latest-wins aggregation |
| Reported stolen | 40 incidents | Tests Rule 2 (STOLEN + case OPEN) |
| REG vs CAM conflict (make/model/colour) | 25 vehicles | Tests Rule 4 (cloned plate) |
| Seen by camera, never registered | 15 plates | Tests Rule 3 (UNREGISTERED) |
| OCR-corrupted plates | 20 plates | Tests plate-matching robustness |
| Data-quality noise (missing make, "Hyundia" misspelling) | ~15 rows | Tests `fix_make` transform |
| **Scrapped/shredded, then seen again** | **1 vehicle (`DL03SC5566`)** | Tests the new Rule 2b (§7) |

Plus **five (now six) story vehicles** with known right answers, injected by
`data/inject_demo_fixtures.py`, used in every automated test and the demo:

| Plate | Story | Decision |
|---|---|---|
| `DL01AB1234` | Everything agrees | `CLEAR` |
| `DL05CD9876` | Policy expired 2026-06-10 | `UNINSURED — REPORT` |
| `HR26EF4455` | Stolen, case open | `STOLEN — ALERT POLICE` |
| `UP16GH1122` | REG says Hyundai Creta/red, CAM says Kia Seltos/blue | `SUSPICIOUS — CLONED PLATE` |
| `MH12IJ7788` | Seen by camera, no REG row | `UNREGISTERED / SUSPICIOUS` |
| `DL03SC5566` | Registered 2011, shredded 2024, seen 2026 | `SCRAPPED — ALERT POLICE` |

---

## 5. Schema matching — how the system learns what a column means

**File:** `mediator/matcher.py`. **Rubric item 5.**

The core problem: REG calls it `registration_no`, INS calls it `vehicle_reg`, THEFT calls it
`vehicle_number`, CAM calls it `plate_id`. Nobody told the mediator these are the same fact. The
matcher discovers that correspondence automatically, from evidence, without a human writing
`registration_no = plate_number` by hand.

### 5.1 The formula

```
Score = 0.40 · N (name similarity)  +  0.20 · C (type/constraint compatibility)  +  0.40 · I (instance/value similarity)
```

Computed for **every (source column, global attribute) pair**, producing the full similarity matrix
the GUI renders as a heatmap. A correspondence is accepted when its score clears **θ = 0.55**.

### 5.2 N — name similarity (40% weight)

`compute_name_similarity()` tokenises both names (splitting on `_`, camelCase, etc.) and compares
them with **Jaro-Winkler** string similarity plus a small thesaurus (so `reg` and `registration`
score highly even though they're not close character-for-character). `registration_no` vs
`plate_number` scores well here even though the words don't share a prefix, because tokenised
`{registration, no}` overlaps semantically with `{plate, number}` through the thesaurus step.

### 5.3 C — type/constraint compatibility (20% weight)

`compute_type_compatibility()` checks whether the source column's SQL type and constraints (is it
a primary key? a foreign key?) are plausible for the target global attribute. A `VARCHAR(12)`
column that's a primary key is a plausible match for `plate_number` (which should be unique); an
`INTEGER` foreign key is not.

### 5.4 I — instance/value similarity (40% weight)

`compute_instance_similarity()` looks at up to 20 **actual sample values** pulled live from
`/schema` and checks whether they *look like* the target attribute — does the text match a plate
pattern, a date pattern, a known colour name? This is the signal that catches things name-matching
alone would miss, and it's why `/schema` returning real sample values (not placeholders) matters:
the matcher genuinely needs to see `'DL-01-AB-1234'` to decide `vehicle_reg` is a plate column.

### 5.5 What the matcher cannot do — and what that means for "mapping"

The matcher finds **correspondences** (this column ≈ this attribute). It does **not** figure out:

1. **The transform** needed to reconcile formats — e.g. that `policy_until` stores
   `"15/01/2026"` and needs `parse_ddmmyyyy()` to become the ISO date the integrator compares.
2. **The join path** to reach a column in a second table — e.g. that `owner_name` actually lives in
   `OWNERS`, reached via `VEHICLE_REGISTRATION.owner_id = OWNERS.owner_id`.
3. **The aggregation strategy** for a source with multiple rows per vehicle — e.g. that INS needs
   "latest policy wins" when a vehicle has been renewed.

That's the human-validation step your course phrase captures exactly: **"matching discovers
correspondences; mapping operationalises them into executable integration logic."** The 31
validated mappings in `MAPPING_REGISTRY` (see `tests/fixtures.py` → `APPROVED_MAPPINGS`) are that
operationalisation — each row carries `transform_fn`, `join_path`, and `aggregate`, not just the
column name. Tab 3 of the GUI (Matcher & Heatmap) demonstrates the discovery step live; Tab 4
(Catalog & Registry) shows the resulting operationalised mapping table.

---

## 6. Query decomposition — one plate becomes four different SQL queries

**Files:** `mediator/planner.py`, `mediator/decomposer.py`. **Rubric item 6.**

### 6.1 Planning — minimising which sources are contacted

`plan_query()` reads `SOURCE_CATALOG.covers[]` for each source and only selects sources that can
answer the requested attributes. Query scope `UC1` (insurance only) selects `{REG, INS}` — REG is
always included to confirm the vehicle actually exists — while `UC2` (full profile) selects all
four. This is visible on stage: switch the Query Scope dropdown in Tab 1 and Tab 2's "Sources
Contacted" count drops from 4 to 2.

### 6.2 Decomposition — one canonical plate, four different queries

`decompose_query(source_id, canonical_plate)` builds SQL **generically from the mapping metadata**,
not from a hardcoded per-source template:

1. Looks up which table holds `plate_number` for this source (via the mapping row where
   `global_attr = "plate_number"`).
2. Normalises the plate the way *that source* stores it — `UPPER(REPLACE(REPLACE(col, '-', ''),
   ' ', '')) = 'DL05CD9876'` — so the same canonical plate matches `DL05CD9876`,
   `DL-05-CD-9876`, and `dl 05 cd 9876` without three separate code paths.
3. Adds `JOIN`s only for tables the requested mapping actually needs (e.g. `JOIN OWNERS ON
   VEHICLE_REGISTRATION.owner_id = OWNERS.owner_id` only when `owner_name` was requested).
4. Adds `ORDER BY ... LIMIT 1` when the mapping specifies `aggregate: latest_by:<col>`, pushing the
   "which policy is current" logic down into SQL where the DB can use it.

The result, for one query on `DL05CD9876`, is genuinely four different SQL statements against four
different tables in four different dialects — this is your single strongest screen for rubric 6
(**Tab 2: Plan Trace**, open all four expanders side by side).

### 6.3 Federation — parallel execution with a hard deadline

`mediator/executor.py` dispatches every selected source's query **simultaneously** via a
`ThreadPoolExecutor`, not one after another. This matters because a federated query's latency is
the latency of the **slowest** source, not the sum of all four:

```python
CONNECT_TIMEOUT_S = 0.5   # a live wrapper accepts TCP within milliseconds; longer only delays a DOWN verdict
GRACE_S = 0.25            # backstop on top of the slowest source's own timeout
```

Each source has its own `timeout_ms` in the catalog (1500ms by default). The executor computes a
hard deadline as `max(all sources' timeouts) + GRACE_S` and gives up on anything not back by then,
classifying it `TIMEOUT` rather than hanging forever. `test_sources_are_called_in_parallel_not_in_sequence`
proves this: two sources that each take 700ms return in under 1.2s total, not 1.4s — the
mathematical signature of parallel, not sequential, execution.

---

## 7. Integration — turning four raw answers into one decision

**Files:** `mediator/integrator.py`, `mediator/decide.py`. **Rubric item 8 (the biggest single item, 3 marks).**

### 7.1 Provenance — every fact remembers where it came from

For every attribute the integrator fills in, it also stamps `profile["provenance"][attr]` with:

```python
{"source": "REG", "authority": "OFFICIAL", "trust": 0.95, "fetched_at": "2026-09-15T..."}
```

This is not cosmetic. It's what lets a decision be *defended*: "the owner name came from REG,
fetched three seconds ago, from a source we trust at 0.95" is an auditable claim; a bare merged row
is not. The GUI's "View Per-Attribute Provenance" expander shows all ~21 of these per query.

### 7.2 Latest-wins aggregation (for sources with multiple rows per vehicle)

INS can have several policy rows for one vehicle (renewals). The integrator picks the one with the
maximum `policy_until` after parsing the `DD/MM/YYYY` text — genuinely comparing dates, not
strings, or `"31/12/2027"` would sort before `"05/06/2026"` lexicographically and pick the wrong
policy. Verified: on real renewal pairs, `2027-03-02` was correctly chosen over `2024-09-04`.

### 7.3 Conflict detection — shown, never silently resolved

When REG and CAM disagree on make, model, or colour, the integrator records **both values** plus
the resolution rule:

```python
{"attribute": "vehicle_make", "values_by_source": {"REG": "Hyundai", "CAM": "Kia"},
 "resolution": "OFFICIAL authority (REG) preferred over OBSERVATIONAL (CAM)", "chosen": "Hyundai"}
```

This is **trust-based conflict resolution**, the simplest defensible strategy: an authoritative
source (REG, trust 0.95) always wins over an observational one (CAM, trust 0.60) on overlapping
attributes. The alternative — averaging, voting, or picking the more recent value — would either
make no sense for categorical data (you can't average "Hyundai" and "Kia") or would need
probabilistic truth discovery, which the design doc explicitly defers to **Part B** of the course.
Say that if asked: "we use deterministic, explainable trust-priority resolution; probabilistic
truth discovery is out of scope for Part A by design."

### 7.4 Derived attributes — computed, never fetched as such

Two attributes exist only as a computation over stored ones, and this matters for a subtlety in
`mediator/contract.py`: `insurance_status` and `stolen_status` are **derived**, not stored anywhere.

- `insurance_status`: `NONE` if no policy row exists, `EXPIRED` if `insurance_expiry < today`,
  `VALID` otherwise. `UNKNOWN` if INS itself is unreachable — this distinction (no data vs.
  couldn't check) is exactly what prevents a false "clear" when a source is simply down.
- `stolen_status`: `STOLEN` if `stolen_flag=Y AND recovered_flag=N AND case_status=OPEN`,
  `RECOVERED` if recovered or case closed, `NOT_REPORTED` otherwise, `UNKNOWN` if THEFT is down.

### 7.5 The decision engine — seven ordered rules, first match wins

`evaluate_vehicle_decision()` in `mediator/decide.py`. **Deterministic and rule-based on purpose** —
no ML, no probability, because the design doc explicitly scopes that out for Part A ("Deliberately
deferred: probabilistic truth discovery & uncertainty (Part B) ... ML anomaly detection (only if
time permits — it is an extension, not the core)"). Every rule returns a **decision, a confidence
level, and a plain-English reason** — that `reasons[]` list is the "can we explain and defend the
answer?" property your course cares about.

```
1. A required source is DOWN/TIMEOUT       → UNDETERMINED, LOW        "cannot verify ... safely"
2. Officially scrapped/shredded (added)    → SCRAPPED — ALERT POLICE / — REGISTRATION VOID, HIGH
3. stolen_status = STOLEN and case OPEN    → STOLEN — ALERT POLICE, HIGH
4. Seen by CAM, absent in REG              → UNREGISTERED / SUSPICIOUS, MEDIUM
5. REG vs CAM disagree on ≥2 of make/model/colour → SUSPICIOUS — CLONED PLATE, MEDIUM
6. No policy, or insurance_expiry < today  → UNINSURED — REPORT, HIGH
7. registration_status != ACTIVE           → REGISTRATION INVALID — REPORT, HIGH
8. Otherwise                                → CLEAR, HIGH (MEDIUM if never seen by camera)
```

**Rule 1 is the most important rule in the whole system** for grading purposes: it is what makes
"refuse to decide when a source is unreachable" real instead of a slide bullet. Rule ordering
matters — rule 2 (scrapped) sits **above** rules 6/7 (insurance/registration) on purpose: a
destroyed vehicle being seen on the road is a worse finding than its paperwork lapsing, so
"this vehicle was destroyed" must outrank "its paperwork lapsed", or a scrapped car would just
report as `UNINSURED`.

### 7.6 Ministry reporting — UC4, materialised for audit

`mediator/report.py`. `file_ministry_report()` writes the full decision — plate, decision,
confidence, reasons, the **entire profile as evidence JSON**, and which sources were used — into
`REPORT_LOG` in `meta.db`, timestamped. `generate_report_pdf()` renders a one-page ReportLab PDF
of the same. This is the one piece of state the mediator *does* persist centrally, and it's
legitimate to: it's an audit trail of decisions already made, not a cache of source data being used
to answer the next query.

---

## 8. Latency — what "reasonable performance" actually means here, and how we bound it

Federation trades predictable speed for freshness (§2). We don't ignore performance; we bound it
explicitly in three places:

1. **Parallel dispatch, not sequential** (§6.3) — the wall-clock cost of a federated query is
   `max()` over sources, not `sum()`. This is the single biggest lever.
2. **A short connect timeout (0.5s)** separate from the read timeout. If a wrapper's TCP port isn't
   accepting connections, we don't wait the full 1500ms to find out — a dead source is classified
   `DOWN` fast, freeing the deadline budget for sources that are actually alive.
3. **A hard deadline with grace, never open-ended** — `max(all timeouts) + 0.25s`. No single slow
   or hung source can stall the whole query past that ceiling; it's classified `TIMEOUT` and the
   others' answers are still returned.

On the real four-laptop, three-engine deployment tonight, observed end-to-end query latency was
**76–240ms** per query (see the plan trace `total_elapsed_ms` field) — genuine network round trips
over a phone hotspot to PostgreSQL, MySQL and SQLite on three different machines, not
sub-millisecond localhost calls. That number is itself evidence the demo is really federated: if
you ever see latency drop to ~1ms, a source's `base_url` has silently been pointed back at
`127.0.0.1` and you're no longer testing the real system (see §12, "gotcha #2").

**What we explicitly do not do:** cache query results, or precompute anything. `CLAUDE.md`'s
invariant is direct: *"No caching of source data. Freshness is why we chose federation over a
warehouse; a cache would contradict the thesis. Health checks may be cached 5s for GUI badges only,
never for decisions."* If asked "wouldn't caching make this faster?" — yes, and it would also
reintroduce exactly the staleness problem federation exists to avoid. That trade-off is the thesis
of the whole project, not an oversight.

---

## 9. The GUI, tab by tab — every control explained

**File:** `app/app.py`, Streamlit. Five tabs plus a sidebar.

### Sidebar — Live Source Data Mutator

Five buttons: renew/expire insurance, report stolen, log a camera sighting, register a new vehicle.

Each button now calls `POST /admin/mutate` on the owning source's own wrapper, over HTTP — a
**fixed, named menu of writes** (`sources/wrapper_template.py: ADMIN_ACTIONS`), never arbitrary
SQL, and using a **second, separate database connection** from the one `/query` uses. `/query`'s
connection stays read-only regardless of whether admin mode is on anywhere — see §11 for exactly
why that boundary matters and how it's enforced. If a source hasn't opted in (its
`<SOURCE>_ADMIN_URL` isn't set), the button shows the equivalent `scripts/mutate_source.py` command
to run on the laptop that owns it, instead of claiming a success that didn't happen.

### Tab 1 — Investigate

The main query interface. Five (now six) quick-demo buttons pre-fill a **dirty** version of each
story plate (e.g. `DL-05-cd-9876`, `hr 26 ef 4455`) to demonstrate plate-format normalisation live,
not just the clean canonical form. A free-text field accepts any plate in any format. A **Query
Scope** dropdown toggles between `UC1` (insurance-only, 2 sources) and `UC2` (full profile, 4
sources) — this is live evidence of source minimisation (§6.1). Below the query: source health
cards (🟢/🟡/🔴), the decision banner with confidence, per-attribute provenance (expander), detected
conflicts, and a "File Report to Ministry" button that produces a downloadable PDF on the spot.

### Tab 2 — Plan Trace

Shows, per query: the canonical plate, how many sources were contacted and which, total elapsed
time, and — expanded by default — **the exact SQL sent to each source**, its status, row count and
individual latency. This is the strongest evidence for rubric item 6.

### Tab 3 — Matcher & Heatmap

Pick a live source, run the matcher against its **real, live `/schema`** response (not a cached
copy), and see the N/C/I-scored correspondence table plus a Plotly similarity-matrix heatmap. This
is rubric item 5, live.

### Tab 4 — Catalog & Registry

Two read-only tables — `SOURCE_CATALOG` (which sources exist, their trust/authority/timeout) and
`MAPPING_REGISTRY` (all 31 validated mappings, with their transform functions and join paths) — plus
a live form: **"Register New Source (UC6)"**, which registers a fifth source (PUC, pollution
certificates) with zero mediator code changes. This is the extensibility use case and it is real:
tonight we registered PUC live and watched the very next query contact 5 sources instead of 4,
with `puc_expiry` populated in the profile.

### Tab 5 — Ministry Reports

Every filed report, its reasons, and a PDF download — the audit trail from §7.6, browsable.

---

## 10. What data integration concepts each part of this maps to (for your lecture notes)

| Course concept | Where it lives here |
|---|---|
| **Global-as-View (GAV)** | `MAPPING_REGISTRY`: each source's local schema is expressed as a view over the global vocabulary, not the reverse. |
| **Mediator / wrapper architecture** | `mediator/*` = mediator; `sources/*/wrapper.py` = the four wrappers, one per source. |
| **Schema matching (lexical + instance-based)** | `mediator/matcher.py`, N/C/I scoring, §5. |
| **Schema mapping (correspondences → executable logic)** | `MAPPING_REGISTRY` rows: `transform_fn`, `join_path`, `aggregate` per column. |
| **Query planning / source selection** | `mediator/planner.py`, §6.1. |
| **Query decomposition** | `mediator/decomposer.py`, §6.2. |
| **Parallel query execution over a network** | `mediator/executor.py`, ThreadPoolExecutor, §6.3/§8. |
| **Result integration / entity resolution across sources** | `mediator/integrator.py` — every source's row for one plate is joined on the canonical plate key. |
| **Provenance / lineage** | `profile["provenance"]`, §7.1. |
| **Conflict detection and resolution (trust-based)** | `profile["conflicts"]`, §7.3. |
| **Derived / computed attributes vs. stored attributes** | `insurance_status`, `stolen_status` — never fetched, always computed, §7.4. |
| **Rule-based decision support** | `mediator/decide.py`, §7.5. |
| **Graceful degradation / partial failure handling** | Rule 1 → `UNDETERMINED`, §7.5, §12. |
| **Extensibility (schema evolution without code change)** | UC6 — register a source live, §9 Tab 4. |
| **Source autonomy** | Each wrapper's DB bound to `127.0.0.1`; only the HTTP API is published, §11. |

---

## 11. What "read-only" and "autonomous" actually mean here — and the admin console addition

Two invariants from `CLAUDE.md` are worth understanding precisely, because they're the two things
most likely to get probed in a viva:

**"Each agency publishes an API, not a database."** Every source's real database (PostgreSQL,
MySQL, SQLite) listens on `127.0.0.1` only — never on the LAN. What's exposed to the network is a
small FastAPI wrapper with exactly three endpoints: `GET /health`, `GET /schema`, `POST /query`.
Nobody outside that laptop can open a raw connection to the database; they can only ask the wrapper
a question through those three doors.

**"The mediator's query path is read-only."** `POST /query`'s SQL guard (`guard_sql()` in
`sources/wrapper_template.py`) rejects anything that isn't a single `SELECT`: it blocks `;`
(multiple statements), SQL comments, and a long list of forbidden keywords (`INSERT`, `UPDATE`,
`DELETE`, `DROP`, `GRANT`, ... — 30+ keywords). Beyond the textual guard, the **database connection
itself is opened read-only** at the session level (`default_transaction_read_only=on` for
Postgres, `SET SESSION TRANSACTION READ ONLY` for MySQL, `PRAGMA query_only = ON` for SQLite) — so
even a guard bug can't actually write, because the database itself refuses.

**So how does the GUI update data at all, without breaking that?** This is the interesting design
answer, not a contradiction. `POST /admin/mutate` is a **second, entirely separate** endpoint on
each wrapper, backed by a **second, separate database connection** — not the read-only one `/query`
uses. It:

- Is **off by default** on every laptop (returns `404`) unless that laptop explicitly sets
  `<SOURCE>_ADMIN_URL` — an opt-in, not a standing hole.
- Accepts **only a fixed, named menu of actions** (`renew`, `expire`, `steal`, `clear`, `sight`,
  `register` — one set per source, defined in `ADMIN_ACTIONS`), never arbitrary SQL. You cannot
  send it a `DROP TABLE`; there is no code path that would even parse one.
- Never touches the connection `/query` uses, so the read-only guarantee that matters for the
  **mediator's own query path** — the thing the professor is actually asking about when they ask
  "is this read-only?" — is completely unaffected.

The honest framing, if asked: *"The mediator itself never writes — it only ever issues SELECT, and
we enforce that at three layers: a SQL guard, a keyword blocklist, and a read-only database
session. What you're seeing update live is each agency's own operator console, reached through its
own separate endpoint with its own separate, writable connection — which is exactly how a real
insurance company would update its own database, not something the mediator can reach into."*

### Turning it on (optional — not required for the demo to be correct)

On the laptop that owns a source, export one more variable before starting the wrapper:

```bash
export INS_ADMIN_URL="mysql+pymysql://root:<pw>@127.0.0.1:3306/insdb"   # an OWNER account, not the read-only one
python -m sources.ins.wrapper
```

The sidebar button on the mediator's GUI will then genuinely renew/expire the policy on that
laptop's live database instead of showing the fallback CLI command. If you don't do this on any
laptop, nothing breaks — the buttons still work exactly as they did before this feature existed,
by showing the equivalent `scripts/mutate_source.py` command to run by hand.

---

## 12. Every real bug found tonight, and what it teaches

Worth knowing these, because a professor probing "did you actually test this, or just claim it
works?" is testing exactly this kind of thing.

**1. `MAPPING_REGISTRY` starts empty on a fresh clone.** It was only ever filled by clicking
"Accept & Persist" in the Schema Matching tab. `scripts/seed_mappings.py` now seeds the validated
set automatically. *Lesson: a demo that depends on a manual GUI step every time is fragile.*

**2. `/schema`'s shape didn't match what the matcher actually reads.** The wrapper returned a list
of tables; `mediator/matcher.py` iterates `tables.items()`, which needs a dict. Fixed by making
every column carry both spellings of every fact (`pk`/`is_pk`, `samples`/`sample_values`) so two
different consumers can each read the shape they expect. *Lesson: a contract has to match its real
consumer, not just the spec someone wrote before seeing the consumer's code.*

**3. PostgreSQL folds unquoted identifiers to lowercase; SQLAlchemy's reflection API doesn't know
that.** `CREATE TABLE OWNERS` really creates a table called `owners`. Loading data crashed
mid-load, silently leaving PostgreSQL with empty tables while `/health` still reported `up: true`
— the crash happened *after* the DDL ran but *before* any row was inserted. *Lesson: `/health`
checking `SELECT 1` proves the database is reachable, not that it has data — a good health check
and a working system are different claims.*

**4. `DROP TABLE` (which a reload does) silently discards `GRANT`s.** Reloading a PostgreSQL/MySQL
source for new data took the wrapper's read-only account's permissions with it. Every real query
then failed with `permission denied` while `/health` still said `up: true` — same failure shape as
bug 3, different root cause. `scripts/load_source.py --grant <role>` now re-applies it automatically.
*Lesson: side effects of "just reload the data" aren't always obvious, and health checks that don't
exercise the actual query path will miss them.*

**5. The GUI's live-update buttons wrote to a file nobody was querying.** `live_update.py` hardcoded
`sources/ins/ins.db` — the local SQLite fallback. It worked when every source really was that local
file (single-laptop rehearsal mode) and silently stopped meaning anything the moment INS moved to
MySQL on another laptop: the write succeeded into an orphaned file, the GUI said "Updated," and the
re-query correctly showed no change. *Lesson: "it worked before" and "it works now" can both be
true if the deployment changed underneath the code — this is exactly why the admin-console rewrite
in §11 talks to the source over HTTP instead of assuming a local file path.*

**6. Demoing UC6 (registering PUC) makes `test_planner_uc2` fail afterwards** — the test asserts
exactly 4 sources are contacted, and a 5th registered source makes that 5. Not a bug; a reminder
that `pytest` and a live demo share the same `meta.db`, so running the suite right after a demo
session will show an "unexpected" failure that isn't one.

---

## 13. Rubric, mapped to exactly what to say and show

| # | Item | Marks | What to say | What to show |
|---|---|---|---|---|
| 1 | Scope of work | 1 | The one-sentence pitch (§1) | Nothing — recite it |
| 2 | Innovation | 1 | Refuses to decide (Rule 1); zero source-specific code in the executor; adds a 5th source with no code change | Kill INS → `UNDETERMINED`; register PUC live |
| 3 | Schema design | 2 | Three DBMS, four date formats, three boolean encodings, four plate spellings, engineered by design | The heterogeneity table (§3), or Tab 3 against two different sources |
| 4 | Data population | 2 | 600+ vehicles, seeded engineered conflicts and gaps (§4) | Tab 1's five/six quick-demo buttons, each a different outcome |
| 5 | Schema matching | 2 | N/C/I hybrid score, θ=0.55, live against real `/schema` | Tab 3: run the matcher, read the heatmap |
| 6 | Decomposition & federation | 2 | One canonical plate → four different SQL strings, dispatched in parallel with a hard deadline | Tab 2: all four SQL expanders open side by side |
| 7 | Communication between sources | 2 | Four laptops, three DBMS engines, HTTP-only, DB never leaves `127.0.0.1` | `configure_cluster.py --probe` → 4/4, real network latencies (76–240ms, not <1ms) |
| 8 | Integration + GUI | 3 | Provenance on every field, conflicts shown not averaged, rule-based explainable decision, Ministry PDF | Tab 1 provenance expander + conflicts on `UP16GH1122`; file a report and download the PDF |

Total: 15. Every row above has been verified running, tonight, on the real four-laptop deployment —
not simulated, not claimed from reading the code.
