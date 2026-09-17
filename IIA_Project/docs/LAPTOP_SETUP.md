# Install and bring-up, machine by machine

From a blank laptop to a serving source. `NETWORK.md` explains the topology, the environment
variables, the latency budget and what to do when a source is not `OK`; this file is the runbook.

Target deployment: **four laptops, one database each**, three DBMS engines — plus an optional 5th
for the extensibility source (PUC), which can equally well run alongside laptop 1.

| Laptop | Source | Engine | Wrapper port | Also runs |
|---|---|---|---|---|
| 1 | `REG` — Regional Transport Office | PostgreSQL | 8001 | mediator + Streamlit GUI |
| 2 | `INS` — Insurance provider | MySQL | 8002 | |
| 3 | `THEFT` — Police crime records | SQLite | 8003 | |
| 4 | `CAM` — Road camera network | PostgreSQL | 8004 | |
| 5 (optional) | `PUC` — Pollution certificate authority | SQLite | 8005 | |

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
pytest -q            # 546 passed, 3 deselected
```

**Run the loads before `pytest`, not after.** Four schema-matcher tests read sample values out of
each source's `/schema`, so on a clone with no databases yet they fail with
`AssertionError: None != 'plate_number'`. That is missing data, not broken code.

**Run `pytest` in a shell with no `<SOURCE>_DB_URL` set,** or only after that source's real database
is loaded. The end-to-end tests start the four wrappers in-process, and a wrapper reads its
`<SOURCE>_DB_URL` when it is imported. So a shell where you exported `INS_DB_URL` points the test's
INS wrapper at MySQL. If that database exists but has not been loaded yet, INS truthfully answers
"no policy" and `DL01AB1234` decides `UNINSURED — REPORT` instead of `CLEAR`:

```
AssertionError: 'UNINSURED — REPORT' != 'CLEAR'
: Decision mismatch for DL01AB1234
```

The mediator is working correctly there — it is reporting an empty database. Either load the real
database, or open a fresh terminal (no env var) to run the suite against the local SQLite copies.

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
python data/inject_demo_fixtures.py     # appends the demo vehicles (incl. the scrapped-plate story)
git add -f *.csv && git commit -m "Regenerate synthetic data"
```
</details>

---

## 2. Rehearsal mode — the whole federation on one laptop

**One-command bring-up.** Instead of exporting `<ID>_DB_URL` / `<ID>_PORT` by hand, run
`python scripts/serve.py <SRC> --db-url "<your SQLAlchemy URL>"` once on that laptop (e.g.
`python scripts/serve.py INS --db-url "mysql+pymysql://iia_reader:pw@127.0.0.1:3306/insdb" --admin-url "mysql+pymysql://root:pw@127.0.0.1:3306/insdb"`;
`--admin-url` is the owner account `/admin/*` writes use — see "A note on two accounts"). It writes those settings to
`sources/<id>/laptop.env` (gitignored) and starts the wrapper; every later run, after a reboot or a `git pull`, is just
`python scripts/serve.py <SRC>`. `--port N` overrides the port, `--readonly-admin` disables `/admin/*` on that laptop
(`<ID>_ADMIN=off`), `--check` verifies the demo plates exist in the configured database without serving. On start it
prints this laptop's IP address(es) and the exact `python scripts/configure_cluster.py --set <SRC>=<ip>` line for laptop 1.

Do this first. It is the same wrappers, registry, executor, integrator and GUI as the four/five-laptop
deployment; only the database URLs differ.

Section 1 already loaded the five SQLite databases and seeded the registry, so this is one command:

```bash
python run_system.py                   # five wrappers (REG/INS/THEFT/CAM/PUC) + the GUI on :8501
```

Keep this working. If the hotspot dies mid-demo, this is the fallback — say out loud that it is the
rehearsal mode.

**After every `git pull`, on every laptop:** wrapper-owning code (`sources/`) and the synthetic
CSVs can both change between sessions, and a wrapper that keeps serving a database built from the
*old* CSVs will quietly disagree with the rest of the federation (a demo plate that used to be
`CLEAR` can start reading `UNINSURED`, or vice versa). So the routine after every pull is:

```bash
git pull
python scripts/load_source.py <SRC> --verify    # this laptop's own source only, reload from the new CSVs
python scripts/serve.py <SRC>                   # restart the wrapper (re-reads sources/<id>/laptop.env)
```

