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
| Ours (bounded) | 115–134 | 21–22 | ≈0.5 s |
| B0: every match plus its full FK closure | 15,150–21,058 | 63–97 | 6–10 s |
| B2: mediator walks FK edges itself with plain queries | ≈8,000 | 150–161 | ≈1 s |

That is about 130–180× fewer rows than B0. The Context Evaluation page has the scale (×1/×10/×100), exactness,
sensitivity and convergence charts (`scripts/evaluate_context.py` → `data/context_eval.json`).

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
  | + context | 0.99 | 1.00 |
  | + anchors | 0.998 | 1.00 |

## R4 — prompt, generated code, modified code (2 marks)

- **Prompts:** `docs/ai/PROMPTS_PHASE3.md`, in order. It holds the student's prompts, the task given to each coding
  sub-agent, and the corrections sent after an independent design review.
- **Generated code:** commits named `generated: …`, one per workstream: R1 schemas, R2 context, R2 free text and
  analytics, R3 documents, and the website.
- **Modified code:** commits named `reviewed: …` plus the in-file markers. Example: review found that the
  "copy everything" baseline was silently truncated (a query-length limit and a timeout). The B0 figure was being
  understated; fixing it made the comparison fair.
- To show the changes: `git log --oneline --grep="generated:\|reviewed:"`, then `git show <commit>`.

## Demo in five steps (Context Explorer page)

1. **`DL05CD9876`:**
   - verdict;
   - "each database sent only its best rows — N instead of M";
   - the related records grouped by source, with their join paths;
   - the graph.
2. **The ALIBI plate from the examples:** the PUC test inside the stolen interval shows as a red warning sign; the
   Documents tab shows the FIR narrative that says "recovered" while the FIR is still OPEN.
3. **`stolen white Creta Gurgaon`:** the parsed chips, then ranked vehicles; open one to see its context.
4. **Stop the INS wrapper and search again:** the answer is marked partial (a lower bound) instead of crashing.
5. **Context Evaluation page:**
   - rows moved per approach and scale;
   - exactness against the central oracle;
   - the threshold-convergence chart.
