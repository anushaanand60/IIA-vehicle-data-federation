# Phase 3 walkthrough: context-driven exploration

For the team. It explains what the phase-3 work does, how it does it, and where the code lives, in the order a
question travels through the system. `WHAT_WAS_BUILT.md` is the rubric checklist; this is the story behind it. The
last section lists open problems and ideas for the next round.

---

## 1. The idea in one paragraph

Phases 1–2 answered a fixed question: *is this plate insured?* Phase 3 answers an open one: *what else do the
agencies know that is related to this plate, or to this description?* That related material is the **context**: the
owner, the previous owner, the policy and its claims, the FIR and the people named in it, the cameras that saw the
car, the pollution test, and the documents that mention any of these. It is spread over six autonomous databases
that share no keys. The naive way to collect it is to copy every matching row and everything joined to it into one
place, then rank it there. Our way is to make **each database rank its own related rows and send only its best
few**, together with a guarantee about what it held back. The mediator follows links into other databases only
while they can still beat what it already has. Documents are then searched *with* that context rather than with the
raw question. The answer is one readable case file.

```
question ─► understand ─► each DB walks its own FK graph ─► best n + bound per DB
                                  ▲                                   │
                                  │   follow chassis / plate / person │
                                  └──── into other DBs, best bound ◄──┘   (stop when nothing left can win)
                                                    │
                                   context rows ────┼──► documents searched with the context (DOCS source)
                                                    │
                       warning signs · timeline · who-says-what · cited summary · cost receipt
```

## 2. The six sources (R1)

Each source grew from 1–2 tables to 4–5 tables joined by foreign keys. The old tables are untouched, so phases 1–2
still score 621/621.

| Source | Engine | Tables | Joins to other sources through |
|---|---|---|---|
| REG | PostgreSQL | VEHICLE_REGISTRATION, OWNERS, VEHICLE_IDENTITY, OWNERSHIP_HISTORY, OWNER_CONTACTS | plate, chassis, owner |
| INS | MySQL 8 | POLICY_RECORDS, INSURERS, POLICY_HOLDERS, POLICY_PARTIES, CLAIMS | plate, chassis, holder |
| THEFT | SQLite | CRIME_RECORDS, STATIONS, STOLEN_PROPERTY, PERSONS, INCIDENT_PERSONS | plate, chassis, person |
| CAM | PostgreSQL | PLATE_CAPTURES, CAMERAS, ZONES, CAMERA_ZONES, CHALLANS | plate (OCR-noisy) |
| PUC | SQLite | POLLUTION_CERT, TEST_CENTRES, ANALYSERS, CERT_TESTS | plate |
| DOCS | SQLite | DOCUMENTS (1,152 texts: FIR narratives, surveyor reports, accident reports, news, RTO notices, witness statements) | words only, no keys |

- Every agency keeps its own habits: INS dates are `DD/MM/YYYY` text, THEFT uses epoch integers, CAM uses ISO
  timestamps, plates are written four different ways, and people's names are spelled differently in each agency.
- `scripts/phase3_generate.py` and `phase3_plant.py` (seed 20261111) write the data and plant 18 stories:
  - alibis (a PUC test while the car is officially stolen);
  - quick claims (a theft claim days after the policy starts);
  - transfers just before a loss;
  - a person ring (one mobile shared by several owners and accused);
  - a cloned plate;
  - hard negatives.
- `data/context_graph.json` is the only place the mediator learns the schema. It holds each table's primary key,
  plate, chassis, time column and its format, text columns and "decision" columns, the FK edges, and the three
  cross-database bridges.

## 3. Step by step, with real plates

### 3.1 Understanding the question
`mediator/query_understanding.py`
- A plate (`DL05CD9876`, or `dl 05 cd 9876`) is recognised by rules.
- For plain words ("stolen white Creta Gurgaon"), rules place what they can. The local LLM (qwen2.5:3b through
  Ollama) only sees the leftover words. Its suggestions are checked against the schema vocabulary.
- **The LLM never writes SQL.** Everything works with it switched off.