and, **on laptop 1 only**, re-seed the registry before restarting the GUI, because a schema or
mapping change (a new `transform_fn`, a new `aggregate`, a new attribute) lands in
`tests/fixtures.py::APPROVED_MAPPINGS`, not automatically in `meta.db`:

```bash
python scripts/seed_mappings.py
streamlit run app/app.py                        # or: python run_system.py
```

Skipping the reload is the single most common "it worked yesterday" bug on demo day — `/health`
still reports `up: true` against stale data, so nothing *looks* wrong until a decision is checked
against the expected table in §5.

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

### Open the wrapper port (Windows Firewall)

The wrapper binds `0.0.0.0`, so it is listening — but Windows silently drops inbound connections to
a new port, which from laptop 1 looks exactly like a dead source. **Each source laptop opens one
rule, for its own port only** (REG 8001, INS 8002, THEFT 8003, CAM 8004, PUC 8005). Run it in an
**Administrator** PowerShell, on laptop 2 for example:

```powershell
netsh advfirewall firewall add rule name="IIA wrapper 8002" dir=in action=allow protocol=TCP localport=8002
```

Only the wrapper port is opened. The database port (5432 / 3306) stays closed and the engine stays
bound to `127.0.0.1` — that is the source-autonomy argument: each agency publishes an API, never
its database.

**Verify from laptop 1**, once every laptop has its rule and its wrapper running:

```powershell
python scripts\configure_cluster.py --probe
```

```
  UP    REG    http://127.0.0.1:8001      (PostgreSQL)   admin: on
  UP    INS    http://192.168.43.12:8002  (MySQL)        admin: on
  UP    THEFT  http://192.168.43.13:8003  (SQLite)       admin: on
  UP    CAM    http://192.168.43.14:8004  (PostgreSQL)   admin: on

4/4 source(s) reachable
```

`admin: on` is the second half of the check and the one people forget: `--probe` also calls
`GET <base_url>/admin/actions` on every source. `on` means that laptop will accept its own
agency's writes, `off` means it was started with `<SOURCE>_ADMIN=off`, and `unreachable` means the
port never answered at all (firewall rule missing, wrapper not running, or a stale address).

**Why this makes the cross-laptop demo work with no sync step.** GUI writes — the SQL Console, the
Source Editor sidebar and Challan Guard — POST straight to `http://<laptop-ip>:800x/admin/...` on
the laptop that owns that data; reads always go live through `/query` at query time. The mediator
stores no copy of any source row, so a policy renewed on laptop 2 is visible in the next query run
on laptop 1, laptop 3 or laptop 4 — immediately, with no ETL, no refresh, no replication and no
cache to invalidate. That is the whole federation thesis, demonstrable in two laptops and ten
seconds.

### A note on two accounts

Loading needs to create tables; serving must not. So each source laptop uses **two** URLs:

* an **owner** account for `scripts/load_source.py` (one-off), and
* a **read-only** account in `<SOURCE>_DB_URL` for the wrapper's `/query` (what runs during the demo).

**Admin writes are on by default, and need the owner account.** `/admin/mutate` and `/admin/sql` use
`<SOURCE>_ADMIN_URL`; if that is unset the wrapper reuses `<SOURCE>_DB_URL`, which on PostgreSQL/MySQL
is the SELECT-only `iia_reader` — every write then fails with *permission denied*. So on a
PostgreSQL/MySQL laptop always pass **both** URLs to `serve.py`:

```powershell
python scripts/serve.py INS --db-url "mysql+pymysql://iia_reader:secret@127.0.0.1:3306/insdb" --admin-url "mysql+pymysql://root:<pw>@127.0.0.1:3306/insdb"
```

Both are saved to `sources/<id>/laptop.env` (`<SOURCE>_DB_URL`, `<SOURCE>_ADMIN_URL`; gitignored) and
re-loaded by a later plain `python scripts/serve.py <SOURCE>`. The owner-side tools
`scripts/mutate_source.py` and `scripts/sql.py` pick up that same `laptop.env` automatically
(order: `--url`, env `<SOURCE>_ADMIN_URL`, env `<SOURCE>_DB_URL`, laptop.env admin URL, laptop.env
DB URL, local SQLite), so they edit the database the wrapper actually serves — not the SQLite copy.
For ad-hoc SQL on this laptop's own database, without PowerShell `python -c` quoting:

