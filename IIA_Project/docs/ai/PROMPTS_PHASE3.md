# Phase 3 prompts (vibe-coding log)

Every prompt behind the phase-3 work (context discovery, documents, analytics), in order, for rubric 4.

- **student**: typed by the student to Claude Code.
- **sub-agent task**: the specification Claude Code handed to a coding sub-agent; the generated code came from these.
- **follow-up to a sub-agent**: corrections sent to a sub-agent mid-build (e.g. after the design review).

The code each prompt produced is in the `generated: …` commits; what was changed by review is in the paired
`reviewed: …` commits and in `docs/phase3/WHAT_WAS_BUILT.md`. Personal details and passwords are redacted.


### Prompt 1 — 2026-11-11 12:20 UTC — student

```text
So for this project, we have another part of it coming up now, which I'll give the description of later. But in the meanwhile, after my review with my professor for the second part of the project, that was when we built this part about checking, decomposing and doing the other part of it, matching and cleansing. He told me that when we do the selection of tables, so say there are a couple of entities. One is the entire car registration or car checker entity, and then he has access to the 4 tables. So we should have implemented something that would have made it so that pursuant to a query, only the queries that match, so sort of this decomposition happens in all the 4 databases, so that not all queries that match go up, only the best that match amongst the 4 tables for each of the 4 tables are picked up and then are sent back to the whoever is querying it. So I think what happens now is when somebody enters a query, all the queries that match at the individual database layers are sent back to them. Instead of that happening, the best n queries or something should have been selected from each of these tables and sent back. So my professor is looking for such kind of innovations in the project that we are trying to implement, in that instead of a regular software that, and instead of a regular program that we're sort of building, try to include some stuff that is unique in the terms of scalability, in terms of fetching, in terms of stuff that isn't commonly done. For this, if you have to research papers or find out papers that can help you with it, do that. There is no stopping you for this. I'm going to upload my next part of the project that we have to do. I want come up with a plan that shows me the whole architecture, how you would go about planning this, and then let me know if you can do some actual strong innovation instead of just cleanliness or nothing wrong being shown. Those are not innovations. Innovation is something that is either scalable and fast or a completely new way of implementing stuff, or we find a paper that has done something unique in the space and that is not commonly done by people, anyone who can just prompt you and ask you to build this should not be able to basically do it. We put some effort and we work into stuff like that.

<pasted_content id="217b">
Project Phase 3 evaluation and rubric:
This phase is about the seamless integration of unstructured data with structured query results. I already described you this task in the last class in detail.
1. Expand each of the DB having at least having 4-5 tables (that is, entity information is distributed in different tables) and some of these tables are joined by FK-PK. (Marks: 2)
2. Discover the context from the query results and beyond across databases by exploiting the FK-PK relationships. 

The database instances should be running on different m/cs. The user (sub)query should be federated to these databases and each database should discover the context.  The over all context accumulation should be highly related to the user query.  (marks: 3)
3.Using the query context, search the unstructured data which is highly corelated to the query context and integrate it (marks: 3)
4. Show the prompt, generated code and your modified code (marks: 2)
</pasted_content id="217b">

Also, you have a tendency to reference the slides in your project. Please don't. This is a completely standalone project. Slides only guide the ways we try to do stuff and the methods that she's basically taught us in class. Whenever you've created a project, I want you to ensure that there is no unnecessary clutter on the project, that the UI is smooth and nice to view and use. And right now I want to say basically what it is that the prof wants me from this project. Ask me any questions that you need to so that I can go and ask the professor. Research papers, research find stuff that could help you with. I will be enabling Fable 5.5.2 as an advisor for you. So ask it whatever questions you want, as much as you want it to, and use many subagents for your development. Ensure that stuff is done correctly. Ensure that you can justify each of your choices as per the rubrics, and ensure that this is, I think, an analytics sort of stuff. So he wants us to see how much of, so given different tables like the ones he says, context and tables, he wants us to show how can we include relevant analytics also for this. So find stuff that could help you with respect to that also. Ensure that you're very thorough with how 1, 2, and 3 points are linked to each other, because A, he wants you to expand the number of tables. B, he wants you to get the context. Now, context means you could have a lot of stuff for car registration license plates. And turns out, if most groups will use LLMs to build their projects, which is allowed in the course. So he wants us to find unique stuff that could, even if people use LLMs, do not come up in. So find stuff that is implementable, that is real life, but that is also unique. And then third, using the query context, search unstructured data. Unstructured data, I would believe, are documents and stuff like that, but yes, go ahead through it, make a plan first, let's see how We can do it research before planning, come up with a final plan that has been researched, that has been validated, and then we'll start building. And you ask me questions also, which I can ask my professor or answer myself.
```


### Prompt 2 — 2026-11-11 12:21 UTC — sub-agent task

**Task** (Research FK-graph context discovery and distributed top-k, model default):

```text
You are researching for a university data-integration project (federated mediator over autonomous databases). Use WebSearch (and fetch pages/papers where useful). Return a dense, well-cited research brief (~1,200-1,800 words) — no fluff.

CONTEXT OF THE PROJECT: Five autonomous databases on different laptops (PostgreSQL, MySQL, SQLite), each behind its own HTTP wrapper that accepts SELECT queries: REG (vehicle registration: owners, vehicles), INS (insurers, policies), THEFT (police crime records), CAM (cameras, plate captures), PUC (pollution certificates). A mediator decomposes a user query (e.g. a number plate, or keywords like "stolen white Creta Gurgaon") into per-source SQL, runs it in parallel, and integrates results. Each DB will be expanded to 4-5 tables linked by FK-PK. NEW REQUIREMENT from the professor: "Discover the context from the query results and beyond across databases by exploiting the FK-PK relationships. The user (sub)query should be federated to these databases and each database should discover the context. The overall context accumulation should be highly related to the user query." His explicit feedback: today every matching row from every source is shipped back; instead, each source should return only the BEST n context tuples, ranked, and the mediator should merge them — he wants genuine innovation in scalability/fetching, not routine code.

RESEARCH QUESTIONS (cite authors, venue, year, and the key mechanism of each):
1. Context/neighbourhood discovery over relational schema graphs via FK-PK: Object Summaries and size-l Object Summaries (Fakas; Fakas, Cai, Mamoulis VLDB 2011 and follow-ups; "affinity" of relations to a data subject), ObjectRank (Balmin, Hristidis, Papakonstantinou VLDB 2004), DISCOVER / candidate networks (Hristidis & Papakonstantinou VLDB 2002), BANKS (Bhalotia et al. ICDE 2002), DBXplorer, BLINKS, random walk with restart / personalized PageRank over tuple graphs, "Précis queries" (Koutrika, Simitsis, Ioannidis ICDE 2006 — very relevant: logical subset of DB related to a query, with weights on schema graph edges and constraints on size). For each: how importance/relevance of a related tuple is scored, how expansion along FK edges is bounded, complexity.
2. Distributed/federated top-k so only the best n tuples cross the network: Fagin's Threshold Algorithm (TA) and NRA (PODS 2001/JCSS 2003), TPUT (Cao & Wang PODC 2004), KLEE (Michel, Triantafillou, Weikum VLDB 2005), rank-join / HRJN (Ilyas, Aref, Elmagarmid VLDB 2003), top-k over distributed sources with early termination. Which of these fit "each source computes local scored context, mediator merges global top-n with provably minimal transfer"? Note round-trips vs bandwidth trade-offs.
3. Any work that combines (1) and (2): distributed keyword search over multiple relational databases / federated object summaries / top-k context across autonomous sources (e.g. "keyword search over distributed databases", "Kite" (Sayyadian et al. ICDE 2007, keyword search over heterogeneous databases with foreign-key discovery across databases!), "Q system" (Talukdar et al.)). Kite and Q are especially relevant — describe them precisely.
4. Concretely recommend ONE implementable, defensible design for our setting (5 DBs, a few hundred to a few thousand rows each, Python, ~1 week of build): how each wrapper computes a locally ranked context (scored by what), what crosses the network, how the mediator merges (which algorithm), how the "relatedness to the query" is guaranteed and measured, and what metric shows the scalability gain (e.g., tuples shipped / bytes / round trips vs naive, precision@n of context vs a judged set). Flag anything novel relative to "just run joins and return everything".

Give exact paper titles so they can be cited. End with a short list of the 5 most useful references with URLs (DBLP/ACM/arXiv/author PDF).
```


### Prompt 3 — 2026-11-11 12:22 UTC — sub-agent task

**Task** (Research structured-to-unstructured context retrieval, model default):

```text
You are researching for a university data-integration project. Use WebSearch (and fetch pages/papers where useful). Return a dense, well-cited research brief (~1,200-1,800 words).

PROJECT: A federated mediator over 5 autonomous relational databases about Indian vehicles (registration/owners, insurance policies, police crime/theft records, road-camera plate captures, pollution certificates), each on a different laptop. A user query (number plate, or keywords) is decomposed and federated; each database discovers "context" by traversing FK-PK relationships (owner, vehicle, policy, claim, FIR, sightings...). NEW RUBRIC ITEM: "Using the query context, search the unstructured data which is highly correlated to the query context and integrate it." Unstructured data will likely be text documents: e.g. police FIR narratives, insurance claim/surveyor reports, accident reports, news articles about thefts, RTO circulars, possibly scanned PDFs/images. The professor wants genuine innovation, not "embed the query and do vector search".

RESEARCH QUESTIONS (cite authors, venue, year, mechanism):
1. Linking structured data to text: EROCS ("Efficiently linking text documents with relevant structured information", Chakaravarthy, Gupta, Roy, Mohania — SIGMOD 2006 / VLDB 2006), SCORE / "Structured and unstructured data integration", entity linking of documents to database tuples, "joining text and relations", annotation-based approaches (IBM Avatar/SystemT), entity-centric document retrieval. How do they use a database entity's attributes (and related tuples) as the query against text, and how are matches scored?
2. Using structured context to drive document retrieval: query expansion from structured context (weighted term/entity expansion), entity-weighted BM25 / BM25F, relevance models (Lavrenko & Croft) seeded by structured context, knowledge-graph-enhanced retrieval, "context-aware retrieval", and modern equivalents (GraphRAG, hybrid sparse+dense retrieval, Reciprocal Rank Fusion — Cormack et al. SIGIR 2009, ColBERT, SPLADE). What is genuinely uncommon vs what every student will do (plain embeddings + cosine)?
3. Noisy text: documents with misspelled names, OCR'd plate numbers, Hindi/English mixes — approximate entity mention matching (q-grams, edit distance, phonetic) to link documents to tuples. Any work on "approximate entity extraction with dictionaries" (e.g., Chakrabarti et al., "Approximate entity extraction", Wang et al. "Faerie" SIGMOD 2011 — dictionary-based approximate entity extraction with q-grams!). Describe Faerie precisely; our project already has a q-gram blocking index and an OCR-confusion-aware plate matcher we could reuse.
4. Integration/presentation of retrieved documents with the structured result: evidence attribution/provenance, passage-level highlighting, conflict detection between text and DB (e.g., FIR says stolen but DB says active), and "truth discovery" across structured+unstructured sources.
5. Recommend ONE implementable, defensible pipeline for our setting (CPU-only Python, a corpus of a few hundred to a few thousand short documents, ~1 week build, offline at demo time — no paid APIs): indexing, how query context becomes the retrieval query, scoring/fusion, linking back to tuples, conflict detection, and evaluation (how to build a judged set, metrics like nDCG@k/MRR, ablation "query only" vs "query+context"). Also name realistic public sources of vehicle-related Indian text we could legitimately use or adapt (e.g. news on vehicle theft, NCRB, MoRTH circulars, court judgments on Indian Kanoon) versus generated synthetic documents, with pros/cons.

End with the 5 most useful references with URLs.
```


