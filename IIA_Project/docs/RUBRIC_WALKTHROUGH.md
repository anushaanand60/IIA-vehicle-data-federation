# Rubric walkthrough — Project A

The evaluation, criterion by criterion, in marking order. Each step says what to open, what to run,
what the evaluator should see, and the one sentence that claims the marks. About 20 minutes total.

Bring-up first: `docs/DEMO_DAY.md`. Detailed clicks for every feature: `docs/SHOW_EACH_FEATURE.md`.

**Who does what.** Laptop 1 drives the website and the terminal. Laptops 2–4 keep their wrapper
terminal visible and run the live-change commands when called.

**Numbers quoted here were measured on 17 Sep 2026.**

| Rubric | Marks | Time | Main evidence |
|---|---|---|---|
| 1. Scope of work | 1 | 1 min | problem, sources, decisions, out of scope |
| 2. Innovation | 1 | 3 min | Challan Guard, live |
| 3. Schema design | 2 | 2 min | 5 schemas, 3 engines, deliberate heterogeneity |
| 4. Populating data | 2 | 2 min | 3,170 rows, same CSVs on every laptop, `--verify` |
| 5. Matching / APIs | 2 | 3 min | matcher 23/23, wrapper API |
| 6. Decomposition and federation | 2 | 3 min | Plan Trace, one query becomes five dialect-specific SQLs |
| 7. Communication between sources | 2 | 3 min | `--probe`, cross-laptop write, refusal on a dead source |
| 8. Integration and GUI | 3 | 4 min | the five stories, provenance, conflicts, 621/621 |

---

## 1. Define the scope of work — 1 mark

**Say it.** "Four agencies hold different facts about the same vehicle and share no keys, formats or
database engine. Given a number plate, we decide live whether the vehicle is uninsured, stolen,
cloned or unregistered, and we refuse to decide when an agency cannot be reached. Nothing is copied
into a central store."

**Show it.** Open `docs/report.md` §0 and the website's sidebar.

| In scope | Out of scope, on purpose |
|---|---|
| 4 autonomous sources + 1 added live (PUC) | a central warehouse or any copy of source data |
| 3 DBMS engines on separate laptops | machine learning or probabilistic fusion |
| global schema, mappings, federated queries | payment or legal enforcement of fines |
| decision rules, conflict detection, refusal | authentication on the admin API |
| GUI, reports, audit log, live writes | |

**Claim.** "The scope is a virtual, mediator-based integration system with a defensible decision
at the end, not just a joined table."

---

## 2. What is new or innovative — 1 mark

**Say it.** "Camera e-challans are frequently wrong: misread characters, cloned plates, stolen cars
fined to the victim. The Supreme Court's 2025–26 order to auto-challan uninsured vehicles makes
that worse. Challan Guard is a verify-before-fine workflow that uses our live federation at the
moment of the fine and again at the moment of a dispute."

Sources are listed in `docs/FIELD_RESEARCH.md`.

**Show it.** Sidebar → **Challan Guard**.

1. Pick case 1, `DL05CD9B76`, press **Verify (live)**. The stepper shows the misread corrected to
   `DL05CD9876` and a Rs 2000 challan issued to the right owner.
2. Pick case 2, `DLO1AB1234`, verify. REJECTED: that vehicle is insured. A wrongful fine prevented.
3. Pick case 3, `UP16GH1122`, verify. HOLD: Gurgaon to Agra in 30 minutes at 340 km/h. Cloned plate.
4. Stop the INS wrapper on laptop 2, verify case 1 again. HOLD: INS unreachable, no fine on half
   the evidence. Restart INS.

**Other innovations, one line each if asked.** Refusal instead of guessing when a source is down.
OCR-tolerant plate matching. Live writes through each agency's own API. Evidence-bundle PDF.
Watchlist with alerts. Citizen self-check that hides personal data. Query audit log.

**Claim.** "The innovation is not a new screen. It is a workflow that only works because the
integration is live."

---

