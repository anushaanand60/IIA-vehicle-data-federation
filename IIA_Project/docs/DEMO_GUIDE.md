# Demo guide — Uninsured Vehicle Identification

The one document to learn, rehearse and run the demo from. Every command here was run against the
real code; verdicts and outputs quoted are what the system actually printed on 17 Sep 2026.

| Part | What it gives you |
|---|---|
| [0. How the system works](#0-how-the-system-works) | the five-minute mental model |
| [1. Set it up and test it yourselves](#1-set-it-up-and-test-it-yourselves) | solo rehearsal, then the multi-laptop setup |
| [2. See the databases and schemas](#2-see-the-databases-and-schemas) | website, main laptop, source laptops |
| [3. Practise changing data from PowerShell](#3-practise-changing-data-from-powershell) | drills for source laptops and the main laptop |
| [4. Show every feature](#4-show-every-feature) | what to click, what to point at, where it stops |
| [5. Rubric walkthrough](#5-rubric-walkthrough) | the eight criteria in marking order |
| [6. Troubleshooting and viva answers](#6-troubleshooting-and-viva-answers) | when something breaks, when they ask why |

Deeper references, only if you need them: `docs/LAPTOP_SETUP.md` (installing PostgreSQL and
MySQL), `docs/demo_script.md` (spoken narration), `docs/FIELD_RESEARCH.md` (sources for the
innovation), `docs/heterogeneity_table.md`, `docs/report.md`.

---

## 0. How the system works

Four agencies keep different facts about the same vehicles. They were designed separately, so they
share no keys, spell plates differently, store dates differently and run on different database
engines. A fifth agency (pollution certificates) is added live to show extensibility.

| Source | Agency | Engine | Port | What it knows | Plate stored as |
|---|---|---|---|---|---|
| REG | Regional Transport Office | PostgreSQL | 8001 | owner, make, model, colour, registration status | `DL01AB1234` |
| INS | Insurance provider | MySQL | 8002 | policies, start and expiry | `DL-01-AB-1234` |
| THEFT | Police | SQLite | 8003 | theft, recovery, scrapping incidents | `dl 01 ab 1234` |
| CAM | Road cameras | PostgreSQL | 8004 | sightings: where, when, what the camera saw | OCR output, may be wrong |
| PUC | Pollution certificates | SQLite | 8005 | certificate validity | `DL01AB1234` |

Each agency runs a small **wrapper**: a web API in front of its database. The database itself only
listens on its own laptop. The wrapper offers:

| Endpoint | Does |
|---|---|
| `GET /health` | is the database up, which engine |
| `GET /schema` | tables, columns, types, sample values |
| `POST /query` | one `SELECT` only, whitelisted tables, 200-row cap, 3-second timeout |
| `POST /admin/sql` | one `INSERT`, `UPDATE` or `DELETE` on whitelisted tables; the agency can switch it off |
| `POST /admin/mutate` | named actions such as renew a policy or report a theft |

The **mediator** runs on the main laptop with the website. When you type a plate it:

1. **Plans** which agencies can answer the question, from the catalog in `mediator/meta.db`.
2. **Decomposes** the question into one SQL statement per agency, in that agency's own table names,
   column names and plate spelling, generated from the mapping rules.
3. **Executes** all of them in parallel over HTTP, each with its own timeout.
4. **Integrates** the answers into one vehicle profile: converts dates and flags, picks the latest
   policy or sighting, tags every value with its source and trust, and detects conflicts.
5. **Decides** with ordered, explainable rules, and refuses to decide if an agency needed for the
   answer did not reply.

Nothing is copied. Every answer is read from the agencies at the moment you ask. That is why a
change made on any laptop shows up on the next query everywhere, with no sync step.

---

## 1. Set it up and test it yourselves

### 1.1 Every laptop, once

Windows PowerShell, from wherever you keep code:

```powershell
git clone https://github.com/anushaanand60/IIA-vehicle-data-federation.git
cd IIA-vehicle-data-federation\IIA_Project
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Already cloned? Then:

```powershell
cd IIA-vehicle-data-federation\IIA_Project
git stash          # only if git pull complains about local changes
git pull
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

If `Activate.ps1` is blocked: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, then retry.

Build local copies of all five databases and the mediator's registry. Every laptop does this: it is
the rehearsal setup, what the tests run against, and a fallback if a real engine misbehaves.

```powershell
python scripts\load_source.py REG
python scripts\load_source.py INS
python scripts\load_source.py THEFT
python scripts\load_source.py CAM
python scripts\load_source.py PUC
python scripts\seed_mappings.py
python scripts\seed_challan_cases.py --reset
```

The databases and `meta.db` are not in git, so every clone builds them with these commands.

Check the install. Close the website first, because the tests use ports 8001-8005 too:

```powershell
python -m pytest -q                                    # all passed, 3 deselected
python scripts\evaluate_ground_truth.py --start-wrappers   # 621/621 = 100.0%
python scripts\evaluate_matcher.py                     # OVERALL 23 0 0 1.00 1.00 1.00
```

### 1.2 Rehearse alone on one laptop

Anyone can run the whole system alone. Use this to practise every part of this guide.

```powershell
python scripts\configure_cluster.py --local
python run_system.py
```

Open http://localhost:8501 and press Ctrl+F5 once. `run_system.py` starts all five wrappers and the
website. Ctrl+C stops everything.

In solo mode every source is on `127.0.0.1`, so in parts 2 and 3 use `127.0.0.1` wherever the guide
says a laptop's IP.

### 1.3 The real demo: four databases on three laptops

| Laptop | Role | Runs | Ports to open |
|---|---|---|---|
| **Main** (laptop 1) | mediator + website + REG | PostgreSQL `regdb`, REG wrapper, PUC wrapper, website | 8001, 8005 |
| **Laptop 2** | insurance agency | MySQL `insdb`, INS wrapper | 8002 |
| **Laptop 3** | police and cameras | THEFT SQLite file, PostgreSQL `camdb`, THEFT and CAM wrappers | 8003, 8004 |

If you have four laptops, move CAM to laptop 4. Nothing else changes.

**Step 1 — one network.** Campus Wi-Fi blocks laptops from reaching each other. Use a phone
hotspot. On every laptop:

```powershell
ipconfig | Select-String IPv4
```

Write the three addresses down. They change if a laptop reconnects.

**Step 2 — open the wrapper ports.** On each laptop, in an **Administrator** PowerShell, one rule
per port that laptop serves:

```powershell
netsh advfirewall firewall add rule name="IIA wrapper 8002" dir=in action=allow protocol=TCP localport=8002
```

Only wrapper ports open. The database ports (5432, 3306) stay closed. Agencies publish an API, not a
database.

**Step 3 — create each database, its read-only account, and load it.** Installing PostgreSQL and
MySQL is in `docs/LAPTOP_SETUP.md` §3. Every database needs its **own** grants: an account that can
read `regdb` cannot read `camdb`. `--grant iia_reader` re-applies read access after every load,
because a load drops and rebuilds the tables and a dropped table loses its grants.

Laptop 2, MySQL:

```powershell
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS insdb; CREATE USER IF NOT EXISTS 'iia_reader'@'localhost' IDENTIFIED BY 'secret';"
python scripts\load_source.py INS --url "mysql+pymysql://root:<pw>@127.0.0.1:3306/insdb" --verify --grant iia_reader
```

Laptop 3, SQLite and PostgreSQL. `CREATE ROLE` says "already exists" if the account was made
before; that is fine, carry on.

```powershell
python scripts\load_source.py THEFT --verify
psql -U postgres -c "CREATE DATABASE camdb;"
psql -U postgres -c "CREATE ROLE iia_reader LOGIN PASSWORD 'secret';"
psql -U postgres -d camdb -c "GRANT CONNECT ON DATABASE camdb TO iia_reader; GRANT USAGE ON SCHEMA public TO iia_reader;"
python scripts\load_source.py CAM --url "postgresql+psycopg2://postgres:<pw>@127.0.0.1:5432/camdb" --verify --grant iia_reader
```

Main laptop, PostgreSQL:

```powershell
psql -U postgres -c "CREATE DATABASE regdb;"
psql -U postgres -c "CREATE ROLE iia_reader LOGIN PASSWORD 'secret';"
psql -U postgres -d regdb -c "GRANT CONNECT ON DATABASE regdb TO iia_reader; GRANT USAGE ON SCHEMA public TO iia_reader;"
python scripts\load_source.py REG --url "postgresql+psycopg2://postgres:<pw>@127.0.0.1:5432/regdb" --verify --grant iia_reader
```

**Check the read-only account before starting the wrapper.** This is the exact account the wrapper
will use. It must print a row count, not an error:

```powershell
python scripts\sql.py CAM --url "postgresql+psycopg2://iia_reader:secret@127.0.0.1:5432/camdb" "SELECT COUNT(*) AS n FROM plate_captures"
python scripts\sql.py INS --url "mysql+pymysql://iia_reader:secret@127.0.0.1:3306/insdb" "SELECT COUNT(*) AS n FROM POLICY_RECORDS"
python scripts\sql.py REG --url "postgresql+psycopg2://iia_reader:secret@127.0.0.1:5432/regdb" "SELECT COUNT(*) AS n FROM vehicle_registration"
```

Expected: CAM 918, INS 564, REG 605.

`--verify` prints the row count for each story plate. `MH12IJ7788` must show 0 in REG: it is the
deliberately unregistered vehicle.

**Step 4 — start the wrappers.** Each wrapper gets two addresses: `--db-url` is the read-only account
it answers queries with, `--admin-url` is the owner account it uses for writes. Both are remembered
in `sources\<id>\laptop.env`, so next time `python scripts\serve.py INS` is enough.

Laptop 2:

```powershell
python scripts\serve.py INS --db-url "mysql+pymysql://iia_reader:secret@127.0.0.1:3306/insdb" --admin-url "mysql+pymysql://root:<pw>@127.0.0.1:3306/insdb"
```

Laptop 3, two terminals:

```powershell
python scripts\serve.py THEFT
```

```powershell
python scripts\serve.py CAM --db-url "postgresql+psycopg2://iia_reader:secret@127.0.0.1:5432/camdb" --admin-url "postgresql+psycopg2://postgres:<pw>@127.0.0.1:5432/camdb"
```

Main laptop, first terminal. Start REG **before** the website, or the website will take port 8001
with the local SQLite copy:

```powershell
python scripts\serve.py REG --db-url "postgresql+psycopg2://iia_reader:secret@127.0.0.1:5432/regdb" --admin-url "postgresql+psycopg2://postgres:<pw>@127.0.0.1:5432/regdb"
```

Each laptop checks itself before telling the main laptop it is ready:

```powershell
Invoke-RestMethod http://127.0.0.1:8002/health
```

It must say `up: True` **and the right engine**. `SQLite` on laptop 2 means the URL never reached the
wrapper.

**Step 5 — point the main laptop at the others.** Second terminal on the main laptop, with the real
addresses:

```powershell
python scripts\configure_cluster.py --set REG=127.0.0.1 INS=<laptop2-ip> THEFT=<laptop3-ip> CAM=<laptop3-ip>
python scripts\configure_cluster.py --probe
```

Expected: `4/4 source(s) reachable`, each line with the right engine and `admin: on`.
`unreachable` means a missing firewall rule, a stopped wrapper or a changed IP.

**Step 6 — start the website.**

```powershell
python scripts\seed_mappings.py
python scripts\seed_challan_cases.py --reset
streamlit run app\app.py
```

Open http://localhost:8501 and press Ctrl+F5. The sidebar's *Sources* chips should all say `UP`.

**Step 7 — the first cross-laptop test.** Do this before anything else.

1. Main laptop: Investigate → `DL05CD9876` → **UNINSURED — REPORT**.
2. Laptop 2:
   ```powershell
   python scripts\mutate_source.py renew INS DL05CD9876 --until 31/12/2027
   ```
3. Main laptop: run the same plate → **CLEAR**.
4. Laptop 2 undoes it:
   ```powershell
   python scripts\mutate_source.py expire INS DL05CD9876 --until 10/06/2026
   ```

If the verdict flipped, the federation works across laptops.

**Going back to solo mode** on the main laptop: `python scripts\configure_cluster.py --local`, then
`python run_system.py`. Re-run the `--set` line to go back to the real cluster.

**After every `git pull`**, on each laptop reload that laptop's own source and restart its wrapper:
`python scripts\load_source.py <SRC> --verify` (add the same `--url` as step 3), then
`python scripts\serve.py <SRC>`. On the main laptop also run `python scripts\seed_mappings.py`.

---

## 2. See the databases and schemas

### 2.1 On the website — yes, from the main laptop

| Page | What you see |
|---|---|
| **SQL Console** | pick a source: its published tables and columns are listed, and a worked example query per source is pre-filled. Run any `SELECT` and the rows appear. |
| **Matcher** | pick a source: every column with its type and sample values, and which global attribute it matched with what score. |
| **Catalog** | every registered agency: address, engine, trust, timeout, what it covers, and the 38 mapping rules from source columns to global attributes. |
| **Plan Trace** | after any lookup: the exact SQL each agency received and what it returned. |

The website shows what each agency **publishes** through its API. It cannot show tables an agency
chose not to publish, which is the point.

### 2.2 From PowerShell on any laptop, through the agency's API

This works from the main laptop or any other laptop on the hotspot. Paste this block once per
PowerShell window. It defines three commands:

```powershell
function Show-Tables($Ip, $Port) {
  $t = (Invoke-RestMethod "http://${Ip}:$Port/schema").tables
  foreach ($p in $t.PSObject.Properties) {
    "`n== $($p.Name) =="
    $p.Value.columns | Select-Object name, type, @{n="samples"; e={ ($_.samples | Select-Object -First 3) -join " | " }} | Format-Table -AutoSize | Out-String
  }
}
function Read-Sql($Ip, $Port, $Sql) {
  $body = @{ sql = $Sql } | ConvertTo-Json
  (Invoke-RestMethod -Method Post -Uri "http://${Ip}:$Port/query" -ContentType "application/json" -Body $body).rows | Format-Table -AutoSize
}
function Write-Sql($Ip, $Port, $Sql) {
  $body = @{ sql = $Sql } | ConvertTo-Json
  $r = Invoke-RestMethod -Method Post -Uri "http://${Ip}:$Port/admin/sql" -ContentType "application/json" -Body $body
  "rows affected: $($r.rows_affected)"
}
```

If PowerShell says `The term 'Read-Sql' is not recognized`, the block was not pasted in this window.

Then, for example:

```powershell
Show-Tables <laptop3-ip> 8003
Read-Sql <laptop2-ip> 8002 "SELECT COUNT(*) AS n FROM POLICY_RECORDS"
Invoke-RestMethod http://<laptop2-ip>:8002/health
```

Real output of `Show-Tables 127.0.0.1 8003`:

```
== CRIME_RECORDS ==
name           type    samples
----           ----    -------
incident_id    INTEGER 1 | 2 | 3
vehicle_number TEXT    dl 01 gh 0599 | up 16 cd 0391 | hr 26 ab 0597
fir_no         TEXT    FIR00001/2026 | FIR00002/2026 | FIR00003/2026
reported_date  INTEGER 1787682600 | 1782239400 | 1788201000
incident_type  TEXT    THEFT | SHREDDING
stolen_flag    TEXT    Y | N
recovered_flag TEXT    N | Y
case_status    TEXT    OPEN | CLOSED
police_station TEXT    Connaught Place PS | MG Road PS | Sector 14 PS
```

### 2.3 On the laptop that owns the database

The owner can see everything, including tables the API does not publish.

**With the project's helper**, same command on every engine. It uses the address `serve.py` saved:

```powershell
python scripts\sql.py INS --tables
python scripts\sql.py INS "SELECT policy_id, vehicle_reg, policy_until, is_active FROM POLICY_RECORDS WHERE vehicle_reg = 'DL-05-CD-9876'"
```

**With the engine's own client:**

| Laptop | Engine | List tables | Describe a table |
|---|---|---|---|
| Main | PostgreSQL | `psql -U postgres -d regdb -c "\dt"` | `psql -U postgres -d regdb -c "\d vehicle_registration"` |
| 2 | MySQL | `mysql -u root -p insdb -e "SHOW TABLES;"` | `mysql -u root -p insdb -e "DESCRIBE POLICY_RECORDS;"` |
| 3 | SQLite | `python scripts\sql.py THEFT --tables` | same |
| 3 | PostgreSQL | `psql -U postgres -d camdb -c "\dt"` | `psql -U postgres -d camdb -c "\d plate_captures"` |

PostgreSQL folds unquoted table names to lower case, which is why `\d` uses `vehicle_registration`.

### 2.4 Every table at a glance

| Source | Table | Rows | Columns |
|---|---|---|---|
| REG | `OWNERS` | 605 | owner_id, full_name, address_line, city |
| REG | `VEHICLE_REGISTRATION` | 605 | registration_id, registration_no, owner_id, make, model, colour, fuel_type, registered_on, reg_status, rto_code |
| INS | `INSURERS` | 5 | insurer_id, insurer_name |
| INS | `POLICY_RECORDS` | 564 | policy_id, vehicle_reg, insurer_id, policy_type, policy_start (`dd/mm/yyyy`), policy_until (`dd/mm/yyyy`), is_active (`1/0`), premium_inr |
| THEFT | `CRIME_RECORDS` | 42 | incident_id, vehicle_number, fir_no, reported_date (Unix epoch), incident_type, stolen_flag (`Y/N`), recovered_flag (`Y/N`), case_status, police_station |
| CAM | `CAMERAS` | 7 | camera_id, location_name, lat, lon |
| CAM | `PLATE_CAPTURES` | 918 | capture_id, plate_id, camera_id, captured_at (ISO), observed_make, observed_model, observed_colour, ocr_confidence |
| PUC | `POLLUTION_CERT` | 424 | cert_no, regn_number, valid_upto (ISO), tested_at, emission_norm |

---

## 3. Practise changing data from PowerShell

Every drill has three lines: **look**, **change**, **undo**. After each change, run the plate on the
website's Investigate page and watch the verdict move. Always run the undo before the next drill.

There are three ways, from most direct to most impressive:

| Way | Where you type it | Goes through | Needs |
|---|---|---|---|
| A. named action | the laptop that owns the data | the database directly | nothing |
| B. your own SQL | the laptop that owns the data | the database directly | nothing |
| C. your own SQL over the API | **any** laptop, including main | the agency's wrapper | the helper block from 2.2 |

### 3.1 Way A — named actions, on the owning laptop

```powershell
# INS, laptop 2 — DL05CD9876: UNINSURED -> CLEAR -> UNINSURED
python scripts\mutate_source.py show   INS DL05CD9876
python scripts\mutate_source.py renew  INS DL05CD9876 --until 31/12/2027
python scripts\mutate_source.py expire INS DL05CD9876 --until 10/06/2026

# THEFT, laptop 3 — DL01AB1234: CLEAR -> STOLEN -> CLEAR
python scripts\mutate_source.py show  THEFT DL01AB1234
python scripts\mutate_source.py steal THEFT DL01AB1234
python scripts\mutate_source.py clear THEFT DL01AB1234

# CAM, laptop 3 — DL01AB1234: CLEAR -> SUSPICIOUS (camera saw a different car) -> CLEAR
python scripts\mutate_source.py sight            CAM DL01AB1234 --make Kia --model Seltos --colour Blue
python scripts\mutate_source.py delete_sightings CAM DL01AB1234

# REG, main laptop — DL01AB1234: CLEAR -> REGISTRATION INVALID -> CLEAR
python scripts\mutate_source.py set_status REG DL01AB1234 --status SUSPENDED
python scripts\mutate_source.py set_status REG DL01AB1234 --status ACTIVE
```

`delete_sightings` removes every camera row for that plate, including the original one. If you want
the dataset exactly as shipped afterwards, reload CAM with `load_source.py CAM`.

### 3.2 Way B — your own SQL, on the owning laptop

Each agency spells the plate its own way, so raw SQL must use that spelling.

**Laptop 2, INS** — `DL05CD9876`: UNINSURED → CLEAR → UNINSURED

```powershell
python scripts\sql.py INS "SELECT policy_id, vehicle_reg, policy_until, is_active FROM POLICY_RECORDS WHERE vehicle_reg = 'DL-05-CD-9876'"
python scripts\sql.py INS "UPDATE POLICY_RECORDS SET policy_until = '31/12/2027', is_active = 1 WHERE vehicle_reg = 'DL-05-CD-9876'"
python scripts\sql.py INS "UPDATE POLICY_RECORDS SET policy_until = '10/06/2026', is_active = 0 WHERE vehicle_reg = 'DL-05-CD-9876'"
```

**Laptop 2, INS** — `DL01AB0002` has no policy at all: add one, then remove it

```powershell
python scripts\sql.py INS "INSERT INTO POLICY_RECORDS (policy_id, vehicle_reg, insurer_id, policy_type, policy_start, policy_until, is_active, premium_inr) VALUES (9001, 'DL-01-AB-0002', 1, 'COMPREHENSIVE', '01/01/2026', '01/01/2027', 1, 9000)"
python scripts\sql.py INS "DELETE FROM POLICY_RECORDS WHERE policy_id = 9001"
```

**Laptop 3, THEFT** — `DL01AB1234`: CLEAR → STOLEN — ALERT POLICE → CLEAR

```powershell
python scripts\sql.py THEFT "INSERT INTO CRIME_RECORDS (incident_id, vehicle_number, fir_no, reported_date, incident_type, stolen_flag, recovered_flag, case_status, police_station) VALUES (9001, 'dl 01 ab 1234', 'FIR9001/2026', 1788000000, 'THEFT', 'Y', 'N', 'OPEN', 'Demo PS')"
python scripts\sql.py THEFT "DELETE FROM CRIME_RECORDS WHERE incident_id = 9001"
```

**Laptop 3, THEFT** — `HR26EF4455`: close the police case

```powershell
python scripts\sql.py THEFT "UPDATE CRIME_RECORDS SET case_status = 'CLOSED' WHERE incident_id = 41"
python scripts\sql.py THEFT "UPDATE CRIME_RECORDS SET case_status = 'OPEN' WHERE incident_id = 41"
```

**Laptop 3, CAM** — `DL01AB1234`: CLEAR → SUSPICIOUS — POSSIBLE CLONED PLATE → CLEAR

```powershell
python scripts\sql.py CAM "INSERT INTO PLATE_CAPTURES (capture_id, plate_id, camera_id, captured_at, observed_make, observed_model, observed_colour, ocr_confidence) VALUES (9001, 'DL01AB1234', 'CAM001', '2026-09-04T12:00:00', 'Kia', 'Seltos', 'Blue', 0.97)"
python scripts\sql.py CAM "DELETE FROM PLATE_CAPTURES WHERE capture_id = 9001"
```

**Main laptop, REG** — `DL01AB1234`: CLEAR → REGISTRATION INVALID — REPORT → CLEAR

```powershell
python scripts\sql.py REG "UPDATE VEHICLE_REGISTRATION SET reg_status = 'SUSPENDED' WHERE registration_no = 'DL01AB1234'"
python scripts\sql.py REG "UPDATE VEHICLE_REGISTRATION SET reg_status = 'ACTIVE' WHERE registration_no = 'DL01AB1234'"
```

Ids like `9001` are written explicitly because MySQL and PostgreSQL do not auto-number these tables.

### 3.3 Way C — your own SQL over the API, from any laptop

Paste the helper block from section 2.2 first. The main laptop, or any teammate, now changes another
laptop's database through that agency's published API.

```powershell
# INS on laptop 2 — DL05CD9876: UNINSURED -> CLEAR -> UNINSURED
Read-Sql  <laptop2-ip> 8002 "SELECT policy_id, vehicle_reg, policy_until, is_active FROM POLICY_RECORDS WHERE vehicle_reg = 'DL-05-CD-9876'"
Write-Sql <laptop2-ip> 8002 "UPDATE POLICY_RECORDS SET policy_until = '31/12/2027', is_active = 1 WHERE vehicle_reg = 'DL-05-CD-9876'"
Write-Sql <laptop2-ip> 8002 "UPDATE POLICY_RECORDS SET policy_until = '10/06/2026', is_active = 0 WHERE vehicle_reg = 'DL-05-CD-9876'"

# THEFT on laptop 3 — DL01AB1234: CLEAR -> STOLEN -> CLEAR
Write-Sql <laptop3-ip> 8003 "INSERT INTO CRIME_RECORDS (incident_id, vehicle_number, fir_no, reported_date, incident_type, stolen_flag, recovered_flag, case_status, police_station) VALUES (9001, 'dl 01 ab 1234', 'FIR9001/2026', 1788000000, 'THEFT', 'Y', 'N', 'OPEN', 'Demo PS')"
Write-Sql <laptop3-ip> 8003 "DELETE FROM CRIME_RECORDS WHERE incident_id = 9001"

# CAM on laptop 3 — DL01AB1234: CLEAR -> SUSPICIOUS -> CLEAR
Write-Sql <laptop3-ip> 8004 "INSERT INTO PLATE_CAPTURES (capture_id, plate_id, camera_id, captured_at, observed_make, observed_model, observed_colour, ocr_confidence) VALUES (9001, 'DL01AB1234', 'CAM001', '2026-09-04T12:00:00', 'Kia', 'Seltos', 'Blue', 0.97)"
Write-Sql <laptop3-ip> 8004 "DELETE FROM PLATE_CAPTURES WHERE capture_id = 9001"

# REG on the main laptop — DL01AB1234: CLEAR -> REGISTRATION INVALID -> CLEAR
Write-Sql 127.0.0.1 8001 "UPDATE VEHICLE_REGISTRATION SET reg_status = 'SUSPENDED' WHERE registration_no = 'DL01AB1234'"
Write-Sql 127.0.0.1 8001 "UPDATE VEHICLE_REGISTRATION SET reg_status = 'ACTIVE' WHERE registration_no = 'DL01AB1234'"
```

Verified live: each change above moved the verdict exactly as written, and each undo put it back.

| Change | Before | After |
|---|---|---|
| REG registration suspended | CLEAR | REGISTRATION INVALID — REPORT |
| INS policy renewed | UNINSURED — REPORT | CLEAR |
| THEFT theft reported | CLEAR | STOLEN — ALERT POLICE |
| CAM different car seen | CLEAR | SUSPICIOUS — POSSIBLE CLONED PLATE |

### 3.4 What the API refuses — try these too

```powershell
Write-Sql 127.0.0.1 8002 "DROP TABLE POLICY_RECORDS"
Write-Sql 127.0.0.1 8002 "DELETE FROM POLICY_RECORDS; DELETE FROM INSURERS"
Write-Sql 127.0.0.1 8002 "UPDATE OWNERS SET full_name = 'x'"
Read-Sql  127.0.0.1 8002 "DELETE FROM POLICY_RECORDS"
```

| Sent | Refused with |
|---|---|
| `DROP TABLE ...` to `/admin/sql` | statement must start with INSERT, UPDATE or DELETE |
| two statements joined by `;` | multiple statements are not allowed |
| `OWNERS` sent to INS | table 'OWNERS' is not in the whitelist |
| `DELETE` to `/query` | query must start with SELECT |

`sql.py` is **not** guarded like this. It is the database owner's own tool on their own laptop, the
same as `psql` or `mysql`.

To stop all remote writes to one agency, start its wrapper with `--readonly-admin`. Its `/admin`
endpoints then answer 404, and `--probe` on the main laptop shows `admin: off`.

---

## 4. Show every feature

Every plate used here exists in the databases. An audit of every plate in the website, scripts and
seed data found none that is fake. Those missing from a source are missing on purpose.

| Plate | REG | INS | THEFT | CAM | PUC | Story |
|---|---|---|---|---|---|---|
| `DL01AB1234` | 1 | 1 | 0 | 1 | 1 | clean |
| `DL05CD9876` | 1 | 1 | 0 | 1 | 1 | policy expired 10/06/2026 |
| `HR26EF4455` | 1 | 1 | 1 | 1 | 1 | stolen, case open |
| `UP16GH1122` | 1 | 1 | 0 | 2 | 1 | camera saw a different car |
| `MH12IJ7788` | 0 | 0 | 0 | 1 | 0 | camera only, never registered |
| `DL01AB0002` | 1 | 0 | 0 | 1 | 0 | registered, never insured |
| `DL05CD9B76` | 0 | 0 | 0 | 1 | 0 | camera misread of `DL05CD9876` |
| `DLO1AB1234` | 0 | 0 | 0 | 1 | 0 | camera misread of `DL01AB1234` |
| `KA05MN9999` | 0 | 0 | 0 | 0 | 0 | nobody has it, for onboarding |

### 4.1 Investigate — a lookup reads, never writes

**Show.** Investigate → type `dl-05 cd 9876` in any spelling → press enter.
**Point at.** The verdict banner, "Who answered" with each agency's status and row count, and the
profile where every value names its source and trust. `0 rows` means the agency answered "no".
**Where it stops.** A lookup never writes. "Today" is fixed at 04/09/2026 so every laptop agrees.

### 4.2 The five story vehicles

Press each demo button:

| Plate | Verdict | Say |
|---|---|---|
| `DL01AB1234` | CLEAR | four agencies agree; the verdict also says what it did not ask |
| `DL05CD9876` | UNINSURED — REPORT | expiry parsed from `dd/mm/yyyy`, latest of several policies, compared to today |
| `HR26EF4455` | STOLEN — ALERT POLICE | the police record outranks everything |
| `UP16GH1122` | SUSPICIOUS — POSSIBLE CLONED PLATE | registry says one car, camera saw another |
| `MH12IJ7788` | UNREGISTERED / SUSPICIOUS | seen on camera, absent from every authority |

### 4.3 An unknown plate — onboard it live

**Show.** Investigate → `KA05MN9999` → **UNKNOWN VEHICLE — NOT REGISTERED**. Press **Register
vehicle** and press **Re-evaluate this plate**: now **UNINSURED — REPORT**, because REG knows it and
INS does not. Press **Add policy** and re-evaluate: **CLEAR**. Each button is one request to that
agency's own wrapper. Leave *Insurer id* blank to use the first insurer.
**Where it stops.** It creates one owner, one vehicle and one policy row from the form. It shows live
writes through an agency's API; it is not a full RTO workflow.
**Clean up.** Source Editor → REG → unregister `KA05MN9999`, and INS → delete_policies `KA05MN9999`.

### 4.4 Plan Trace — decomposition made visible

**Show.** After any lookup, sidebar → **Plan Trace**. Sources contacted and skipped with reasons, the
exact SQL each agency received in its own names and plate spelling, and a latency bar per source.
Then Investigate → *Query scope* → `UC1: Insurance Verification Only` → same plate. Plan Trace now
shows only REG and INS contacted, with THEFT and CAM skipped and why.

### 4.5 Refusal when an agency is down

**Show.** Laptop 3 presses Ctrl+C on the THEFT wrapper. Main laptop re-runs `DL01AB1234`: the verdict
becomes UNDETERMINED and names THEFT. Plan Trace shows THEFT `DOWN`, the others `OK`. Restart with
`python scripts\serve.py THEFT` and it is CLEAR again.
**Where it stops.** No retry, no cached fallback, on purpose: a cached answer would be a stale answer.

### 4.6 Changing data from the website

**Source Editor.** Sidebar → **Source Editor** → source, action, plate → submit. Actions: REG register,
set_status, unregister; INS renew, expire, add_policy, delete_policies; THEFT steal, clear, shred,
delete_incidents; CAM sight, delete_sightings; PUC issue, revoke.

**SQL Console.** Sidebar → **SQL Console** → pick a source → type one statement → run. `SELECT` goes to
`/query`; `INSERT`, `UPDATE`, `DELETE` go to `/admin/sql` on the laptop that owns the data, with the
row count before and after. Any SQL from section 3.2 works here without the `python scripts\sql.py`
part.

### 4.7 Challan Guard — the innovation

**The problem.** Camera e-challans are often wrong: a misread character, a cloned plate, a stolen car
fined to its victim. The Supreme Court's 2025–26 order to auto-challan uninsured vehicles makes this
bigger. Sources: `docs/FIELD_RESEARCH.md`.

**What it does.** A camera sighting becomes a *candidate*, never a fine. **Verify (live)** runs six
checks against the agencies, in order, stopping at the first that decides:

1. **Reachable?** REG, INS or THEFT down → HOLD. No fine on half the evidence.
2. **Who is it really?** Tries the plate as read plus one-character swaps (0↔O, 8↔B, 1↔I, 5↔S, 2↔Z,
   6↔G), scores registered candidates against the make and colour the camera saw. Misread
   corrected; two equal matches → HOLD.
3. **Cloned?** Speed between consecutive sightings of that vehicle above 160 km/h → HOLD, and the plate
   is watchlisted.
4. **Stolen before the sighting?** REJECT, route to police.
5. **Registration invalid?** REJECT with the reason.
6. **Insured on the day?** Yes → REJECT, no offence. No → ISSUE Rs 2000, or Rs 4000 for a repeat.

Every step, source answer and timestamp is saved as the case's evidence. The mediator writes only to
its own `meta.db`, never to an agency.

**Show.**

1. Sidebar → **Challan Guard**. The queue holds 12 candidates.
2. In the **Case** box pick case 1 → **Verify (live)**. A pop-up gives the verdict; read the steps
   from the stepper. The Case box stays on the case you verified.
3. Repeat for cases 2–5.
4. Press **Verify all candidates (live)** for cases 6–12, which are real camera captures.

| # | Read as | Result |
|---|---|---|
| 1 | `DL05CD9B76` | corrected to `DL05CD9876`; ISSUED Rs 2000, policy expired 10/06/2026 |
| 2 | `DLO1AB1234` | corrected to `DL01AB1234`; REJECTED, insured until 14/01/2027 — wrongful fine prevented |
| 3 | `UP16GH1122` | HOLD — Sector 29 Crossing to Agra in 30 min at 340 km/h, cloned plate |
| 4 | `HR26EF4455` | REJECTED — stolen before the sighting, route to police |
| 5 | `DL01AB0002` | ISSUED Rs 2000 — no policy on record |
| 6 | `MH12EF0198` | ISSUED — policy expired 28/04/2026 |
| 7 | `DL05EF0004` | REJECTED — insured on the day of the sighting |
| 8 | `DL05AB0145` | ISSUED — no policy on record |
| 9 | `HR26GH0230` | REJECTED — reported stolen |
| 10 | `UP16CD0365` | ISSUED — policy expired 16/06/2026 |
| 11 | `DL01AB0570` | REJECTED — registration suspended |
| 12 | `HR26CD0300` | ISSUED — policy expired 04/05/2026 |

After all 12: **Held 1, Issued 6, Rejected 5, Wrongful fines prevented 5.**

**Live twist — a dead agency.** Stop the INS wrapper, verify case 1 again: HOLD, "INS unreachable —
refusing to fine on partial evidence". Restart INS.

**Reset the queue** before a fresh run: `python scripts\seed_challan_cases.py --reset`.

**Where it stops.** One misread character per plate. Travel speed uses straight-line distance, which
under-estimates road distance and so never flags an honest driver. Fines follow MV Act §196 only.

### 4.8 Citizen Check and the dispute loop

**Show.**

1. Verify case 5 (`DL01AB0002`) so it is ISSUED.
2. Laptop 2:
   ```powershell
   python scripts\mutate_source.py add_policy INS DL01AB0002 --start 01/01/2026 --until 01/01/2027
   ```
3. Sidebar → **Citizen Check** → *Dispute a challan* → case `5`, any reason → submit.
4. **CANCELLED** — "record changed since issue: insurance_expiry was none now 2027-01-01".
5. Undo on laptop 2: `python scripts\mutate_source.py delete_policies INS DL01AB0002`.

With no change to the records, the same dispute returns **UPHELD**, "re-verified live".
**Where it stops.** The citizen view hides the owner and masks the plate. There is no identity check on
who files a dispute.

### 4.9 Watchlist, reports, audit log

- **Watchlist.** Add `UP16GH1122` with a reason. Look it up in Investigate: an alert is raised and
  listed with time and location. Challan Guard adds clone suspects here itself.
- **Reports.** On an Investigate verdict press **File report to the Ministry**. Reports lists it; the
  evidence bundle downloads as a PDF with sources asked, their answers, the rule that fired and the
  code version.
- **Audit log.** Reports → query audit log: every lookup with plate, sources, statuses, verdict, time.

**Where it stops.** Reports are stored in `meta.db`. Nothing is sent to a real ministry.

### 4.10 Catalog — adding a fifth agency

**Show.** Sidebar → **Catalog**: four agencies with address, engine, trust, and 38 mapping rules.
Register PUC (port 8005) with the form, look up `DL01AB1234` again: the profile now includes pollution
certificate validity. No mediator code changed.

### 4.11 Matcher

**Show.** Sidebar → **Matcher** → INS. The matcher proposes `vehicle_reg → plate_number` from sample
values and names, with a score for every pair. On the gold standard it finds 23 of 23 with no false
matches (`python scripts\evaluate_matcher.py`).
**Where it stops.** Transforms such as date formats and join paths are approved by a person.

### 4.12 Plate photo

**Show.** Investigate → *Or upload a plate photo* → choose an image. The reading and close alternatives
appear; pick one to investigate.
**Where it stops.** Offline EasyOCR. Run it once before the demo so its model is already downloaded.

---

## 5. Rubric walkthrough

About 20 minutes. The main laptop drives; laptops 2 and 3 keep their wrapper windows visible and run
commands when called.

| # | Criterion | Marks | Time | Evidence |
|---|---|---|---|---|
| 1 | Scope of work | 1 | 1 min | problem, sources, decision, out of scope |
| 2 | Innovation | 1 | 3 min | Challan Guard live |
| 3 | Schema design | 2 | 2 min | 5 schemas, 3 engines, deliberate heterogeneity |
| 4 | Populating data | 2 | 2 min | 3,170 rows, identical on every laptop |
| 5 | Matching / APIs | 2 | 3 min | matcher 23/23, uniform wrapper API |
| 6 | Decomposition and federation | 2 | 3 min | Plan Trace |
| 7 | Communication between sources | 2 | 3 min | probe, cross-laptop change, dead source |
| 8 | Integration and GUI | 3 | 4 min | five stories, provenance, 621/621 |

### 5.1 Scope of work — 1 mark

**Say.** "Four agencies hold different facts about the same vehicle, with no shared keys, formats or
engine. Given a plate, we decide live whether it is uninsured, stolen, cloned or unregistered, and
refuse to decide when an agency cannot be reached. Nothing is copied into a central store."

**Show.** `docs/report.md` §0 and the website sidebar.

| In scope | Out of scope, on purpose |
|---|---|
| 4 autonomous agencies + 1 added live | a warehouse or any copy of source data |
| 3 engines on separate laptops | machine learning, probabilistic fusion |
| global schema, mappings, federated SQL | actually collecting fines |
| decisions, conflicts, refusal | login on the admin API |
| website, reports, audit log, live writes | |

### 5.2 Innovation — 1 mark

**Say.** "Camera e-challans are often wrong, and the Supreme Court's order to auto-challan uninsured
vehicles scales that up. Challan Guard checks every camera fine against the live agencies before it
is issued, and again when a citizen disputes it."

**Show.** Section 4.7 steps 1–3 for cases 1, 2 and 3, then the dead-agency twist.
**Also new, one line each if asked.** Refusal over guessing. OCR-tolerant matching. Writes through
each agency's own API. Evidence-bundle PDF. Watchlist alerts. Data-minimised citizen view. Audit log.

### 5.3 Schema design — 2 marks

**Say.** "Five schemas designed as if by different agencies, on three engines. Every difference is
deliberate and absorbed by a named mechanism."

**Show.** The files `sources\reg\schema.sql`, `sources\ins\schema.sql`, `sources\theft\schema.sql`,
`sources\cam\schema.sql`, `sources\puc\schema.sql`, then the table in section 2.4, then
`docs\heterogeneity_table.md`. Point at:

- plates in four spellings;
- dates as ISO, `dd/mm/yyyy` text, Unix epoch integers and ISO timestamps;
- flags as `1/0`, `Y/N` and status words;
- owners in a separate table only in REG.

**Prove it on the real engines.** Section 2.3: `DESCRIBE POLICY_RECORDS` on laptop 2,
`\d vehicle_registration` on the main laptop.

### 5.4 Populating data — 2 marks

**Say.** "Seeded generators plus hand-planted story vehicles, committed as CSV, so every laptop loads
identical data into its own engine."

**Show.** Generators `reg.py`, `ins.py`, `theft.py`, `cam.py`, `puc.py`; story vehicles in
`data\inject_demo_fixtures.py`; loader `scripts\load_source.py`. Row counts from section 2.4.
Run on laptop 2: `python scripts\load_source.py INS --url "..." --verify`.
**Point at.** Several policies per vehicle, noisy camera plates with confidence scores, recovered and
scrapped vehicles, cameras at real Delhi-NCR coordinates.

### 5.5 Schema matching and APIs — 2 marks

**Say.** "Two of the listed options: a matcher that discovers which column is which, and a uniform
API per agency for fetching."

**Show the matcher.** Section 4.11, then:

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

**Show the API.** From the main laptop, after pasting the helper block:

```powershell
Invoke-RestMethod http://<laptop2-ip>:8002/health
Show-Tables <laptop2-ip> 8002
Read-Sql <laptop2-ip> 8002 "SELECT vehicle_reg, policy_until FROM POLICY_RECORDS WHERE vehicle_reg = 'DL-05-CD-9876'"
Read-Sql <laptop2-ip> 8002 "DELETE FROM POLICY_RECORDS"
```

The last one is refused: "query must start with SELECT".

### 5.6 Decomposition and federation — 2 marks

**Say.** "One global question becomes a different SQL statement per agency, in its own names and plate
spelling, generated only from the mapping rules, executed in parallel at query time."

**Show.** Section 4.4, both scopes. Point at INS matching `DL-05-CD-9876` through `vehicle_reg` joined
to `INSURERS`, THEFT matching a lower-case spaced plate, CAM also trying OCR look-alikes, and the
latency bars: total time is about the slowest agency, not the sum.

**Prove it is metadata-driven:**

```powershell
Select-String -Path mediator\executor.py -Pattern "policy_until","vehicle_reg","POLICY_RECORDS","CRIME_RECORDS","PLATE_CAPTURES"
```

No output: no agency's table or column name is written into the executor.

### 5.7 Communication between sources — 2 marks

**Say.** "One database per laptop, each listening only locally. Only the wrapper port is on the
network, and the mediator reaches every agency over HTTP on every query."

1. **Reachability.** `python scripts\configure_cluster.py --probe` → 4/4 up, different IPs, right
   engines, `admin: on`.
2. **The database itself is not exposed.**
   ```powershell
   Test-NetConnection <laptop2-ip> -Port 3306
   Test-NetConnection <laptop2-ip> -Port 8002
   ```
   3306 fails, 8002 succeeds.
3. **A change on one laptop is seen on another.** Section 1.3 step 7.
4. **A broken link is handled.** Section 4.5.

### 5.8 Integration and GUI — 3 marks

**Say.** "Five result sets in five formats become one profile. Each value carries its source and trust,
conflicts are detected, many rows per vehicle resolve latest-wins, and the decision comes with its
reasons."

1. The five story vehicles, section 4.2.
2. On `UP16GH1122`, the profile: make and colour from REG at trust 0.95, observed make and colour from
   CAM at trust 0.60, and the conflict panel saying which wins and why.
3. Correctness at scale, before the website is started:
   ```powershell
   python scripts\evaluate_ground_truth.py --start-wrappers
   ```
   `621/621 = 100.0%`.
4. Sidebar tour, one sentence each: Challan Guard, Watchlist, Reports, Citizen Check, Catalog,
   SQL Console, Source Editor.

---

## 6. Troubleshooting and viva answers

### 6.1 When something is wrong

| Symptom | Cause | Fix |
|---|---|---|
| `--probe` says `unreachable` | firewall rule missing, wrapper stopped, IP changed | step 2 of 1.3 on that laptop; re-check `ipconfig`; re-run `--set` |
| `--probe` says `admin: off` | wrapper started with `--readonly-admin` | restart it without that flag |
| `/health` says SQLite on laptop 2 | the URL never reached the wrapper | `serve.py INS --db-url "..." --admin-url "..."` |
| `permission denied for table ...` | the read-only account has no grant on this database | reload with `--grant iia_reader` (step 3) |
| `password authentication failed for user "iia_reader"` or `role ... does not exist` | the account was never created on this laptop | the `CREATE ROLE` / `CREATE USER` line in step 3 |
| `could not connect to server` / `Connection refused` on 5432 or 3306 | the database service is stopped | Services app: start `postgresql-x64-17` or `MySQL80`, as Administrator |
| website writes fail with "permission denied" | wrapper has no owner account for writes | restart with `--admin-url` |
| a PowerShell change does not show on the website | changed a different copy of the data | run it on the laptop that owns the data, or use Way C |
| every profile is blank | mapping registry empty | `python scripts\seed_mappings.py` |
| two laptops disagree on a plate | one laptop has data from older CSVs | `git pull`, `load_source.py <SRC> --verify`, restart the wrapper |
| `Address already in use` | an old wrapper still running | close its window, or `Get-Process python \| Stop-Process` |
| `The term 'Read-Sql' is not recognized` | helper block not pasted in this window | paste the block from 2.2 |
| tests fail with UNDETERMINED | website running during tests | stop the website, re-run |
| website looks like an old design | cached stylesheet | Ctrl+F5 |
| Challan Guard queue empty | `meta.db` rebuilt | `python scripts\seed_challan_cases.py --reset` |

### 6.2 Likely questions

| Question | Answer |
|---|---|
| Why federation, not a warehouse? | Freshness and autonomy. A policy renewed a minute ago counts now; a warehouse would fine that driver on yesterday's copy. |
| Why GAV? | Few, stable global attributes, each defined once as a query over the sources. Rewriting stays simple and explainable. |
| What if an agency is slow? | It has its own timeout. It becomes `TIMEOUT` and the verdict says it could not decide. No retry, no cache. |
| How do you add an agency? | A wrapper on its laptop, a catalog row, mapping rows. No mediator code. PUC was added that way. |
| Can the mediator change an agency's data? | Lookups cannot: `/query` accepts only SELECT. Writes go through `/admin`, which each agency can switch off. |
| Is the write API secure? | It has a statement guard, a table whitelist and a separate owner account, but no login. A real agency would add authentication. |
| Why these decision rules and this order? | Theft and scrapping outrank insurance because they change who is responsible. Refusal comes first because a verdict on missing evidence is not defensible. |
| Is the data real? | Synthetic, from seeded generators, with realistic formats and noise. Every plate in the demo exists in the databases. |
