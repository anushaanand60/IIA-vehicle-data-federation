# Demo day runbook

One page. Every command in the order it is typed. `docs/LAPTOP_SETUP.md` explains *why* each step
exists and covers the real database engines in full; this file is what you follow on the day.

| Laptop | Owner | Source | Engine | Wrapper port | Also runs |
|---|---|---|---|---|---|
| 1 | *you* | `REG` | PostgreSQL | 8001 | mediator + the website |
| 2 | | `INS` | MySQL | 8002 | |
| 3 | | `THEFT` | SQLite | 8003 | |
| 4 | | `CAM` | PostgreSQL | 8004 | |
| 1 or 5 | | `PUC` | SQLite | 8005 | the extensibility story |

---

## Phase 0 — every laptop, the night before

```powershell
cd C:\path\to\IIA-vehicle-data-federation\IIA_Project
git pull
.venv\Scripts\Activate.ps1                 # first time: py -3.12 -m venv .venv
python -m pip install -r requirements.txt

python scripts\load_source.py REG
python scripts\load_source.py INS
python scripts\load_source.py THEFT
python scripts\load_source.py CAM
python scripts\load_source.py PUC
python scripts\seed_mappings.py

python -m pytest -q                         # expect: 646 passed, 3 deselected
```

Databases and `meta.db` are gitignored, so every clone rebuilds them with the loader commands.
If `git pull` refuses because of local edits: `git stash`, pull, then `git stash pop`.

**Do not run `pytest` and the website at the same time** — they fight over ports 8001-8005.

---

## Phase 1 — the network, on the day

Campus Wi-Fi isolates clients from each other. Use a phone hotspot. Connect all four laptops.

Each laptop prints its own address:

```powershell
ipconfig | Select-String IPv4
```

Each **source** laptop opens its own wrapper port once, in an **Administrator** PowerShell
(8001 REG, 8002 INS, 8003 THEFT, 8004 CAM, 8005 PUC):

```powershell
netsh advfirewall firewall add rule name="IIA wrapper 8002" dir=in action=allow protocol=TCP localport=8002
```

Only the wrapper port opens. The database port (5432 / 3306) stays shut and the engine stays bound
to `127.0.0.1` — each agency publishes an API, never its database.

---

## Phase 2 — start the sources (laptops 2, 3, 4)

Each teammate runs **only their own block**, then reads out their IP address.

**Laptop 2 — INS on MySQL**

```powershell
python scripts\load_source.py INS --url "mysql+pymysql://root:<pw>@127.0.0.1:3306/insdb" --verify
python scripts\serve.py INS --db-url "mysql+pymysql://iia_reader:secret@127.0.0.1:3306/insdb"
```

**Laptop 3 — THEFT on SQLite**

```powershell
python scripts\load_source.py THEFT --verify
python scripts\serve.py THEFT
```

**Laptop 4 — CAM on PostgreSQL**

```powershell
python scripts\load_source.py CAM --url "postgresql+psycopg2://postgres:<pw>@127.0.0.1:5432/camdb" --verify
python scripts\serve.py CAM --db-url "postgresql+psycopg2://iia_reader:secret@127.0.0.1:5432/camdb"
```

The first `serve.py` remembers the URL in `sources/<id>/laptop.env`, so after a reboot or a pull it
is just `python scripts\serve.py INS`. Each one prints its IP and the exact `--set` line for laptop 1.

Check locally before telling laptop 1 you are up:

```powershell
curl http://127.0.0.1:8002/health
```

`"up":true` **and the right `"dbms"`**. If it says `SQLite` on laptop 2 or 4, the URL never reached
the process and the wrapper fell back to the local file.

---

## Phase 3 — laptop 1, the mediator

Start REG **before** the website. The website auto-starts any wrapper whose port is free, so if the
site goes first it will occupy 8001 with the local SQLite copy instead of your PostgreSQL.

```powershell
# terminal 1 - this laptop's own source
python scripts\serve.py REG --db-url "postgresql+psycopg2://iia_reader:secret@127.0.0.1:5432/regdb"
```