## 3. Database schema design — 2 marks

**Say it.** "Five schemas, designed as if by different agencies, on three engines. Every
heterogeneity is deliberate, and each one is absorbed by a named mechanism in the mediator."

**Show the files.** `sources/reg/schema.sql`, `sources/ins/schema.sql`, `sources/theft/schema.sql`,
`sources/cam/schema.sql`, `sources/puc/schema.sql`.

| Source | Engine | Tables | Plate column and format |
|---|---|---|---|
| REG — transport office | PostgreSQL | `OWNERS`, `VEHICLE_REGISTRATION` | `registration_no` = `DL01AB1234` |
| INS — insurer | MySQL | `INSURERS`, `POLICY_RECORDS` | `vehicle_reg` = `DL-01-AB-1234` |
| THEFT — police | SQLite | `CRIME_RECORDS` | `vehicle_number` = `dl 01 ab 1234` |
| CAM — cameras | PostgreSQL | `CAMERAS`, `PLATE_CAPTURES` | `plate_id` = OCR output, e.g. `DLO1AB1234` |
| PUC — pollution | SQLite | `POLLUTION_CERT` | `regn_number` |

**Show the heterogeneity.** Open `docs/heterogeneity_table.md`. Point at three rows:

- **Dates** in four encodings: ISO in REG, `dd/mm/yyyy` text in INS, Unix epoch integers in THEFT,
  ISO timestamps in CAM.
- **Booleans** in three encodings: `1/0` in INS, `'Y'/'N'` in THEFT, status words in REG.
- **Structure**: REG normalises owners into a separate table; nothing else does.

**Show it on the real engines.** On laptop 2:

```powershell
mysql -u root -p insdb -e "DESCRIBE POLICY_RECORDS;"
```

On laptop 1 or 4:

```powershell
psql -U postgres -d regdb -c "\d vehicle_registration"
```

Or, from laptop 1, the schema each agency publishes through its API:

```powershell
(Invoke-RestMethod http://<laptop2-ip>:8002/schema).tables | Select-Object table
```

**Claim.** "The schemas are autonomous by design. Nothing in them was chosen to make integration
easy."

---

## 4. Populating the data in these tables — 2 marks

**Say it.** "Synthetic but realistic data from seeded generators, with hand-planted story vehicles,
committed as CSV so every laptop loads byte-identical data into its own engine."

**Show the pipeline.** Generators: `reg.py`, `ins.py`, `theft.py`, `cam.py`, `puc.py`. Story
vehicles: `data/inject_demo_fixtures.py`. Loader: `scripts/load_source.py`.

**Show the counts.**

| Table | Rows |
|---|---|
| REG `OWNERS` | 605 |
| REG `VEHICLE_REGISTRATION` | 605 |
| INS `INSURERS` | 5 |
| INS `POLICY_RECORDS` | 564 |
| THEFT `CRIME_RECORDS` | 42 |
| CAM `CAMERAS` | 7 |
| CAM `PLATE_CAPTURES` | 918 |
| PUC `POLLUTION_CERT` | 424 |

**Run it.** On laptop 2, loading into real MySQL and checking the story plates:

```powershell
python scripts\load_source.py INS --url "mysql+pymysql://root:<pw>@127.0.0.1:3306/insdb" --verify
```

`--verify` prints the row count for each story plate. `MH12IJ7788` must be absent from REG: it is
the deliberately unregistered vehicle.

**Point at the realism.** Many policies per vehicle, camera plates with OCR noise and confidence
scores, recovered and scrapped vehicles in the police records, cameras with real Delhi-NCR
coordinates.

**Claim.** "The data exercises every decision path, and the same plates exist on every laptop."

---

## 5. Schema matching and APIs for data fetching — 2 marks

This project does two of the listed options: a schema matcher, and an API per source.

### 5a. Schema matcher

**Say it.** "The matcher reads each agency's published schema and sample values, and proposes which
column is which global attribute. It combines name similarity, a domain thesaurus and value
patterns."

