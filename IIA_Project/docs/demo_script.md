# 10-Minute Evaluation Demo Script

Each row: the click/command, then one sentence to say. Story plates: `DL01AB1234` (clean),
`DL05CD9876` (expired policy), `HR26EF4455` (stolen), `UP16GH1122` (cloned plate),
`MH12IJ7788` (unregistered, seen by camera), `DL03SC5566` (scrapped, seen again).

| Timing | Action | What to Say |
| :--- | :--- | :--- |
| **0:00 – 0:45** | Pitch | *"Four autonomous databases — Registration, Insurance, Theft and a Road Camera network — were designed in isolation and share no keys, no naming convention, not even the same DBMS. We built a federated mediator: it plans which sources can answer a question, rewrites it into each source's own SQL, executes live over the network, integrates the answers with provenance and trust, and produces a defensible decision — including refusing to decide when a source is unreachable. Nothing is copied centrally; every query reads live."* |
| **0:45 – 1:30** | Heterogeneity | Show the four wrapper terminals (or `configure_cluster.py --probe`) and one plate spelled four ways: `DL01AB1234` (REG), `DL-01-AB-1234` (INS), `dl 01 ab 1234` (THEFT), OCR-corrupted `DLO1AB1Z34` (CAM). *"Three DBMS engines, four plate spellings, four date encodings — nothing is shared but the road."* |
| **1:30 – 2:15** | Matcher tab | Open **3. Matcher & Heatmap**, pick a source, run the matcher against its live `/schema`. *"This discovers that `vehicle_reg` and `plate_number` are the same fact from name similarity, type compatibility and real sample values — no one hard-coded the correspondence."* |
| **2:15 – 3:15** | Investigate + Plan Trace | In **1. Investigate**, query `DL05CD9876`. Show the decision banner (`UNINSURED — REPORT` or a ladder tier), source chips, profile, provenance. Switch to **2. Plan Trace**: open the SQL expanders. *"One canonical plate becomes four different SQL statements, dispatched in parallel, and every attribute here remembers which agency said it and how much we trust them."* |
| **3:15 – 4:15** | SQL console — read | Open **6. SQL Console**, pick `INS`, run the pre-filled `SELECT` example for `DL-05-CD-9876`. *"This is the agency's own database, in its own dialect — the mediator never sees this table name, only the registry does."* |
| **4:15 – 5:30** | Live write on another laptop | Still in SQL Console (or the sidebar **Source Editor**), pick `INS`, action `renew` (or an `UPDATE … SET policy_until` statement), apply it against the real INS laptop. Show the before/after row count. *"That write just happened on the insurance laptop's own MySQL database, through its own admin endpoint — the mediator itself only ever sends SELECT."* |
| **5:30 – 6:00** | Re-query | Back in Investigate, click "re-run in Investigate" (or re-search `DL05CD9876`). Decision flips to `CLEAR` in under a second. *"No ETL, no cache, no restart — freshness is the whole thesis."* |
| **6:00 – 7:00** | Unknown plate onboarding | Type a fresh plate nobody has seen, e.g. `KA05MN9999`. Decision shows `UNKNOWN VEHICLE — NOT REGISTERED` with the onboarding wizard. Step 1 Register (REG), Step 2 Insure (INS), Re-evaluate. *"The GUI handles the plate end-to-end — register it, insure it, and the decision becomes CLEAR live, the same as the Supreme Court's mandate for real-time ANPR-to-VAHAN registration checks."* |
| **7:00 – 7:45** | OCR upload | In Investigate, upload a plate photo (or a synthetic one). EasyOCR proposes the top candidate plus "did you mean" repairs for common misreads (O/0, I/1, B/8, S/5). Click a candidate to query. *"This is the camera's own job — ANPR reads a plate, we repair the common OCR confusions before looking it up."* |
| **7:45 – 8:30** | Watchlist / risk | Query `HR26EF4455`. Point to the risk gauge and its factor list (base decision points + conflicts + staleness + trust). Add a plate to the watchlist in **7. Watchlist & Alerts**, re-query it, show the alert logged. *"This mirrors the UK MIB's Operation Tutelage marker and India's IIB ANPR flags — a persistent hit raises a standing alert, not just a one-off answer."* |
| **8:30 – 9:00** | UC5 — stop a wrapper | Kill the INS wrapper process. Re-query `DL01AB1234`. INS chip turns `DOWN`, decision degrades to `UNDETERMINED`/`LOW`, reasons name INS explicitly. *"The system refuses to guess when a source is unreachable — that refusal is the safety property this whole project is graded on."* Restart the wrapper, re-query, back to `CLEAR`. |
| **9:00 – 9:20** | UC6 — register PUC | In **4. Catalog & Registry**, register the PUC source (port 8005) if not already live. Re-query any plate — `puc_expiry` appears. *"A fifth agency joins with zero engine code changes — only a metadata registration."* |
| **9:20 – 9:40** | Self-check | Open **8. Citizen Self-Check**, look up a plate. Show only registration/insurance/PUC/stolen status — no owner name, no camera location — and the "asked N of M sources" caption. *"This is the askMID analogue: a citizen gets a data-minimised answer, and the mediator only contacted the sources it actually needed."* |
| **9:40 – 10:00** | Ministry evidence PDF | Back in Investigate on a flagged plate, click "File Report to Ministry", download the PDF. *"The bundle carries the decision, the rule that fired, every source's exact SQL and status, camera evidence, and our own commit hash — enough for a Ministry official to defend the decision, not just read the verdict."* |

