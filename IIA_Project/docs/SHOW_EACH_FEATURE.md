# How to show each feature, and how far it goes

For each feature: what it proves, the exact clicks or commands, what to point at on screen, and
where it stops. Bring-up is in `docs/DEMO_DAY.md`; this file assumes the site is open on laptop 1.

Every plate below is real. An audit on 17 Sep 2026 checked every plate hard-coded in the app,
scripts and seed data against the five databases. None is fake. The ones missing from a source are
missing on purpose, and the story says so.

| Plate | REG | INS | THEFT | CAM | PUC | Story |
|---|---|---|---|---|---|---|
| `DL01AB1234` | 1 | 1 | 0 | 1 | 1 | clean, everything agrees |
| `DL05CD9876` | 1 | 1 | 0 | 1 | 1 | policy expired 10/06/2026 |
| `HR26EF4455` | 1 | 1 | 1 | 1 | 1 | reported stolen, case open |
| `UP16GH1122` | 1 | 1 | 0 | 2 | 1 | camera saw a different car: cloned plate |
| `MH12IJ7788` | 0 | 0 | 0 | 1 | 0 | camera only, never registered |
| `DL01AB0002` | 1 | 0 | 0 | 1 | 0 | registered, never insured |
| `DL05CD9B76` | 0 | 0 | 0 | 1 | 0 | camera misread of `DL05CD9876` |
| `DLO1AB1234` | 0 | 0 | 0 | 1 | 0 | camera misread of `DL01AB1234` |

---

## 1. Typing a plate reads; it never adds

**What it proves.** A lookup is a live read across the agencies, and nothing is copied or created.

**Show it.** Investigate → type `dl-05 cd 9876` in any spelling → the verdict appears.
Then open **Plan Trace**: the SQL each agency received, in its own column names and plate format,
and how long each took.

**Point at.** "Who answered" lists each source with its status and row count. `0 rows` still means
the source answered: no policy is a fact, not a failure.

**Where it stops.** Nothing is written by a lookup. The only way a lookup leads to a write is the
next section, and that needs a button press.

## 2. A plate nobody knows

**What it proves.** An unknown plate is reported as unknown, not guessed. The operator can then
onboard it live into the agencies' own databases.

**Show it.** Investigate → type `KA05MN9999` → the verdict is `UNKNOWN VEHICLE — NOT REGISTERED`.
The page offers **Register vehicle**, then **Add policy**, then **Re-evaluate this plate**. Each
button is one POST to that agency's own wrapper. After re-evaluate, the same plate now reads
registered and insured.

**Where it stops.** Registration creates one owner and one vehicle row with the details typed into
the form. It is a demo of live writes through the agency's API, not a full RTO workflow.

## 3. The five story vehicles

Investigate → press each demo button in turn.

| Button | Verdict you should see | The sentence to say |
|---|---|---|
| `DL01AB1234` | CLEAR | four agencies agree, and the verdict says which sources it did *not* ask |
| `DL05CD9876` | UNINSURED — REPORT | expiry is read from INS in its own `dd/mm/yyyy` format and compared to today |
| `HR26EF4455` | STOLEN — ALERT POLICE | police record wins over everything else |
| `UP16GH1122` | SUSPICIOUS — POSSIBLE CLONED PLATE | REG says one car, the camera saw another |
| `MH12IJ7788` | UNREGISTERED / SUSPICIOUS | a camera saw it; no agency ever registered it |

The integrated profile shows every attribute tagged with the source it came from and that source's
trust score.

**Where it stops.** The decision rules are fixed, explainable rules, not a learned model. "Today"
is pinned to 04/09/2026 so every laptop gives the same answers on any day.

## 4. Refusing when a source is down

**Show it.** Teammate 3 presses Ctrl+C on the THEFT wrapper. Re-run `DL01AB1234`.
The verdict becomes UNDETERMINED and names THEFT as unreachable. Restart with
`python scripts\serve.py THEFT` and it is CLEAR again.

**Where it stops.** There are no retries and no cached fallback, on purpose. A cached answer would
defeat the freshness argument.

## 5. Changing data — five ways, all live

