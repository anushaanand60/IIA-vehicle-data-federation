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

`plan_query()` in `mediator/planner.py` reads `SOURCE_CATALOG.covers[]` for each source and only
selects sources that can answer the requested attributes. Query scope `UC1` (insurance only)
selects `{REG, INS}` while `UC2` (full profile) selects all catalogued sources (4, or 5 once PUC is
registered). This is visible on stage: switch the Query Scope dropdown in Tab 1 and Tab 2's
"Sources Contacted" count drops from 4 to 2; the Citizen Self-Check tab (§14) contacts 3 and never
CAM, for a different set of attributes again.

**As of the metadata-driven planner refactor, `mediator/planner.py` contains no source id at
all** — a test (`tests/test_planner_metadata.py`) greps the file for `"REG"`/`"INS"`/`"THEFT"`/
`"CAM"` and fails if any appear. The old behaviour ("REG is always included to confirm the vehicle
exists") is now a **metadata property**, not a branch: two attribute flags in `mediator/schema.py`
drive it —

- `derived_from`: an attribute that is *computed*, not stored (`insurance_status` ←
  `insurance_expiry`; `stolen_status` ← `case_status`, `last_incident_date`) is planned by planning
  what it is derived from, so requesting `insurance_status` still reaches INS even if INS's
  `covers` list is edited to drop it.
- `needs_identity_check`: an attribute flagged this way (`insurance_start`, `insurance_expiry`,
  `insurance_status`) additionally selects **every** catalogued source whose
  `SOURCE_CATALOG.identity_authority = 1` — seeded to REG by default, but a plain catalog row a
  demonstrator can flip live with `mediator.catalog.set_identity_authority("REG", False)`, not a
  hard-coded `if s_id == "REG"`. That is how UC1 ("is this vehicle insured?") still confirms REG
  has a matching vehicle at all.

A newly registered source (UC6, PUC) is planned correctly with zero code change, because planning
is just set intersection over catalog metadata — verified live and by test.

### 6.2 Decomposition — one canonical plate, four different queries

`decompose_query(source_id, canonical_plate)` builds SQL **generically from the mapping metadata**,
not from a hardcoded per-source template:

1. Looks up which table holds `plate_number` for this source (via the mapping row where
   `global_attr = "plate_number"`).
2. Normalises the plate the way *that source* stores it — `UPPER(REPLACE(REPLACE(col, '-', ''),
   ' ', '')) = 'DL05CD9876'` — so the same canonical plate matches `DL05CD9876`,
   `DL-05-CD-9876`, and `dl 05 cd 9876` without three separate code paths. For `CAM` specifically
   (the one `authority: OBSERVATIONAL` source), both the column expression and the literal are
   additionally folded through `REPLACE(REPLACE(x,'O','0'),'I','1')` so an OCR-misread capture
   (`MH121J7788` for `MH12IJ7788`) still matches — REG/INS/THEFT keep exact matching, because they
   are not sensor data and should not silently absorb a typo.
3. Emits a **`LEFT JOIN`**, not an inner join, for every `join_path` — a missing owner or insurer
   lookup row no longer hides the whole vehicle from the profile, only that one joined field.
4. Selects an **explicit column list** (every mapped `(table, column)` pair plus the identifier),
   never `SELECT *` — aliased `table__col` for any name that collides across joined tables, so the
   decomposer's own SQL never depends on column *order* matching what the integrator expects.
5. Adds `ORDER BY ... LIMIT 1` when the mapping specifies `aggregate: latest_by:<col>` **and** that
   column's `transform_fn` is not `parse_ddmmyyyy` — INS's `policy_until` is `DD/MM/YYYY` text, and
   reassembling it into a sortable string needs `substr() || substr()`, which MySQL reads as
   logical `OR` rather than concatenation unless a non-default SQL mode is set. Rather than ship an
   ORDER BY that means something different on two engines, the decomposer skips the pushdown for
   that one column and lets the integrator (§7.2) parse the date and pick the latest row in Python;
   THEFT's epoch `reported_date` and CAM's ISO `captured_at` sort identically everywhere, so those
   two keep the SQL-side pushdown.

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

**This is metadata-driven too, not `if s_id == "INS"`.** `_pick_latest_row(rows, mappings)` finds
*that source's own* mapping row whose `aggregate` starts `latest_by:`, reads that column out of
each row (falling back to the decomposer's `table__col` alias when needed), pushes it through that
mapping's own `transform_fn` (so INS parses `DD/MM/YYYY`, CAM parses ISO, THEFT reads an epoch
int), and takes the maximum under a total order that keeps numbers numeric, ISO strings
lexicographic, and any unparseable value lowest — one bad row is ignored, not fatal. A source with
no declared ordering just keeps the first row its wrapper returned. `mediator/integrator.py`
carries no `if s_id == "INS"/"CAM"/"THEFT"` branches any more; the stolen/recovered/case-status
flags it reads for THEFT come from the mapped global attributes, same as everything else.

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

### 7.5 The decision engine — nine ordered rules, first match wins

`evaluate_vehicle_decision()` in `mediator/decide.py`. **Deterministic and rule-based on purpose** —
no ML, no probability, because the design doc explicitly scopes that out for Part A ("Deliberately
deferred: probabilistic truth discovery & uncertainty (Part B) ... ML anomaly detection (only if
time permits — it is an extension, not the core)"). Every rule returns a **decision, a confidence
level, and a plain-English reason** — that `reasons[]` list is the "can we explain and defend the
answer?" property your course cares about.

```
1. A required source is DOWN/TIMEOUT/ERROR → UNDETERMINED, LOW        names the specific source
2. Officially scrapped/shredded            → SCRAPPED — ALERT POLICE / — REGISTRATION VOID, HIGH
3. stolen_status = STOLEN and case OPEN    → STOLEN — ALERT POLICE, HIGH
4. Seen by CAM, absent in REG (REG asked+OK) → UNREGISTERED / SUSPICIOUS, MEDIUM
5. REG vs CAM disagree on ≥2 of make/model/colour → SUSPICIOUS — CLONED PLATE, MEDIUM
6. No REG row, no CAM sighting, no THEFT record, → UNKNOWN VEHICLE — NOT REGISTERED, MEDIUM
   no INS policy (REG asked+OK)                    (new — Task 0.5, onboarding wizard follows)
7. No policy → UNINSURED — REPORT, HIGH. Otherwise, escalation ladder by days lapsed since
   insurance_expiry (new — Task 2.5, mirrors UK Continuous Insurance Enforcement):
     1-15 days  → UNINSURED — ADVISORY, MEDIUM
     16-30 days → UNINSURED — WARNING, HIGH
     >30 days   → UNINSURED — REPORT, HIGH
8. registration_status != ACTIVE           → REGISTRATION INVALID — REPORT, HIGH
9. Otherwise                                → CLEAR, HIGH (all core sources asked+OK and CAM saw
                                               the vehicle; MEDIUM otherwise)
```

**Rule 1 is the most important rule in the whole system** for grading purposes: it is what makes
"refuse to decide when a source is unreachable" real instead of a slide bullet, and it now also
catches a wrapper's own `400`/`500` (`ERROR`), not just `DOWN`/`TIMEOUT` — a malformed response is
"cannot verify," not "no record." Rule ordering matters throughout — rule 2 (scrapped) sits
**above** rules 7/8 (insurance/registration) on purpose: a destroyed vehicle being seen on the road
is a worse finding than its paperwork lapsing, so "this vehicle was destroyed" must outrank "its
paperwork lapsed", or a scrapped car would just report as `UNINSURED`. Rule 6 (unknown vehicle) sits
**below** the stolen/cloned/unregistered rules for the same reason in the other direction: a plate
that *is* on record somewhere, even a bad record, is a stronger and more specific finding than
"nothing on record anywhere we asked."

**A decision-honesty hardening pass (ported from a sibling review) rewrote every rule's
*evidence*, not its verdicts.** Before that pass, an *unasked* source's status silently defaulted
to `"NONE"`/`"NOT_REPORTED"`, which reads identically to "we checked and found nothing" — a UC1
(insurance-only) query would answer `CLEAR` as if THEFT and CAM had agreed, when neither was even
asked. Now: `insurance_status`/`stolen_status` are computed as `None` unless that source was in
`requested_sources` at all; `DOWN`/`TIMEOUT`/`ERROR` map to `"UNKNOWN"` (a failed answer, not an
absence); `CLEAR` is only `HIGH` confidence when every core source was asked and answered `OK` and
CAM actually saw the vehicle; and every rule's reason text ends with
`"Sources not checked in this query: X, Y."` for anything skipped by the planner. This is the
single most defensible line in the whole decision engine if a professor asks "how do you know an
absence really means nothing exists, rather than nobody looked?"

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

**File:** `app/app.py`, Streamlit — now **9 tabs** plus a sidebar (up from 5 at the first demo —
see §17 for exactly what changed and why), and only ~120 lines itself: it
is theme injection, the masthead, the sidebar call, and nine `with tab: render()` calls. The shell
order is the order the demo is narrated in — *Investigate · Challan Guard · Watchlist · Reports ·
Citizen Check · Plan Trace · Catalog · Matcher · SQL Console* — the two things an operator actually
does first, the machinery that proves how it was done last; the subsections below are grouped by
what each tab is for rather than by that order (Challan Guard has §18 to itself). Every
tab's actual body lives in its own `app/tabs/*.py` module, built from shared render helpers in
`app/components.py` (`decision_banner`, `source_chips`, `profile_sections`, `conflict_panel`,
`provenance_table`, `risk_gauge`, `alert_banner`) and one injected stylesheet from `app/theme.py`
(AirSentinel-derived tokens; every status/decision carries an explicit colour token *and* an
icon/word, never colour alone, so a colour-blind grader or a black-and-white printout still reads
the verdict).

### Sidebar — Source Editor

Replaced the old fixed five-button "Live Source Data Mutator." `app/tabs/source_editor.py::
render_sidebar()` asks each wrapper's own `GET /admin/actions` what it can do — **no action list is
hard-coded in the GUI** — and builds source → action → plate → per-action-parameter inputs from
that answer live. Apply calls `POST /admin/mutate` on the owning wrapper over HTTP, using a
**second, separate database connection** from the one `/query` uses; `/query`'s connection stays
read-only regardless of whether admin mode is on anywhere — see §11. A wrapper with
`<SOURCE_ID>_ADMIN=off` shows an honest info message instead of a dead form; an unreachable wrapper
shows a DOWN warning, never a traceback. The equivalent `scripts/mutate_source.py` command line is
always shown alongside, as the CLI fallback for the laptop that actually owns the source.

### Tab 1 — Investigate

The main query interface. Demo buttons pre-fill a **dirty** version of each story plate (e.g.
`DL-05-cd-9876`, `hr 26 ef 4455`) to demonstrate plate-format normalisation live, not just the
clean canonical form. A free-text field accepts any plate in any format, and an upload/paste slot
above it feeds a plate photo through OCR (§13). A **Query Scope** dropdown toggles between `UC1`
(insurance-only, 2 sources) and `UC2` (full profile, all catalogued sources) — this is live
evidence of source minimisation (§6.1). Below the query: source status chips including a muted
"NOT ASKED" chip for every catalogued source the planner skipped (so the plan is legible on
screen, not only on Tab 2), the decision banner with confidence and reasons, an onboarding wizard
when the decision is `UNKNOWN VEHICLE — NOT REGISTERED` (§14), the profile in three-to-four
columns, detected conflicts, per-attribute provenance (expander), a risk gauge and alert banner
(§15), a watchlist add/remove toggle, and a "File Report to Ministry" button producing a
downloadable evidence-bundle PDF (§16) on the spot.

### Tab 2 — Plan Trace

Shows, per query: the canonical plate, how many sources were contacted and which (as chip badges,
including any skipped source), total elapsed time, and — per source, in an expander — **the exact
SQL sent**, its status, row count and individual latency as a bar chart. This is the strongest
evidence for rubric item 6.

### Tab 3 — Matcher & Heatmap

Pick a live source, run the matcher against its **real, live `/schema`** response (not a cached
copy), and see the N/C/I-scored correspondence table plus a Plotly similarity-matrix heatmap. This
is rubric item 5, live. Graded numbers (§5.6): precision/recall 1.00/1.00 on REG, INS, THEFT, CAM;
1.00/1.00 overall — 23 of 23 correspondences across all five sources, after a one-line domain boost for PUC's `valid_upto` → `puc_expiry`.

### Tab 4 — Catalog & Registry

Two read-only tables — `SOURCE_CATALOG` (which sources exist, their trust/authority/timeout/
`identity_authority` flag) and `MAPPING_REGISTRY` (all 36 validated mappings across REG, INS,
THEFT, CAM, PUC, with their transform functions, join paths and aggregates) — plus a live form:
**"Register New Source (UC6)"**, which registers a fifth source (PUC, pollution certificates) with
zero mediator code changes. This is the extensibility use case and it is real: registering PUC
live and re-querying contacts 5 sources instead of 4, with `puc_expiry` populated in the profile.

### Tab 5 — Ministry Reports

Every filed report, its decision, the rule that fired, its risk score, its reasons, and an
evidence-bundle PDF download (§16) — the audit trail from §7.6, browsable.

### Tab 6 — SQL Console

Type raw SQL against any one source's own database, in that source's own dialect. Reads go through
the wrapper's read-only `POST /query`; writes go through `POST /admin/sql` (a guarded single
`INSERT`/`UPDATE`/`DELETE` against a whitelisted table, no DDL — the free-SQL sibling of
`/admin/mutate`'s fixed action menu), present only on a laptop that has not set
`<SOURCE_ID>_ADMIN=off`. Keeps the last 10 statements per session with one-click re-run, and after
a successful write shows the touched table's row count before and after plus a "re-run in
Investigate" button if a plate literal is recognised in the statement. This is the tab where "live
write on another laptop, re-query, decision changes" (see `docs/demo_script.md`) actually happens on
stage.

### Tab 7 — Watchlist & Alerts

Add or remove a plate from the watchlist, and browse the alert log — see §15.

### Tab 8 — Citizen Self-Check

The askMID analogue — see §14.

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
small FastAPI wrapper with six endpoints: `GET /health`, `GET /schema`, `POST /query` (read-only,
what the mediator uses), and three admin doors — `GET /admin/actions`, `POST /admin/mutate`,
`POST /admin/sql` — that the mediator's own code never calls. Nobody outside that laptop can open a
raw connection to the database; they can only ask the wrapper a question through those doors.

**"The mediator's query path is read-only."** `POST /query`'s SQL guard (`guard_sql()` in
`sources/wrapper_template.py`) rejects anything that isn't a single `SELECT`: it blocks `;`
(multiple statements), SQL comments, and a long list of forbidden keywords (`INSERT`, `UPDATE`,
`DELETE`, `DROP`, `GRANT`, ... — 30+ keywords). Beyond the textual guard, the **database connection
itself is opened read-only** at the session level (`default_transaction_read_only=on` for
Postgres, `SET SESSION TRANSACTION READ ONLY` for MySQL, `PRAGMA query_only = ON` for SQLite) — so
even a guard bug can't actually write, because the database itself refuses.

**So how does the GUI update data at all, without breaking that?** This is the interesting design
answer, not a contradiction. `POST /admin/mutate` and `POST /admin/sql` are **entirely separate**
endpoints on each wrapper, backed by a **second, separate database connection** — not the
read-only one `/query` uses. They:

- Are **on by default** on every wrapper as of the write-everywhere overhaul — a wrapper started
  with just `<SOURCE_ID>_DB_URL` derives its own writable admin connection automatically (strip a
  SQLite read-only URI to a plain file path; reuse the Postgres/MySQL URL as-is). Setting
  `<SOURCE_ID>_ADMIN=off` before starting that one wrapper is the only way back to the original
  read-only server (`404` on all three admin routes) — the earlier design (admin off unless a
  laptop opted in with `<SOURCE_ID>_ADMIN_URL`) was flipped once "every laptop's writes must be
  demoable without extra setup" (rubric R1) turned out to matter more than "opt-in by default."
- `POST /admin/mutate` accepts **only a fixed, named menu of actions** per source (`register`,
  `renew`, `expire`, `add_policy`, `steal`, `clear`, `shred`, `sight`, `issue`, `revoke`, …,
  defined in `ADMIN_ACTIONS`), each a short list of parameterised SQL statements in one
  transaction — never arbitrary SQL, and `GET /admin/actions` lists exactly what a given wrapper
  supports (which is also how the GUI's Source Editor sidebar builds its own form with no
  hard-coded action list).
- `POST /admin/sql` is the free-SQL sibling for the SQL Console tab: it accepts a single
  `INSERT`/`UPDATE`/`DELETE` against a whitelisted table, no DDL, no multiple statements — the same
  guard discipline as `/query`'s guard, just permitting three more statement kinds and requiring
  the writable connection.
- Neither ever touches the connection `/query` uses, so the read-only guarantee that matters for
  the **mediator's own query path** — the thing the professor is actually asking about when they
  ask "is this read-only?" — is completely unaffected by any of this.

The honest framing, if asked: *"The mediator itself never writes — it only ever issues SELECT, and
we enforce that at three layers: a SQL guard, a keyword blocklist, and a read-only database
session. What you're seeing update live is each agency's own operator console, reached through its
own separate endpoints with their own separate, writable connection — which is exactly how a real
insurance company would update its own database, not something the mediator can reach into. Admin
writes are on by default now so every laptop can demonstrate this without extra setup; a laptop can
opt back out with one environment variable, `<SOURCE_ID>_ADMIN=off`, without touching the mediator
at all."* See the FAQ in `docs/report.md` §6 for the same answer in one paragraph.

### Turning it off, if you want a strictly read-only laptop for a demo

```bash
export INS_ADMIN=off
python scripts/serve.py INS       # or: python -m sources.ins.wrapper
```

`/admin/actions`, `/admin/mutate` and `/admin/sql` all return `404` on that laptop; `/query` is
completely unaffected either way, since it was never the connection admin uses.

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

## 13. Plate photo → OCR → lookup

**File:** `mediator/plate_ocr.py`, GUI slot `app/tabs/ocr_upload.py`. Mirrors an ANPR camera's own
job (`docs/FIELD_RESEARCH.md` facts 1 and 4): read a plate off a photo, correct the reading, look
it up against the registration/insurance authorities before anything is issued.

Split deliberately into a pure half and a model half, so the defensible part needs no model at
all: `canonical(text)` (uppercase alphanumerics) and `normalise_candidates(text)` are plain
functions with no EasyOCR dependency; `read_plate(image_bytes)` is the only part that touches the
model, and a missing/broken `easyocr` install raises a clean `OCRUnavailable` with the
`pip install easyocr` line — never an `ImportError` at import time, so the rest of the app is
unaffected if OCR was never set up on a given laptop.

**The confusion repair is position-aware, not a blind substitution table.** The Indian plate
pattern `^[A-Z]{2}\d{2}[A-Z]{1,3}\d{4}$` says which index of a plate wants a letter and which wants
a digit, so each confusion pair (O↔0, I↔1, B↔8, S↔5, Z↔2, G↔6) is only applied in the direction
that position allows — letter→digit at a digit position, digit→letter at a letter position.
Blind, both-directions substitution would invent plates the pattern forbids and explode the
candidate list combinatorially; position-awareness is what keeps `DLO1AB1Z34` → `DL01AB1234` as
essentially the only sane candidate instead of one of a few hundred. Verified against the *real*
model, not just synthetic images: a PIL-rendered `DL01AB1234` plate is actually read back as
`DLOIAB1234` (both confusions firing at once) by EasyOCR, and that failing assertion is what forced
the repair step to exist rather than being a guess about what OCR gets wrong.

**GUI behaviour:** upload or paste a photo → spinner → the top candidate becomes a clickable button
labelled with its confidence (`DL01AB1234 (62%)`), with "did you mean" variants of the top guess as
further buttons; a click sets the search box and re-runs the query. Never raises on a bad image or
a missing model — degrades to `st.warning` with the install/pre-download instructions.

---

## 14. Unknown-plate onboarding and the citizen self-check

**Onboarding (Task 0.5).** Type any plate that matches no source at all, and the decision comes
back `UNKNOWN VEHICLE — NOT REGISTERED` (Rule 6, §7.5) — REG was asked and answered `OK`, found no
row, and neither did CAM, THEFT, or INS; absence is only evidence because every one of those
sources actually answered. `app/tabs/onboarding.py::render_unknown_plate()` then renders a 4-step
wizard right under the decision banner: **Register** (posts to REG's `/admin/mutate` `register`
action), **Insure** (INS's `add_policy`, optional), **PUC** (issue a certificate, optional, only
shown when PUC is in the catalog), **Re-evaluate** (re-runs the query). Each step shows the exact
`detail` string the wrapper's own action returned — the same "show the SQL/effect, not just a
success toast" discipline as the SQL Console and Source Editor. This is the live version of India's
Supreme Court mandate (FIELD_RESEARCH fact 1): a plate with no record gets registered and insured
through the same real-time pipeline that later checks it, not a separate offline process.

**Citizen self-check (Task 2.4), the askMID analogue** (FIELD_RESEARCH fact 5 — UK's MID lets a
citizen self-check their own vehicle for free). `app/tabs/self_check.py` requests exactly five
attributes — `registration_status`, `insurance_status`, `insurance_expiry`, `puc_expiry`,
`stolen_status` — through the same `run_global_query()` the Investigate tab uses. **Nothing here is
special-cased for privacy**; the planner's own attribute-driven source selection (§6.1) is what
keeps CAM off the plan, because none of those five attributes are in CAM's `covers`, and
`insurance_status`/`insurance_expiry` being `needs_identity_check` is what still brings REG in. The
tab renders four `st.metric` tiles and never shows `owner_name`, never a camera field, never a raw
row — and a caption at the bottom names exactly which sources were asked and which were not
("contacted 3 of 4 sources"), so the minimisation claim is checkable on screen, not asserted.

---

## 15. Watchlist, alerts, and the explainable risk score

**Files:** `mediator/watchlist.py`, `mediator/risk.py`, tab `app/tabs/watchlist.py`. Mirrors the UK
MIB's **Operation Tutelage/Scalis** (FIELD_RESEARCH fact 7 — live ANPR sightings compared against
the policy database, a persistent mismatch flags the vehicle to patrols) and India's IIB
uninsured-vehicle ANPR flags (facts 1, 3).

