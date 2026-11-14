# Phase 3: what was built, where, and why

One page per rubric point. Every changed line in an existing file sits between `CONTEXT-ADDED (Rn: reason)` and
`CONTEXT-END`, or under `CONTEXT-REPLACED (Rn: reason):` with the old lines kept as comments. New files say
`CONTEXT-ADDED (Rn)` in their docstring. `git grep -n "CONTEXT-ADDED\|CONTEXT-REPLACED"` lists them all.

## R1 — every database has 4–5 tables, joined FK→PK (2 marks)

| Source (engine) | Tables | New FK→PK joins |
|---|---|---|
| REG (PostgreSQL) | OWNERS, VEHICLE_REGISTRATION, **VEHICLE_IDENTITY**, **OWNERSHIP_HISTORY**, **OWNER_CONTACTS** | identity→registration, history→registration, history→owners ×2, contacts→owners |
| INS (MySQL 8) | INSURERS, POLICY_RECORDS, **POLICY_HOLDERS**, **POLICY_PARTIES**, **CLAIMS** | parties→policies, parties→holders, claims→policies |
| THEFT (SQLite) | CRIME_RECORDS, **STATIONS**, **STOLEN_PROPERTY**, **PERSONS**, **INCIDENT_PERSONS** | records→stations, property→records, incident_persons→records/persons |
| CAM (PostgreSQL) | CAMERAS, PLATE_CAPTURES, **ZONES**, **CAMERA_ZONES**, **CHALLANS** | camera_zones→cameras/zones, challans→captures |
| PUC (SQLite) | POLLUTION_CERT, **TEST_CENTRES**, **ANALYSERS**, **CERT_TESTS** | tests→certs, tests→analysers, analysers→centres |
| DOCS (SQLite, new) | DOCUMENTS | unstructured; linked to rows by text, not keys |

- Old tables are untouched. That is why phase 1–2 still grade **621/621** on the ground truth and the matcher stays at
  23 TP / 0 FP.
- Each agency keeps its own formats: INS dates are `DD/MM/YYYY` text, THEFT uses epoch integers, CAM uses ISO timestamps.
- Entity facts are split across tables on purpose. A vehicle's chassis sits in REG.VEHICLE_IDENTITY, INS.POLICY_PARTIES
  and THEFT.STOLEN_PROPERTY. A person is an owner in REG, a policy holder in INS and a complainant or accused in THEFT,
  spelled differently each time.
- Files:
  - `sources/*/schema.sql`
  - `scripts/phase3_generate.py` and `phase3_plant.py` (seeded with 20261111, byte-reproducible)
  - `scripts/phase3_scale.py` (×10 and ×100 copies for measurement)
  - `data/phase3_scenarios.json` (18 planted cases)

## R2 — context discovered inside each database and followed across databases (3 marks)

**Inside each DBMS: one statement per source** (`sources/context_sql.py`, `context_dialect.py`,
`context_endpoint.py`, served at `POST /context` on each wrapper):
1. The FK graph comes from `data/context_graph.json`; no table name is hard-coded.
2. A single `WITH RECURSIVE` walks every FK edge in both directions from the seed rows.
3. Each edge weight is `min(1, 1/(1+ln avg fan-out))`, measured live, so a hub edge counts for less.
4. A path's affinity is the product of its edge weights. Paths below θ, or that revisit a row, are cut.
5. Relevance = query keywords + overlap with the query's time window + "decision" boosts (an open stolen FIR, a lapsed
   policy, a theft claim), clipped to [0,1]. Score = affinity × relevance.
6. Per-parent limits (`ROW_NUMBER() OVER (PARTITION BY parent)`) stop one busy parent from taking every slot.
7. Each source returns only its **best n** rows, plus an exact bound: no row it kept back can score above the (n+1)-th.
8. The SQL was checked on SQLite, PostgreSQL 18 and MySQL 8.0: all three give identical answers.

**Across databases: bounded, multi-round** (`mediator/context_discovery.py`, `context_bridges.py`, `context_people.py`):
- A returned row's chassis, plate or person becomes a follow-up request to the other sources (a bind join).
- Each follow-up gets an upper bound: affinity × bridge confidence × the best that source could still offer.
- Bridge confidence is 1 for a chassis, the OCR match probability for a camera plate, and the Fellegi–Sunter posterior
  for a person.