**If time is short, cut in this order:** self-check (9:20) → UC6 PUC (9:00) → OCR (7:00), and keep
the SQL-console write → re-query (4:15–6:00) and the UC5 refusal (8:30) no matter what — those two
are the rubric's core claims (freshness and honest failure).

---

# Challan Guard (5 min)

The standout segment: **verify before you fine.** Run it after the 10-minute script, or in place of
7:00–9:00 if only one can be shown.

**Before the demo** (mediator laptop, once):

```powershell
python scripts\seed_challan_cases.py       # files the five demo ANPR sightings as CANDIDATE cases
```

Then open the GUI, tab **2. Challan Guard**. For every case below the gesture is the same: pick the
case in the queue selector, press **Verify (live)**, and read the stepper — Sources reachable →
Identity → Clone signal → Theft → Registration → Insurance at sighting time. Each step is one live
federated query; nothing on this page is precomputed.

Opening line: *"An ANPR camera cannot issue a fine. It can only say 'I think I saw this plate here
at this time.' Everything between that and a ₹2,000 challan is what this tab does — and it does it
against the four agencies live, at the moment of the decision."*

| # | Case | Verify → | What to say |
| :--- | :--- | :--- | :--- |
| **1** | Read `DL05CD9B76` at Sector 29 Crossing, 04 Sep 11:00 (OCR confidence 0.71) | **ISSUED ₹2000**, resolved to `DL05CD9876` — *"uninsured on 2026-09-04: policy expired 2026-06-10"* | *"The camera read a B where the plate says 8. `DL05CD9B76` is registered to nobody, so a naive system either drops the case or fines a ghost. The guard substitutes one confusable character at a time, finds `DL05CD9876` registered with the make and colour the camera actually observed, and only then asks the insurer. The policy expired in June. The fine is owed — by the right person."* |
| **2** | Read `DLO1AB1234` at Ring Road Junction, 04 Sep 08:30 | **REJECTED**, resolved to `DL01AB1234` — *"insured on 2026-09-04 (policy valid to 2027-01-14) — no offence"* | *"Same class of misread — letter O for zero — but this time the real vehicle is fully insured. About 90% of wrongful challans in India start exactly here. No fine is ever raised, no citizen has to appeal, and the whole reason is that we checked the insurer at fine time instead of a copied table."* |
| **3** | `UP16GH1122`, Yamuna Expressway Toll, Agra, 04 Sep 11:30 | **HOLD** — *"impossible travel: Sector 29 Crossing → Yamuna Expressway Toll, Agra in 30 min (340 km/h) — cloned plate suspected"* | *"The same plate was in Gurgaon half an hour earlier, 170 km away. No car does 340 km/h, so the plate exists twice. This is the signal UK patent GB2448780A describes and the Met's ANPR uses. The guard refuses to fine either sighting and puts the plate on the watchlist as a clone suspect — because fining here means fining whichever owner is innocent."* |
| **4** | `HR26EF4455`, Cyber City Entrance, 02 Sep 19:45 | **REJECTED** — *"vehicle reported stolen before sighting — route to police, do not fine owner"* | *"Police records say this car was stolen before the camera saw it. The person driving it is not the person we would have fined. Note the ordering: theft is checked before insurance, because 'your stolen car was uninsured' is not a sentence any enforcement system should produce."* |
| **5a** | `DL01AB0002`, NH8 Toll Plaza, 04 Sep 07:10 | **ISSUED ₹2000** — *"uninsured on 2026-09-04: no policy on record"* | *"Correctly read, registered, active, and genuinely uninsured. This is the challan the Supreme Court's order is about — and it is the one we are now going to have overturned, live."* |
| **5b** | **Teammate, on laptop 2 (INS):** `python scripts\mutate_source.py add_policy INS DL01AB0002 --start 01/01/2026 --until 01/01/2027` | — | *"The insurance laptop just backdated a policy in its own MySQL database. Nobody told the mediator. There is no sync step, because there is nothing to sync."* |
| **5c** | Tab **5. Citizen Check** → *Dispute a challan* → case id of 5a, reason "I renewed before that date" | **CANCELLED** — *"record changed since issue: insurance_expiry was none now 2027-01-01"* | *"The dispute re-ran the identical verification against the live sources and the answer changed, so the challan is cancelled and the diff says exactly which fact moved. The citizen sees their own masked plate, the sighting and the outcome — no owner record, no camera coordinates."* Undo afterwards: `python scripts\mutate_source.py delete_policies INS DL01AB0002` |
| **6** | **Stop the INS wrapper**, then Verify any unverified case | **HOLD** — *"INS unreachable — refusing to fine on partial evidence"* | *"This is the property the whole project is graded on. A missing insurer is not evidence of no insurance. With a warehouse you would have a stale row and issue the fine anyway; federating makes the outage visible, and the only honest verdict is to refuse."* Restart the wrapper and re-verify to close the loop. |

Closing line: *"Five sightings, one fine issued to the right owner, three wrongful fines prevented,
and one refusal. The counter at the top of the tab — 'wrongful fines prevented' — is the number
this feature exists to move, and every one of those outcomes came from a query executed while you
watched."*

**If time is short:** cases 1 and 2 (misread both ways) and case 6 (refusal) carry the argument;
case 5's dispute loop is the one to show if a second laptop is available.