```powershell
# terminal 2 - point the mediator at the other laptops, using their real addresses
python scripts\configure_cluster.py --set REG=127.0.0.1 INS=192.168.43.12 THEFT=192.168.43.13 CAM=192.168.43.14
python scripts\configure_cluster.py --probe
```

`--probe` must print `4/4 source(s) reachable`, the right engine on each line, and `admin: on`
everywhere. `unreachable` means a missing firewall rule, a stopped wrapper, or a stale address.

Then seed the mediator's own metadata and the challan queue, and start the site:

```powershell
python scripts\seed_mappings.py
python scripts\seed_challan_cases.py         # 5 ANPR candidates for Challan Guard
streamlit run app\app.py                     # http://localhost:8501
```

Hard-refresh the browser once (Ctrl+F5) so no old stylesheet is cached.

---

## Phase 4 — the demo

Full narration is in `docs/demo_script.md`. The commands that go with it:

**Live write from another laptop.** Teammate 2 types this while the plate is on screen:

```powershell
python scripts\mutate_source.py show   INS DL05CD9876
python scripts\mutate_source.py renew  INS DL05CD9876 --until 31/12/2027
```

Re-run the same plate on laptop 1 — the verdict flips from `UNINSURED` to `CLEAR` with no sync
step, because nothing was ever copied. Undo it with `expire INS DL05CD9876 --until 10/06/2026`.

Other sources, same idea:

```powershell
python scripts\mutate_source.py steal THEFT DL01AB1234
python scripts\mutate_source.py sight CAM   DL01AB1234 --make Kia --model Seltos --colour Blue
```

Undo those two with `clear THEFT DL01AB1234` and `delete_sightings CAM DL01AB1234`.

**Refusal.** Teammate 3 presses Ctrl+C on the THEFT wrapper. Re-run the plate: the answer is
`UNDETERMINED`, not a guess. Restart with `python scripts\serve.py THEFT`.

**Challan Guard, the five seeded cases.** Open the Challan Guard page, pick a case, press
*Verify (live)*:

| Case | What happens |
|---|---|
| `DL05CD9B76` | misread corrected to `DL05CD9876`, ISSUED Rs 2000 |
| `DLO1AB1234` | resolves to `DL01AB1234`, REJECTED — insured, wrongful fine prevented |
| `UP16GH1122` | HOLD — impossible travel, cloned plate suspected |
| `HR26EF4455` | REJECTED — stolen before the sighting, route to police |
| `DL01AB0002` | ISSUED Rs 2000 — no policy on record |

**The dispute loop.** With case 5 issued, teammate 2 runs:

```powershell
python scripts\mutate_source.py add_policy INS DL01AB0002 --start 01/01/2026 --until 01/01/2027
```

Go to *Citizen Check* → *Dispute a challan* → case number `5` → submit. The fine is **CANCELLED**,
citing exactly what changed. Undo with `delete_policies INS DL01AB0002`.

**Hold on a dead source.** Stop the INS wrapper, verify case 1 again: HOLD, "INS unreachable —
refusing to fine on partial evidence".

---

## Phase 5 — if the hotspot dies

Everything runs on laptop 1 alone. Say out loud that this is rehearsal mode.

```powershell
python scripts\configure_cluster.py --local
python run_system.py                          # five wrappers + the site
```

To go back to the real cluster afterwards, re-run the `--set` line from Phase 3.

---

## When something is wrong

| Symptom | Cause | Fix |
|---|---|---|
| `--probe` says `unreachable` | firewall rule missing, wrapper stopped, IP changed after reconnect | re-run Phase 1 on that laptop, re-check `ipconfig` |
| `/health` says `SQLite` on laptop 2 or 4 | `<SRC>_DB_URL` never reached the wrapper | restart with `serve.py <SRC> --db-url "..."` |
| every profile is blank | `MAPPING_REGISTRY` is empty | `python scripts\seed_mappings.py` |
| a plate decides differently on two laptops | one laptop serves data built from older CSVs | `git pull`, then `load_source.py <SRC> --verify`, then restart |
| `Address already in use` | a previous wrapper is still running | close that terminal, or stop the stray python process, then start again |
| the site looks like the old design | cached stylesheet | Ctrl+F5 |