### 3.2 Inside each database: one scored walk (R2, "per DB")
`sources/context_sql.py`, `sources/context_endpoint.py`; served at `POST /context` on every wrapper.

The mediator sends each wrapper the seeds (plate, chassis or person keys), keywords, a time window, n and θ. The
wrapper turns the graph into **one** `WITH RECURSIVE` statement and runs it inside its own DBMS:

1. **Edge weights are measured live.**
   - `w = min(1, 1 / (1 + ln(average fan-out)))`.
   - An edge where one parent has many children (a busy camera, a police station) gets a low weight.
   - Example: 4 children per parent on average gives w ≈ 0.42.
2. **Affinity.** A row's affinity is the product of the weights on its path, times 0.8 per hop. Paths below θ =
   0.05 are cut, and so are paths that revisit a row.
3. **Relevance** combines three things, clipped to [0, 1]:
   - query keywords found in the row's text columns;
   - overlap with the query's time window;
   - "decision" boosts: an OPEN FIR, an inactive policy, a theft claim.
4. **Score = affinity × relevance.**
5. A per-parent limit (`ROW_NUMBER() OVER (PARTITION BY parent)`, at most 3 children each) stops one busy parent
   from filling every slot.
6. The database returns its **best n = 10 rows**, plus the **(n+1)-th score as a bound**. Nothing it kept back can
   score higher than that bound.

The same SQL gives identical answers on SQLite, PostgreSQL 18 and MySQL 8.0.

### 3.3 Across databases: follow links only while they can win (R2, "beyond across databases")
`mediator/context_discovery.py`, `context_bridges.py`, `context_people.py`, `person_resolution.py`

- **Follow-ups.** A returned row's chassis, plate or person becomes a follow-up into the other databases (a bind
  join). It carries an upper bound:
  `bound = affinity so far × bridge confidence × the best that database could still offer`.
- **Bridge confidence** depends on the link:
  - chassis = 1;
  - a camera plate = the OCR match probability (`DLO5CD9B76` is probably DL05CD9876, `DL05CD9877` is not);
  - a person = the Fellegi–Sunter match posterior. m/u are learned by EM with no labels, and blocking keys are sent
    to the sources, so whole person tables never travel.
- **Order and stopping.**
  - Follow-ups are expanded best bound first.
  - Discovery **stops when the n-th best score found so far is at least every remaining bound**.
  - That rule is exact, not a heuristic: whatever is left could not enter the top n.
- **MH12EF0248 for real:**
  - 6,531 follow-ups were considered, 92 were followed, and **6,439 were pruned** by the bound.
  - The answer cost 115 rows. Copying every match and its closure would have moved 12,762.

### 3.4 Documents with the context (R3)
`sources/docs/`, `mediator/doc_retrieval.py`, `doc_linking.py`, `doc_conflicts.py`

- **DOCS is a sixth autonomous source.** It runs its own BM25 search (`POST /search`) and returns only the top hits.
- **The context builds the query:**
  - Descriptors are names, station, make, colour and place, each weighted by row score × source trust × 0.8^hops ×
    rarity.
  - Anchors are the plate, chassis, FIR, policy and claim numbers.
  - Plate anchors tolerate OCR errors: a q-gram filter, then an OCR-aware similarity of at least 0.92.
- **Linking.** A document is linked to a database row only when enough weighted mentions of that row's values
  appear. A row with an identifier needs that identifier to appear.
