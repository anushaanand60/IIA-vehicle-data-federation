# Network and deployment

How the four source laptops and the mediator talk to each other, how to bring each one up, and what
to check when a source shows `DOWN`. Names follow the team design PDF (§3 schemas, §9 deployment).

---

## 1. Topology

```
                  phone hotspot, e.g. 192.168.43.0/24 (campus Wi-Fi isolates clients)

   laptop 1 (.11)       laptop 2 (.12)       laptop 3 (.13)       laptop 4 (.14)
   REG                  INS                  THEFT                CAM
   PostgreSQL regdb     MySQL insdb          SQLite theft.db      PostgreSQL camdb      bound 127.0.0.1
   wrapper :8001        wrapper :8002        wrapper :8003        wrapper :8004         bound 0.0.0.0
        │                    │                    │                    │
        └────────────────────┴─────────┬──────────┴────────────────────┘
                                       │ HTTP/JSON, parallel, per-source timeout
                          mediator + Streamlit (laptop 1)
                          reads meta.db or mappings.json; holds no source data
```

- **The database never listens on the LAN.** Only the wrapper port is reachable: each agency publishes
  an API, not a database.
- The mediator is a process, not a data source; running it on laptop 1 adds no second database there.

| Source | Port | DBMS | Tables the wrapper whitelists | Driver |
|---|---|---|---|---|
| REG | 8001 | PostgreSQL | `VEHICLE_REGISTRATION`, `OWNERS` | `psycopg2-binary` |
| INS | 8002 | MySQL | `POLICY_RECORDS`, `INSURERS` | `pymysql` |
| THEFT | 8003 | SQLite | `CRIME_RECORDS` | none |
| CAM | 8004 | PostgreSQL | `PLATE_CAPTURES`, `CAMERAS` | `psycopg2-binary` |
| PUC (UC6) | 8005 | SQLite | `POLLUTION_CERT` | none |

---

## 2. Bringing up one source laptop

Installing the database engine, loading the schema and data, and the read-only account are in
`LAPTOP_SETUP.md` §3. From the repository root on a laptop where that is already done:

```bash
python -m pip install -r requirements.txt

export REG_DB_URL="postgresql+psycopg2://iia_reader:secret@127.0.0.1:5432/regdb"
export REG_TABLES="VEHICLE_REGISTRATION,OWNERS"      # the whitelist, lookup tables included

uvicorn sources.reg.wrapper:app --host 0.0.0.0 --port 8001     # PDF §9.2 start command
```

`python -m sources.reg.wrapper` does the same. On Windows PowerShell set variables with
`$env:REG_DB_URL = "..."`.

| Variable | Default | Purpose |
|---|---|---|
| `<ID>_DB_URL` | see `sources/<id>/wrapper.py` | SQLAlchemy URL of this laptop's database |
| `<ID>_TABLES` | the PDF §3 tables | whitelist; the only tables `/schema` and `/query` touch |
| `<ID>_DBMS` | engine name | label returned by `/health` |
| `<ID>_PORT` | 8001–8004 | wrapper port |
| `WRAPPER_HOST` | `0.0.0.0` | `127.0.0.1` keeps a wrapper local-only |
| `WRAPPER_STATEMENT_TIMEOUT_S` | `3` | database statement timeout |

Check locally, then from the mediator laptop:

```bash
curl http://127.0.0.1:8001/health
for p in 8001 8002 8003 8004; do curl -s http://192.168.43.1$((p-8000)):$p/health; echo; done
```

### A read-only database account

The wrapper opens every connection read-only; give it an account that cannot write either.

```sql
-- PostgreSQL: REG on regdb (CAM on camdb: plate_captures, cameras)
CREATE ROLE iia_reader LOGIN PASSWORD 'secret';
GRANT CONNECT ON DATABASE regdb TO iia_reader;
GRANT USAGE ON SCHEMA public TO iia_reader;
GRANT SELECT ON vehicle_registration, owners TO iia_reader;

-- MySQL: INS on insdb
CREATE USER 'iia_reader'@'localhost' IDENTIFIED BY 'secret';
GRANT SELECT ON insdb.POLICY_RECORDS TO 'iia_reader'@'localhost';
GRANT SELECT ON insdb.INSURERS TO 'iia_reader'@'localhost';
```

PostgreSQL folds unquoted `VEHICLE_REGISTRATION` to `vehicle_registration`, so the registry's
uppercase names work unchanged. **MySQL on Linux or macOS is case-sensitive about table names**: the
registry and `INS_TABLES` must use the exact `CREATE TABLE` spelling. For SQLite, make `theft.db`
read-only for the wrapper's user; the wrapper also sets `PRAGMA query_only = ON`.

### Keep the database on localhost

- PostgreSQL: `listen_addresses = 'localhost'` in `postgresql.conf`.
- MySQL: `bind-address = 127.0.0.1` in `my.cnf` / `my.ini`.