### Prompt 4 — 2026-11-11 12:22 UTC — sub-agent task

**Task** (Research domain schemas, analytics and uniqueness, model default):

```text
You are researching for a university data-integration project. Use WebSearch (fetch pages where useful). Return a dense brief (~1,200-1,600 words) with citations.

PROJECT: Federated mediator over autonomous Indian vehicle databases on different machines: REG (Regional Transport Office: owners, vehicles), INS (insurer, policies), THEFT (police FIR/crime records), CAM (road cameras, plate captures), PUC (pollution certificates). Phase 3 requires: each database expanded to 4-5 tables with FK-PK relationships (entity information distributed across tables); context discovery across databases via FK-PK; then retrieval of correlated unstructured data (documents). The student also wants relevant ANALYTICS built on the discovered context, and innovation that "anyone prompting an LLM would not come up with".

RESEARCH:
1. Realistic schemas: What tables/fields do real Indian systems have? VAHAN (RC details: owner, hypothecation/financier, fitness certificate, permits, tax, ownership transfer history, blacklist/NOC), SARATHI (driving licences), insurance (policy, claims, surveyor, agent, premium, NCB, endorsements; IIB — Insurance Information Bureau of India data), CCTNS (police FIR, accused, property/vehicle seized, IPC/BNS sections, police station), e-Challan (violations, fines, payment status), PUC (test centre, readings), FASTag toll transactions, ANPR/ITMS camera systems. Propose 4-5 tables per source with FK-PK links that are realistic and give interesting multi-hop context (e.g. owner→other vehicles; policy→claims→surveyor; FIR→accused→other FIRs; camera→location→zone; violation→challan→payment).
2. Context analytics that are meaningful for this domain and uncommon: e.g. anomaly signals across sources (claim filed shortly after theft report; vehicle seen on camera after reported stolen; ownership transfer just before claim — insurance fraud indicators; repeated violations), temporal timelines of an entity across sources, "data subject profiles", fraud rings via shared attributes (same owner address/phone across many vehicles — entity resolution graphs), insurance fraud detection literature (link analysis in insurance fraud, e.g. Šubelj, Furlan, Bajec "An expert system for detecting automobile insurance fraud using social network analysis" Expert Systems with Applications 2011 — describe it), graph-based anomaly detection, explanation of aggregates. Which of these can be computed from federated context with top-k/ranked results rather than full scans?
3. Uniqueness: list 5-8 innovation ideas for this phase that are research-backed, implementable in ~1 week in Python, demonstrable live, and unlikely to be produced by a generic LLM-built student project. For each: one-line mechanism, the paper/system it draws on, and how to measure it. Prioritise ideas around: distributed top-k context fetching, provenance/explanations, cross-source fraud/anomaly indicators, linking text to tuples with noisy matching.
4. Sources of realistic unstructured data in this domain (FIR text samples, insurance claim narratives, accident reports, news) and whether synthetic generation is acceptable/standard in such coursework.

End with the most useful references with URLs.
```


### Prompt 5 — 2026-11-11 12:28 UTC — sub-agent task

**Task** (Fable advisor critiques phase-3 design, model fable):

```text
You are a senior advisor (database research + systems) reviewing a student team's DESIGN before they build it. Be blunt and specific. Do not write code. Do not use tools unless you need to check a fact. ~900-1,300 words.

COURSE PROJECT (Information Integration). Phases 1-2 exist and work: five autonomous databases on three laptops (REG vehicle registration on PostgreSQL; INS insurance on MySQL; THEFT police on SQLite; CAM road cameras on PostgreSQL; PUC pollution certs on SQLite), each behind a FastAPI wrapper (/health, /schema, /query SELECT-only with whitelist + LIMIT 200 + 3 s timeout, /admin/sql for writes). A Python mediator plans, decomposes (global-as-view mappings), executes in parallel, integrates (transforms, provenance, trust: REG .95, INS .90, THEFT .90, CAM .60), and decides (UNINSURED / STOLEN / CLONED ...). Phase 2 added noisy plate matching (q-gram blocking, 10 string measures + noisy-channel open-world model + stacked logistic regression, CLIP for car photos) and RDR-based cleansing. Today each DB has only 1-2 tables, and the mediator ships every matching row.

PHASE 3 RUBRIC (10 marks): (1) Expand each DB to 4-5 tables with FK-PK joins, entity info distributed across tables [2]. (2) "Discover the context from the query results and beyond across databases by exploiting the FK-PK relationships. The DB instances run on different machines. The user (sub)query should be federated to these databases and each database should discover the context. The overall context accumulation should be highly related to the user query." [3]. (3) "Using the query context, search the unstructured data which is highly correlated to the query context and integrate it." [3]. (4) Show prompt, generated code and modified code [2].
PROFESSOR'S EXPLICIT FEEDBACK: today every matching row from every DB is returned; instead each DB should return only the BEST n context tuples and the mediator merge them; he wants real innovation in scalability/fetching, "not cleanliness". He also hinted at analytics on the context. Many groups will use LLMs; he wants things an LLM-prompted project would not produce. No paid APIs; CPU laptops; demo offline; ~2 weeks.

DRAFT DESIGN:
A. Schemas (rubric 1), modelled on documented real fields: REG: owner, vehicle(reg_no, chassis_no...), ownership_history, hypothecation(+financier), fitness_tax. INS: policy_holder (deliberately separate identity from REG owner), policy(prev_policy self-FK), claim, surveyor, agent. THEFT (CCTNS-like): police_station, fir(BNS sections), property(stolen/recovered, chassis), person, fir_person(role). CAM: zone, location, camera, capture, challan. PUC (PUCC 2.0: presence at geotagged centre mandatory): test_centre, analyser, certificate, reading. Three cross-source key tiers: chassis_no (strong), plate (noisy, OCR), person name/mobile/address (fuzzy). Synthetic generator with seed + planted scenarios.
B. Context discovery (rubric 2) "Federated Size-n Context with Threshold Merge": each wrapper gets POST /context {plate|tokens, n, theta}: seeds = matching tuples; best-first FK expansion in both directions, each hop one SELECT ... WHERE fk IN (...) ORDER BY importance LIMIT n (Précis semijoin pattern); tuple score s = Af(path) * Im(t) * (1+lambda*kw), Af = product of edge weights w(e)=w0/(1+ln avg fan-out) (Fakas size-l OS reverse cardinality), Im = normalised log in-degree * recency decay; stop when Af<theta; safe pruning via Fakas max/mmax bounds; return top-n connected tuples with provenance paths + an upper bound u_s on anything unreturned. Mediator: 2-round union variant of TPUT (round 1: ceil(n/m)+slack from each; tau = n-th best of trust*score; round 2: only sources with trust*u_s > tau asked for tuples >= tau). "Beyond across databases": bridge values (chassis, plate, person) become Kite-style abstract states with upper bound parent*bridge-confidence*max; expand highest bound first; stop when all frontier bounds <= tau; depth <= 2 hops. Keyword mode ("stolen white Creta Gurgaon"): per-token seeds per source, HRJN rank-join on the plate across sources, then expand. Metrics: tuples/bytes/round trips/SQL statements vs baselines B0 (ship everything + full FK closure) and B1 (each source ships top-n), scaling data x1/x10/x100; precision@n/nDCG@n vs a judged context set; exactness vs a centralised oracle.
C. Unstructured (rubric 3): the document store is a SIXTH autonomous source with its own wrapper (POST /search with a context bundle, not SQL). Corpus: ~1-2k template-generated synthetic FIR narratives / surveyor reports / accident reports / news-style stories with injected noise (OCR plates, Hinglish, transliteration variants) and planted conflicts, plus ~50-100 real public docs (MACT judgments from Indian Kanoon, MoRTH/IRDAI circulars) re-mapped to the fleet. Context -> typed weighted mentions w = idf * trust(source) * gamma^hops; anchors (plate, chassis, FIR no., policy no.) vs descriptors (name, station, make/colour). Retrieval: Faerie-style approximate dictionary extraction where the dictionary is the query's OWN context (reusing our q-gram index and OCR-confusion edit distance), BM25 with context-weighted query, RM3 seeded by anchor-matched docs, optional MiniLM; fuse with RRF; EROCS-style segment linking (score = sum tf*w, support threshold theta) back to tuples; conflict detection text vs DB (regex/gazetteer extractors; KBT-style extraction-confidence x doc-trust; flag, don't override). Eval: judged set via pooling, nDCG@10/MRR, link P/R, ablation query-only vs +context.
D. Analytics on context: cross-source indicators — "alibi" contradictions (PUC test or camera capture or challan during a reported stolen interval => false report or clone; PUCC 2.0 makes presence mandatory, which we believe is novel), theft-claim timing, transfer-before-loss, impossible travel; ring detection via Subelj et al. 2011 Iterative Assessment Algorithm on the k-hop context entity graph; fleet-wide "suspicion leaderboard" via federated Threshold Algorithm (sorted/random access endpoints per source).
E. Rubric 4: prompts logged; generated: / reviewed: commit pairs; markers in code.

QUESTIONS FOR YOU:
1. Which parts are genuinely innovative and defensible vs over-engineered or academically shaky? Is "union-TPUT" meaningful when sources hold disjoint relations (is it just a trivial top-n merge?) — if weak, what would make the distributed top-k claim real (e.g., a global score that genuinely aggregates across sources per entity)?
2. Is the size-l OS / Précis-based local context scoring the right core for rubric 2? Any flaw (e.g. score non-monotonicity, pruning safety, cross-source affinity calibration)?
3. Rubric 3: is EROCS + Faerie + BM25/RM3 + RRF the right level, or too much? What is the minimal set that is still clearly beyond "embed and cosine"?
4. Scope risk for ~2 weeks with 3-4 people: what to cut, what to keep, in what build order? What is the single strongest demo moment?
5. What will the professor most likely attack, and what should the team ask him BEFORE building (list the sharpest 5-7 clarification questions)?
6. Any better-known or more recent work we missed that he might expect (e.g., federated top-k, keyword search over federated databases, data lakes table discovery like Aurum/JOSIE/LSH Ensemble, "joinable table" discovery)?
```


### Prompt 6 — 2026-11-11 12:45 UTC — student

```text
In questions for my professor, the answers are one, context can be whatever is most suited for our project. Two, best end. Again, this best end thing that you mentioned, I was trying to link it to the second part of the project wherein we had to do matching and cleansing, right? So in the matching part of it, for each individual database, we do this sort of local matching or local selection at end. Over here, best end can be, again, what is recommended per database or overall. I think per database works fine because then you do it at a local end and you make it somewhat more scalable and easier. You can use more than one round to get them if that is more efficient and more innovative. Beyond across databases, the point third that you made doesn't. Whatever you recommend, I think following values into other databases would be the thing. I think beyond across database, have you taken it verbatim from what I've said? In that case, I mean, just across databases, how do you get the context that I've attached the lecture slide in the previous thing, right? So whatever it means from that, try to take. Scalability measure, latency. Latency is the main scalability measure in that. More than latency, he liked to see it in terms of how much data is, you know, being made more efficient. In that, if we say if our previous context took 50,000 rows and we shifted down to like 10,000 or 5,000, that's an improvement of the scale that we can show. Unstructured data, anything. It doesn't matter. It doesn't matter whatever there is. There is no minimum size. Any documents can be taken. There is no need for real documents. Generated documents are fine. They can live on their own database or whatever it is. That is not a problem. And Analytics would be per query, if fleet-wide is possible, that is another better thing, I'm assuming. Each database discovers the context again. This is pursuant to whatever is best implementable to us. If we can do the second part of it inside the database itself, should be fine, but go through the slides. I think it is supposed to be like two separate things, but I'm not really sure. Query types can be, I think, free text queries would be innovative, right? Like imagine like a search engine where we say, list all cars with, I don't know, a stolen white Creta Gurgaon, something like that. Or can we try making that innovative? I don't know. Local models, see, LLM usage was initially meant for like how I'm doing with you, right? Planning and building stuff with code. But if we can use actual LLMs within the project, I have Olama on my system, I think. So I think you can work with LLMs. There is no problem in working with LLMs. Real local LLMs. And questions for me, you can ignore. None of them are important. Phase 3 deadline is 21st November. But we will plan on finishing it off in the next 2-3 days. There is no time constraints. Team split is the same thing. Same for people. And the plans can go wherever you think is important. Not an issue.
```


