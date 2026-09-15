# Install and bring-up, machine by machine

From a blank laptop to a serving source. `NETWORK.md` explains the topology, the environment
variables, the latency budget and what to do when a source is not `OK`; this file is the install
runbook that comes before it.

Two modes:

| | Mode A — one machine | Mode B — four laptops |
|---|---|---|
| When | developing, and the day before the demo | the demo itself |
| Sources | all four on `127.0.0.1:8001-8004` | one per laptop, over a hotspot |
| Databases | A1 SQLite mocks / A2 the real three engines | PostgreSQL, MySQL, SQLite |
| Installs | A1 Python only / A2 + PostgreSQL + MySQL | one engine per laptop |

Mode A1 works **today** and needs no database software. A2 and B additionally need
`sources/*/schema.sql` and `data/generate.py`, which are still teammate deliverables (§5).

---

## 1. Every machine, once

Python 3.11 or newer, git, and the repository.

```powershell
# Windows
winget install Python.Python.3.12 Git.Git
git clone <repo-url> "iia proj1"
cd "iia proj1"
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

```bash
# Linux / macOS
sudo apt install -y python3-venv git          # macOS: brew install python git
git clone <repo-url> iia-proj1 && cd iia-proj1
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
```

`requirements.txt` lists both database drivers. A source laptop only needs its own
(`psycopg2-binary` for REG and CAM, `pymysql` for INS, nothing for THEFT); installing all of it is
simpler and harmless. Check the install:

```bash
pytest -q            # 252 passed, 1 skipped, 3 deselected — no database needed
```

If that is green, the transport layer on this machine is sound before any DBMS enters the picture.

---

## 2. Mode A — everything on this laptop

### A1. Mock federation (no database software)

```bash
python scripts/run_local.py          # four sources on :8001-8004 + the GUI on :8501
```

One process per source, so killing a printed PID is exactly what a laptop dropping off the network
looks like. In a second terminal:

```bash
python -m mediator.executor DL05CD9876      # the six story plates all work
python -m mediator.executor HR26EF4455      # after killing :8002 → INS DOWN, the rest OK
```

Variants: `--no-gui`, `--only REG,CAM`, `--with-puc` (the fifth agency on :8005 for the
add-a-source demo). The mocks run the **real** wrapper modules over the real schema shapes — same
guard, same `/health`, `/schema`, `/query`, same executor, same GUI. Only the database URL differs.

### A2. The three real engines on one laptop

Same as Mode B below, except every `base_url` stays `http://127.0.0.1:<port>` and the two PostgreSQL
databases (`regdb`, `camdb`) share one server on 5432. Do §3.1–§3.4 on this one machine, skipping
every firewall step. Worth doing as a dress rehearsal: it proves the SQL the registry generates runs
on PostgreSQL and MySQL, which the SQLite mocks cannot prove.

---

## 3. Mode B — four laptops

### 3.0 Network first

Campus Wi-Fi isolates clients from each other; use a phone hotspot. Connect all four laptops, then on
each one note its address and write the four down in one place:

```powershell
ipconfig | Select-String IPv4          # Windows
```
```bash
ip addr show | grep 'inet '            # Linux
ipconfig getifaddr en0                 # macOS
```

Hotspot addresses can change when a laptop reconnects, so re-check them on demo day before blaming
the code. Every `base_url` in the registry has to match.

### 3.1 Laptop 1 — REG, PostgreSQL, port 8001 (also runs the mediator and GUI)

**Install.** Windows: the EDB installer from postgresql.org, or `winget install PostgreSQL.PostgreSQL.17`.
Linux: `sudo apt install postgresql`. macOS: `brew install postgresql@17 && brew services start postgresql@17`.
Remember the `postgres` superuser password you set.

**Create the database, load the schema and the data.**

