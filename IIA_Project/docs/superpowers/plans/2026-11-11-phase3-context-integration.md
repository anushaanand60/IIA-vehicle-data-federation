# Phase 3: federated context discovery and unstructured-data integration

Status: design agreed with the student on 2026-11-11, build next. Deadline 21 Nov 2026; target 2–3 days.
Rubric: (1) 4–5 tables per DB with FK-PK [2]; (2) federated context discovery via FK-PK, per DB, highly
related to the query [3]; (3) search correlated unstructured data with the query context and integrate it [3];
(4) prompt, generated code, modified code [2].

Rules for this phase: the product never mentions lecture slides; no UI clutter; every change marked in code
(`CONTEXT-ADDED (Rn: reason)` … `CONTEXT-END`, `CONTEXT-REPLACED` with the old lines commented); prompts
logged from day 1; `generated:` / `reviewed:` commit pairs.

---

## 1. What the professor asked for, and our answer

| Ask | Our answer |
|---|---|
| Each DB returns only its best n, not every match | Context is discovered **inside each DBMS** by one scored recursive query that returns only the top n rows per source, with the path that explains each row |
| "Beyond across databases" | Values found in one source (chassis, plate, person) are followed into the others, cheapest-first by an upper bound, until nothing left can beat the n-th result; people are resolved across agencies with Fellegi–Sunter |
| Scalability shown as less data moved | Rows and bytes shipped vs a baseline that ships every match plus its full FK closure, at generator scale ×1 / ×10 / ×100; latency reported too |
| Unstructured data correlated with the context | A sixth autonomous source holds documents; the **context**, not the raw query, drives retrieval; passages are linked back to rows and conflicts flagged |
| Free-text queries | "stolen white Creta Gurgaon": a local LLM turns the text into typed predicates checked against the schema registry; the best k vehicles are found with a distributed threshold algorithm |
| Analytics | Per query: cross-source "alibi" contradictions, quick claims, transfer-before-loss. Fleet-wide: a most-suspicious-vehicles leaderboard via the same top-k |

## 2. Architecture

```
  query: "DL05CD9876"  |  "stolen white Creta Gurgaon"
                          │
  MEDIATOR (laptop A) ─ query understanding (rules + local LLM, validated)
     │  round 1: /context to each source (seeds + n)       ┐
     │  round 2+: bridge follow-ups, best upper bound first│ bounded, multi-round
     │  entity top-k: threshold algorithm over sources     ┘
     │  person resolution across agencies (Fellegi–Sunter, EM-learned m/u)
     │  context bundle ─────────────────────────► DOCS /search (6th source)
     │  analytics (alibi, timing, transfer) + leaderboard
     ▼
  REG  INS  THEFT  CAM  PUC        each: /context runs ONE scored WITH RECURSIVE query in the DBMS
  5    5    5      5    4–5 tables  returns top-n rows + path + bound "nothing unsent beats X"
```

## 3. Rubric 1: schemas (generator rewrites the data; phase 1–2 tables keep working through views or migration)