**Show it.** Sidebar → **Matcher** → pick INS. Point at `vehicle_reg → plate_number` found from the
values, not the name.

**Run the evaluation.**

```powershell
python scripts\evaluate_matcher.py
```

```
source    tp  fp  fn  precision  recall     f1
REG        7   0   0       1.00    1.00   1.00
INS        5   0   0       1.00    1.00   1.00
THEFT      3   0   0       1.00    1.00   1.00
CAM        6   0   0       1.00    1.00   1.00
PUC        2   0   0       1.00    1.00   1.00
OVERALL   23   0   0       1.00    1.00   1.00
```

**Show the approved mappings.** Sidebar → **Catalog** → *GAV mapping rules*: 38 rules, each with its
transform and join path.

### 5b. An API per source

**Say it.** "Each agency publishes an API, never its database. Same contract on all five."

```powershell
Invoke-RestMethod http://<laptop2-ip>:8002/health
(Invoke-RestMethod http://<laptop2-ip>:8002/schema).tables.columns | Select-Object -First 5 name, type
$q = @{ sql = "SELECT vehicle_reg, policy_until FROM POLICY_RECORDS WHERE vehicle_reg = 'DL-05-CD-9876'" } | ConvertTo-Json
(Invoke-RestMethod -Method Post -Uri http://<laptop2-ip>:8002/query -ContentType application/json -Body $q).rows
```

**Show the guard.** The read API refuses writes:

```powershell
$bad = @{ sql = "DELETE FROM POLICY_RECORDS" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://<laptop2-ip>:8002/query -ContentType application/json -Body $bad
```

**Claim.** "Mappings are discovered and scored, then approved by a person, and all data is fetched
through a uniform, guarded API."

---

## 6. Seamless SQL decomposition and federation — 2 marks

**Say it.** "One global question, 'full profile of this plate', becomes a different SQL statement
for each agency, written in that agency's table names, column names and plate format, generated only
from the mapping registry."

**Show it.**

1. Investigate → press `DL05CD9876`.
2. Sidebar → **Plan Trace**.
3. Point at *sources selected* and *skipped*, with the reason.
4. Open the SQL for INS and for THEFT side by side. INS matches `DL-05-CD-9876` through
   `vehicle_reg` joined to `INSURERS`. THEFT matches a lower-case, spaced plate through
   `vehicle_number`. CAM also tries OCR look-alikes.
5. Point at the latency bar per source. The calls run in parallel, so total time is roughly the
   slowest source, not the sum.

**Show that the planner selects sources.** Investigate → change *Query scope* to
`UC1: Insurance Verification Only` → run the same plate. Plan Trace now contacts only REG and INS, and
says why THEFT and CAM were skipped.

**Prove it is metadata-driven.** No source table or column name appears in the executor:

```powershell
Select-String -Path mediator\executor.py -Pattern "policy_until","vehicle_reg","POLICY_RECORDS","CRIME_RECORDS","PLATE_CAPTURES"
```

No output means none.

**Claim.** "Decomposition is generated from metadata, executed in parallel over the network at query
time, and a new source needs registry rows, not code."

---

## 7. Establishing communication between the data sources — 2 marks

**Say it.** "Four laptops, one database each. Each database listens only on its own machine. Only the
wrapper port is open on the network, and the mediator reaches every agency over HTTP."

**Step 1 — reachability.** On laptop 1:

```powershell
python scripts\configure_cluster.py --probe
```

Expect four lines `UP`, each with a different IP, the right engine, and `admin: on`.

**Step 2 — the database is not exposed.** On laptop 1, try the MySQL port on laptop 2 directly:

```powershell
Test-NetConnection <laptop2-ip> -Port 3306
Test-NetConnection <laptop2-ip> -Port 8002
```

3306 fails, 8002 succeeds. Agencies publish an API, not a database.

**Step 3 — a change on one laptop is seen on another, with no sync.**