### Open the wrapper port

```powershell
netsh advfirewall firewall add rule name="IIA REG wrapper" dir=in action=allow protocol=TCP localport=8001
```

```bash
sudo ufw allow 8001/tcp
```

macOS asks the first time Python accepts a connection; allow it.

---

## 3. The mediator laptop

1. Note each laptop's address (`ipconfig`, `ip addr`, `ipconfig getifaddr en0`) and put it in the
   registry's `base_url` (`SOURCE_CATALOG.base_url` in `meta.db`).
2. Validate and see the coverage matrix, joins and the identity source:
   ```bash
   python scripts/validate_registry.py mediator/meta.db
   ```
3. Run the end-to-end contract test (health, registry columns exist in each real `/schema`, rows for a
   known plate):
   ```bash
   pytest -q -m live                       # IIA_LIVE_PLATE=... for another plate
   ```
4. Query: `python -m mediator.executor DL05CD9876`. The first output line names the registry used.

---

## 4. Rehearsing on one machine

```bash
python scripts/run_local.py                     # four mocks on 127.0.0.1:8001-8004 plus the GUI on :8501
python -m mediator.executor DL05CD9876          # second terminal
```

`sources/_mock/run_all.py` is the same thing without the GUI. Installing the real engines on one
machine instead is `LAPTOP_SETUP.md` §2.

The mocks run the real wrapper modules over the PDF §3 schemas; only the database URL differs. Kill a
printed PID to watch that source go `DOWN`, or start with `--only REG,THEFT,CAM`.

**UC6 rehearsal (add a source):**

```bash
python sources/_mock/run_all.py --with-puc
IIA_REGISTRY=sources/_mock/mock_mappings_uc6.json python -m mediator.executor DL01AB1234 --attrs puc_expiry
```

The only difference between the two registries is the PUC entry and `"global_schema":
{"attributes": ["puc_expiry"]}`.

---

## 5. Latency budget

| Stage | Budget | Set in |
|---|---|---|
| TCP connect to a wrapper | 0.5 s, or `timeout_ms` if smaller | `CONNECT_TIMEOUT_S`, `mediator/executor.py` |
| Full reply | `timeout_ms` per source (mock: 1500 ms) | registry |
| Statement inside the database | 3 s | `WRAPPER_STATEMENT_TIMEOUT_S` |
| Whole query | slowest `timeout_ms` + 0.25 s, at worst | executor backstop |

Calls run in parallel, so the total is roughly the slowest source. On the mocks a four-source query
takes 5–10 ms once the mediator process is warm. The first query in a fresh process takes about
0.3 s longer (measured one-time warm-up, not per query), so the CLI shows ~0.3 s while the long-running
Streamlit app pays it once. With one source killed a query takes about 510 ms (the connect budget). A
separate connect budget is needed because Windows retries a refused SYN for ~2.7 s; a live wrapper
accepts TCP in milliseconds even when its database is slow.

---

## 6. When a source is not `OK`

| Status and error | Likely cause | Fix |
|---|---|---|
| `DOWN`, `no TCP connection ... within 500 ms` | wrong IP, laptop off the hotspot, firewall, wrapper not running | `curl http://<ip>:<port>/health` from the mediator |
| `DOWN`, `ConnectError ... refused` | host reachable, nothing on that port | start the wrapper; check `<ID>_PORT` |
| `DOWN`, `HTTP 503: No module named 'pymysql'` | driver missing on that laptop | `python -m pip install pymysql` |
| `DOWN`, `HTTP 503: database unreachable` | database stopped, wrong URL or password | test the URL locally |
| `ERROR`, `HTTP 400: table 'OWNERS' is not in the whitelist` | `<ID>_TABLES` lacks a lookup table the registry joins | add it to `<ID>_TABLES` |
| `ERROR`, `HTTP 400: database rejected the query: no such column` | registry names a column the real schema lacks, or wrong table case on MySQL | `pytest -m live` names the missing columns |
| `ERROR`, `could not build SQL` | unknown template hole, or no `{joins}` hole for a joined column | `CONTRACTS.md` §2.5 |
| `TIMEOUT`, `HTTP 504: statement timeout` | slow query, usually no index on the plate column | index it |
| `TIMEOUT`, `no response within ... ms` | overloaded laptop or slow network | raise that source's `timeout_ms` |

The executor ignores `HTTP_PROXY` and similar variables, so a system proxy cannot swallow LAN traffic.

---

## 7. Known limitations

- **No authentication and plain HTTP on the wrapper API.** Anyone on the hotspot can read the
  whitelisted tables. Acceptable for coursework on a private hotspot.
- **The guard is textual, not a parser** (`CONTRACTS.md` §1). The read-only connection and account are
  what actually prevent writes.