### Prompt 7 — 2026-11-11 12:49 UTC — student

```text
Yes, I think this plan is fine. Start with multiple agents in parallel. I think you can implement the whole thing together in one go. There's no problems in that. Go for everything, not just the first part. Go for everything and show me a working version of this. Try to make that your end goal. /goal ensure that I get to see a final working model of this. Try to use as many UI skills as you want, improve the project, come up with newer research things that you want to implement in the meanwhile, consult Fable 5.2 as much as you want, and blew my mind.
```


### Prompt 8 — 2026-11-11 12:56 UTC — sub-agent task

**Task** (WS-A schemas and generator, model default):

```text
You are workstream A of a 5-way parallel build of "phase 3" of a university Information-Integration project (federated mediator over 5 autonomous vehicle databases). Repo: <home>\IIA-vehicle-data-federation\IIA_Project (Windows; use `.venv/Scripts/python.exe`; Bash tool is Git Bash). Other agents are editing other files at the same time.

READ FIRST: `docs/phase3/CONTRACT.md` (exact table/column names, house rules, file ownership, interfaces), `data/context_graph.json`, `docs/superpowers/plans/2026-11-11-phase3-context-integration.md`.

YOUR JOB (rubric point R1: each DB has 4-5 tables, entity information distributed across tables, FK-PK joins):
1. Append the NEW tables from CONTRACT.md to `sources/{reg,ins,theft,cam,puc}/schema.sql` exactly as named (plain ANSI SQL that runs on SQLite, PostgreSQL and MySQL 8 — check the existing file style; declare REFERENCES where the parent is in the same file). Do NOT alter existing tables. Mark additions with `-- CONTEXT-ADDED (R1: ...)` / `-- CONTEXT-END`.
2. Write `scripts/phase3_generate.py` (seeded, deterministic, idempotent): reads the EXISTING root CSVs (`reg_owners.csv`, `reg_vehicle_registration.csv`, `ins_policy_records.csv`, `theft_crime_records.csv`, `cam_plate_captures.csv`, `cam_cameras.csv`, `puc_records.csv` …) and writes the NEW tables' CSVs at repo root as `<src>_<table lower>.csv` (e.g. `reg_vehicle_identity.csv`, `ins_claims.csv`, `theft_stolen_property.csv`, `cam_challans.csv`, `puc_cert_tests.csv`). NEVER modify existing CSV rows (phase 1-2 ground truth 621/621 and ~1000 tests depend on them). Realistic Indian data: 17-char VINs (e.g. MA3EWDE1S00123456), engine nos, 10-digit mobiles starting 6-9, pincodes, real-ish Delhi/Gurgaon/Noida/Mumbai/Bengaluru station/zone/centre names (CRIME_RECORDS.police_station values must all exist in STATIONS; CAMERAS camera_ids must all exist in CAMERA_ZONES). Keep each agency's own formats (INS dates DD/MM/YYYY text, THEFT epoch ints, CAM ISO timestamps). Every vehicle in REG gets a VEHICLE_IDENTITY row; INS POLICY_PARTIES chassis matches REG's chassis for the same plate (a few deliberately missing/typo'd); THEFT STOLEN_PROPERTY chassis for each FIR's vehicle; policy holders are INS's own identity records (name spelled differently from REG owner sometimes, e.g. initials, "Kumar"/"Kr.", swapped order; same mobile most of the time). PERSONS/INCIDENT_PERSONS: complainant usually = the owner (fuzzy name, same mobile), some accused/witnesses.
3. Plant the scenarios listed in CONTRACT.md (ALIBI, QUICK_CLAIM, TRANSFER_BEFORE_LOSS, PERSON_RING, CLONE, HARD NEGATIVES) using EXISTING vehicles wherever possible (pick existing stolen vehicles from theft_crime_records.csv; for ALIBI use a PUC CERT_TEST inside the stolen interval, and a CHALLAN on an existing capture of that plate that falls after the FIR date if one exists). Do not break the 7 story vehicles listed in the contract. Write `data/phase3_scenarios.json`: list of {id, kind, plates, chassis, persons, expected_indicators, evidence:[[source, table, pk],…], explanation}. Aim for 2-4 instances of each kind.
4. Extend `scripts/load_source.py` SOURCES (marked `# CONTEXT-ADDED`) with all new tables (parents first) plus `"DOCS": [("docs_documents.csv","DOCUMENTS")]` (WS-D creates that CSV and `sources/docs/schema.sql`; make load_source tolerate a missing CSV for DOCS by skipping with a message if that's not already the behaviour). Check `data/load_all.py` (a teammate's legacy SQLite loader): if the local cluster/tests use it, add the new tables there too, marked. Also find how the local SQLite files `sources/<id>/<id>.db` get built and make sure re-running the documented load rebuilds them including new tables.
5. Extend each `sources/<id>/wrapper.py` default_tables whitelist with the new tables (marked edit).
6. `scripts/phase3_scale.py --factor N --out data/scale/xN/`: clones the whole dataset N times (new plates/ids/chassis/persons, same distributions, same schemas incl. old tables) into one SQLite file per source, for scalability measurement. Keep scale data out of git (check .gitignore; add `data/scale/` marked if needed).
7. Run the generator and reload the five local SQLite DBs. Then verify phase 1-2 still work: run `.venv/Scripts/python.exe -m pytest -q -x tests/ -p no:cacheprovider` — NOTE other agents are working concurrently; they will not bind ports 8001-8006, so you may run the full suite (it starts a local cluster on those ports). If the schema matcher / mapping / ground-truth tests break because the new tables add columns, investigate and fix minimally with markers (e.g. matcher should ignore phase-3 tables if mappings were computed from the old ones — but do not rewrite the matcher; prefer a small, explained filter). Report every test failure you could not fix, with the output. Also run `scripts/evaluate_ground_truth.py` (or however ground truth 621/621 is checked; grep for 621) and report.
8. Write a short unit test `tests/test_phase3_schema.py`: each source has ≥4 tables after load, every FK in context_graph.json resolves (no orphan child rows), each planted scenario's evidence rows exist.

RULES: Mark every edit to an existing file (`# CONTEXT-ADDED (R1: reason)` … `# CONTEXT-END`, or `# CONTEXT-REPLACED (R1: reason):` + old lines commented + new lines). New files say `CONTEXT-ADDED (R1)` in their docstring. NO git commit/stash/checkout/reset. Do not edit files outside your ownership: you own schema.sql files, wrappers' default_tables lines, load_source.py, load_all.py, the two new scripts, the new CSVs, data/phase3_scenarios.json, tests/test_phase3_schema.py, .gitignore. Do NOT touch data/context_graph.json, mediator/context_types.py, docs/phase3/CONTRACT.md — if they are wrong, say so in your report. Never mention lecture slides. Type hints; modules under ~300 lines; one-line comments naming trade-offs. Never print or log password <redacted>

FINAL REPORT (concise): files created/changed, row counts per new table, the planted scenario list (ids/plates), test results (exact pass/fail counts), anything other workstreams must know (e.g. real column types, any deviation from the contract).
```


### Prompt 9 — 2026-11-11 12:56 UTC — sub-agent task

**Task** (WS-B in-DB context discovery, model default):

```text
You are workstream B of a 5-way parallel build of "phase 3" of a university Information-Integration project (federated mediator over autonomous vehicle databases on different laptops: REG PostgreSQL, INS MySQL 8, THEFT SQLite, CAM PostgreSQL, PUC SQLite; locally all SQLite). Repo: <home>\IIA-vehicle-data-federation\IIA_Project (Windows; `.venv/Scripts/python.exe`; Bash tool = Git Bash). Other agents edit other files concurrently; workstream A is right now adding the new tables + data (they may not be loaded yet when you start).

READ FIRST: `docs/phase3/CONTRACT.md`, `data/context_graph.json`, `mediator/context_types.py`, `mediator/source_client.py`, `docs/superpowers/plans/2026-11-11-phase3-context-integration.md` §4, and `sources/wrapper_template.py` (FastAPI factory, `guard_sql`, `create_app`).

RUBRIC R2 (3 marks): "discover context from the query results and beyond, across databases, exploiting FK-PK; the (sub)query is federated; each DB discovers context; the accumulated context must be highly related to the query". Professor's extra asks: each DB returns only its best n context tuples (not everything), real innovation in scalability/fetching, measured as how much less data moves.