**Watchlist.** `add`/`remove`/`listing`/`is_watched`/`check(profile)` — every plate canonicalised
through the same `norm_plate()` the rest of the system uses, so `dl-05 cd 9876` and `DL05CD9876`
are one marker regardless of which tab or source spelling put it there. `check(profile)`, called
from `mediator/core.py` after every integration, files a `{plate, reason, seen_at, location,
decision, ts}` alert carrying whatever camera evidence that particular query returned — and,
separately, raises a "Hotlist hit" alert for any STOLEN/SCRAPPED/SHREDDED decision even when nobody
marked that plate, because the standing rule catching it is itself worth logging.

**Risk score.** `score(profile) -> RiskScore(value 0-100, factors[])`, deliberately arithmetic, not
learned: **CLAUDE.md's own invariant is "no ML, no probabilistic fusion" for Part A**, and this
follows it to the letter. Base points come from the decision itself (STOLEN/SCRAPPED/SHREDDED 90
down to CLEAR 5), then up to five modifiers that can only ever *raise* the score: conflicts
(+5 each, capped), a policy expiring within 30 days (+10, only counted before expiry so it never
double-counts an already-`UNINSURED` verdict), no camera sighting in 60 days (+5, **only when CAM
was actually asked** — a source never asked still makes no claim, the same discipline as §7.5's
decision honesty pass, carried into the score), a down core source (+10), and low mean source trust
(+5). The clamp to 0-100 is itself emitted as a factor, so `sum(factors) == value` always holds and
the risk gauge reads as visible arithmetic, not an opaque index a professor has to take on faith.
`LEVELS`: LOW <25, MEDIUM <55, HIGH <80, CRITICAL ≥80.