```bash
psql -U postgres -c "CREATE DATABASE regdb;"
psql -U postgres -d regdb -f sources/reg/schema.sql
python data/generate.py --source REG          # teammate deliverable; their flags win, see §5
```

**A read-only account** — the wrapper opens read-only connections, but the account should not be able
to write either:

```sql
-- psql -U postgres -d regdb
CREATE ROLE iia_reader LOGIN PASSWORD 'secret';
GRANT CONNECT ON DATABASE regdb TO iia_reader;
GRANT USAGE ON SCHEMA public TO iia_reader;
GRANT SELECT ON vehicle_registration, owners TO iia_reader;
```

**Keep the database off the LAN.** In `postgresql.conf`: `listen_addresses = 'localhost'`, then
restart the service. Only the wrapper port is published — that is the source-autonomy argument, and
the professor will ask about it.

**Start the wrapper.**

```powershell
$env:REG_DB_URL = "postgresql+psycopg2://iia_reader:secret@127.0.0.1:5432/regdb"
$env:REG_TABLES = "VEHICLE_REGISTRATION,OWNERS"
python -m uvicorn sources.reg.wrapper:app --host 0.0.0.0 --port 8001
```
```bash
export REG_DB_URL="postgresql+psycopg2://iia_reader:secret@127.0.0.1:5432/regdb"
export REG_TABLES="VEHICLE_REGISTRATION,OWNERS"
python -m uvicorn sources.reg.wrapper:app --host 0.0.0.0 --port 8001
```

**Open the port.**

```powershell
netsh advfirewall firewall add rule name="IIA REG wrapper" dir=in action=allow protocol=TCP localport=8001
```
```bash
sudo ufw allow 8001/tcp                 # macOS: allow Python when it first asks
```

**Verify.** `curl http://127.0.0.1:8001/health` → `{"up":true,…}`, and
`curl http://127.0.0.1:8001/schema` should list both tables with sample values — that response is
what the teammates' schema matcher consumes, so an empty `samples` array means the data did not load.

### 3.2 Laptop 2 — INS, MySQL, port 8002

**Install.** Windows: MySQL Installer from mysql.com (choose *Server only*), or
`winget install Oracle.MySQL`. Linux: `sudo apt install mysql-server`.
macOS: `brew install mysql && brew services start mysql`.

```bash
mysql -u root -p -e "CREATE DATABASE insdb;"
mysql -u root -p insdb < sources/ins/schema.sql
python data/generate.py --source INS
```

```sql
CREATE USER 'iia_reader'@'localhost' IDENTIFIED BY 'secret';
GRANT SELECT ON insdb.POLICY_RECORDS TO 'iia_reader'@'localhost';
GRANT SELECT ON insdb.INSURERS TO 'iia_reader'@'localhost';
```

`bind-address = 127.0.0.1` in `my.ini` (Windows: `C:\ProgramData\MySQL\MySQL Server 8.0\my.ini`) or
`my.cnf`, then restart the service.

```bash
export INS_DB_URL="mysql+pymysql://iia_reader:secret@127.0.0.1:3306/insdb"
export INS_TABLES="POLICY_RECORDS,INSURERS"
python -m uvicorn sources.ins.wrapper:app --host 0.0.0.0 --port 8002
```

> **MySQL on Linux and macOS is case-sensitive about table names.** `INS_TABLES` and the registry's
> `source_table` must use the exact `CREATE TABLE` spelling, or every query comes back
> `ERROR · table doesn't exist`. Windows MySQL folds case and will hide the problem until demo day.

Firewall: port 8002.

### 3.3 Laptop 3 — THEFT, SQLite, port 8003

Nothing to install beyond Python — the database is a file.

```bash
mkdir -p data
sqlite3 data/theft.db < sources/theft/schema.sql
python data/generate.py --source THEFT
export THEFT_DB_URL="sqlite:///data/theft.db"     # relative to the directory uvicorn starts in
export THEFT_TABLES="CRIME_RECORDS"
python -m uvicorn sources.theft.wrapper:app --host 0.0.0.0 --port 8003
```