1. Laptop 1: Investigate → `DL05CD9876` → UNINSURED.
2. Laptop 2:
   ```powershell
   python scripts\mutate_source.py renew INS DL05CD9876 --until 31/12/2027
   ```
3. Laptop 1: run the same plate again → CLEAR.
4. Laptop 2 undoes it:
   ```powershell
   python scripts\mutate_source.py expire INS DL05CD9876 --until 10/06/2026
   ```

**Step 4 — communication failure is handled.** Laptop 3 presses Ctrl+C on THEFT. Laptop 1 re-runs
`DL01AB1234` → UNDETERMINED, naming THEFT. Plan Trace shows THEFT as `DOWN` and the others `OK`.
Laptop 3 restarts with `python scripts\serve.py THEFT`.

**Claim.** "Communication is live, per query, with a per-source timeout. A failure becomes a status
and an honest verdict, never a crash and never a stale answer."

---

## 8. Query results integration and a simple GUI — 3 marks

**Say it.** "Five raw result sets in five formats become one vehicle profile. Each value carries the
source it came from and that source's trust. Conflicts are detected, many rows per vehicle are
resolved latest-wins, and a decision is made with its reasons."

**Step 1 — the five stories.** Investigate → press each demo button.

| Plate | Verdict | What integration had to do |
|---|---|---|
| `DL01AB1234` | CLEAR | normalise four plate formats into one vehicle |
| `DL05CD9876` | UNINSURED — REPORT | parse `dd/mm/yyyy`, pick the latest of several policies, compare to today |
| `HR26EF4455` | STOLEN — ALERT POLICE | turn `'Y'`/`'N'` flags and epoch dates into a theft status |
| `UP16GH1122` | SUSPICIOUS — POSSIBLE CLONED PLATE | registry says one car, camera says another: flag, don't average |
| `MH12IJ7788` | UNREGISTERED / SUSPICIOUS | seen by a camera, absent from every authority |

**Step 2 — provenance.** On `UP16GH1122`, point at the profile cards: make and colour from REG with
trust 0.95, observed make and colour from CAM with trust 0.60, and the conflict panel saying which
side wins and why.

**Step 3 — correctness at scale.**

```powershell
python scripts\evaluate_ground_truth.py --start-wrappers
```

Expected: `621/621 = 100.0%`, every vehicle in the dataset decided as the ground-truth file says.

```powershell
python -m pytest -q
```

Expected: all tests passed. Run these before the site is started, or on a second laptop.

**Step 4 — the GUI beyond one lookup.** Tour the sidebar in one sentence each:

- **Challan Guard** — the integrated verdict driving a real workflow.
- **Watchlist** — plates that raise an alert whenever they are queried.
- **Reports** — file the verdict to the ministry; the evidence bundle downloads as a PDF.
- **Citizen Check** — the same integration, showing a citizen only their own facts.
- **Catalog** — register the PUC source live; the next profile includes pollution validity.
- **SQL Console** / **Source Editor** — change an agency's data from the browser; re-query to see it.

**Claim.** "Integration is correct on every vehicle in the dataset, explains every value it shows,
and the GUI makes each step visible rather than hiding it."

---

## If they ask

| Question | Short answer |
|---|---|
| Why federation, not a warehouse? | Freshness and autonomy. A policy renewed a minute ago counts now. A warehouse would fine that driver on yesterday's copy. |
| Why GAV? | Few, stable global attributes. Each is defined once as a query over the sources, which keeps query rewriting simple and explainable. |
| What if a source is slow? | Per-source timeout. It becomes `TIMEOUT` and the verdict says it could not decide. No retry, no cache. |
| How do you add a sixth agency? | A wrapper on its laptop, a catalog row, mapping rows. No mediator code changes. PUC was added exactly that way. |
| Can the mediator change a source? | Reads never can: `/query` only accepts SELECT. Writes go through `/admin`, which each agency can turn off. |
| Is the admin API secure? | It has a statement guard and a table whitelist, but no login. A real deployment adds authentication. |
