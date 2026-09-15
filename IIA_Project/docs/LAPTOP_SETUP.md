# Install and bring-up, machine by machine

From a blank laptop to a serving source. `NETWORK.md` explains the topology, the environment
variables, the latency budget and what to do when a source is not `OK`; this file is the runbook.

Target deployment: **four laptops, one database each**, three DBMS engines.

| Laptop | Source | Engine | Wrapper port | Also runs |
|---|---|---|---|---|
| 1 | `REG` — Regional Transport Office | PostgreSQL | 8001 | mediator + Streamlit GUI |
| 2 | `INS` — Insurance provider | MySQL | 8002 | |
| 3 | `THEFT` — Police crime records | SQLite | 8003 | |
| 4 | `CAM` — Road camera network | PostgreSQL | 8004 | |

The database listens on `127.0.0.1` only. The **wrapper port is the only thing published** — each
agency publishes an API, not a database. That is the source-autonomy argument, and the professor
will ask about it.

---

## 1. Every machine, once

Python 3.11 or newer, git, and the repository.

```powershell
# Windows
winget install Python.Python.3.12 Git.Git
git clone https://github.com/anushaanand60/IIA-vehicle-data-federation.git
cd IIA-vehicle-data-federation\IIA_Project
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

```bash
# Linux / macOS
git clone https://github.com/anushaanand60/IIA-vehicle-data-federation.git
cd IIA-vehicle-data-federation/IIA_Project
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
```

Now build the local SQLite copy of all four sources and fill the mapping registry. Every laptop
does this, whatever station it ends up serving: it is the rehearsal federation, it is what the test
suite runs against, and on a source laptop it stays as the fallback if the real engine misbehaves.

```bash
python scripts/load_source.py REG
python scripts/load_source.py INS
python scripts/load_source.py THEFT
python scripts/load_source.py CAM
python scripts/seed_mappings.py
```

Then check the install:

```bash
pytest -q            # 267 passed, 3 deselected
```

**Run the loads before `pytest`, not after.** Four schema-matcher tests read sample values out of
each source's `/schema`, so on a clone with no databases yet they fail with
`AssertionError: None != 'plate_number'`. That is missing data, not broken code.

If that is green, this machine can run the whole federation on its own before any DBMS enters the
picture.

### The data is already in the repository

`reg_*.csv`, `ins_*.csv`, `theft_*.csv`, `cam_*.csv` and `puc_records.csv` are committed, so every
laptop loads **identical** data and the cross-source stories line up. Do not regenerate them per
laptop — `reg.py` and friends use a fixed seed, but a different Faker version would still produce
different owners and the sources would stop agreeing.

<details>
<summary>Regenerating the data (one machine only, then commit the CSVs)</summary>

```bash
python reg.py && python ins.py && python theft.py && python cam.py && python puc.py
python data/inject_demo_fixtures.py     # appends the five demo vehicles
git add -f *.csv && git commit -m "Regenerate synthetic data"
```
</details>

---

## 2. Rehearsal mode — the whole federation on one laptop

Do this first. It is the same wrappers, registry, executor, integrator and GUI as the four-laptop
deployment; only the database URLs differ.

Section 1 already loaded the four SQLite databases and seeded the registry, so this is one command:

```bash
python run_system.py                   # four wrappers + the GUI on :8501
```

Keep this working. If the hotspot dies mid-demo, this is the fallback — say out loud that it is the
rehearsal mode.

---

## 3. Four laptops

### 3.0 Network first

Campus Wi-Fi usually isolates clients from each other. Use a phone hotspot. Connect all four
laptops, then note each address:

```powershell
ipconfig | Select-String IPv4          # Windows
```
```bash
ip addr show | grep 'inet '            # Linux
ipconfig getifaddr en0                 # macOS
```

Write the four addresses in one place. Hotspot addresses change when a laptop reconnects, so
re-check them on demo day before blaming the code.

### A note on two accounts

Loading needs to create tables; serving must not. So each source laptop uses **two** URLs:

* an **owner** account for `scripts/load_source.py` (one-off), and
* a **read-only** account in `<SOURCE>_DB_URL` for the wrapper (what runs during the demo).

---

### 3.1 Laptop 1 — REG, PostgreSQL, port 8001

**Install.** Windows: `winget install PostgreSQL.PostgreSQL.17` or the EDB installer from
postgresql.org. Linux: `sudo apt install postgresql`. macOS:
`brew install postgresql@17 && brew services start postgresql@17`. Remember the `postgres` password.

**Create the database and load it.** `load_source.py` runs `sources/reg/schema.sql` itself — that
file is plain ANSI SQL and needs no PostgreSQL-specific edits.

```bash
psql -U postgres -c "CREATE DATABASE regdb;"
python scripts/load_source.py REG --url "postgresql+psycopg2://postgres:<pw>@127.0.0.1:5432/regdb" --verify
```

`--verify` must report 1 row for `DL01AB1234`, `DL05CD9876`, `HR26EF4455`, `UP16GH1122` and
**0 for `MH12IJ7788`** — that vehicle is deliberately unregistered.

**A read-only account for the wrapper:**

```sql
-- psql -U postgres -d regdb
CREATE ROLE iia_reader LOGIN PASSWORD 'secret';
GRANT CONNECT ON DATABASE regdb TO iia_reader;
GRANT USAGE ON SCHEMA public TO iia_reader;
GRANT SELECT ON vehicle_registration, owners TO iia_reader;
```

**Keep the database off the LAN.** In `postgresql.conf` set `listen_addresses = 'localhost'`, then
restart the service.

**Start the wrapper.**

```powershell
$env:REG_DB_URL = "postgresql+psycopg2://iia_reader:secret@127.0.0.1:5432/regdb"
python -m sources.reg.wrapper
```
```bash
export REG_DB_URL="postgresql+psycopg2://iia_reader:secret@127.0.0.1:5432/regdb"
python -m sources.reg.wrapper
```

**Open the port.**

```powershell
netsh advfirewall firewall add rule name="IIA REG wrapper" dir=in action=allow protocol=TCP localport=8001
```
```bash
sudo ufw allow 8001/tcp                 # macOS: allow Python when it first asks
```

**Verify.** `curl http://127.0.0.1:8001/health` must report `"up":true` **and
`"dbms":"PostgreSQL"`**. If it says `SQLite`, `REG_DB_URL` did not reach the process — the wrapper
falls back to the local file rather than failing, and `/health` is how you catch that.