Every route below changes the agency's own database. The next query on **any** laptop sees the
change, because the mediator stores no copy. After each change, re-run the plate in Investigate.

### 5a. Website — Source Editor (named actions)

Sidebar → **Source Editor** → pick a source, an action and a plate → submit.

| Source | Actions |
|---|---|
| REG | register, set_status, unregister |
| INS | renew, expire, add_policy, delete_policies |
| THEFT | steal, clear, shred, delete_incidents |
| CAM | sight, delete_sightings |
| PUC | issue, revoke |

Example: INS → renew → `DL05CD9876` → Investigate flips UNINSURED to CLEAR.
Undo: INS → expire → `DL05CD9876`, until `10/06/2026`.

### 5b. Website — SQL Console (your own SQL)

Sidebar → **SQL Console** → pick the source → type one statement → run.
A `SELECT` goes to the read-only `/query`. An `INSERT`, `UPDATE` or `DELETE` goes to `/admin/sql`
on the laptop that owns that data. The console shows the row count before and after.

These are all reversible. Run the first line, show the verdict change, then run the undo line.

```sql
-- REG: registration suspended  ->  DL01AB1234 becomes REGISTRATION INVALID
UPDATE VEHICLE_REGISTRATION SET reg_status = 'SUSPENDED' WHERE registration_no = 'DL01AB1234'
UPDATE VEHICLE_REGISTRATION SET reg_status = 'ACTIVE' WHERE registration_no = 'DL01AB1234'

-- INS: policy renewed  ->  DL05CD9876 becomes CLEAR
UPDATE POLICY_RECORDS SET policy_until = '31/12/2027', is_active = 1 WHERE vehicle_reg = 'DL-05-CD-9876'
UPDATE POLICY_RECORDS SET policy_until = '10/06/2026', is_active = 0 WHERE vehicle_reg = 'DL-05-CD-9876'

-- THEFT: theft reported  ->  DL01AB1234 becomes STOLEN — ALERT POLICE
INSERT INTO CRIME_RECORDS (incident_id, vehicle_number, fir_no, reported_date, incident_type, stolen_flag, recovered_flag, case_status, police_station) VALUES (9001, 'dl 01 ab 1234', 'FIR9001/2026', 1788000000, 'THEFT', 'Y', 'N', 'OPEN', 'Demo PS')
DELETE FROM CRIME_RECORDS WHERE incident_id = 9001

-- CAM: camera sees a different car  ->  DL01AB1234 becomes SUSPICIOUS — POSSIBLE CLONED PLATE
INSERT INTO PLATE_CAPTURES (capture_id, plate_id, camera_id, captured_at, observed_make, observed_model, observed_colour, ocr_confidence) VALUES (9001, 'DL01AB1234', 'CAM001', '2026-09-04T12:00:00', 'Kia', 'Seltos', 'Blue', 0.97)
DELETE FROM PLATE_CAPTURES WHERE capture_id = 9001
```

Each agency stores the plate its own way: `DL01AB1234` in REG, `DL-05-CD-9876` in INS,
`dl 01 ab 1234` in THEFT. That is the heterogeneity the mediator absorbs on reads, so raw SQL has
to use each agency's own spelling. The ids are written explicitly because MySQL and PostgreSQL do
not auto-number these tables.

### 5c. PowerShell on the owning laptop — named actions

```powershell
python scripts\mutate_source.py show  INS DL05CD9876
python scripts\mutate_source.py renew INS DL05CD9876 --until 31/12/2027
python scripts\mutate_source.py expire INS DL05CD9876 --until 10/06/2026
python scripts\mutate_source.py steal THEFT DL01AB1234
python scripts\mutate_source.py clear THEFT DL01AB1234
python scripts\mutate_source.py sight CAM DL01AB1234 --make Kia --model Seltos --colour Blue
python scripts\mutate_source.py delete_sightings CAM DL01AB1234
```

### 5d. PowerShell from any laptop — raw SQL over the agency's API

This is the strongest proof for a viva. Laptop 3 changes laptop 2's database through laptop 2's
published API, and laptop 1 sees it. Replace the address with the owning laptop's IP.