- Follow-ups are expanded best bound first. Discovery stops as soon as the n-th best score so far is at least every
  remaining bound. That stopping rule is exact, and a randomised test checks the result against a central oracle.
- People are matched across agencies by Fellegi–Sunter, with m/u learned by EM and no labels
  (`mediator/person_resolution.py`). Blocking keys go to the sources, so whole person tables never travel.

**Free text** ("stolen white Creta Gurgaon"):
- Parsing (`mediator/query_understanding.py`): rules first. The local LLM (qwen2.5:3b via Ollama) sees only the words
  the rules could not place, and each of its predicates is checked against the schema vocabulary. It never writes SQL.
- Ranking (`mediator/entity_topk.py`): the best vehicles are found with Fagin's threshold algorithm or TPUT. Each source
  scores vehicles inside its own DBMS.

**Analytics** (`mediator/analytics.py`):
- Per-query contradictions that no single agency can see:
  - a PUC test, camera capture or challan while the car was officially stolen;
  - a theft claim days after the policy started;
  - a sale shortly before a loss;
  - one mobile shared across several owners, holders and accused.
- A fleet-wide "most suspicious" leaderboard using the same top-k machinery.

**Data moved, measured on the local databases for one plate (n = 10):**

| Approach | Rows shipped | Round trips | Time |
|---|---|---|---|
| Ours (bounded) | 107–134 | 15–22 | ≈0.3 s |
| B0: every match plus its full FK closure | 12,762–21,058 | 63–97 | 6–10 s |
| B2: mediator walks FK edges itself with plain queries | ≈8,000–8,300 | 150–161 | ≈0.6 s |

That is about 110–180× fewer rows than B0, with the same answer. Against a central oracle that copies every
match and re-ranks it with the same scores, our top rows agree on **100%** of the evaluation questions. One round
of per-database top-n without the bound agrees on only 74%. The **Proof** page shows this, plus the scale chart
(×1/×10/×100) and the document-ranking gain (`scripts/evaluate_context.py` → `data/context_eval.json`). Every
search's own cost is printed at the bottom of its case file.

## R3 — unstructured data found with the context and integrated (3 marks)

- **DOCS, a sixth autonomous source** (`sources/docs/`, port 8006). It holds 1,152 documents: FIR narratives, surveyor
  reports, accident reports, news, RTO notices and witness statements.
  - 60 were rewritten by the local LLM; a rewrite is kept only if it keeps every date and identifier.
  - The corpus includes OCR'd plates, Hinglish, six date styles and 59 planted text-vs-database conflicts.
- **Search inside the source:** DOCS runs BM25 (`/search`) itself. Its plate anchors are OCR-tolerant: a q-gram filter,
  then OCR-aware similarity of at least 0.92. That accepts `DLO5CD9B76` as DL05CD9876 and rejects the one-off plate
  DL05CD9877.
- **The context builds the search** (`mediator/doc_retrieval.py`):
  - Descriptors (names, station, make, colour, place) are weighted by row score × source trust × 0.8^hops × rarity.
  - Anchors (plate, chassis, FIR, policy and claim numbers) are added on top.
- **Linking documents to rows** (`mediator/doc_linking.py`, an EROCS-style method): a document is tied to a row only when
  enough weighted mentions of that row's values appear, and a row with an identifier needs that identifier to appear.
- **Integration:**
  - Text-vs-database conflicts are flagged (`mediator/doc_conflicts.py`): "recovered" in the text while the FIR is OPEN,
    a different loss date, a different colour.
  - A short summary (`mediator/grounded_summary.py`) must cite a document or row in every sentence; any sentence with an
    unsupported citation or number is dropped.
- **Measured** (`scripts/evaluate_docs.py`):

  | Search mode | nDCG@10 | Conflict recall |
  |---|---|---|
  | Question only | 0.89 | 0.87 |
  | + context | 0.98 | 1.00 |
  | + anchors | 0.97 | 1.00 |