---

### 3.2 Laptop 2 — INS, MySQL, port 8002

**Install.** Windows: MySQL Installer from mysql.com (*Server only*) or `winget install Oracle.MySQL`.
Linux: `sudo apt install mysql-server`. macOS: `brew install mysql && brew services start mysql`.

```bash
mysql -u root -p -e "CREATE DATABASE insdb;"
python scripts/load_source.py INS --url "mysql+pymysql://root:<pw>@127.0.0.1:3306/insdb" --verify
```

```sql
CREATE USER 'iia_reader'@'localhost' IDENTIFIED BY 'secret';
GRANT SELECT ON insdb.POLICY_RECORDS TO 'iia_reader'@'localhost';
GRANT SELECT ON insdb.INSURERS TO 'iia_reader'@'localhost';
```

Set `bind-address = 127.0.0.1` in `my.ini` (Windows:
`C:\ProgramData\MySQL\MySQL Server 8.0\my.ini`) or `my.cnf`, then restart the service.

```bash
export INS_DB_URL="mysql+pymysql://iia_reader:secret@127.0.0.1:3306/insdb"
python -m sources.ins.wrapper
```

> **MySQL on Linux and macOS is case-sensitive about table names.** The tables are created as
> `POLICY_RECORDS` / `INSURERS`, and `MAPPING_REGISTRY.source_table` must use that exact spelling or
> every query returns `ERROR - table doesn't exist`. Windows MySQL folds case and hides this until
> demo day.

Firewall: port 8002. `/health` must say `"dbms":"MySQL"`.

---

### 3.3 Laptop 3 — THEFT, SQLite, port 8003

Nothing to install beyond Python — the database is a file.

```bash
python scripts/load_source.py THEFT --verify        # writes sources/theft/theft.db
python -m sources.theft.wrapper
```

`THEFT_DB_URL` can be left unset: SQLite is this source's real engine, so the default is correct
here. The wrapper sets `PRAGMA query_only = ON`; mark the file read-only for the serving account if
you want belt and braces. Firewall: port 8003.

---

### 3.4 Laptop 4 — CAM, PostgreSQL, port 8004

Exactly section 3.1 with `camdb`, `PLATE_CAPTURES` / `CAMERAS` and port 8004:

```bash
psql -U postgres -c "CREATE DATABASE camdb;"
python scripts/load_source.py CAM --url "postgresql+psycopg2://postgres:<pw>@127.0.0.1:5432/camdb" --verify
export CAM_DB_URL="postgresql+psycopg2://iia_reader:secret@127.0.0.1:5432/camdb"
python -m sources.cam.wrapper
```

CAM is the observational source: plates are OCR output and are meant to be noisy. Do not "fix" them
in the database — the plate matching and the cloned-plate story both depend on it.