- **REG:** owner, vehicle (reg_no, chassis_no, engine_no, make, model, colour, rc_status), ownership_history, hypothecation + financier, fitness_tax.
- **INS:** policy_holder (own identity, not REG's owner), policy (prev_policy self-FK, chassis_no), claim (loss_date, intimation_date, loss_type, amount, status, narrative_doc_id), surveyor, agent.
- **THEFT:** police_station, fir (occurrence window, BNS sections, status, brief_facts_doc_id), property (reg_no_text, chassis_no, STOLEN/RECOVERED, recovery_date), person, fir_person (role).
- **CAM:** zone, location, camera, capture, challan.
- **PUC:** test_centre (geotagged), analyser, certificate (test_ts, valid_upto), reading.
- Cross-source keys in three tiers: chassis (exact), plate (noisy, phase-2 matcher), person name/mobile/address (fuzzy).
- Seeded generator with planted scenarios: false theft report (PUC test inside the stolen window), clone, quick claim, transfer before loss, person rings across agencies, hard negatives (plates one character off, same name different person).
- Must keep phase 1–2 behaviour: existing views/mappings regenerated, ground-truth and scenario tests updated.

## 4. Rubric 2: context discovery

**4.1 In-DBMS scored context (per source).** `POST /context {seeds, n, theta, window}` on each wrapper runs one
`WITH RECURSIVE` query generated from that source's FK graph (metadata, not hand-written per source):
- path affinity = product of edge weights, w(e) = w0 / (1 + ln avg fan-out), expert overrides allowed; stop when < θ;
- query relevance: keyword match, overlap with the query's time window, and decision relevance (does the row change or support a verdict: open FIR, lapsed policy, presence event in a stolen window);
- per-parent limits with window functions, so one high fan-out parent cannot starve the rest;
- returns top n rows, each with its join path, plus u_s, an upper bound on any row not returned;
- bounds are computed in the same query from live data, so `/admin/sql` writes cannot make them stale.
The wrapper guard is extended to allow a read-only `WITH RECURSIVE … SELECT`.

**4.2 Across databases (multi-round, bounded).** Bridge values from returned rows become follow-up states
(a bind join): score bound = parent score × bridge confidence × that source's max. Bridge confidence is 1 for
chassis, the phase-2 match probability for plates, the Fellegi–Sunter posterior for people. The state with the
highest bound is expanded first; expansion stops when every frontier bound ≤ the n-th best score. Bridge values
are shipped as a compact filter to cut transfer.

**4.3 Person resolution across agencies.** Owner (REG), policy holder (INS), complainant/accused (THEFT) are
blocked (name q-grams, mobile, pincode), scored with Fellegi–Sunter weights whose m/u are learned by EM without
labels, clustered into golden person records. This is what makes "owner → other vehicles → their FIRs and claims"
possible across sources.

**4.4 Free-text queries and entity-level top-k.** Text → typed predicates (make, colour, status, place, dates,
names) by rules, with qwen2.5:3b (JSON mode) for what rules miss; every predicate is validated against the schema
registry, never free SQL. Each source scores vehicles by its own predicates; the mediator runs Fagin's threshold
algorithm with bounds discounted by identity-match probability (noisy plates), stopping once the top k cannot change.

**4.5 Measurement.** Baseline B0: every match + full FK closure from every source; B1: each source ships top n
without bounds. Report rows, bytes, round trips, latency; exactness vs a central oracle; precision@n and nDCG@n on
a judged set; sensitivity of w0, θ, n.

## 5. Rubric 3: unstructured data

- **Corpus (6th source, own wrapper and DB):** ~1–2k documents (FIR brief facts, surveyor/claim reports, accident
  reports, news-style items, RTO notices) generated from ground truth by the local LLM and by templates, with noise
  (OCR plates, Hinglish, spelling variants) and planted conflicts; gold doc→row links kept by the generator.
- **Context → query:** anchors (plate, chassis, FIR no., policy no.) matched typo-tolerantly with the phase-2
  q-gram index and OCR-aware distance, using the query's own context as the dictionary; descriptors (names,
  station, make/colour, place) weighted by rarity × source trust × γ^hops.
- **Ranking:** BM25 with the context-weighted query; anchor matches boost; optional fusion by reciprocal rank.
- **Linking:** passages tied back to rows by summed weighted context terms with a support threshold.
- **Integration:** documents shown beside the rows they support; text-vs-database conflicts flagged (e.g. FIR date
  vs claim narrative date, "recovered" in text vs OPEN in DB); an optional short grounded summary by the local LLM
  that must cite a document or row for every sentence.
- **Evaluation:** generator gold as qrels; nDCG@10, MRR, link precision/recall, conflict recall; ablation:
  query only → + context → + anchors.

## 6. Analytics

- Per query: alibi contradictions (PUC test, capture or challan inside a stolen interval), quick claim (policy
  start → loss, loss before FIR), transfer before loss, lapsed cover with recent sightings.
- Fleet-wide: most-suspicious-vehicles leaderboard using 4.4's top-k, each score explained by its indicators.

## 7. Website (clean, few pages)

- **Context Explorer:** one search box (plate or free text) → verdict, context graph (rows by source, with paths),
  rows-shipped counter vs baseline, documents with highlighted passages and conflicts, analytics.
- **Context Evaluation:** data-reduction and latency charts, exactness, retrieval ablation.
- Existing pages unchanged except where the schema change requires it.

## 8. Workstreams and order (parallel)

| WS | Owner | Content |
|---|---|---|
| A | schemas | new schemas, generator with planted scenarios, migration of phase 1–2 mappings and tests |
| B | context | recursive `/context`, guard extension, bound-driven cross-source expansion, Fellegi–Sunter persons |
| C | query + top-k | free-text understanding, entity threshold algorithm, analytics and leaderboard |
| D | documents | doc generator, 6th source, retrieval, linking, conflicts |
| E | evaluation + UI | baselines and oracle, metrics, two pages |

Order: A first (everything depends on it) → B, C, D in parallel → E → full test run, three-laptop rehearsal.

## 9. Risks

- MySQL on laptop B must be 8.0+ for recursive CTEs (check `SELECT VERSION()`); fallback: per-hop queries from the wrapper.
- Schema change can break phase 1–2 tests and the ground truth; migrate in one step and rerun everything.
- Local LLM speed: keep it off the critical path (rules first, LLM only for unparsed text, cached per query string).