- **Documents are evidence the reader can open:**
  - A relevance gate hides documents that matched only common words (for DL05CD9876, 5 of 8).
  - Each card says what the document is, who issued it, when, and why it was retrieved.
  - The reader shows the full text with every matched phrase numbered against the database record it supports or
    contradicts.
- **Who says what** (`mediator/claim_matrix.py`): each fact (colour, make, owner, theft date, case status, …) is set
  against each agency *and each document*. Text is treated as one more source, with a trust-weighted
  best-supported value.

## R4 — prompt, generated code, modified code (2 marks)

- **Prompts:** `docs/ai/PROMPTS_PHASE3.md`, in order. It holds the student's prompts, the task given to each coding
  sub-agent, and the corrections sent after an independent design review.
- **Generated code:** commits named `generated: …`, one per workstream: R1 schemas, R2 context, R2 free text and
  analytics, R3 documents, and the website.
- **Modified code:** commits named `reviewed: …` plus the in-file markers. Example: review found that the
  "copy everything" baseline was silently truncated (a query-length limit and a timeout). The B0 figure was being
  understated; fixing it made the comparison fair. A second example: review timed a search at 16 s and traced it to
  the HTTP client building a new SSL context for every call on Windows (250 ms each). One pooled client made it
  under 5 s; the database work itself was 0–70 ms per call.
- To show the changes: `git log --oneline --grep="generated:\|reviewed:"`, then `git show <commit>`.

## The case file: what a viewer sees for one plate (Context Explorer)

One scroll, no tabs. Each block is a feature most federated projects do not have, and each is explainable in one
sentence.

| Block | What it shows | Rubric | Files |
|---|---|---|---|
| Header | The car itself (make, model, colour, owner), the verdict, and **which agencies the verdict rests on**: warning signs recomputed with each agency removed, in memory, nothing re-fetched | R2 | `mediator/robustness.py` |
| What happened | 3–5 sentences, each citing the record or document it rests on; contradictions first | R3 | `mediator/grounded_summary.py` |
| Timeline | One time axis rebuilt from five databases (four date formats) and the dates written in documents. The stolen period is a red band; a PUC test or camera hit inside it is ringed; a document that disagrees with a database is drawn as a dashed fork | R2 + R3 | `mediator/timeline.py` |
| Warning signs | Cross-agency contradictions no single agency can see | R2 | `mediator/analytics.py` |
| Documents | Only documents about this car; Open shows the full text with numbered links to records | R3 | `mediator/doc_retrieval.py`, `app/tabs/context_docs.py` |
| Who says what | Fact × source grid, documents included | R3 | `mediator/claim_matrix.py` |
| Related records | Records in words ("Insurance policy POL-000562 · TrustLine · valid till …"), with the FK path that reached each | R1 + R2 | `app/tabs/context_widgets.py` |
| How it was found | **Why each record is here**: its FK path, the edge weights multiplied out, its score against the cut-off. **Leads not followed**: cross-database follow-ups the bound pruned, each with a Pursue button that follows it anyway and shows it could not change the answer | R2 | `mediator/context_explain.py`, `context_discovery.py` |
| What this answer cost | An itemised bill per agency (rows, KB, round trips, "held back: nothing above x"), the live copy-everything count, and the stopping rule round by round | R2 | `app/tabs/context_receipt.py` |

## Demo in six steps

1. **`DL05CD9876`:** read the header line, then "What happened", then the timeline: a camera sighting 85 days
   after the insurance cover ended. Scroll to "What this answer cost": 123 rows instead of 17,157.
2. **`MH12EF0248`:** the timeline rings a PUC test inside the stolen band. Under Documents, open the police FIR
   narrative: "recovered" is marked against the FIR that is still OPEN. "Who says what" shows the same dispute.
3. **How it was found → Pursue a lead:** it fetches the pruned branch anyway; nothing it brings back beats the
   cut-off. That is the stopping rule proven live.
4. **`stolen white Creta Gurgaon`:** ranked vehicles, each with every agency's reason in words; open the first.
5. **Stop the INS wrapper and search again:** the verdict says partial (a lower bound) instead of crashing.
6. **Proof page:** same answer as copying everything (100%), how much less data moves as the data grows, and
   better documents with the context.