BUILD:
1. `sources/context_endpoint.py` (new): given one source's section of context_graph.json, a SQLAlchemy dialect name (sqlite / postgresql / mysql), and the request, generate ONE scored `WITH RECURSIVE` SQL statement that runs inside the DBMS:
   - seeds: rows matching the seed plates (compare canonicalised: uppercase alphanumerics — write dialect-correct SQL expressions to strip spaces/hyphens and upper-case the plate column), seed chassis, seed pks per table;
   - expands along FK edges in BOTH directions up to max_hops; path affinity = product of edge weights; edge weight = explicit `w` or w0/(1+ln(avg fan-out)) where avg fan-out is measured live (a cheap COUNT query, computed per request or cached ≤ 60 s — note: caching statistics, not data, is allowed);
   - relevance = affinity × (base + keyword hits in the table's `text` columns + time-window overlap via the table's `time` col/fmt (iso_date, iso_ts, ddmmyyyy, epoch — dialect-specific parsing) + `decision` boosts), clipped to [0,1];
   - stop expanding a path when affinity < theta; avoid cycles (track visited table#pk in the path string);
   - per-parent limit with ROW_NUMBER() OVER (PARTITION BY parent ORDER BY score DESC) so one high fan-out parent can't starve the rest;
   - return the top n rows (each: table, pk, the row's columns as JSON or a second fetch by pk — your choice, explain it), path, hops, score; PLUS `bound` = an upper bound on the score of any row NOT returned (n-th score, or the best pruned frontier) computed from the same live data.
   - mode "closure" (baseline B0): no theta, no top-n — the full FK closure of the seeds (cap at a big number, e.g. 50,000).
   Generating per-row heterogeneous columns in one recursive CTE is awkward; a sound design is: the recursive CTE walks (table, pk, path, affinity) over a UNION of per-edge steps, a final SELECT ranks, then the endpoint fetches the selected rows' columns with one `SELECT * … WHERE pk IN (…)` per table. That still counts as in-DB discovery: the ranking and pruning happen in the DBMS. Keep it all metadata-driven: no table/column names hard-coded in Python.
   - Verify the SQL is valid for sqlite (run it), and at least render it for postgresql and mysql dialects and eyeball / unit-test the dialect-specific parts (string functions: SQLite `replace`/`upper`, Postgres `regexp_replace`/`upper`, MySQL `REGEXP_REPLACE`/`UPPER`; date parsing: SQLite `date()`/`julianday`, Postgres `to_date`/`to_timestamp`, MySQL `STR_TO_DATE`/`FROM_UNIXTIME`; recursive CTE syntax is the same; MySQL needs `CAST(... AS CHAR)` for string concatenation of the path via CONCAT; Postgres `||` needs text casts).
2. Mount `POST /context` in `sources/wrapper_template.py` `create_app` with a MINIMAL marked edit (`# CONTEXT-ADDED (R2: …)` … `# CONTEXT-END`): loads the source's graph section from data/context_graph.json (path overridable via env `CONTEXT_GRAPH`), runs the generated SQL read-only with the existing statement timeout, returns `{"rows","bound","row_count","elapsed_ms","sql"}`; source unknown in the graph → 404. Also extend the guard so a read-only `WITH RECURSIVE … SELECT` is accepted where needed, without weakening the existing injection protection (existing guard tests must still pass: `tests/test_wrapper*.py`, `tests/test_guard*` — grep for them).
3. `mediator/context_discovery.py`: `discover(query, n=10, theta=0.05, mode="bounded", client=None, max_rounds=4) -> ContextResult` (signature in CONTRACT.md). `query` is a ParsedQuery or a plain string (a plate → canonicalise; anything else → `from mediator.query_understanding import parse` lazily, which workstream C is writing; if import fails, treat the string as keywords).
   - Round 1: /context to every source in parallel (ThreadPoolExecutor) with the plate seeds + keywords + window.
   - Bridge values in returned rows (chassis/plate/person per the `bridges` section) become follow-up states: bound = parent row score × bridge confidence × that source's reported bound or max. Plate confidence: CAM rows → use phase-2 match probability if cheaply available (`mediator/plate_match.py` — look for a probability function; else an OCR-similarity proxy), others 1.0. Person: Fellegi–Sunter posterior from (4).
   - Expand the frontier state with the highest bound first; stop when every remaining bound ≤ the current n-th best global score, or max_rounds. Ship bridge values compactly (dedupe; a Bloom filter or a sorted set of canonical values — if you do a Bloom filter, the wrapper side must test membership, keep it simple and explained).
   - Modes: "bounded" (above), "topn" (baseline B1: one round, top n per source, no follow-ups), "closure" (baseline B0: one round of full FK closure from every source plus following every bridge value once with closure). Fill SourceStats via SourceClient (rows, bytes, round trips, latency, bound, status); `complete=False` if any source not OK; `baseline_rows` optional.
   - A source DOWN never raises.
4. `mediator/person_resolution.py`: blocking (name q-grams / mobile / pincode), comparison vector (name Jaro-Winkler bands, mobile exact, pincode/address token overlap), Fellegi–Sunter with m/u learned by EM (no labels) — implement EM yourself (numpy ok), log-likelihood ratio → posterior, threshold, union-find clustering into PersonCluster with a golden record (most frequent / longest name, mobile, address). `resolve_persons(records)` and `fit_em(...)` per CONTRACT. Records can be fetched by the mediator from REG OWNERS+OWNER_CONTACTS, INS POLICY_HOLDERS, THEFT PERSONS via SourceClient.query — provide a helper `fetch_person_records(client, owner_ids=…, …)` or similar used by discover.
5. Tests (yours: `tests/test_context_endpoint.py`, `tests/test_context_discovery.py`, `tests/test_person_resolution.py`): build tiny temp SQLite DBs in-test that follow the contract schemas, use FastAPI `TestClient` on `create_app` (NO real ports — do NOT start the local cluster or bind 8001-8006; other agents run concurrently), and a fake client object for discover. Test: top-n returned and ≤ n; per-parent limit works; theta prunes; bound ≥ score of every unreturned row (compare against the closure mode as an oracle); keyword/time/decision raise the right rows; guard still rejects injections; discover stops early with bounds and ships fewer rows than closure; DOWN source → complete=False, no exception; EM recovers sensible m/u on synthetic pairs and clusters the obvious duplicates while keeping "same name, different mobile" apart.
   Run your tests plus the existing wrapper/guard tests (those use TestClient? check; if a test needs `local_cluster`, skip it — the integrator runs the full suite).

RULES: Mark every edit to an existing file (`# CONTEXT-ADDED (R2: reason)` … `# CONTEXT-END`, or `# CONTEXT-REPLACED (R2: reason):` + old lines commented + new lines). New files say `CONTEXT-ADDED (R2)` in the docstring. NO git commit/stash/checkout/reset. Only edit files you own: the new files above + the marked edit in sources/wrapper_template.py. Do NOT edit context_types.py / source_client.py / context_graph.json / CONTRACT.md — if they need a change, say exactly what in your report (you may add helper code in your own modules instead). Never mention lecture slides. Type hints; modules ≲ 300 lines (split if needed); a one-line comment naming each non-obvious trade-off (the student must defend it to a professor). Cite the ideas in docstrings by their research names where apt (size-l object summaries / Précis-style weighted schema graph, Fagin's threshold algorithm, bind join, Fellegi–Sunter) — not lecture material.

FINAL REPORT (concise): files, the SQL design in 5 lines, an example generated SQL for SQLite (short), test results with counts, dialect caveats (esp. MySQL), any contract change you need.
```


### Prompt 10 — 2026-11-11 12:57 UTC — sub-agent task

**Task** (WS-C query, top-k, analytics, model default):

```text
You are workstream C of a 5-way parallel build of "phase 3" of a university Information-Integration project (federated mediator over autonomous vehicle databases: REG, INS, THEFT, CAM, PUC (+ DOCS), each behind a FastAPI wrapper with a guarded read-only `POST /query {"sql"}` that appends LIMIT 200). Repo: <home>\IIA-vehicle-data-federation\IIA_Project (Windows; `.venv/Scripts/python.exe`; Bash tool = Git Bash). Other agents edit other files concurrently; workstream A is adding new tables + data right now.

READ FIRST: `docs/phase3/CONTRACT.md`, `data/context_graph.json`, `mediator/context_types.py`, `mediator/source_client.py`, the plan `docs/superpowers/plans/2026-11-11-phase3-context-integration.md` §4.4 and §6. Skim `mediator/plate_match.py`, `mediator/lexicons.py`, `mediator/catalog.py`, `mediator/registry_loader.py` / the mappings (global attributes like vehicle_make, vehicle_colour, stolen_status, last_seen_location; how global attributes map to each source's columns) — reuse the registry rather than hard-coding columns where you reasonably can.

BUILD:
1. `mediator/llm.py`: tiny Ollama client (http://127.0.0.1:11434, model `qwen2.5:3b` is installed; `llama3.2:3b` maybe). `available()` (cached 30 s), `generate_json(prompt, model, timeout_s) -> dict|None` (format=json, temperature 0), `generate_text(...)`. Disk cache keyed by (model, prompt hash) in `data/llm_cache.json` (LLM outputs only — never source data). Never raises; returns None when Ollama is off.
2. `mediator/query_understanding.py`: `parse(text, use_llm=True) -> ParsedQuery`. Examples it must handle: "DL05CD9876", "dl-05 cd 9876", "list all stolen white Creta Gurgaon", "expired insurance Hyundai seen in Noida last month", "vehicles owned by Rahul Sharma", "stolen cars tested for PUC after theft". Rules first: plates (canonicalise), makes/models (derive the vocabulary from data you can query, or a lexicon + the registry; Creta → make Hyundai model Creta), colours, statuses (stolen, expired/lapsed/uninsured, recovered, cancelled), places (Gurgaon/Gurugram, Noida, Delhi…, matched to CAM locations/zones, THEFT stations/districts, REG cities), relative dates ("last month", "after theft"), person names. The LLM (JSON mode) is called only for text the rules leave unexplained; its output is VALIDATED against an allow-list of attributes/ops/values (never free SQL, never trusted blindly), each kept predicate gets confidence; `notes` explain every predicate's origin. Cache per query string. With use_llm=False it must still work.
3. `mediator/entity_topk.py`: `topk(query, k=10, client=None) -> TopKResult` — Fagin's Threshold Algorithm over VEHICLES (the entity), distributed across sources: each source produces a ranked list of (plate, partial score) for its own predicates via a sorted `/query` with ORDER BY score DESC LIMIT depth (sorted access, paging deeper only as needed: depth 10, 20, 40…), random access = one `/query` by plate IN (…) to fetch missing partials. Total score = weighted sum of partials; partial bounds from unseen sources are discounted by identity-match confidence (CAM plates are OCR-noisy: canonical-plate confidence, e.g. from plate_match similarity; exact sources 1.0). Stop when the k-th best total ≥ threshold (sum of the last-seen scores). Report sorted/random accesses, rows shipped, `baseline_rows` (= rows a naive "fetch every matching row from every source" would ship — measure it by COUNT queries, not by shipping), depth. Each RankedVehicle gets a human explanation list. Source-specific SQL must be built from metadata (registry / context_graph.json column lists + value vocabularies), not hard-coded per source in the algorithm; a small per-attribute mapping table at the top of the module is acceptable if registry lookup is impractical — explain.
4. `mediator/analytics.py`: `indicators_for(plate, context=None, client=None) -> list[Indicator]` — cross-source contradictions: ALIBI_PUC (CERT_TESTS test_ts inside a stolen interval = FIR reported_date → recovered_on or now), ALIBI_CAMERA (capture/challan inside the stolen interval; noisy plates via canonical/OCR-tolerant matching), QUICK_CLAIM (theft claim loss_date ≤ 15 days after policy_start, or intimation before FIR), TRANSFER_BEFORE_LOSS (ownership transfer ≤ 30 days before FIR/claim), LAPSED_COVER_SEEN (policy expired but captures after expiry), PERSON_RING (a mobile shared across ≥3 vehicles' owners/holders/accused). Each indicator: severity, plain-English explanation naming dates, evidence (source, table, pk). Use the context rows when given (no extra traffic), else fetch with SourceClient.query. Date formats differ per source (ISO date, DD/MM/YYYY text, epoch ints, ISO timestamps) — parse via metadata (`time.fmt` in context_graph.json) or existing `mediator/transforms.py` helpers. `leaderboard(k=10, client=None) -> list[RankedVehicle]`: fleet-wide most-suspicious vehicles via the same threshold-algorithm machinery (each source contributes a suspicion partial score from its own tables, e.g. THEFT open stolen FIR, INS theft claim/quick claim, PUC test after theft, CAM sightings after theft), explanations = indicator titles. `data/phase3_scenarios.json` (written by workstream A, may appear while you work) lists planted cases + expected indicators: use it in a test that's skipped if absent.
5. Tests (`tests/test_query_understanding.py`, `tests/test_entity_topk.py`, `tests/test_analytics.py`): use a FAKE client (object with `.query(source_id, sql)` running SQL on temp SQLite DBs you build in the test following the contract schemas, and `.stats()`), and `use_llm=False` (plus one test mocking llm.generate_json to check validation drops an invalid attribute). TA test: result equals brute-force top-k on random data, with fewer accesses than full scan. Do NOT start the local cluster or bind ports 8001-8006 (other agents run concurrently).

RULES: New files say `CONTEXT-ADDED (R2: …)` in the docstring; any edit to an existing file is marked (`# CONTEXT-ADDED (R2: reason)` … `# CONTEXT-END`) — you should not need to edit existing files. NO git commit/stash/checkout/reset. Do NOT edit context_types.py / source_client.py / context_graph.json / CONTRACT.md or other workstreams' files; request changes in your report. Never mention lecture slides. Type hints; modules ≲ 300 lines; one-line comments naming non-obvious trade-offs (student must defend it). SQL must go through the wrapper's guard: single SELECT, no semicolons, whitelisted tables, it appends LIMIT 200 if absent (so page explicitly with LIMIT/OFFSET or ORDER BY … LIMIT).

FINAL REPORT (concise): files, how TA is distributed (5 lines), parse() output for the 6 example queries (with use_llm=False and, if Ollama is up, True), test counts, contract change requests.
```


### Prompt 11 — 2026-11-11 12:57 UTC — sub-agent task

**Task** (WS-D documents source, model default):

```text
You are workstream D of a 5-way parallel build of "phase 3" of a university Information-Integration project (federated mediator over autonomous vehicle databases REG, INS, THEFT, CAM, PUC behind FastAPI wrappers). Repo: <home>\IIA-vehicle-data-federation\IIA_Project (Windows; `.venv/Scripts/python.exe`; Bash = Git Bash). Other agents edit other files concurrently; workstream A is right now adding the new structured tables + data (CSV files at repo root like `theft_stolen_property.csv`, `ins_claims.csv`, `theft_persons.csv`… — may appear while you work).

RUBRIC R3 (3 marks): "Using the query context, search unstructured data that is highly correlated with that context, and integrate it." Student's answer: generated documents are fine, own database.

READ FIRST: `docs/phase3/CONTRACT.md`, `data/context_graph.json`, `mediator/context_types.py` (DocResult, RetrievedDoc, Mention, Conflict, ContextResult, ContextRow), `mediator/source_client.py`, plan §5 in `docs/superpowers/plans/2026-11-11-phase3-context-integration.md`, `sources/wrapper_template.py` (`create_app`), `sources/reg/wrapper.py` (thin wrapper pattern), `sources/server_manager.py`, `scripts/configure_cluster.py`, `mediator/plate_match.py` (q-gram / OCR-aware plate similarity to reuse).

BUILD:
1. DOCS as a 6th autonomous source: `sources/docs/__init__.py`, `sources/docs/schema.sql` (DOCUMENTS per contract; plus an FTS-free design — keep it portable), `sources/docs/wrapper.py` (port 8006, SQLite by default, same pattern as other wrappers via the factory) with an extra `POST /search` route: body `{"terms":[{"term","weight"}], "anchors":[{"value","attr"}], "k": 20, "doc_types": null}` → BM25 ranking computed INSIDE the source (the wrapper owns its inverted index built at startup / on first call from DOCUMENTS; implement BM25 yourself — no new dependency; tokeniser that keeps plate-like tokens, normalises hyphens/spaces, lowercases), anchor hits boost; returns top k `{doc_id, doc_type, title, issued_on, score, body}` plus `elapsed_ms`. Anchor matching must be typo/OCR tolerant (plate "DLO5CD9B76" ≈ "DL05CD9876"; reuse plate_match utilities or a q-gram + OCR-confusion distance). Add DOCS (8006) with marked edits to `sources/server_manager.py` SOURCE_CONFIGS and `scripts/configure_cluster.py` DEFAULT_PORTS (keep `start_all` default behaviour for existing tests unchanged unless trivially safe; DOCS should start in the normal local cluster used by `scripts/run_local.py`/app if that's how others start — check, mark edits).
2. `scripts/phase3_docs_generate.py`: builds `docs_documents.csv` (repo root) + `data/phase3_gold.json` from the structured data (root CSVs incl. A's new ones when present; degrade gracefully to old CSVs only). ~1,000-1,500 docs: FIR_NARRATIVE (brief facts per FIR: who, when, where, chassis, station), SURVEYOR_REPORT per claim, ACCIDENT_REPORT, NEWS (e.g. "white Creta stolen from Sector 29 Gurugram"), RTO_NOTICE (suspension/cancellation, transfer), WITNESS_STATEMENT. Mostly templates with variety; optionally a fraction (e.g. 60-100 docs) written by the local LLM via Ollama HTTP (http://127.0.0.1:11434, model `qwen2.5:3b`, temperature 0.7, seeded, cached) — must still work with Ollama off (template-only). Noise: OCR'd plates, Hinglish phrases, spelling variants ("Gurgaon"/"Gurugram", "Kr."), date written "12th July 2026". Planted conflicts (~5%): text says "recovered on …" while DB case OPEN; narrative loss date differs from claim loss_date; colour in text ≠ REG colour. Distractors: docs about the one-character-off plates and same-name different persons. Gold: per doc → linked rows [[source, table, pk],…], relevant plates, planted conflicts. Also docs for the scenarios in `data/phase3_scenarios.json` if present (e.g. the ALIBI vehicle's FIR narrative + a PUC centre notice). Reproducible with a fixed seed.
3. `mediator/doc_retrieval.py`: `retrieve(context: ContextResult, k=5, mode="context+anchors", client=None) -> DocResult`:
   - mode "query_only": terms = the raw query text only (baseline);
   - "context": build a weighted term query from the context rows (descriptors: names, station, make/model/colour, places, insurer…), weight = row score × source trust (REG .95, INS .9, THEFT .9, CAM .6, PUC .8) × gamma^hops × rarity (idf if known, else 1) — explain;
   - "context+anchors": + anchors (plate, chassis, FIR no., policy no.) extracted from the context.
   Then link each retrieved doc back to rows (EROCS-style: find mentions of context values in the text — exact/fuzzy, record Mention spans; a row is linked if its summed mention weight ≥ support threshold), and flag Conflicts (text date vs DB date, recovered vs OPEN, colour/make mismatch, different plate for same chassis). Snippet = best passage around the strongest mention. Uses SourceClient.post("DOCS","/search",…) so DOCS rows shipped are counted.
4. `mediator/grounded_summary.py`: `summarise(context, docs, use_llm=True) -> list[{"sentence","citations"}]`: with Ollama, prompt qwen2.5:3b to write 3-5 short sentences using ONLY the given facts, each followed by citation ids ([D:doc_id] / [REG:VEHICLE_REGISTRATION#405]); post-validate: drop any sentence without a valid citation; fallback without LLM = template sentences from rows/docs. Never raises.
5. `mediator/doc_eval.py` (or inside a script): nDCG@10, MRR, link precision/recall, conflict recall vs `data/phase3_gold.json` for the three modes — function `evaluate_docs(contexts: dict[plate -> ContextResult], client) -> dict` used by workstream E; plus `scripts/evaluate_docs.py` that runs it (it may need the live cluster — fine to leave as a script; don't run it while others use ports).
6. Tests `tests/test_docs_source.py`, `tests/test_doc_retrieval.py`: TestClient on the docs app over a temp SQLite with a handful of docs (BM25 sanity, anchor OCR tolerance, ranking), retrieval with a FAKE client and hand-built ContextResult (context mode beats query_only on a planted case; mentions spans correct; conflict detected; linking threshold), summary fallback and citation validation (mock llm). Do NOT bind ports 8001-8006 or start the local cluster.
7. Run the generator once (after checking whether A's CSVs exist; if not yet, run it with old CSVs and note it must be re-run) and load docs into `sources/docs/docs.db` (A is adding a DOCS entry to scripts/load_source.py SOURCES; if not there yet, load via your own small function inside your generator script, `--load`).

RULES: New files say `CONTEXT-ADDED (R3: …)` in the docstring; edits to existing files marked (`# CONTEXT-ADDED (R3: reason)` … `# CONTEXT-END`). NO git commit/stash/checkout/reset. Own only: sources/docs/*, the marked DOCS lines in server_manager.py and configure_cluster.py, the new scripts/modules/tests above, docs_documents.csv, data/phase3_gold.json. Don't edit context_types.py / source_client.py / context_graph.json / CONTRACT.md / load_source.py — request changes in your report. Never mention lecture slides. Type hints; modules ≲ 300 lines; one-line trade-off comments (the student must defend them). Cite ideas by research names where apt (BM25, EROCS-style entity-document linking, approximate entity extraction / q-gram filtering).

FINAL REPORT (concise): files, corpus stats (docs per type, LLM-written count, conflicts planted), /search API, retrieval design (5 lines), test counts, a sample retrieval for one planted plate, contract change requests.
```


### Prompt 12 — 2026-11-11 12:58 UTC — sub-agent task

**Task** (WS-E UI pages and evaluation, model default):

```text
You are workstream E of a 5-way parallel build of "phase 3" of a university Information-Integration project: a Streamlit website over a federated mediator for 5 autonomous vehicle databases (+ a 6th documents source). Repo: <home>\IIA-vehicle-data-federation\IIA_Project (Windows; `.venv/Scripts/python.exe`; Bash = Git Bash). Workstreams A-D are concurrently building the backend you will display; their modules may not exist yet — build against the exact interfaces in the contract and degrade gracefully (show a short "not available yet" note) when an import fails.

READ FIRST: `docs/phase3/CONTRACT.md` (interfaces section!), `mediator/context_types.py`, `mediator/source_client.py`, plan `docs/superpowers/plans/2026-11-11-phase3-context-integration.md` (§1, §4.5, §5 evaluation, §6, §7), `app/app.py` (PAGES nav), `app/theme.py` (styles, sidebar group headings by nth-of-type), 2-3 existing tabs for the house style (e.g. `app/tabs/investigate.py`, `app/tabs/plan_trace.py`, `app/tabs/matching_eval.py`), `tests/test_tabs_render.py` and `tests/test_self_check.py` (they assert 15 nav pages). Invoke the `developing-with-streamlit` skill (Skill tool) and follow its discovery step for version-matched docs; use the `dataviz` skill before writing charts.

BUILD:
1. `app/tabs/context_explorer.py` `render()`: ONE search box (plate or free text, with 4-5 example chips: a story plate like DL05CD9876, an ALIBI scenario plate from `data/phase3_scenarios.json` if present, "stolen white Creta Gurgaon", "expired insurance Hyundai seen in Noida"). Flow: `query_understanding.parse` → if plates: `context_discovery.discover(...)` (bounded) + `analytics.indicators_for` + `doc_retrieval.retrieve` + `grounded_summary.summarise`; if no plate: `entity_topk.topk` → ranked vehicles, click one to open its context. Show, cleanly and with little clutter: the parsed predicates (chips, with "rule"/"LLM" origin); a "data moved" counter (rows & KB shipped vs baseline, % saved, round trips, per-source bars); the context grouped by source (score, join path rendered as a small chain "VEHICLE_REGISTRATION → OWNERS → …", via-bridge badge); a context graph (nodes = rows coloured by source, edges = FK/bridge; use graphviz `st.graphviz_chart` or a simple SVG — no new heavy dependency); analytics indicators as severity-tagged cards with evidence; documents with highlighted mention spans (anchors vs descriptors different styles) and conflict callouts, plus the grounded summary with citations. "Undetermined"/partial banner when `complete=False`. Spinners; results cached in session_state per query (never cache source data across queries — compute per run).
2. `mediator/context_eval.py` + `scripts/evaluate_context.py` → `data/context_eval.json`: for a query set (story plates + scenario plates + ~10 free-text queries): run discover in modes closure (B0), topn (B1), bounded (ours) and record rows/bytes/round trips/latency per source; exactness vs an oracle (closure-mode result re-ranked centrally = "what a central warehouse would pick": overlap@n, plus precision@n / nDCG@n against relevance judged from `data/phase3_scenarios.json` evidence and the generator ground truth); sensitivity over n ∈ {5,10,20}, theta ∈ {0.02,0.05,0.1}; scale ×1/×10/×100 if `data/scale/x10` etc. exist (workstream A writes `scripts/phase3_scale.py`; you can serve them via in-process wrappers — `sources.wrapper_template.create_app(db_url, …)` with FastAPI TestClient or a temp port outside 8001-8006 — your choice, explain), entity top-k vs naive (accesses, rows), and document retrieval ablation by calling `mediator/doc_eval.evaluate_docs` if present. The script must run headless and write JSON; the page only reads the JSON (+ a "re-run" button).
3. `app/tabs/context_evaluation.py` `render()`: charts from `data/context_eval.json`: data reduction (rows shipped B0 vs B1 vs ours, per scale — log axis is OK if labelled), latency, exactness, sensitivity, top-k accesses, retrieval ablation (nDCG@10/MRR by mode). Plain-language one-line takeaway above each chart. Use Altair/Vega via st (already used in the repo? check) or st native charts; follow the dataviz skill (one axis, colour by series in fixed order, legends, text in text colours).
4. Nav: add both pages to `app/app.py` PAGES as a new sidebar group (e.g. "Context (phase 3)") — keep group headings correct in `app/theme.py` (nth-of-type positions), and update the nav-count assertions in `tests/test_tabs_render.py` and `tests/test_self_check.py` (15 → 17) with `# CONTEXT-REPLACED (R2: two phase-3 pages):` + old line commented + new line. Mark all edits to existing files.
5. Tests `tests/test_context_pages.py`: AppTest renders both pages without exception with the backend monkeypatched to return small hand-built ContextResult/TopKResult/DocResult/Indicator objects (no cluster, no ports), and with backend modules missing (graceful). Do NOT start the local cluster or bind ports 8001-8006 (others run concurrently); you may run `tests/test_tabs_render.py` only if it does not require the cluster.

DESIGN: the website must be smooth, uncluttered and pleasant: consistent with the existing theme (read theme.py tokens/CSS classes and reuse them, e.g. existing KPI tile classes), few elements above the fold (search box, verdict line, data-moved counter), progressive disclosure (expanders/tabs) for detail, no walls of JSON, no raw dataclass dumps. Never mention lecture slides/course weeks anywhere in UI text. Explain things in plain words a professor will grasp in seconds (e.g. "Each database ranked its own rows and sent only its best 10 — 312 rows instead of 4,870 (94% less)").

RULES: New files say `CONTEXT-ADDED (R2/R3: …)` in the docstring; edits to existing files marked (`# CONTEXT-ADDED (Rn: reason)` … `# CONTEXT-END`, or `# CONTEXT-REPLACED (Rn: reason):` + old lines commented + new lines). NO git commit/stash/checkout/reset. Own only: the files listed above + the marked nav/theme/test-count edits. Don't edit context_types.py / source_client.py / context_graph.json / CONTRACT.md or other workstreams' modules — request changes in your report. Type hints; modules ≲ 300 lines (split helpers into `app/tabs/context_widgets.py` etc.).

FINAL REPORT (concise): files, page layout description, what's wired vs stubbed waiting on other workstreams, test counts, contract change requests.
```


### Prompt 13 — 2026-11-11 12:58 UTC — sub-agent task

**Task** (Fable advisor design critique, model fable):

```text
You are an expert advisor (databases, data integration, IR) reviewing a student team's phase-3 design for a graded Information-Integration project. Read-only: do not edit any files.

Repo: <home>\IIA-vehicle-data-federation\IIA_Project. Read: `docs/superpowers/plans/2026-11-11-phase3-context-integration.md`, `docs/phase3/CONTRACT.md`, `data/context_graph.json`, `mediator/context_types.py`. (Implementation is being written right now by other agents; don't review code that doesn't exist yet.)

Rubric: (1) each DB 4-5 tables with FK-PK [2]; (2) discover context from query results and beyond, across DBs on different machines, via FK-PK; each DB discovers context; accumulated context highly related to the query [3]; (3) using the context, search highly-correlated unstructured data and integrate it [3]; (4) show prompt, generated code, modified code [2]. Professor additionally asked: each DB returns only its best-n context tuples; genuine innovation in scalability/fetching (measured mainly as rows/bytes moved, also latency) that a typical LLM-prompted project would not produce; analytics.

Give me, in ≤ 600 words:
1. The 3 most likely ways this design fails or looks weak in a 10-minute demo + viva (correctness of the bound/stopping rule across sources, scoring that's arbitrary, evaluation that's circular because the same generator creates data, gold and queries, etc.) and the cheapest fix for each.
2. Any flaw in the scoring/bound semantics as specified (affinity × relevance, per-parent limits, bound u_s, bridge-discounted frontier bounds): is the early-stop rule actually sound? If not, state the exact correct condition.
3. Up to 3 small additions (each ≤ half a day) with the highest "wow per hour" for the professor, grounded in real research ideas — named precisely — that fit this architecture. No ML training, no cloud LLMs.
4. One sentence the student should say to defend "in-DBMS context discovery" against "you could just do it in Python at the mediator".
Be concrete and terse.
```


### Prompt 14 — 2026-11-11 13:00 UTC — follow-up to a sub-agent

**Message** (Soundness fixes for bounds/stop rule):

```text
Advisor review of the WS-B design — please apply these (they override the earlier brief where they conflict):
1. Relevance is NOT monotone along a path (low-relevance VEHICLE_IDENTITY row → high-relevance open FIR). Frontier bounds must use the parent's AFFINITY (product of edge weights × bridge confidences), not its score.
2. Rows below a source's local top-n can still carry bridge values. /context must also return, for every row in the θ-closure (affinity ≥ θ) that has a bridge column, just {table, pk, affinity, bridge attr, bridge value} — cheap, bridge columns only — so the mediator can create frontier states from them. Count these in rows/bytes shipped honestly (report them separately e.g. "bridge_hints").
3. Window functions are not allowed in the recursive term on MySQL 8/SQLite: enumerate the θ-closure in the CTE (cycle guard, keep MAX affinity per table#pk), then apply per-parent ROW_NUMBER + score ranking + LIMIT in the OUTER SELECT. Then u_s = the (n+1)-th score exactly (0 if fewer). Oracle/closure comparison must use the same diversification and a pk tie-break.
4. Clamp: relevance to [0,1]; edge weight min(1, ·) (avg fan-out < 1 would give w > 1).
5. Stop rule (sound): with score = A·R, stop when τ_n ≥ max( max_s u_s , max_f A(f)·c_f·W_t·R̄_t ), τ_n = n-th best global score, f over ALL unexpanded bridge states (from returned rows and bridge hints), W_t = largest first-hop edge weight in target source t (≤1), R̄_t = max attainable relevance in t from metadata (clip at 1). DOWN source ⇒ u_s = 1 and complete=False.
6. Add a randomised property test: bounded mode result == central-oracle top-n (closure re-ranked with the same scoring + diversification) over many random seeds on a synthetic multi-source fixture.
7. Record per round in ContextResult-adjacent trace (add to your module, e.g. `discover(...).trace` is not in the dataclass — return it via a module-level `last_trace` or put it in SourceStats? better: add an optional `trace: list[dict]` you attach as attribute `result.trace`) : round no., τ_n, max frontier bound, follow-ups expanded vs pruned. The UI will plot τ_n vs max frontier bound per round (threshold-convergence plot).
8. Bridge shipping: per follow-up choose value list vs Bloom filter by estimated bytes (SDD-1 semijoin profitability idea + Bloomjoin, Mackert & Lohman); record the choice and measured false-positive rate in the trace. If Bloom is too much work, keep value lists but still log the estimated bytes of both options.
9. Person resolution: don't ship whole person tables — push blocking keys (mobile, pincode, name q-gram prefix) to the sources as /query filters and fetch only candidate records; count that traffic.
10. Also implement a baseline mode "mediator" (B2): mediator-side per-edge traversal in Python — one /query per FK edge per hop, no in-DB ranking — this is what a typical project does; record its rows/round trips.
```


### Prompt 15 — 2026-11-11 13:00 UTC — follow-up to a sub-agent

**Message** (TA threshold discount fix + TPUT):

```text
Advisor review for WS-C: (1) In the threshold algorithm, do NOT discount the threshold by identity probability — discount object scores only; the undiscounted threshold is the valid bound. (2) For the fleet leaderboard, also implement TPUT (Cao & Wang, PODC 2004: three-phase uniform threshold — phase 1 top-k from each source, phase 2 fetch all with partial ≥ τ1/m, phase 3 random access to complete) alongside TA, and expose both via a `method="ta"|"tput"` parameter with access/round-trip/rows counts so the evaluation can chart TA vs TPUT. (3) Add a property test: results equal brute-force top-k on random data for both methods.
```


### Prompt 16 — 2026-11-11 13:01 UTC — follow-up to a sub-agent

**Message** (Evaluation upgrades from advisor review):

```text
Advisor review for WS-E evaluation — please include: (1) a 4th baseline B2 = discover(mode="mediator") (mediator-side per-edge traversal, one /query per FK edge per hop — WS-B is adding it; skip gracefully if absent). (2) Lead with oracle exactness (bounded result vs central-oracle top-n), not nDCG — the generator makes data and gold, so nDCG is partly circular; show nDCG/precision as secondary, plus rank stability (Kendall τ) when edge weights/boosts are perturbed ±50%. (3) Include hub seeds (a person owning many vehicles, a busy camera, a busy police station) at ×1/×10/×100 and plot rows & bytes vs scale per mode — single plates are too small to show reduction. (4) Threshold-convergence plot: per round τ_n (n-th best score) vs max frontier bound, crossing at the stop (WS-B attaches `result.trace`, a list of dicts with round, tau_n, max_frontier_bound, expanded, pruned; skip if absent), and a "follow-ups pruned by bound: k of m" figure on the Explorer page. (5) Top-k: TA vs TPUT (WS-C adds `method="ta"|"tput"` to leaderboard/topk) on round trips and rows.
```


### Prompt 17 — 2026-11-11 13:41 UTC — follow-up to a sub-agent

**Message** (CLAIMS.claim_status width bug):

```text
Bug from WS-B: CLAIMS.claim_status VARCHAR(12) cannot hold 'INVESTIGATING' (13 chars) — PostgreSQL rejects, MySQL strict errors. Please change it to VARCHAR(16) in sources/ins/schema.sql (inside your CONTEXT-ADDED block) and I'll update CONTRACT.md. Also make sure the new zone/station/test-centre tables are in each wrapper's whitelist (WS-C needs them for location predicates).
```


### Prompt 18 — 2026-11-11 15:31 UTC — student

```text
continue
```


### Prompt 19 — 2026-11-11 16:30 UTC — student

```text
continue
```


### Prompt 20 — 2026-11-11 16:30 UTC — follow-up to a sub-agent

**Message** (Please finish and report now):

```text
Your background work appears finished (no python processes running). Please check its output, make the CLAIMS.claim_status VARCHAR(16) fix if not done, and send your final report now.
```


### Prompt 21 — 2026-11-11 16:30 UTC — follow-up to a sub-agent

**Message** (Please finish and report now):

```text
Your background work appears finished (no python processes running). Please check its output, wrap up and send your final report now.
```


### Prompt 22 — 2026-11-11 16:30 UTC — follow-up to a sub-agent

**Message** (Please finish and report now):

```text
Your background work appears finished (no python processes running). Please check its output, wrap up and send your final report now. Note: WS-B and WS-C are done; ContextResult now has real fields `trace` (list of dicts) and `hints_shipped` (dict), TopKResult has `round_trips` and `method`.
```


### Prompt 23 — 2026-11-11 16:35 UTC — follow-up to a sub-agent

**Message** (Three fixes from evaluation findings):

```text
Integration found three issues in your modules (your work is committed; keep editing in place, still no git ops, mark changes with CONTEXT-ADDED/REPLACED):
1. PERF: `context_scoring.plate_confidence` runs ~32k uncached OCR edit distances per discover call — ~90% of the 10 s per plate; closure takes 37 s. Cache it (lru_cache on (canonical, value) or precompute per distinct value) and any similar hot path. Target: bounded discover < 2 s per plate locally.
2. B2 `mode="mediator"` ignores `seeds=` and keywords, so for hub/free-text queries it ships 16 rows — not a fair baseline. Make it honour the same seeds/keywords/window as bounded, traversing per FK edge per hop with plain /query.
3. Bounded mode should set `ContextResult.baseline_rows` (the rows B0 closure would ship) cheaply, WITHOUT shipping them — e.g. an optional `count_only` flag on /context that returns the closure row count from the same recursive CTE (one integer per source), and the bridge follow-ups' counts likewise; or if that is too costly, compute it only when `discover(..., with_baseline=True)` and have the UI pass True. Explain the choice.
Also: `ContextResult` now has real dataclass fields `trace: list[dict]` and `hints_shipped: dict[str,int]`, and `SourceStats.hints_shipped: int` — switch from attribute assignment to those fields. Then run your 3 test files + tests/test_context_eval.py + tests/test_context_pages.py and report timings for DL05CD9876 / HR26EF4455 bounded vs closure vs mediator on the local SQLite DBs (use in-process TestClient as before; no ports 8001-8006).
```


### Prompt 24 — 2026-11-11 20:50 UTC — student

```text
status?
```


### Prompt 25 — 2026-11-14 13:28 UTC — student

```text
Okay, so I went through the project and you added the two new this context features in the end. I want to see as to how better the UI could be made, and not in the UI sense, but in terms of features. I'm really confused because I just entered in one of them the the license plate number, and it showed me a bunch of documents, and they were not really clear as to what the documents were. I couldn't open the documents to see them, and it was a whole lot of mess and jumble. And the second context evaluation feature, I was not even sure what that was. So I want you to expand as much as you can, make the UI much more simpler, much more appealing to see. First, let me know what features are we implementing. Could we do better than that to make it sure that there is some innovation and some novelty to what we're approaching? Novelty and innovation is not just clean and error-free implementations, but unique features that most people won't think about implementing. I want that to happen, and yeah, go ahead and try planning and then working with it. Consult Fable 5.2 as much as required to help you think, analyze, come up with a plan, and then implement it.
```


### Prompt 26 — 2026-11-14 13:30 UTC — sub-agent task

**Task** (Fable: novel features + UX plan, model fable):

```text
You are a design + research advisor (read-only: do NOT edit any files). Repo: <home>\IIA-vehicle-data-federation\IIA_Project (Python, Streamlit app under app/, mediator under mediator/, wrappers under sources/).

## Project
A federated mediator over 6 autonomous agency databases about vehicles (REG PostgreSQL, INS MySQL, THEFT SQLite, CAM PostgreSQL, PUC SQLite, DOCS SQLite with 1,152 unstructured documents: FIR narratives, surveyor reports, accident reports, news, RTO notices, witness statements, with OCR'd plates, Hinglish, planted text-vs-DB conflicts). No warehouse; every query reads live. Read docs/phase3/WHAT_WAS_BUILT.md and docs/phase3/CONTRACT.md first — they describe what exists: in-DBMS scored WITH RECURSIVE context discovery with best-n + exact bound per source, multi-round bound-driven cross-source expansion (bind join via chassis/plate/person bridges with confidences), Fellegi–Sunter person resolution, free-text parse (rules + local LLM qwen2.5:3b via Ollama, never writes SQL), TA/TPUT top-k, analytics indicators (ALIBI_PUC etc.), DOCS BM25 search with OCR-tolerant anchors, context-weighted retrieval, EROCS-style doc→row linking, doc conflicts, citation-validated grounded summary.

Graded rubric (Phase 3, Information Integration course, due 21 Nov 2026; ~5 working days left): R1 4–5 tables per DB with FK-PK [2]; R2 federated context discovery via FK-PK per DB and across DBs, highly related to the query [3]; R3 search correlated unstructured data with the context and integrate it [3]; R4 show prompt / generated code / modified code [2]. Professor wants: each DB returns only its best n; real innovation in scalability and fetching measured as rows/bytes moved; analytics.

## The user's complaint (verbatim gist)
"I entered a licence plate and it showed me a bunch of documents; they were not clear as to what the documents were. I couldn't open the documents to see them, and it was a whole lot of mess and jumble. The second Context Evaluation feature, I was not even sure what that was. Make the UI much simpler and more appealing — but mainly in terms of FEATURES. Novelty and innovation is not clean error-free implementation but unique features most people won't think about implementing."

Current pages: app/tabs/context_explorer.py (search box → verdict banner → KPI tiles → 5 tabs: Related records / Graph / Warning signs / Documents / Data moved), app/tabs/context_flow.py, app/tabs/context_widgets.py, app/tabs/context_evaluation.py (6 panels of charts: exactness vs oracle, data moved at scale, convergence, sensitivity, TA vs TPUT, retrieval ablation). Read them plus mediator/doc_retrieval.py, mediator/doc_linking.py, mediator/doc_conflicts.py, mediator/grounded_summary.py, mediator/analytics.py, mediator/context_discovery.py (skim), and a few rows of docs_documents.csv.

## What I need from you (return as your final message, ≤ 1,400 words, concrete)
1. Diagnosis: why the Explorer + Evaluation feel like a jumble to a first-time user (be specific to the code).
2. A redesigned information architecture for both pages — simple, story-first. E.g. Explorer as a "case file" with a timeline; Evaluation folded into something a viewer understands in 10 s (or merged into the Explorer as a live "what this query cost" receipt). Decide whether Context Evaluation should remain a separate page.
3. 5–7 NOVEL features, ranked by (rubric marks × wow ÷ effort), each with: one-line pitch, why it's novel for a federated-integration course project (what most teams won't think of), which rubric item it scores, the mechanism in 3–5 sentences (algorithm, not vibes), which existing modules it reuses, and an effort estimate in hours. Must stay defensible to a professor: explicit, explainable mechanisms; no data copied into a central store; local LLM allowed only via Ollama and never to write SQL. Ideas worth evaluating (accept, reject or improve): a unified cross-agency event TIMELINE reconstructed from rows + dates extracted from documents (with conflicts shown as forks); a "why this row?" provenance explanation showing the exact FK path, edge weights and bound that admitted it; a "what the warehouse would have cost" live receipt per query; a document viewer that opens the full text with highlighted entity spans linked to DB rows (click a span → jump to row); a "contradiction board" / claim matrix (each attribute × each source incl. text, with agreement); counterfactual "what-if a source is down" toggle showing which conclusions survive (robustness of verdict); query-by-example / "find vehicles with a similar context"; source-budget slider (n) showing live rows moved vs answer quality; an "investigator's question suggestions" panel generated from the discovered context (next best query); automatic entity-resolution of persons across agencies shown as a network ring. Feel free to propose better ones.
4. Recommended build order for ~2 days of implementation by parallel coding agents (split into 3–4 independent workstreams with file ownership, so they don't collide), and the list of files each touches.

Be decisive. Give recommendations, not surveys.
```


### Prompt 27 — 2026-11-14 13:36 UTC — student

```text
Retry
```


### Prompt 28 — 2026-11-14 14:03 UTC — student

```text
hi
```


### Prompt 29 — 2026-11-14 14:03 UTC — sub-agent task

**Task** (WS1 documents reader + claim matrix, model default):

```text
You are WS1 (Documents) in a four-agent build. First read the shared brief: docs/ai/PHASE3_CASEFILE_BRIEF.md — it holds the repo path, the seam, and all the rules. Follow them exactly.

You OWN (and may edit only): `mediator/doc_retrieval.py`, `sources/docs/wrapper.py` and `sources/docs/textindex.py` (only if `/search` does not already return body/issuer/issued_on), new `mediator/claim_matrix.py`, new `app/tabs/context_docs.py`, new `tests/test_claim_matrix.py`, new `tests/test_context_docs.py`, and you may add tests to `tests/test_doc_retrieval.py`.

The user's complaint you are fixing: "it showed me a bunch of documents, they were not really clear as to what the documents were, I couldn't open the documents to see them, a whole lot of mess and jumble." Concretely, for DL05CD9876 today, 3 documents are really about the car (scores 40–80) and 5 are about other cars (scores ~1.3, they matched only generic words like "silver Maruti Swift" or "TrustLine Insurers"). The cards show raw enum codes, a raw BM25 score, and "supports INS · INSURERS #4" chips.

Build:
1. **Retrieval fills the new fields** (`mediator/doc_retrieval.py`): `RetrievedDoc.body` (full text, so `Mention.start/end` index into it), `issuer`, `issued_on`, `why` (plain-language reasons, strongest first, e.g. "names this plate (written DL-05CD-9876)", "names the policy POL-000562", "names the owner Priya Kumar Singh", "same make and colour"; built from the mentions' attr, kind and weight), and `relevant`. Relevance gate: a document is relevant when it carries at least one anchor mention (plate, chassis, FIR, policy or claim number) or links at least one context row other than a pure lookup table. Pick the rule and defend it in a one-line comment. Keep ranking otherwise unchanged. The docs eval (`scripts/evaluate_docs.py`, `mediator/doc_eval.py`) must still run. Check that nDCG does not drop: run `.venv/Scripts/python.exe scripts/evaluate_docs.py` before and after and report both.
2. **Claim matrix** (`mediator/claim_matrix.py`, pure, no network): input a ContextResult plus a DocResult; output rows = facts about the vehicle (colour, make, model, owner name, policy valid-till, theft date, recovery/case status, loss date where present) and columns = each agency that states it plus each relevant document that states it. Each cell holds the value as stated, normalised for comparison (case, grey/gray, date formats via `doc_conflicts.to_date` and the graph time spec), and a status: agree, conflict or silent. Each row gets a trust-weighted majority value; reuse the trust weights from the existing registry or catalog if one exists, otherwise define them in one dict with a comment. Text values come from `Mention`s and `Conflict`s (`doc_conflicts.detect` already compares colour, make, model, dates and recovery). Keep it metadata-driven where the existing doc modules are; follow their precedent where they name columns.
3. **UI** (`app/tabs/context_docs.py`):
   - `render_documents(res)`: relevant documents only, conflicts first. A one-line caption gives the count of hidden documents, with an expander to see them.
   - Each card shows: the document type in words ("Surveyor report", "Police FIR narrative", "RTO notice", "Accident report", "News item", "Witness statement"), the title, the issuer, the issued date, the "why" reasons as small plain chips, a red badge per contradiction with a one-line plain explanation, a 2-line snippet, and an **Open** button.
   - Open uses `st.dialog` to show a reader. On the left, the full body with mentions highlighted, each highlight numbered `[1]`, `[2]`… and coloured by the source of the row it supports; contradicted spans are red with the database value shown. On the right, the numbered list of the database records this document supports or contradicts, described in plain words (e.g. "Insurance policy POL-000562 — TrustLine, valid till 12/07/2026"), and the document's metadata.
   - `render_claims(res)`: the matrix as a compact HTML table (green tick / red cross / grey dash and the value), with a one-line takeaway above it, e.g. "2 facts disputed: colour (camera and one news item say silver; registration says white)".

Test against the live sources with the demo plates (MH12EF0248 must show D00260's recovered-vs-OPEN contradiction; DL05CD9876 must show 3 relevant documents and hide the 5 others). Keep each module under ~200 lines. Do not commit. Write your final report as the brief asks.
```


### Prompt 30 — 2026-11-14 14:03 UTC — sub-agent task

**Task** (WS2 cross-agency timeline, model default):

```text
You are WS2 (Timeline) in a four-agent build. First read the shared brief: docs/ai/PHASE3_CASEFILE_BRIEF.md — it holds the repo path, the seam, and all the rules. Follow them exactly.

You OWN (and may edit only): new `mediator/timeline.py`, new `app/tabs/context_timeline.py`, new `tests/test_timeline.py`. Import `mediator/doc_conflicts.py`, `mediator/analytics.py`, `mediator/doc_retrieval.py` (`load_graph`) and `app/tabs/context_widgets.py` without editing them.

Feature: a **cross-agency timeline with forks**, the one artefact no single agency holds. Five databases use four date encodings, and documents add free-text dates.

Mechanism (`mediator/timeline.py`, pure, no network):
- **Row events:** for every `ContextRow` in `res["context"].rows`, read the table's `time` spec (`col`, `fmt`) from `data/context_graph.json` (inspect it: `sources -> SID -> tables -> TABLE -> time`). Parse it with `doc_conflicts.to_date` and the format, and emit an Event: date, source_id, table, pk, a plain label such as "Policy POL-000562 starts", "Theft reported (FIR …)", "PUC test", "Camera sighting at <camera/location>", "Ownership transfer", "Claim filed". Where a table has a second meaningful date (e.g. a policy's end, a recovery date), check whether the graph spec or the row carries it and emit an event for it too. If you must name a column, keep the mapping in one small dict with a comment saying why the graph does not carry it. If adding a key to the graph would be cleaner, propose it in your report rather than editing the graph.
- **Text events:** for each relevant `RetrievedDoc` in `res["docs"].docs` (use `.body` if non-empty, else `.snippet`; WS1 is filling `body` in parallel, so support both), run `doc_conflicts.text_dates()` and `date_class()`. That gives the attribute class, the target (source, table) and a tolerance. Emit a text event tied to the document.
- **Forks:** if a text event's class matches a linked row's date in the same (source, table) and |Δdays| exceeds the tolerance, it is a fork: "text says X / database says Y".
- **Intervals:** stolen intervals from `analytics.stolen_intervals(rows, now)` (read its signature: it takes the analytics row-dict shape `D.Rows`; find how `indicators_from_rows` builds that from a ContextResult and reuse that path) and policy validity intervals where derivable. Any event inside a stolen interval from PUC, CAM or challans is flagged `inside_stolen=True`; that is the ALIBI made visible.
- Output: a `Timeline` dataclass with events, forks, intervals and a few counts. Deterministic and sorted.

UI (`app/tabs/context_timeline.py`, `render_timeline(res)`):
- A plotly timeline with one lane per agency (plain names, source colours from `context_widgets`). Event markers carry hover text with the plain label and date.
- Stolen interval as a soft red band labelled "reported stolen"; policy cover as a soft band in the insurance lane.
- Inside-stolen events in red with a ring. Forks drawn as two linked markers with a dashed connector and a "text says / database says" hover.
- A one-sentence takeaway above the chart, computed from the data, e.g. "A PUC test on 14 Aug 2026 happened while the car was officially stolen (FIR open since 2 Aug)." or "12 events from 4 agencies, 2026-01 to 2026-10; no contradictions in time."
- Below the chart, a compact chronological list (date, agency chip, label, and a "from document D00260" note for text events), collapsed by default if longer than 8 items.
- Light theme tokens; works at 1440 px and narrow widths; no modebar.

Tests: pure-function tests with hand-made ContextRows covering all four date encodings, a fork, an inside-stolen event, and empty input. Also try live: `run_query("MH12EF0248")` should show the PUC test inside the stolen band and the recovered-vs-OPEN document. Do not commit. Write your final report as the brief asks.
```


### Prompt 31 — 2026-11-14 14:04 UTC — sub-agent task

**Task** (WS3 why-row, leads, robustness, receipt, model default):

```text
You are WS3 (Proof and cost) in a four-agent build. First read the shared brief: docs/ai/PHASE3_CASEFILE_BRIEF.md — it holds the repo path, the seam, and all the rules. Follow them exactly.

You OWN (and may edit only): `mediator/context_discovery.py`, `mediator/context_scoring.py`, `mediator/context_bridges.py`, `sources/context_sql.py`, `sources/context_endpoint.py` (only if needed to carry affinity or edge weights; keep SQL identical across SQLite, PostgreSQL and MySQL), new `mediator/context_explain.py`, new `mediator/robustness.py`, new `app/tabs/context_receipt.py`, new tests `tests/test_context_explain.py`, `tests/test_robustness.py`, `tests/test_context_receipt.py`. Existing tests `tests/test_context_discovery.py` and `tests/test_context_endpoint.py` must stay green; run them.

Features:
1. **"Why is this row here?" admission proof.**
   - Fill `ContextRow.affinity` and `ContextRow.edge_weights` during discovery. `/context` with `explain=True` may already return edge weights and affinity; read `sources/context_endpoint.py` and `context_sql.py`. Prefer always carrying `affinity` if it is already a column in the result, so no extra round trip.
   - `context_explain.explain_row(row, ctx)` returns a plain-language proof: the path as readable steps ("Vehicle registration → Ownership history (weight 0.62) → Owner (0.81)"), affinity = product of weights × bridge confidence, relevance = score ÷ affinity, and "admitted because score 0.30 ≥ the cut-off 0.24 (10th best)". Also the per-source guarantee: "nothing REG held back could score above <SourceStats.bound>".
2. **Leads not followed.**
   - In `context_discovery.py`, before pruned frontier states are discarded (around `run.frontier = []`, and at the stop), record them into `ContextResult.pruned_leads` as dicts: target, attr, value, bound, from, round. Use plain types, de-duplicate, and keep the top 20 by bound.
   - This must not change which rows are returned. Verify that `tests/test_context_discovery.py` (including the oracle test) stays green.
   - `render_why(res)`:
     - An expander-style section "How each record was found". Pick a record from a selectbox (plain labels) to see its proof.
     - Then "Leads not followed (the bound proved they could not change the answer)": the top 8 leads in plain words, e.g. "Camera captures of a plate that looks like DL05CD9B76 — best possible score 0.08, below the cut-off 0.24".
     - Each lead has a **Pursue** button. It runs `discover(parsed, seeds={...}, ...)` from that lead (read discover's `seeds` parameter) and shows the extra rows inline with a note: "followed anyway: N more records, none scored above the cut-off" (or which did). This demonstrates the bound was right.
3. **Verdict robustness, source knock-out** (`mediator/robustness.py`, pure, no network, nothing stored):
   - For each source S present, recompute `analytics.indicators_from_rows(plate, rows_without_S)` on the rows already in memory. Read how `indicators_for` builds rows from a ContextResult and reuse it; skip any network-dependent part such as `_ring`'s fetch, which needs `use_fetch=False` or similar.
   - Report, per indicator, the minimal set of sources it needs, and per source, which indicators fall without it.
   - `render_robustness(res)`: one compact line for the header, e.g. "Holds without CAM, DOCS · Depends on THEFT and PUC". If there are no indicators: "Clear verdict uses all 6 agencies; losing any one would make it a lower bound". Hover or an expander gives detail.
4. **Live receipt** (`render_receipt(res)`). This replaces the old KPI tiles and the "Data moved" tab:
   - An itemised bill per agency: rows, KB, round trips, and "held back: nothing above <bound>".
   - A total line against "a copy-everything warehouse would move N rows" (`ctx.baseline_rows`, already counted live with `count_only`; see `app/tabs/context_flow.moved`).
   - Follow-ups pruned vs considered.
   - A small chart of the stopping rule per round from `ctx.trace`: the n-th best score so far against the best any remaining follow-up could reach. Discovery stops when the first line is above the second.
   - Plain headline sentence: "This answer cost 115 rows (50 KB) from 6 agencies; copying everything would have moved 17,157 (99% less)."

Keep each module under ~200 lines. Test pure logic with hand-made ContextResults; smoke-test the renderers with AppTest. Try live with DL05CD9876 and MH12EF0248 (sources are up). Do not commit. Write your final report as the brief asks.
```


### Prompt 32 — 2026-11-14 14:04 UTC — sub-agent task

**Task** (WS4 case-file shell + Proof page, model default):

```text
You are WS4 (Shell) in a four-agent build. First read the shared brief: docs/ai/PHASE3_CASEFILE_BRIEF.md — it holds the repo path, the seam, and all the rules. Follow them exactly.

You OWN (and may edit only): `app/tabs/context_explorer.py`, `app/tabs/context_flow.py`, `app/tabs/context_widgets.py`, `app/tabs/context_evaluation.py`, `app/tabs/context_charts.py`, `app/app.py` and `app/theme.py` (nav label only), `tests/test_context_pages.py`, `tests/test_context_eval.py` (only if the page change requires it). Other agents are writing `app/tabs/context_docs.py` (WS1), `context_timeline.py` (WS2) and `context_receipt.py` (WS3) in parallel. They may not exist yet. Import them defensively: if a module or function is missing, skip that block silently and log it to `res["missing"]`. Do not create those files.

Task A — **Explorer → case file** (`context_explorer.py`, plus helpers in `context_widgets.py`):
- Remove the five tabs; make it one calm scroll in the order given in the brief. Keep the search box, the examples pills and free-text top-k behaviour, including Open and Back.
- **Header:**
  - The plate, large.
  - An identity line from the REG registration row in the context: make, model, colour, owner. Find the columns via `data/context_graph.json` or the row itself, as `doc_conflicts._vehicle_conflicts` does.
  - The existing verdict banner, with plain wording.
  - Then `context_receipt.render_robustness(res)`.
  - One subtle line "Answered in 0.6 s from 6 agencies · 115 rows moved instead of 17,157". It must read as a fact, not a KPI wall.
- **"What happened":** the grounded summary (`res["summary"]`, rendered with the existing `w.summary_html`), restyled so the citation chips are small and quiet. Citation chips use plain names ("Insurance policy 562"), not `INS:POLICY_RECORDS#562`, if you can map them cheaply.
- **Timeline:** `context_timeline.render_timeline(res)`.
- **Warning signs:** the existing indicator cards. Make sure each card's wording is plain.
- **Documents:** `context_docs.render_documents(res)`, then `context_docs.render_claims(res)`.
- **"Related records":** the records grouped by agency with plain labels. Change `w.rows_table` or add a new helper so each record reads like "Insurance policy POL-000562 · TrustLine · third party · valid till 12/07/2026" instead of `TABLE #pk` plus raw columns. Map table names to plain nouns in one dict, and show 3–4 meaningful values, skipping ids. The FK chain moves into a small muted "found via" line. Use one expander per agency, collapsed except the first.
- **"How it was found":** `context_receipt.render_why(res)` and the existing graph (`w.context_dot`), inside one section.
- **Receipt:** `context_receipt.render_receipt(res)` at the bottom. Remove the old KPI tiles and the "Data moved" tab: the receipt replaces them.
- Section headings are short questions or nouns ("What happened", "Timeline", "Warning signs", "Documents", "Who says what", "Related records", "How it was found", "What this answer cost"). Add a sticky or top mini table of contents (anchor links) only if it stays clean.

Task B — **Context Evaluation → "Proof"** (`context_evaluation.py`, `context_charts.py`, plus the nav label in `app/app.py`/`theme.py`; update the nav tests):
- Three claim cards, each with one plain sentence, one big number, one chart, and one line "how we measured it":
  1. "Same answer as copying everything to one place?" Overlap of our top rows with the central oracle's: now 100% (the quick run gives bounded 1.0, one-round top-n 0.74, mediator walking FKs 0.97). Show it per approach.
  2. "How much less data crosses the network?" Rows moved as the data grows ×1/×10/×100, log scale, ours vs copy-everything. Read the scales actually present in `data/context_eval.json`.
  3. "Does the context find better documents?" Retrieval with the question only vs + context vs + exact identifiers. Use "ranking quality (1 = perfect)" instead of "nDCG", with nDCG named in the "how measured" line.
- Everything else (stability τ, sensitivity, convergence, TA vs TPUT, judged relevance) goes in one "More measurements" expander, each with a plain one-line title.
- Top line: what this page proves, in one sentence, plus when it was measured and a quiet Re-run button.
- Rename the nav entry to "Proof" (keep the page reachable; update `tests/test_context_pages.py` and any nav-count tests that assert labels).

Constraints: keep `context_explorer.py` under ~250 lines by moving markup helpers into `context_widgets.py`. Keep the existing `cx_`/`ce_` key prefixes. Test with AppTest (`tests/test_context_pages.py`) and update its assertions to the new structure. Do not run the full suite. Do not commit. Write your final report as the brief asks, including the exact order of sections in the final page.
```