**GUI:** the risk gauge and the signed factor list sit under the decision banner on Investigate; a
watchlist add/remove toggle sits in the actions row; Tab 7 (Watchlist & Alerts) lists watched
plates and the last 50 alerts.

---

## 16. Ministry evidence bundle, and the query audit log

**Evidence bundle (Task 2.2), shipped.** `mediator/report.py::build_story(report_row)` is the
single source of truth for what a filed report says — one fact per line, pure text — so the PDF
generator and the tests can never disagree about content. Beyond the decision and reasons, the
bundle now carries: the **rule that fired** (`rule_fired()`, an ordered longest-match table over
the decision string, so an unseen future decision degrades to "Unmapped decision rule" instead of
raising); the **risk score and its factors**; the **per-attribute provenance table** (source,
authority, trust, fetched-at); **every source's exact SQL and status**
(`plan_trace["sqls"]`/`["sources_detail"]`); **camera evidence** when CAM answered (camera id,
location, timestamp, OCR confidence); **watchlist alerts**; and a footer with the mediator's own
short **git commit hash** (`git rev-parse --short HEAD`, cached, "unknown" off-git) and the
generation timestamp. A status-shaped field that is `None` because its source was never asked now
prints `"not checked"` — never a fabricated `"NOT_REPORTED"` — the report-level fix for the same
absence-vs-failure bug that §7.5 fixed at the decision level.