- **Relevance gate.** A document that matched only common words ("silver Maruti Swift", an insurer's name) is
  hidden. For DL05CD9876 that hides 5 of 8.
- **Contradictions** between a document and the record it is linked to:
  - "recovered" in the text while the FIR is OPEN;
  - a different loss date, theft date or policy end date (within a tolerance);
  - a different colour, make or model;
  - a chassis printed next to a different plate.

### 3.5 Putting it together: the case file
`app/tabs/context_explorer.py` and the modules named below. One scroll, top to bottom:

| Block | What it does | Module |
|---|---|---|
| Header | The car (make, model, colour, owner) and the verdict. **Which agencies the verdict rests on**: every warning sign is recomputed with each agency's rows removed, in memory, with nothing re-fetched. For MH12EF0248: "Depends on Police and Pollution certificates". | `mediator/robustness.py` |
| What happened | 3–5 sentences; each must cite the row or document it rests on, and unsupported sentences are dropped | `mediator/grounded_summary.py` |
| Timeline | One time axis rebuilt from five databases (four date formats) plus the dates written inside documents. The stolen period is a red band, and a PUC test or camera hit inside it is ringed. A document that disagrees with a database is drawn as a dashed fork. | `mediator/timeline.py` |
| Warning signs | Contradictions no single agency can see: a PUC test, camera capture or challan while stolen; a quick claim; a sale just before a loss; a lapsed policy while the car is still on the road; a shared mobile | `mediator/analytics.py` |
| Documents | Only documents about this car. Each card says what it is, who issued it, when, and why it was picked. **Open** shows the full text with every matched phrase numbered against the record it supports or contradicts. | `app/tabs/context_docs.py` |
| Who says what | Fact × source grid with documents as columns, so text is treated as one more source; trust-weighted best value | `mediator/claim_matrix.py` |
| Related records | Records in words ("Insurance policy POL-000562 · TrustLine · valid till …") with the FK path that reached each | `app/tabs/context_widgets.py` |
| How it was found | **Why each record is here**: path, weights multiplied out, score against the cut-off. **Leads not followed**: follow-ups the bound pruned. **Pursue** fetches one anyway: on DL05CD9876 it brings back 10 records, best 0.14, below the cut-off 0.24, so the bound was right. | `mediator/context_explain.py` |
| What this answer cost | A bill per agency (rows, KB, round trips, "held back: nothing above x"), the live copy-everything count, and the stopping rule round by round | `app/tabs/context_receipt.py` |

### 3.6 Plain-words questions: the best vehicles
`mediator/entity_topk.py`, `topk_sql.py`

- For "stolen white Creta Gurgaon", each database scores vehicles on its own part of the question, inside its own
  DBMS.
- The mediator merges those ranked lists with **Fagin's threshold algorithm** or **TPUT**, so it stops reading once
  no unseen vehicle can make the top 10. Status words (stolen, expired) weigh twice as much as descriptors.
- The live run read 438 rows instead of 848, and MH12EF0248 ranks first. Each result shows every agency's reason in
  words. **Open** takes you to its case file.

## 4. How we know it works (the Proof page)

| Claim | Number | How measured |
|---|---|---|
| Same answer as copying everything | **100%** of the central oracle's top rows found, on 12 questions. One round of per-DB top-n without the bound gets 74%. | The oracle copies every match plus closure and re-ranks with the same scores (`mediator/context_eval.py`). |
| Far less data moved | 107–134 rows per plate vs 12,762–21,058 (≈110–180× fewer); ≈0.3 s per plate | `scripts/evaluate_context.py`; each case file prints its own receipt |
| Context finds better documents | ranking quality (nDCG@10) 0.89 with the question alone → 0.98 with the context; conflict recall 0.87 → 1.00 | `scripts/evaluate_docs.py`, 40 questions with known answers |

Also measured, under "More measurements" on the Proof page:
- rank stability under ±50% weight noise;
- sensitivity to n and θ;
- how fast the stopping rule converges;
- TA vs TPUT (36 vs 13 round trips).

Tests: **1,256 pass**. They include a randomised test that checks discovery against the central oracle.

## 5. Running it

```bash
python -m sources.server_manager          # six wrappers on 8001-8006 (local SQLite copies)
streamlit run app/app.py                  # website on :8501 -> Context Explorer, Proof
pytest -q                                 # stop the wrappers first: the tests refuse to run on live ports
python scripts/evaluate_context.py --quick                    # refresh the Proof page (~2 min)
python scripts/evaluate_context.py --scales x1,x10,x100       # full scale study (about an hour)
```

To demo: open the Context Explorer and search `MH12EF0248`. Read the header, then the timeline (the ringed PUC
test), then open the police FIR narrative under Documents. Press **Pursue** under "How it was found", then open
**Proof**. The full six-step script is at the end of `WHAT_WAS_BUILT.md`.

## 6. Where the code is

| Job | Files |
|---|---|
| Schema description the mediator reads | `data/context_graph.json` |
| The scored in-database walk | `sources/context_sql.py`, `context_dialect.py`, `context_endpoint.py` |
| Cross-database expansion and stop rule | `mediator/context_discovery.py`, `context_scoring.py`, `context_bridges.py`, `context_people.py` |
| People across agencies | `mediator/person_resolution.py` |
| Questions in plain words | `mediator/query_understanding.py`, `query_vocab.py`, `llm.py` |
| Best-vehicle ranking | `mediator/entity_topk.py`, `topk_sql.py`, `topk_refine.py` |
| Warning signs | `mediator/analytics.py`, `evidence.py` |
| Documents | `sources/docs/`, `mediator/doc_retrieval.py`, `doc_linking.py`, `doc_conflicts.py`, `grounded_summary.py` |
| Case-file features | `mediator/timeline.py`, `claim_matrix.py`, `context_explain.py`, `robustness.py` |
| Website | `app/tabs/context_explorer.py`, `context_flow.py`, `context_widgets.py`, `context_docs.py`, `context_timeline.py`, `context_receipt.py`, `context_evaluation.py` (Proof) |
| Measurement | `mediator/context_eval.py`, `context_eval_run.py`, `scripts/evaluate_context.py`, `scripts/evaluate_docs.py` |
| Prompts and changes (R4) | `docs/ai/PROMPTS_PHASE3.md`, `docs/ai/PHASE3_CASEFILE_BRIEF.md`, commits named `generated:` / `reviewed:`, in-file `CONTEXT-ADDED` / `CONTEXT-REPLACED` markers |

## 7. Open problems and ideas for the next round

**Needed before the four-laptop demo:**
- [ ] Each laptop: `git pull`, then `python scripts/load_source.py <ID>` with its DB URL and `--grant`, so the new
      tables exist. Any `<ID>_TABLES` override must list them.
- [ ] Laptop B (MySQL): check `SELECT VERSION()`; the recursive query needs MySQL 8.
- [ ] The catalog on the demo laptop points at localhost after local testing. Restore the LAN version
      (`mediator/meta.lan-2026-11-11.bak` → `meta.db`, or `configure_cluster.py --set …`).
- [ ] Run the full ×1/×10/×100 evaluation once on a quiet machine. The committed `data/context_eval.json` is the
      quick ×1 run, so the "data grows" chart has one point.

**Known rough edges:**
- 22 "registration suspended/cancelled" notices carry future dates. The body text quotes the date, so fixing them
  means regenerating those documents.
- The local LLM summary usually falls back to the template sentences on a laptop CPU. The templates are fine, but
  the LLM path is rarely what the demo shows.
- The sidebar "Sources" box lists only the original four sources, not PUC or DOCS.
- One data disagreement: a PUC certificate's test centre ("North Delhi Testing") differs from the RTO notice about
  the same test ("Gurugram Auto Hub").
- Several new modules run past the ~200-line guideline, partly because replaced code is kept as comments.
- The graph's `time` key holds one column per table, so the timeline names a few second dates (policy start, claim
  intimation) in code. Adding `"time_extra": [{"col": …, "label": …}]` to the graph would remove that.

**Ideas worth discussing (not built):**
- **Watch mode:** re-run a case file when any agency's data changes. Federation means freshness, so show the diff
  since last time.
- **Budget dial:** let the viewer set n (3 / 5 / 10 / 20) and watch rows moved against agreement with the oracle,
  live.
- **Fleet view of the bound:** for the leaderboard, show how many vehicles each agency never had to send.
- **Second opinion on documents:** a doc-vs-doc contradiction check (two narratives disagreeing with each other, not
  only with the database).
- **Live contract test for `/context`** on the four laptops, like `test_live_sources.py` does for `/query`.