```powershell
$ins = "http://192.168.43.12:8002"

# read
$read = @{ sql = "SELECT policy_id, vehicle_reg, policy_until, is_active FROM POLICY_RECORDS WHERE vehicle_reg = 'DL-05-CD-9876'" } | ConvertTo-Json
(Invoke-RestMethod -Method Post -Uri "$ins/query" -ContentType "application/json" -Body $read).rows | Format-Table

# write
$write = @{ sql = "UPDATE POLICY_RECORDS SET policy_until = '31/12/2027', is_active = 1 WHERE vehicle_reg = 'DL-05-CD-9876'" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$ins/admin/sql" -ContentType "application/json" -Body $write

# undo
$undo = @{ sql = "UPDATE POLICY_RECORDS SET policy_until = '10/06/2026', is_active = 0 WHERE vehicle_reg = 'DL-05-CD-9876'" } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$ins/admin/sql" -ContentType "application/json" -Body $undo
```

The same pattern works for every source. Ports: REG 8001, INS 8002, THEFT 8003, CAM 8004, PUC 8005.
This was run for real on 17 Sep 2026: the write returned `rows_affected: 1`, the read showed the
new date, and the undo restored `10/06/2026`.

### 5e. PowerShell on the owning laptop — the database's own client

The agency's database is a normal database. Its owner can use its own client directly, with the
same SQL as 5b.

```powershell
psql -U postgres -d regdb                      # laptop 1, REG
mysql -u root -p insdb                         # laptop 2, INS
python -c "import sqlite3; c=sqlite3.connect('sources/theft/theft.db'); print(c.execute('SELECT COUNT(*) FROM CRIME_RECORDS').fetchone())"   # laptop 3, THEFT
psql -U postgres -d camdb                      # laptop 4, CAM
```

### What the write path refuses

These were sent to `/admin/sql` on 17 Sep 2026 and all were rejected:

| Sent | Answer |
|---|---|
| `DROP TABLE POLICY_RECORDS` | statement must start with INSERT, UPDATE or DELETE |
| `DELETE FROM POLICY_RECORDS; DELETE FROM INSURERS` | multiple statements are not allowed |
| `UPDATE OWNERS SET ...` sent to INS | table 'OWNERS' is not in the whitelist |

Reads through `/query` refuse every write. An agency can switch its writes off entirely by starting
its wrapper with `<SOURCE>_ADMIN=off`; then `/admin/*` answers 404.

**Where it stops.** There is no login on `/admin`. On a phone hotspot that is fine for a demo. A real
agency would put authentication in front of it, and that is the honest answer if asked.

## 6. Challan Guard — verify before fine

**The problem it answers.** Camera-issued e-challans are often wrong: characters misread (0/O, 8/B),
cloned plates, stolen cars fined to their victims. Delhi Police built an online dispute system
because of it, and the Supreme Court's 2025–26 order to auto-challan uninsured vehicles multiplies it.

**What it does.** A camera sighting becomes a *candidate*, never a fine. Pressing Verify runs six
checks live against the agencies, in order, and stops at the first that decides:

1. **Are the sources reachable?** If REG, INS or THEFT is down, HOLD. It never fines on half the
   evidence.
2. **Who is this really?** It tries the plate as read plus single-character swaps from a fixed
   confusion table (0↔O, 8↔B, 1↔I, 5↔S, 2↔Z, 6↔G), and scores each registered candidate against
   the make and colour the camera saw. A misread gets corrected; two equally good matches HOLD.
3. **Cloned plate?** It pulls every camera sighting of that vehicle and computes the speed needed
   between consecutive sightings. Faster than 160 km/h means HOLD, and the plate is watchlisted.
4. **Stolen before the sighting?** REJECT, route to police, do not fine the owner.
5. **Registration scrapped or invalid?** REJECT with the reason.
6. **Insured on the day of the sighting?** Yes means REJECT, no offence. No means ISSUE, Rs 2000,
   or Rs 4000 if this vehicle already has an issued challan.

Every step, every source answer and its timestamp are stored as the case's evidence bundle. The
mediator writes only to its own `meta.db`. It never writes to an agency.

**Show it.**