---

## 4. Back on the mediator laptop (laptop 1)

**1. Fill the mapping registry.** On a fresh clone `MAPPING_REGISTRY` is empty, and an empty registry
means the decomposer emits `SELECT * WHERE 1=0` and every profile comes back blank:

```bash
python scripts/seed_mappings.py
python scripts/seed_mappings.py --show     # 31 mappings across REG, INS, THEFT, CAM
```

This writes the transforms and join paths that the schema matcher cannot infer — the human
validation step, scripted so it is identical on every laptop. The Schema Matching tab still
demonstrates discovery live; this only means the demo does not depend on clicking it first.

**2. Point the mediator at the other laptops:**

```bash
python scripts/configure_cluster.py --set REG=127.0.0.1 INS=192.168.43.12 \
                                          THEFT=192.168.43.13 CAM=192.168.43.14
python scripts/configure_cluster.py --probe
```

`--probe` must report 4/4 reachable, each with the engine it should be running. This is the moment
that catches a firewall rule or a stale address, and it takes two seconds.

**3. Validate the registry and read the coverage matrix:**

```bash
python scripts/validate_registry.py
```

**4. Prove the integration end to end:**

```bash
python -m mediator.executor DL05CD9876     # raw rows per source + the plan trace
pytest -q -m live                          # the contract test against the real four laptops
streamlit run app/app.py                   # or: python run_system.py
```

---

## 5. Demo-day checklist

1. All four laptops on the hotspot; `configure_cluster.py --probe` reports **4/4**.
2. `scripts/seed_mappings.py --show` lists 31 mappings.
3. `validate_registry.py` clean.
4. Query the five story plates once each to warm the processes (the first query in a fresh process
   pays a one-time ~0.3 s warm-up):

   | Plate | Expected decision |
   |---|---|
   | `DL01AB1234` | CLEAR |
   | `DL05CD9876` | UNINSURED — REPORT |
   | `HR26EF4455` | STOLEN — ALERT POLICE |
   | `UP16GH1122` | SUSPICIOUS — POSSIBLE CLONED PLATE |
   | `MH12IJ7788` | UNREGISTERED / SUSPICIOUS |

5. Show the same vehicle found through three spellings — `DL01AB1234`, `DL-01-AB-1234`,
   `dl 01 ab 1234` — which is the plate-heterogeneity point.
6. **Rehearse the failure story.** Close the INS wrapper's terminal, query `DL05CD9876`, and show
   INS `DOWN` with the decision refusing to conclude:
   *"Insurance records source is currently DOWN; cannot verify insurance status safely."* That
   refusal is the part worth marks — the mediator does not guess when a source is unreachable.
7. Fallback if the hotspot dies: `python run_system.py` on one laptop runs the whole federation
   locally.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/health` says `"dbms":"SQLite"` on laptop 1/2/4 | `<SOURCE>_DB_URL` not set in *that* shell | export it, restart the wrapper |
| Every profile field is `None` | `MAPPING_REGISTRY` empty | `python scripts/seed_mappings.py` |
| Source `DOWN` from the mediator, `OK` locally | firewall, or the port is closed | open the port; the wrapper binds `0.0.0.0` by default |
| Source `DOWN` after a reconnect | hotspot reassigned the address | `configure_cluster.py --set ...` again |
| `table doesn't exist` on MySQL/Linux | table-name case | match the `CREATE TABLE` spelling exactly |
| `--verify` shows 0 rows everywhere | CSVs missing, or wrong working directory | run from `IIA_Project/` |
| `MH12IJ7788` shows 0 rows in REG | correct — it is the unregistered vehicle | nothing to fix |

Anything else not `OK` → `NETWORK.md` section 6, which maps each status and error message to its
cause.

---

## 7. What has been verified, and what has not

Verified end to end on one machine with all four sources on SQLite: the five demo decisions, the
plate-format variants, latest-wins policy selection on renewal pairs, the join-backed fields
(`owner_name`, `insurer_name`, `last_seen_location`), the DD/MM/YYYY and epoch date transforms, and
the INS-down degradation to `UNDETERMINED`. Full suite: 267 passed.

**Not yet verified against live PostgreSQL or MySQL** — neither engine was usable on the machine
this was integrated on. The schemas are plain ANSI SQL and the loader reads column types back from
the live database, so they are expected to work unchanged, but laptops 1, 2 and 4 should each run
`scripts/load_source.py <SRC> --verify` and confirm `/health` reports the right engine *before* demo
day rather than on it. `pytest -q -m live` from the mediator laptop is the contract test that proves
the whole federation over the network.
