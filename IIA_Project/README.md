# Uninsured Vehicle Identification — a federated mediator

Information Integration, Project A. Five autonomous databases, built as if by different agencies on
three database engines, hold different facts about the same vehicles. A mediator answers "is this
vehicle uninsured, stolen, cloned or unregistered?" by querying all of them live, over their own
APIs, at the moment of the question. No data is copied into a central store.

This README is everything needed to set up, run and reproduce the project and its results.

| Section | |
|---|---|
| [1. What is in the project](#1-what-is-in-the-project) | architecture, sources, repository layout |
| [2. Reproduce on one computer](#2-reproduce-on-one-computer) | install, build the data, run the checks, open the app |
| [3. Deploy across several computers](#3-deploy-across-several-computers) | real PostgreSQL and MySQL, one agency per machine |
| [4. Command reference](#4-command-reference) | every script and what it does |
| [5. Regenerate the data](#5-regenerate-the-data) | only if you want new synthetic data |
| [6. Troubleshooting](#6-troubleshooting) | |

AI assistance is disclosed in a separate archive that ships alongside this one.

---

## 1. What is in the project

### 1.1 Architecture

```
            Streamlit GUI  (app/)
                  |
   MEDIATOR  (mediator/)                      metadata: mediator/meta.db
   planner -> decomposer -> executor -> integrator -> decision rules
      |            |            |            |            |
      | HTTP/JSON, in parallel, per-source timeout
      v            v            v            v            v
   wrapper      wrapper      wrapper      wrapper      wrapper       sources/wrapper_template.py
   REG :8001    INS :8002    THEFT :8003  CAM :8004    PUC :8005
   PostgreSQL   MySQL        SQLite       PostgreSQL   SQLite
```

| Source | Agency | Engine | Tables | Plate stored as |
|---|---|---|---|---|
| REG | Regional Transport Office | PostgreSQL | `OWNERS`, `VEHICLE_REGISTRATION` | `DL01AB1234` |
| INS | Insurance provider | MySQL | `INSURERS`, `POLICY_RECORDS` | `DL-01-AB-1234` |
| THEFT | Police | SQLite | `CRIME_RECORDS` | `dl 01 ab 1234` |
| CAM | Road cameras | PostgreSQL | `CAMERAS`, `PLATE_CAPTURES` | OCR output, may contain misreads |
| PUC | Pollution certificates | SQLite | `POLLUTION_CERT` | `DL01AB1234`; added live to show extensibility |

Each source is served by a **wrapper**, a small FastAPI service in front of its database:

| Endpoint | Does |
|---|---|
| `GET /health` | database reachable, which engine |
| `GET /schema` | tables, columns, types, sample values |
| `POST /query` | one `SELECT` only; whitelisted tables; 200-row cap; 3-second timeout |
| `POST /admin/sql` | one `INSERT` / `UPDATE` / `DELETE` on whitelisted tables; on by default, switchable off per source |
| `POST /admin/mutate` | named actions, such as renew a policy or report a theft |

For each lookup the mediator:

1. **plans** which sources can answer, from `SOURCE_CATALOG` in `mediator/meta.db`;
2. **decomposes** the question into one SQL statement per source, in that source's table and column
   names and plate format, generated from `MAPPING_REGISTRY` (global-as-view mappings);
3. **executes** the statements in parallel over HTTP, classifying each source as OK, TIMEOUT, DOWN or
   ERROR;
4. **integrates** the rows into one vehicle profile: date and flag transforms, latest row wins
   within a source, provenance and trust on every value, conflict detection;
5. **decides** with ordered, explainable rules, and answers UNDETERMINED rather than guessing when a
   needed source did not reply.

**Global schema.** The mediated schema, `VEHICLE_PROFILE`, is defined in `mediator/schema.py` as
`GLOBAL_SCHEMA_ATTRIBUTES`. The mappings from each source to it live in `MAPPING_REGISTRY`, seeded by
`scripts/seed_mappings.py`, and are visible on the website's Catalog page.

**Challan Guard** is the innovation feature. A camera sighting becomes a candidate e-challan, never a
fine. Six ordered checks run live against the sources:

1. are the sources reachable;
2. who is the vehicle really, with one-character OCR correction;
3. impossible travel between cameras, a cloned-plate signal;
4. stolen before the sighting;
5. registration valid;
6. insured on the day of the sighting.

A citizen dispute re-runs the checks live. The design rationale and sources are in
`docs/FIELD_RESEARCH.md`.

### 1.2 Repository layout

| Path | Contents |
|---|---|
| `app/` | Streamlit GUI: `app.py`, `theme.py`, `components.py`, one module per page in `tabs/` |
| `mediator/` | planner, decomposer, executor, integrator, transforms, decision rules, schema matcher, catalog, Challan Guard, reports |
| `sources/` | `wrapper_template.py`, one `wrapper.py` and `schema.sql` per source, `server_manager.py`, `_mock/` fixtures for tests |
| `scripts/` | loading, serving, cluster configuration, data changes, evaluation (section 4) |
| `data/` | ground truth, gold mappings, story-vehicle fixtures, Challan Guard candidates |
| `*.csv` | the synthetic source data, committed so every machine loads identical data |
| `reg.py` `ins.py` `theft.py` `cam.py` `puc.py` | seeded data generators (section 5) |
| `tests/` | 47 test modules: pytest, including end-to-end runs against a local cluster |
| `docs/report.md` | project report |
| `docs/heterogeneity_table.md` | every heterogeneity between the sources and how it is resolved |
| `docs/CONTRACTS.md` | wrapper and mediator interfaces |
| `docs/INTEGRATION_REPORT.md` | audit of the original source schemas the mediator adapts to |
| `docs/FIELD_RESEARCH.md` | real-world sources behind the Challan Guard feature |
| `docs/screenshots/` | the GUI |
| `run_system.py` | starts all five wrappers and the GUI on one machine |

Not in the archive, created by the steps below: the database files (`sources/*/*.db`), the
metadata store `mediator/meta.db`, and per-machine settings `sources/*/laptop.env`.

---

## 2. Reproduce on one computer

Every source runs on this machine, on SQLite. This reproduces all results in the report. Tested on
Windows 11 with Python 3.12. Commands are PowerShell; on macOS or Linux use `/` in paths and
`source .venv/bin/activate`.

### 2.1 Install

```powershell
cd IIA_Project
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If PowerShell blocks `Activate.ps1`: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then
retry. `easyocr` pulls in PyTorch and is large. It is only needed for the plate-photo upload, and the
rest of the app works without it.

### 2.2 Build the databases and metadata

```powershell
python scripts\load_source.py REG
python scripts\load_source.py INS
python scripts\load_source.py THEFT
python scripts\load_source.py CAM
python scripts\load_source.py PUC
python scripts\seed_mappings.py
python scripts\seed_challan_cases.py --reset
```

Expected row counts:

| Source | Rows |
|---|---|
| REG | 1,210 (605 owners, 605 vehicles) |
| INS | 569 (5 insurers, 564 policies) |
| THEFT | 42 |
| CAM | 925 (7 cameras, 918 sightings) |
| PUC | 424 |

`seed_mappings.py` reports PUC as skipped. That is intended: PUC joins the catalog live, in
section 2.5.

### 2.3 Reproduce the results

Run these before starting the GUI, because the tests and the GUI use the same ports, 8001–8005.

```powershell
python -m pytest -q
python scripts\evaluate_ground_truth.py --start-wrappers
python scripts\evaluate_matcher.py
```

| Check | Expected |
|---|---|
| test suite | `678 passed, 3 deselected`; the 3 deselected are live tests for real multi-machine sources |
| ground truth | `accuracy 621/621 = 100.0%`: every vehicle's decision matches `data/ground_truth.csv` |
| schema matcher | `OVERALL 23 0 0 1.00 1.00 1.00`: precision and recall against `data/gold_mapping.json` |

### 2.4 Run the system

```powershell
python scripts\configure_cluster.py --local
python run_system.py
```

Open http://localhost:8501 and stop with Ctrl+C. Pages are in the sidebar:

| Page | Try |
|---|---|
| Investigate | the five demo buttons; any plate in any spelling |
| Challan Guard | pick a case and press Verify (live), or Verify all candidates |
| Watchlist, Reports, Citizen Check | alerts, Ministry reports and PDF evidence, a citizen dispute |
| Plan Trace | the SQL each source received, and per-source latency |
| Catalog, Matcher | registered sources and mappings; schema matching live |
| SQL Console, Source Editor | read, and change, a source's data through its API |

Expected verdicts for the story vehicles:

| Plate | Verdict |
|---|---|
| `DL01AB1234` | CLEAR |
| `DL05CD9876` | UNINSURED — REPORT |
| `HR26EF4455` | STOLEN — ALERT POLICE |
| `UP16GH1122` | SUSPICIOUS — POSSIBLE CLONED PLATE |
| `MH12IJ7788` | UNREGISTERED / SUSPICIOUS |
| `KA05MN9999` | UNKNOWN VEHICLE — NOT REGISTERED |

### 2.5 Show live federation and extensibility

**Freshness.** With the GUI running, in a second terminal:

```powershell
python scripts\mutate_source.py renew INS DL05CD9876 --until 31/12/2027
```

Look up `DL05CD9876` again: the verdict changes to CLEAR with no refresh step, because nothing is
cached. Undo:

```powershell
python scripts\mutate_source.py expire INS DL05CD9876 --until 10/06/2026
```

**Refusal.** Stop one wrapper, and the verdict becomes UNDETERMINED, naming that source.

**Extensibility.** Catalog page → *Register a new source* → the form is pre-filled for PUC at
`http://127.0.0.1:8005` → submit. Then run `python scripts\seed_mappings.py`. Look up `DL01AB1234`:
the profile now includes the pollution certificate. No mediator code changed. Remove it again with
`python scripts\configure_cluster.py --remove PUC`.

---

## 3. Deploy across several computers

The intended deployment is one agency per machine, with PostgreSQL and MySQL for the real engines.
Any split works. For example:

| Machine | Runs |
|---|---|
| A | mediator + GUI, REG on PostgreSQL, PUC on SQLite |
| B | INS on MySQL |
| C | THEFT on SQLite, CAM on PostgreSQL |

Every machine first completes sections 2.1 and 2.2.

### 3.1 Network

Put all machines on one network. A phone hotspot works; campus Wi-Fi often blocks machines from
reaching each other. Find each machine's address:

```powershell
ipconfig | Select-String IPv4
```

On each machine, as Administrator, open **only** the wrapper ports that machine serves:

```powershell
netsh advfirewall firewall add rule name="IIA wrapper 8002" dir=in action=allow protocol=TCP localport=8002
```

Keep each database private to its machine:

- **PostgreSQL:** `listen_addresses = 'localhost'` in `postgresql.conf`.
- **MySQL:** `bind-address = 127.0.0.1` in `my.ini` or `my.cnf`.

Restart the database service after either change. Only the wrapper is reachable from the network.

### 3.2 Load each real database

Each database needs an owner account for loading and writing, and a read-only account `iia_reader`
that the wrapper queries with. Grants belong to one database, so create them for each one.
`--grant iia_reader` re-applies read access after each load, because loading rebuilds the tables.

**MySQL, INS:**

```powershell
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS insdb; CREATE USER IF NOT EXISTS 'iia_reader'@'localhost' IDENTIFIED BY 'secret';"
python scripts\load_source.py INS --url "mysql+pymysql://root:<password>@127.0.0.1:3306/insdb" --verify --grant iia_reader
```

**PostgreSQL, REG.** For CAM, use `camdb` in place of `regdb`, and `CAM` in the load command.

```powershell
psql -U postgres -h 127.0.0.1 -c "CREATE DATABASE regdb;"
psql -U postgres -h 127.0.0.1 -c "CREATE ROLE iia_reader LOGIN PASSWORD 'secret';"
psql -U postgres -h 127.0.0.1 -d regdb -c "GRANT CONNECT ON DATABASE regdb TO iia_reader; GRANT USAGE ON SCHEMA public TO iia_reader;"
python scripts\load_source.py REG --url "postgresql+psycopg2://postgres:<password>@127.0.0.1:5432/regdb" --verify --grant iia_reader
```

`--verify` prints a row count for each story plate. `MH12IJ7788` is absent from REG by design. A
`CREATE ... already exists` message is harmless.

Check the read-only account before serving. Run this on the machine that owns the database; it must
print a count, not an error:

```powershell
python scripts\sql.py REG --url "postgresql+psycopg2://iia_reader:secret@127.0.0.1:5432/regdb" "SELECT COUNT(*) AS n FROM vehicle_registration"
```

### 3.3 Start the wrappers

`--db-url` is the read-only account for queries. `--admin-url` is the owner account for writes. Both
are saved to `sources\<id>\laptop.env`, so later starts need only `python scripts\serve.py <SRC>`.

```powershell
python scripts\serve.py INS --db-url "mysql+pymysql://iia_reader:secret@127.0.0.1:3306/insdb" --admin-url "mysql+pymysql://root:<password>@127.0.0.1:3306/insdb"
python scripts\serve.py REG --db-url "postgresql+psycopg2://iia_reader:secret@127.0.0.1:5432/regdb" --admin-url "postgresql+psycopg2://postgres:<password>@127.0.0.1:5432/regdb"
python scripts\serve.py THEFT
```

Each wrapper stays in the foreground, so use one terminal per source. Check each:

```powershell
Invoke-RestMethod http://127.0.0.1:8002/health
```

It must show `up: True` and the right engine. If a PostgreSQL or MySQL source reports `SQLite`, the
URL did not reach the wrapper.

On the machine that runs the GUI, start its own wrappers **before** the GUI. The GUI starts a local
SQLite wrapper on any free port.

### 3.4 Connect the mediator

On the GUI machine, with the real addresses:

```powershell
python scripts\configure_cluster.py --set REG=127.0.0.1 INS=<ip-B> THEFT=<ip-C> CAM=<ip-C>
python scripts\configure_cluster.py --probe
```

The probe must report every source `UP`, with its engine and `admin: on`. Then:

```powershell
python scripts\seed_challan_cases.py --reset
streamlit run app\app.py
```

Test the federation across machines. On machine B:

```powershell
python scripts\mutate_source.py renew INS DL05CD9876 --until 31/12/2027
```

On the GUI, `DL05CD9876` now reads CLEAR. Undo on B:

```powershell
python scripts\mutate_source.py expire INS DL05CD9876 --until 10/06/2026
```

Return to single-machine mode with `python scripts\configure_cluster.py --local`.

---

## 4. Command reference

Run from `IIA_Project`, with the virtual environment active.

| Command | Does |
|---|---|
| `python scripts\load_source.py <SRC> [--url URL] [--sqlite] [--verify] [--grant ROLE]` | drop, create and load one source from its `schema.sql` and CSVs. Default target is the local SQLite file. |
| `python scripts\seed_mappings.py [--show]` | write the approved global-as-view mappings into `MAPPING_REGISTRY` |
| `python scripts\serve.py <SRC> [--db-url URL] [--admin-url URL] [--port N] [--readonly-admin] [--check]` | serve one source's wrapper; settings persist in `sources\<id>\laptop.env`. `--readonly-admin` disables writes. |
| `python run_system.py` | all five wrappers plus the GUI on one machine |
| `streamlit run app\app.py` | GUI only |
| `python scripts\configure_cluster.py --set SRC=IP ... \| --local \| --show \| --probe \| --remove SRC` | point the mediator at source addresses, or check them |
| `python scripts\sql.py <SRC> "<SQL>" [--url URL] \| --tables` | run any SQL on this machine's own copy of a source, or list its tables with column types and row counts |
| `python scripts\mutate_source.py <action> <SRC> <PLATE> [flags]` | named data changes on the machine that owns the source, such as `show`, `renew`, `expire`, `add_policy`, `delete_policies`, `steal`, `clear`, `sight`, `delete_sightings`, `set_status`, `register`, `unregister`; `--help` lists all |
| `python scripts\seed_challan_cases.py [--reset] [--verify] [--show]` | queue the Challan Guard candidates; `--verify` also checks them live |
| `python scripts\evaluate_ground_truth.py --start-wrappers` | score every vehicle's decision against `data/ground_truth.csv` |
| `python scripts\evaluate_matcher.py` | precision and recall of the schema matcher against `data/gold_mapping.json` |
| `python -m pytest -q` | the test suite; `-m live` runs the multi-machine contract tests |

Reading a source over its API, from any machine:

```powershell
$body = @{ sql = "SELECT vehicle_reg, policy_until FROM POLICY_RECORDS WHERE vehicle_reg = 'DL-05-CD-9876'" } | ConvertTo-Json
(Invoke-RestMethod -Method Post -Uri "http://<ip>:8002/query" -ContentType "application/json" -Body $body).rows
```

---

## 5. Regenerate the data

The committed CSVs are what every result above was measured on. Regenerate only if you want
different synthetic data. Run on one machine, then share the CSVs, so every machine loads
identical data:

```powershell
python reg.py
python ins.py
python theft.py
python cam.py
python puc.py
python data\inject_demo_fixtures.py
python data\make_ground_truth.py
```

The generators are seeded, but a different `faker` version produces different names, so ground-truth
counts may change. Reload every source afterwards (section 2.2).

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| tests or ground truth report `UNDETERMINED` | the GUI is using ports 8001–8005, or the catalog points at other machines | stop the GUI; run `configure_cluster.py --local` |
| every profile is blank | mapping registry is empty | `python scripts\seed_mappings.py` |
| a PostgreSQL or MySQL wrapper reports SQLite | URL not given to the wrapper | `serve.py <SRC> --db-url "..." --admin-url "..."` |
| `permission denied for table ...` | read-only account has no grant on this database | reload with `--grant iia_reader` |
| website writes fail | wrapper has no owner account | restart the wrapper with `--admin-url` |
| `password authentication failed` | wrong password, or another PostgreSQL version holds port 5432 | `Get-Service postgresql*`; keep one version running |
| `must be owner of table` when loading | loading with the read-only account, often from a `<SRC>_DB_URL` environment variable | pass the owner URL with `--url`, or `Remove-Item Env:<SRC>_DB_URL` |
| `--probe` says `unreachable` | firewall rule missing, wrapper stopped, or IP changed | section 3.1; `ipconfig` on that machine |
| `Address already in use` | a wrapper is already running on that port | close it, or use the running one |
| GUI looks outdated after changes | cached stylesheet | Ctrl+F5 |