1. Challan Guard → **Where the queue stands** shows the counts.
2. Pick case 1 in the **Case** box → **Verify (live)**. Read the steps aloud from the stepper.
3. Do the same for cases 2–5. Then press **Verify all candidates (live)** for the other seven, which are real camera captures. The counts end at 6 issued, 5 rejected, 1 held, 5 wrongful fines prevented.

| Case | Read as | Result |
|---|---|---|
| 1 | `DL05CD9B76` | corrected to `DL05CD9876`, ISSUED Rs 2000, policy expired 10/06/2026 |
| 2 | `DLO1AB1234` | corrected to `DL01AB1234`, REJECTED, insured until 14/01/2027: wrongful fine prevented |
| 3 | `UP16GH1122` | HOLD, Sector 29 Crossing to Agra in 30 minutes at 340 km/h: cloned plate |
| 4 | `HR26EF4455` | REJECTED, stolen before the sighting: route to police |
| 5 | `DL01AB0002` | ISSUED Rs 2000, no policy on record |

**The two live twists.**

- *A source dies.* Stop the INS wrapper, verify case 1 again: HOLD, "INS unreachable — refusing to
  fine on partial evidence".
- *The record changes after the fine.* See section 7.

**Where it stops.** The confusion table covers one character per plate. Travel speed uses
straight-line distance between camera coordinates, which under-estimates road distance and so never
flags an honest driver. Fine amounts follow MV Act §196 and ignore state variations.

## 7. Citizen Check and the dispute loop

**What it proves.** A citizen sees only what concerns their own vehicle, and a dispute is decided by
re-checking the live records, not by a clerk reading an old printout.

**Show it.**

1. Verify case 5 (`DL01AB0002`) so it is ISSUED.
2. Teammate 2, on the INS laptop:
   ```powershell
   python scripts\mutate_source.py add_policy INS DL01AB0002 --start 01/01/2026 --until 01/01/2027
   ```
3. Sidebar → **Citizen Check** → *Dispute a challan* → case `5`, any reason → submit.
4. Result: **CANCELLED**, "record changed since issue: insurance_expiry was none now 2027-01-01".
5. Undo: `python scripts\mutate_source.py delete_policies INS DL01AB0002`.

If nothing changed, the same dispute comes back **UPHELD** with "re-verified live".

**Where it stops.** The citizen view masks the middle of the plate and shows no owner name. There is
no identity check on who files a dispute.

## 8. Watchlist, alerts, reports and the audit trail

- **Watchlist.** Add `UP16GH1122` with a reason. Query it in Investigate: an alert fires and is
  logged with time and camera location. Challan Guard adds clone suspects here by itself.
- **Reports.** From any Investigate verdict, press **File report to the Ministry**. The Reports page
  lists it, and the evidence bundle downloads as a PDF: sources asked, their answers, the rule that
  fired and the code version.
- **Query audit log.** Reports → audit log lists every lookup: plate, sources asked, each status,
  verdict, time taken. That is the regulated-access record a real bureau keeps.

**Where it stops.** Reports are stored locally in `meta.db`; nothing is actually sent to a ministry.

## 9. Catalog and extensibility

**What it proves.** A new agency can be added without changing mediator code.

**Show it.** Sidebar → **Catalog** shows each registered source, its address, engine, trust score and
the mapping rules. The PUC pollution-certificate source is the fifth agency: register it from the
form and query `DL01AB1234` again. The profile now includes PUC validity.

**Where it stops.** Registering a source needs its mapping rules. The matcher proposes them; a human
approves them.

## 10. Schema Matcher

**What it proves.** Correspondences between each agency's columns and the global attributes are
discovered from names and sample values, not hand-typed.

**Show it.** Sidebar → **Matcher** → pick a source → the proposed matches with scores. On the agreed
gold standard it finds 23 of 23 with no false matches.

**Where it stops.** Transforms such as date formats and join paths are confirmed by a person, which
is standard practice for schema matching.

## 11. Plate photo

**Show it.** Investigate → *Or upload a plate photo* → pick an image of a plate. The reading and
close alternatives appear; choose one to investigate.

**Where it stops.** Runs offline with EasyOCR. The first run downloads its model, so do it once
before the demo. Blurry or angled photos may read badly, which is exactly why Challan Guard exists.