Make the file read-only for the account running the wrapper if you want belt and braces; the wrapper
already sets `PRAGMA query_only = ON`. Firewall: port 8003.

### 3.4 Laptop 4 — CAM, PostgreSQL, port 8004

Exactly §3.1 with `camdb`, `PLATE_CAPTURES` / `CAMERAS`, and port 8004:

```bash
psql -U postgres -c "CREATE DATABASE camdb;"
psql -U postgres -d camdb -f sources/cam/schema.sql
python data/generate.py --source CAM
export CAM_DB_URL="postgresql+psycopg2://iia_reader:secret@127.0.0.1:5432/camdb"
export CAM_TABLES="PLATE_CAPTURES,CAMERAS"
python -m uvicorn sources.cam.wrapper:app --host 0.0.0.0 --port 8004
```

CAM is the observational source: plates are OCR output and are meant to be noisy. Do not "fix" them
in the database — the mediator's plate matching and the cloned-plate story both depend on it.

### 3.5 Back on the mediator laptop

1. Put the four addresses into the registry's `base_url` (`SOURCE_CATALOG.base_url` in `meta.db`, or
   the `base_url` field per source in `mappings.json`) — e.g. `http://192.168.43.12:8002` for INS.
2. Validate the registry and read the coverage matrix:
   ```bash
   python scripts/validate_registry.py mediator/meta.db
   ```
3. Prove the integration end to end:
   ```bash
   pytest -q -m live                  # IIA_LIVE_PLATE=DL01AB1234 for another plate
   python -m mediator.executor DL05CD9876
   streamlit run app/app.py           # until that exists: streamlit run app/tabs/plan_trace.py
   ```

`pytest -m live` is the contract test: it checks each source is healthy, that every column the
registry names really exists in that source's `/schema`, and that a known plate returns rows. Run it
first whenever something looks wrong — it names the missing column instead of making you guess.

---

## 4. Demo-day checklist

1. All four laptops on the hotspot; addresses re-checked and matching the registry.
2. Each source: wrapper started, `curl http://<ip>:<port>/health` answers **from the mediator laptop**,
   not just locally.
3. `python scripts/validate_registry.py <registry>` clean.
4. `pytest -q -m live` green.
5. Six story plates queried once each to warm the processes (the first query in a fresh process pays
   a one-time ~0.3 s warm-up).
6. Rehearse the failure story: close the INS wrapper's terminal, query `DL05CD9876`, show INS `DOWN`
   and the decision refusing to conclude. That degradation is the part worth marks.
7. Fallback if the hotspot dies mid-demo: `python scripts/run_local.py` on one laptop runs the whole
   federation locally. Say out loud that it is the rehearsal mode.

Anything not `OK` → `NETWORK.md` §6, which maps each status and error message to its cause.

---

## 5. What is still blocked on teammates

Modes A2 and B need files that do not exist in the repository yet:

| Needed | Owner | Used by |
|---|---|---|
| `sources/{reg,ins,theft,cam}/schema.sql` | Teammates A & B | §3.1–§3.4 schema load |
| `data/generate.py`, `data/ground_truth.csv` | Teammates A & B | the data load on each laptop |
| `mediator/meta.db` or `mediator/mappings.json` | Teammates A & B | §3.5; until then the loader falls back to `sources/_mock/mock_mappings.json` |
| `mediator/transforms.py`, `integrator.py`, `decide.py`, `report.py` | Teammate C | everything downstream of the executor |
| `app/app.py` and `app/tabs/{investigate,reports}.py` | Teammate C | the full GUI; `app/tabs/plan_trace.py` runs standalone meanwhile |

Until they land, Mode A1 is the whole demo that exists: the transport layer, the registry-driven SQL,
parallel execution, per-source status and the plan trace are all real and testable.