```powershell
python scripts\sql.py INS --tables
python scripts\sql.py INS "SELECT policy_id, vehicle_reg, policy_until FROM POLICY_RECORDS WHERE vehicle_reg = 'DL-05-CD-9876'"
```

To turn a wrapper back into the original read-only server, pass `--readonly-admin` to `serve.py`
(or set `$env:<SOURCE>_ADMIN = "off"` / `export <SOURCE>_ADMIN=off` before starting it). SQLite
laptops (THEFT, PUC) need no admin URL: the wrapper derives a writable one from the file path.

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

**Start the wrapper.** `scripts/serve.py` is the one-command way — it persists the URL to
`sources/reg/laptop.env`, so a reboot or `git pull` only needs `python scripts/serve.py REG` again.
Pass the owner URL too, or `/admin/*` writes run as `iia_reader` and fail:

```powershell
python scripts/serve.py REG --db-url "postgresql+psycopg2://iia_reader:secret@127.0.0.1:5432/regdb" --admin-url "postgresql+psycopg2://postgres:<pw>@127.0.0.1:5432/regdb"
```

The equivalent by hand, if you need to see the environment variable directly:

```powershell
$env:REG_DB_URL = "postgresql+psycopg2://iia_reader:secret@127.0.0.1:5432/regdb"
$env:REG_ADMIN_URL = "postgresql+psycopg2://postgres:<pw>@127.0.0.1:5432/regdb"
python -m sources.reg.wrapper
```
```bash
export REG_DB_URL="postgresql+psycopg2://iia_reader:secret@127.0.0.1:5432/regdb"
export REG_ADMIN_URL="postgresql+psycopg2://postgres:<pw>@127.0.0.1:5432/regdb"
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

One command with `scripts/serve.py` (persists to `sources/ins/laptop.env` for later runs):

```bash
python scripts/serve.py INS --db-url "mysql+pymysql://iia_reader:secret@127.0.0.1:3306/insdb" --admin-url "mysql+pymysql://root:<pw>@127.0.0.1:3306/insdb"
```

or by hand:

```bash
export INS_DB_URL="mysql+pymysql://iia_reader:secret@127.0.0.1:3306/insdb"
export INS_ADMIN_URL="mysql+pymysql://root:<pw>@127.0.0.1:3306/insdb"
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
python scripts/serve.py THEFT                       # or: python -m sources.theft.wrapper
```

`THEFT_DB_URL` can be left unset: SQLite is this source's real engine, so the default is correct
here. The wrapper sets `PRAGMA query_only = ON`; mark the file read-only for the serving account if
you want belt and braces. Firewall: port 8003.

---

### 3.4 Laptop 4 — CAM, PostgreSQL, port 8004

Section 3.1 with `camdb`, `PLATE_CAPTURES` / `CAMERAS` and port 8004. **The grants are per database**:
the `iia_reader` grants made on `regdb` do not reach `camdb`, so grant again here. `--grant iia_reader`
does the table grants for you, and must be repeated on every reload because a reload drops the tables.

```bash
psql -U postgres -c "CREATE ROLE iia_reader LOGIN PASSWORD 'secret';"      # 'already exists' is fine
psql -U postgres -d camdb -c "GRANT CONNECT ON DATABASE camdb TO iia_reader; GRANT USAGE ON SCHEMA public TO iia_reader;"
```

```bash
psql -U postgres -c "CREATE DATABASE camdb;"
python scripts/load_source.py CAM --url "postgresql+psycopg2://postgres:<pw>@127.0.0.1:5432/camdb" --verify --grant iia_reader
python scripts/serve.py CAM --db-url "postgresql+psycopg2://iia_reader:secret@127.0.0.1:5432/camdb" --admin-url "postgresql+psycopg2://postgres:<pw>@127.0.0.1:5432/camdb"
```

CAM is the observational source: plates are OCR output and are meant to be noisy. Do not "fix" them
in the database — the plate matching and the cloned-plate story both depend on it.

---

### 3.5 Laptop 5 (optional) — PUC, SQLite, port 8005

The fifth, extensibility source (UC6). Nothing to install beyond Python:

```bash
python scripts/load_source.py PUC --verify
python scripts/serve.py PUC
```

PUC is not registered in `SOURCE_CATALOG`/`MAPPING_REGISTRY` until it is added — either live in the
Catalog tab's "Register New Source" form during the demo, or ahead of time with
`python scripts/seed_mappings.py` (which seeds PUC's 5 validated mappings alongside the other four
if `SOURCE_CATALOG` already lists it). Running the whole federation without a 5th laptop is fine —
`run_system.py` and `scripts/serve.py PUC` both default to the local SQLite file, so PUC can also
just run alongside REG/INS/THEFT/CAM on laptop 1 for the extensibility demo.

---

## 4. Back on the mediator laptop (laptop 1)

**1. Fill the mapping registry.** On a fresh clone `MAPPING_REGISTRY` is empty, and an empty registry
means the decomposer emits `SELECT * WHERE 1=0` and every profile comes back blank:

```bash
python scripts/seed_mappings.py
python scripts/seed_mappings.py --show     # 36 mappings across REG, INS, THEFT, CAM, PUC
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