**Query audit log (Task 2.6), shipped.** `mediator/catalog.py::init_meta_db` adds `QUERY_LOG(id,
ts, plate, requested_attrs, sources_asked, statuses, decision, confidence, elapsed_ms)` plus
`log_query(row)` / `get_query_log(limit=100, plate=None)`. `mediator/core.py::run_global_query`
calls `log_query(...)` once, right after the decision is annotated onto the profile, wrapped in the
same degrade-don't-crash `try/except` as the risk/watchlist step (§16) — a broken `meta.db` prints
to stderr and the query still returns normally. This is **decisions and traces only, never a copy
of source rows** — exactly the same "audit the decision, not the data" boundary `REPORT_LOG`
already drew for filed reports, just for *every* query answered rather than only the ones an
operator chose to file. It mirrors the regulated-access logging real hubs like India's IIB or the
UK's MID run behind their own APIs. Tab 5 (Ministry Reports) renders it as a second section below
the `REPORT_LOG` listing — a plate filter and a dataframe of `ts`/`plate`/`decision`/`confidence`/
`sources_asked`/`statuses`/`elapsed_ms` — captioned "Decisions and traces only — no source rows are
ever stored (virtual integration)," which is the one-sentence answer if a professor asks whether
logging every query quietly reintroduces the warehouse this project argues against (§2): no, the
row is the *verdict*, not the *evidence*, and the evidence is still fetched live on the next query.

---

## 17. What changed since the first demo, and why — mapped to the professor's review points

The overhaul plan (`docs/superpowers/plans/2026-09-16-overhaul.md`) opens with three review points
from the first class demo. Each is a real gap this round of work closes, not a cosmetic pass:

**R1 — "Writes typed in the GUI/shell must actually change the relation on the owning laptop and
show in the mediator."** Before: the sidebar's five buttons wrote through `live_update.py` to a
hard-coded local SQLite file (`sources/ins/ins.db`), which silently stopped meaning anything the
moment a source moved to its real MySQL/PostgreSQL laptop (bug #5 in §12) — the button said
"Updated," the file changed, and the mediator's next query correctly showed no change, because
nobody was querying that file. Now every write, from any surface — the Source Editor sidebar, the
SQL Console, `scripts/mutate_source.py`, or the onboarding wizard — goes through that source's own
`/admin/mutate` or `/admin/sql`, over HTTP, into the same database the wrapper actually serves, on
whichever DBMS that laptop runs. `docs/demo_script.md`'s live-write and re-query step is built
around proving exactly this: write on one laptop's own console, re-query on the mediator laptop,
watch the decision change.

**R2 — "Unknown plate must be handleable live (enter, check, register, insure, re-evaluate)."**
Before: a plate in no source just came back with mostly-`None` fields and whatever the old rule
ordering happened to produce — there was no path from "nothing on record" to "now it's clean."
Now: a dedicated decision (`UNKNOWN VEHICLE — NOT REGISTERED`, Rule 6, §7.5) triggers a 4-step
onboarding wizard (§15) that registers, insures, optionally certifies, and re-evaluates the exact
plate the citizen typed — end to end, in the GUI, with no shell access needed.

**R3 — "Functionality looked hard-coded / static."** Three concrete fixes, not a rebrand: (1) the
**planner** (§6.1) no longer branches on a source id anywhere — a test greps the file for
`"REG"`/`"INS"`/`"THEFT"`/`"CAM"` and fails if one appears; source selection and "which sources
does an insurance-only question need" are both catalog metadata now (`derived_from`,
`needs_identity_check`, `identity_authority`), so a demonstrator can flip which source authenticates
identity live and watch the plan change. (2) the **integrator's** latest-wins picked a row via
`if s_id == "INS"`/`"CAM"`/`"THEFT"` before; it now reads the mapping's own `aggregate:
latest_by:<col>` and `transform_fn`, so a newly matched source's "which row is newest" logic is a
registry row, not a code change. (3) the whole GUI grew from static demo buttons plus a fixed
sidebar into live, queryable surfaces: the SQL Console runs real queries and writes against real
databases in real dialects; the Source Editor's action list comes from each wrapper's own
`/admin/actions`, not a hard-coded menu; and the onboarding wizard, OCR upload, and watchlist/risk
score all respond to whatever is actually typed or photographed, not a fixed demo script.

---

## 18. Challan Guard — verify before you fine

**The pain point, with sources (`docs/FIELD_RESEARCH.md` facts 12–15).** The Supreme Court's
2025–26 order to auto-challan uninsured vehicles multiplies the volume of ANPR-triggered fines, and
those fines are already wrong often enough that Delhi Traffic Police run an Online Challan Dispute
System; guides to contesting them attribute roughly **90% of wrongful challans to plate misreads**
(O/0, 8/B, 1/I, 5/S), Hyderabad police busted a **cloned-plate racket** in 2025 where duplicate
plates pushed fines onto the original owner, and NCRB's Vahan Samanvay lags on stolen-then-recovered
updates. **The workflow.** Challan Guard sits between the camera and the fine: an ANPR sighting
enters `CHALLAN_CASES` in the mediator's own `meta.db` as a *candidate* — a plate as read, a camera,
a time, an observed make/colour and an OCR confidence — and nothing further happens until an
operator presses **Verify (live)**, which runs six ordered steps, each a real federated query and
each recorded as an event: (1) **sources reachable** — if REG, INS or THEFT is not `OK`, verdict
**HOLD**, `"<SRC> unreachable — refusing to fine on partial evidence"`, and stop; (2) **identity** —
`mediator/plate_resolve.py` generates one-character substitutions from a fixed confusable table,
keeps only candidates that are actually registered, and scores them `+0.5` make match, `+0.3` colour
match, `+0.2` for the literal read, resolving only when the best clears 0.5 and leads the runner-up
by 0.2 (deterministic and explainable in a dispute — no fuzzy matching); (3) **clone signal** —
`mediator/travel_check.py` builds legs between this sighting and the camera network's other
sightings of the resolved plate and flags any leg above 160 km/h as impossible travel, the signal UK
patent GB2448780A describes; (4) **theft** — a theft reported *before* the sighting rejects the
fine and routes to police, deliberately checked before insurance; (5) **registration status**; and
only then (6) **insurance on the date of the sighting**, not today. The verdict is ISSUE (₹2,000,
₹4,000 on repeat, MV Act §196), REJECT or HOLD, and the case carries an evidence bundle of every
source's rows, statuses and fetch times. A citizen then disputes from the Citizen Check tab; the
dispute **re-runs the identical verification live** and cancels the challan if a fact has moved,
naming the diff (`"record changed since issue: insurance_expiry was none now 2027-01-01"`).
**Why federation makes it possible, and a warehouse would not.** Both decisions — issuing and
overturning — are made against the agencies' current records at the moment they are made: the fine
is checked at fine time, the dispute is checked at dispute time, and a teammate adding a policy on
the insurance laptop changes the next verdict with no sync step, no ETL and no cache to invalidate.
A warehouse would answer both questions from a copy whose age nobody at the desk can see, and would
have no way to distinguish "the insurer says no policy" from "we could not reach the insurer" —
which is exactly the distinction the guard turns into a refusal instead of a fine. That refusal, not
the fine, is the feature: the tab's headline counter is **"wrongful fines prevented"**.

---

## 19. The "Visibility" design system — why the GUI looks like one product

The GUI is not styled tab by tab: one token block in `app/theme.py` (`TOKENS` + `FONTS`, injected
once as `--vz-*` CSS custom properties, with a `prefers-color-scheme: dark` override) and one
primitive vocabulary in `app/components.py` — `section`, `group_label`, `kpi_row`, `styled_table`,
`stepper`, `chip`, `card` — are what all nine tabs are composed from, so a change to a rule lands
everywhere at once. The rules are stated and then **enforced by tests**, which is the part worth
saying out loud: exactly one logical block per real display heading with a beacon bar, and
`tests/test_tabs_render.py::test_no_tab_uses_a_markdown_pseudo_heading` fails the build if any tab
reintroduces `st.subheader` or a `###` markdown pseudo-heading or a caption used as a heading;
exactly **one accent** (`#E4572E`), which means "interactive" and nothing else, so status and
decision colours are reserved as *data* colours and are never borrowed for chrome; every coloured
chip picks its ink by **WCAG relative luminance** (`readable_ink` / `readable_fill` in
`components.py`, lifting a fill toward white in 5% steps until a label clears 4.5:1), because the
predecessor's light-on-light banners were unreadable on a projector; colour is never the only
carrier of meaning — the status *word* is always printed beside it (WCAG 1.4.1); numbers are mono
with tabular numerals so a latency column lines up; and nothing on the page is below 0.8rem, pinned
by `tests/test_theme.py`. `styled_table` renders small result sets as escaped HTML rather than
`st.dataframe` so the table can follow the tokens into dark mode, and `stepper` is what draws
Challan Guard's verification run — the same primitive, so a verification reads like the rest of the
product rather than like a debug dump.