`--probe` must report 4/4 reachable, each with the engine it should be running and each `admin: on`.
This is the moment that catches a firewall rule (§3.0, "Open the wrapper port") or a stale address,
and it takes two seconds.

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

**5. Pre-download the plate-OCR model (laptop 1 only, once, while you still have internet).** The
Investigate tab's photo upload (Task 2.1) uses EasyOCR, and EasyOCR fetches its English detection
and recognition weights (~100 MB) on first use — which on demo day means a hotspot with no
internet and a spinner that never ends. Warm the cache in advance with `pip install easyocr`
followed by `python -c "import easyocr; easyocr.Reader(['en'])"`; it takes a few minutes and
leaves the models in `~/.EasyOCR/model`. Nothing else depends on it: without easyocr the upload
slot shows an install hint and typing the plate works exactly as before.

---

## 5. Demo-day checklist

1. All four laptops on the hotspot; `configure_cluster.py --probe` reports **4/4**.
2. `scripts/seed_mappings.py --show` lists 36 mappings.
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
| Source `DOWN` from the mediator, `OK` locally | firewall, or the port is closed | add the `netsh advfirewall` rule in §3.0; the wrapper binds `0.0.0.0` by default |
| `--probe` shows `admin: off` | that laptop started with `<SOURCE>_ADMIN=off` | unset it and restart the wrapper; GUI writes to that source are refused until then |
| Source `DOWN` after a reconnect | hotspot reassigned the address | `configure_cluster.py --set ...` again |
| `table doesn't exist` on MySQL/Linux | table-name case | match the `CREATE TABLE` spelling exactly |
| `--verify` shows 0 rows everywhere | CSVs missing, or wrong working directory | run from `IIA_Project/` |
| `MH12IJ7788` shows 0 rows in REG | correct — it is the unregistered vehicle | nothing to fix |
| `ModuleNotFoundError: No module named 'sqlalchemy'` | the venv is not active; `Activate.ps1` is often blocked by PowerShell's execution policy | call it by path: `.venv\Scripts\python.exe -m pip install -r requirements.txt`, then `.venv\Scripts\python.exe <command>` |
| `AssertionError: 'UNINSURED — REPORT' != 'CLEAR'` in `test_e2e_groundtruth` | `INS_DB_URL` is set and that database is empty | load it, or run `pytest` from a terminal with no `*_DB_URL` set |
| One source's tests fail only on your laptop | your `<SOURCE>_DB_URL` points at a database that exists but was never loaded | `scripts/load_source.py <SRC> --url ... --verify` |

Anything else not `OK` → `NETWORK.md` section 6, which maps each status and error message to its
cause.

---

## 7. What has been verified, and what has not

Verified end to end on one machine with all four sources on SQLite: the five demo decisions, the
plate-format variants, latest-wins policy selection on renewal pairs, the join-backed fields
(`owner_name`, `insurer_name`, `last_seen_location`), the DD/MM/YYYY and epoch date transforms, and
the INS-down degradation to `UNDETERMINED`, the admin write menu on every source, OCR plate reading and repair, the watchlist/risk score, and the evidence-bundle PDF. Full suite: 546 passed, 3 deselected `@pytest.mark.live`.

**Not yet verified against live PostgreSQL or MySQL** — neither engine was usable on the machine
this was integrated on. The schemas are plain ANSI SQL and the loader reads column types back from
the live database, so they are expected to work unchanged, but laptops 1, 2 and 4 should each run
`scripts/load_source.py <SRC> --verify` and confirm `/health` reports the right engine *before* demo
day rather than on it. `pytest -q -m live` from the mediator laptop is the contract test that proves
the whole federation over the network.