---

## 20. Rubric, mapped to exactly what to say and show

| # | Item | Marks | What to say | What to show |
|---|---|---|---|---|
| 1 | Scope of work | 1 | The one-sentence pitch (§1) | Nothing — recite it |
| 2 | Innovation | 1 | Refuses to decide (Rule 1); zero source-specific code in the planner/executor; adds a 5th source with no code change; live agency-side writes; OCR-to-decision; watchlist/risk mirroring real ANPR enforcement (§13-16) | Kill INS → `UNDETERMINED`; register PUC live; write in SQL Console → re-query flips the decision; upload a plate photo |
| 3 | Schema design | 2 | Three DBMS, four date formats, three boolean encodings, four plate spellings, engineered by design | The heterogeneity table (§3), or Tab 3 against two different sources |
| 4 | Data population | 2 | 600+ vehicles, seeded engineered conflicts and gaps (§4), oracle-graded 621/621 | Tab 1's demo buttons, each a different outcome |
| 5 | Schema matching | 2 | N/C/I hybrid score, θ=0.55, live against real `/schema`, precision/recall 1.00/1.00 on 4 of 5 sources | Tab 3: run the matcher, read the heatmap |
| 6 | Decomposition & federation | 2 | One canonical plate → SQL per source, `LEFT JOIN`, explicit columns, dispatched in parallel with a hard deadline | Tab 2: SQL expanders open side by side |
| 7 | Communication between sources | 2 | Four-to-five laptops, three DBMS engines, HTTP-only, DB never leaves `127.0.0.1`; writes go through each wrapper's own `/admin/*`, never the mediator | `configure_cluster.py --probe` → all UP; SQL Console write on a remote laptop, re-query on laptop 1 |
| 8 | Integration + GUI | 3 | Provenance on every field, conflicts shown not averaged, honest absence-vs-failure (§7.5), rule-based explainable decision, risk score, Ministry evidence-bundle PDF | Tab 1 provenance expander + conflicts on `UP16GH1122`; risk gauge on `HR26EF4455`; file a report and download the PDF |

Total: 15. Every numbered claim above traces to a passing test or a script that was actually run —
`python -m pytest -q`, `scripts/evaluate_ground_truth.py`, `scripts/evaluate_matcher.py` — not
simulated, not claimed from reading the code alone.
