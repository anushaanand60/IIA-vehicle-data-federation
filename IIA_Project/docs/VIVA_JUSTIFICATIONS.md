# Viva Justifications — Project A, Uninsured Vehicle Identification

Every design choice, justified with the course's own concepts and wording.

---

## 1. How to use this document

1. Read section 2 first. It is the one-page map from each design choice to a slide page and a file.
2. Before the viva, rehearse the "Likely question → answer" pairs in section 3 and the hard questions in section 5 out loud.
3. Citations such as **(Week 3, p. 18)** point to the `=== PAGE n ===` markers in the slide text. "Not in the provided slides" means the answer rests on general information-integration knowledge, so say it that way.

**60-second pitch.** Four agencies (the transport office, an insurer, the police and a road-camera network) keep different facts about the same vehicles. Their databases were designed in isolation: they share no keys, spell the plate four ways, store dates four ways, and run on three DBMS engines. We built **mediation-based, virtual integration**: "Sources → Wrappers → Mediator → Federated Query" (Week 2, p. 13). Each agency publishes a **wrapper** API, not its database. A hybrid **schema matcher** (linguistic, type and instance signals) discovers correspondences. A human validates them, and they are stored as **GAV mappings** in a metadata registry. For each plate, the mediator **selects** only the sources that cover the question, **decomposes** it into one SQL statement per source, and runs those statements **in parallel at query time**. It then **integrates** the answers on a normalised plate key, with provenance and trust, detects conflicts, and makes a rule-based, explainable decision. When a needed source is unreachable, it refuses to decide. Nothing is copied: "Keep data at the source; integrate at query time" (Week 2, p. 19).

---

## 2. Concept map

| Design choice | IIA concept | Slide | Where in our system |
|---|---|---|---|
| No copy of source data; read live per query | Mediation / virtual integration | Week 2, p. 13, p. 19; DWH, p. 5 | `mediator/executor.py` `_call_all`, `execute_federated_plan` |
| Not a warehouse | Materialisation, ETL, synchronisation problem | DWH, p. 4, p. 6; Week 2, p. 16 | `mediator/catalog.py` stores metadata and audit only |
| Architecture chosen by requirements | Four design questions; selection matrix | Week 2, p. 14, p. 35 | Section 3(b) |
| Ministry report log is stored | Hybrid: materialise what must be stable | Week 2, p. 23 | `REPORT_LOG` in `mediator/catalog.py` `init_meta_db` |
| Global attribute = query over sources | Global-As-View (GAV) | Week 3, p. 18, p. 24; DWH, p. 1 | `MAPPING_REGISTRY`; `mediator/decomposer.py` `decompose_query` |
| `VEHICLE_PROFILE` vocabulary | Mediated / global schema | Week 2, p. 5; Week 3, p. 24 | `mediator/schema.py` `GLOBAL_SCHEMA_ATTRIBUTES` |
| `insurance_status`, `stolen_status` computed | Derived attributes | Week 3, p. 8, p. 19 | `mediator/integrator.py` `integrate_results` step 4 |
| One FastAPI adapter per source | Wrapper | DWH, p. 8; Week 3, p. 5 | `sources/wrapper_template.py` `create_app` |
| Four plate spellings, four date encodings | Naming and representation conflicts | Week 3, p. 8 | `mediator/transforms.py`; `sources/*/schema.sql` |
| 0.40·N + 0.20·C + 0.40·I | Hybrid multi-signal matching | Week 3, p. 11, p. 32 | `mediator/matcher.py` `match_source_schema` |
| Human accepts proposed matches | Semi-automatic matching plus expert validation | Week 3, p. 9, p. 39 | `mediator/catalog.py` `save_mapping` (`validated_by`) |
| `transform_fn`, `join_path`, `aggregate` columns | Correspondences made operational (Clio) | Week 3, p. 6, p. 7; Clio, p. 2–3 | `MAPPING_REGISTRY`; `scripts/seed_mappings.py` |
| Ask only sources that cover the question | Data source selection by coverage | Week 3, p. 4 | `mediator/planner.py` `plan_query` |
| One SQL statement per source | Query decomposition, wrapper invocation | Week 2, p. 20, p. 22 | `mediator/decomposer.py` `decompose_query` |
| Plate key normalised before joining | Entity resolution / record linkage | Week 2, p. 11, p. 38 | `mediator/transforms.py` `norm_plate` |
| `{source, authority, trust, fetched_at}` on every value | Provenance, reliability | Week 2, p. 11, p. 30 | `integrate_results` step 3 |
| REG beats CAM, conflict kept | "When two sources disagree…" | Week 2, p. 5 | `integrate_results` step 5 |
| `UNDETERMINED` when a source is down | Not knowing the answer; availability dependency | Week 2, p. 8, p. 19 | `mediator/decide.py` `evaluate_vehicle_decision` rule 1 |
| CAM trust 0.60, OCR folding | Noisy, untrustworthy sources | Week 3, p. 5; Week 2, p. 38 | `DEFAULT_SOURCES` in `mediator/catalog.py` |
| PUC added through metadata | Extensibility; GAV vs LAV modularity | Week 3, p. 18, p. 24 | `catalog.register_source`; `tests/test_planner_metadata.py` |
| Challan Guard | STP with routing to human review; decision value | Week 2, p. 24, p. 15 | `mediator/challan_guard.py` `_verification` |
| Matcher precision/recall, decision oracle | Labelled correspondences; accuracy | Week 3, p. 32; Week 2, p. 15 | `mediator/matcher_eval.py`; `mediator/ground_truth.py` |

---

## 3. Justifications

### (a) Virtual, federated mediator instead of a data warehouse

**Choice.** Every query reads live from the four source databases. No source row is copied into a central store.

**Concept.** Mediation-based integration is "Virtual integration via federated queries", "Best for real-time access and autonomous source systems" (Week 2, p. 13). Its design principle is "Keep data at the source; integrate at query time", and "Virtual integration favours freshness and source autonomy over predictable performance" (Week 2, p. 19). In virtual integration the "Query is evaluated over current sources". In a warehouse the "Integrated result is stored" and "Freshness becomes an explicit design variable" (DWH, p. 5). A warehouse is "loaded and periodically updated via Extract/Transform/Load (ETL) tools" (DWH, p. 6). Materialisation "creates a synchronization problem" (DWH, p. 4).

**Why it fits our problem.** A roadside decision is about the policy as it stands right now. If a driver renewed at 10:05, a warehouse refreshed at midnight would still call them uninsured at 10:06 and fine them wrongly. The four agencies are separate authorities. An insurer or the police would publish an API, but would not hand over a nightly copy of their database. A fine must also be traceable to which agency said what, and when.

**How we implement it.**
- `mediator/executor.py` `_call_all` posts each source's SQL to its wrapper's `/query` on every query, in parallel.
- `mediator/catalog.py` `init_meta_db` creates only metadata tables (`SOURCE_CATALOG`, `MAPPING_REGISTRY`) plus logs. The `QUERY_LOG` comment says it holds "decisions and traces only, never the raw rows".
- `mediator/core.py` `run_global_query` runs plan → execute → integrate on every call. There is no result cache.
- The one deliberate materialisation is `REPORT_LOG`: an audit trail of decisions already filed. This follows the hybrid principle "Materialize what is common and expensive; virtualize what is volatile" (Week 2, p. 23), adapted as "materialise what must not change later".

**Trade-off we accept.** These are the slide's own costs: "Runtime latency", "Source availability dependency", "Complex query planning" (Week 2, p. 19). A warehouse gives "Fast and predictable analytical queries" and "Source outages need not block queries" (DWH, p. 5). We give up both.

**Likely question → answer.**
- *Q: Why not a warehouse with frequent refresh?* A: Any refresh interval is a window in which we fine people on stale data. Refresh also needs change capture from four autonomous agencies. The DWH deck lists exactly that as the hard part: "Distributed sources may be unavailable when maintenance needs them" (DWH, p. 32).
- *Q: Do you store anything at all?* A: Metadata (catalog and mappings) and audit logs of our own decisions. Challan Guard also keeps an evidence snapshot per case. A dispute never reads that snapshot as truth: it queries the sources live again.
- *Q: What did you give up?* A: Predictable latency and independence from source outages. We bound latency with timeouts, and we turn outages into an explicit `UNDETERMINED`.

### (b) Choosing the architecture among the Week 2 approaches

**Choice.** Mediation for the core lookup. One materialised audit log (the hybrid element). A straight-through-processing style workflow for fines (Challan Guard).

**Concept.** Week 2 lists six approaches: materialised ETL, mediation, hybrid semantic layer plus materialised views, straight-through processing, AI/ML-driven, and context-oriented (Week 2, p. 13). To compare them, ask four questions: "Where does integration happen? When does data become materialized? Where is semantic/identity reasoning performed? What happens when sources change or fail?" (Week 2, p. 14). The selection criteria are "Freshness", "Source autonomy", "Provenance needs" and "Tolerance for duplicated storage". The rule is "Choose architecture based on application requirements, not technology fashion" (Week 2, p. 35). The deck also says "Do not accept 'one architecture for everything.'" (Week 2, p. 36). Peer-to-peer integration and tightly coupled federated DBMSs are not in the provided slides; that part is general II knowledge.

**Why it fits our problem.** Our answers to the four questions:
- **Where does integration happen?** In the mediator process.
- **When is data materialised?** Never for source data; only for our own reports.
- **Where does identity reasoning happen?** In plate normalisation and the integrator.
- **What happens on failure?** A status per source, and a refusal to decide.

On the criteria, we score high on freshness, autonomy and provenance, and we have low tolerance for duplicating citizens' insurance and police records. That profile is exactly the "Mediation → real-time federation" row (Week 2, p. 35).

**How we implement it.**
- Mediation: `mediator/planner.py` → `mediator/executor.py` → `mediator/integrator.py` → `mediator/decide.py`, orchestrated by `mediator/core.py` `run_global_query`.
- Materialised audit: `REPORT_LOG`, `QUERY_LOG`, `CHALLAN_CASES`, `CHALLAN_EVENTS` in `mediator/catalog.py`.
- STP-like path: `mediator/challan_guard.py` `verify` / `verify_all`, with `HOLD` as the route to a human.

**Trade-off we accept.** Two storage paths (live sources and our audit tables) are more complex than one. We also chose not to use the AI/ML-driven approach, even though it is "Best-suited" for schema matching (Week 2, p. 27), because Part A asks for explainable, deterministic mechanisms.

**Likely question → answer.**
- *Q: Which of the six approaches is this?* A: Mediation for lookups, with a small hybrid element: we store decisions, never source data. The fine workflow borrows the STP idea of automating the common path and routing uncertainty to a person.
- *Q: Why not context-oriented integration?* A: That fits entity discovery and knowledge graphs, where "Context changes" (Week 2, p. 30). Our entity key, the plate, is given in the question, so we do not need to discover entities.

### (c) GAV rather than LAV

**Choice.** Each global attribute is defined as a query over source columns: source table, column, transform, join and aggregate.

**Concept.** GAV means the "Global schema defined in terms of sources": "Query reformulation easier", but "Any change in sources schema, needs change in global schema", and "Global relations cannot model any information not present in at least one source". LAV means "Sources defined in terms of global schema": "High modularity and extensibility", "Query reformulation complex" (Week 3, p. 18). GAV is "Best when Few, stable, data sources well-known to the mediator"; its reformulation "reduces to view unfolding (polynomial)". LAV reformulation "Involves answering queries only using views" (Week 3, p. 24). The DWH deck's worked example writes GAV as `Create View CarRentalAvailability … AS Select Reg# AS Vehicle# … UNION …` (DWH, p. 1), with LAV as the reverse (DWH, p. 2).

**Why it fits our problem.** We have five known government-style sources that the mediator team understands well. That is the "corporate integration" case, not a web of unknown sources. View unfolding lets us show the exact SQL behind every value on the Plan Trace tab, which matters when a value leads to a fine.

**How we implement it.**
- `MAPPING_REGISTRY` rows `(source_id, source_table, source_attr, global_attr, transform_fn, aggregate, join_path)`, created in `mediator/catalog.py` `init_meta_db`. There are 38 rules in total.
- `mediator/decomposer.py` `decompose_query` unfolds those rows into one `SELECT` per source.
- The formal rules are written out in `docs/report.md` §4. For example: Insurance(p, …) from `POLICY_RECORDS ⋈ INSURERS WHERE p = norm_plate(vehicle_reg)`, keep max expiry.

**Trade-off we accept.** GAV is "Not modular – Addition of new sources changes the mediated schema" (Week 3, p. 24). We saw that ourselves: adding PUC needed `puc_expiry` declared in `mediator/schema.py`. There is no answering-queries-using-views and no query containment. We do not need them because we never rewrite through source views.

**Likely question → answer.**
- *Q: Why not LAV, since you claim extensibility?* A: LAV pays for modularity with hard reformulation, and it is best with "Many, relatively unknown data sources" (Week 3, p. 24). We have five known sources. Keeping GAV definitions as registry rows rather than code gives back most of the extensibility.
- *Q: Show me a GAV view in your system.* A: `vehicle_make` is `REG.VEHICLE_REGISTRATION.make` through `fix_make`. `owner_name` is `OWNERS.full_name` reached by `VEHICLE_REGISTRATION.owner_id=OWNERS.owner_id`. The decomposer unfolds exactly those rows into SQL.

### (d) Global schema design: VEHICLE_PROFILE and derived attributes

**Choice.** A flat, agency-neutral `VEHICLE_PROFILE` keyed on a canonical plate. Two statuses are derived, never stored.

**Concept.** Integration ties sources together "under a common schema" (Week 2, p. 5). Week 3 names "Derived attributes: age vs date_of_birth" as a matching difficulty (Week 3, p. 8). Its mapping formalism includes "Derived: b = a1 + a2" (Week 3, p. 19). The DWH GAV example derives `DailyPrice*6.5 as WeaklyPrice` inside the view (DWH, p. 1). Building a mediated schema by clustering many schemas ("collective schema matching", Week 3, p. 17) is the alternative we did not use.

**Why it fits our problem.** The question an officer asks ("is this vehicle insured?") is not a column in any source. The insurer stores `policy_until` as text. Whether the vehicle is insured depends on that date relative to a reference date, and on whether a row exists at all. Keeping the derivation in the mediator means one definition of "insured" for every agency.

**How we implement it.**
- `mediator/schema.py` `GLOBAL_SCHEMA_ATTRIBUTES`: the 19 agreed names plus extensions (`camera_lat`, `camera_lon`, `puc_expiry`). `insurance_status` has `"derived_from": ["insurance_expiry"]`; `stolen_status` is derived from `case_status` and `last_incident_date`.
- `mediator/integrator.py` `integrate_results` step 4 computes `insurance_status` as `VALID`, `EXPIRED`, `NONE` (INS answered, no rows) or `UNKNOWN` (INS failed). It is `None` when INS was not asked. `stolen_status` follows the same pattern.
- `mediator/planner.py` `plan_query` plans a derived attribute by planning its inputs. `mediator/contract.py` `Registry.resolve` does the same for the executor path.

**Trade-off we accept.** The profile is flat and keeps only the latest policy and latest sighting, not history. The reference date is pinned (`REFERENCE_TODAY = date(2026, 9, 4)` in `mediator/decide.py`) so the ground truth is reproducible. In production it would be today's date. Challan Guard already compares against the sighting date.

**Likely question → answer.**
- *Q: Why is `insurance_status` not a column in INS?* A: Real insurers store dates, not verdicts. The status depends on today's date and on the difference between "no policy" and "insurer unreachable". That reasoning belongs to the mediator.
- *Q: Why four values instead of true/false?* A: `NONE` means the insurer answered with no policy. `UNKNOWN` means we could not ask. Merging them would turn an outage into an accusation.
- *Q: A pinned date — doesn't that contradict freshness?* A: The data is always fresh; only the evaluation clock is pinned, so 621 expected decisions stay reproducible. Changing it to `date.today()` is a single constant.

### (e) Wrappers, source autonomy and DBMS heterogeneity

**Choice.** One FastAPI wrapper per source, identical in contract. The database listens on localhost only.

**Concept.** "Wrappers are components of data integration systems that communicate with the data sources": they send queries down and convert replies "to a format that can be manipulated by query processor". "Complexity of wrapper depends on nature of data source" (DWH, p. 8). Data acquisition means you must "Build connectors, wrappers,…" (Week 3, p. 5). A key challenge is "Uniform (or source specific) query access to data sources" (Week 2, p. 38). The central trade-off is "Coupling vs autonomy" (Week 2, p. 14).

**Why it fits our problem.** REG and CAM run PostgreSQL, INS runs MySQL, and THEFT and PUC run SQLite, each on the owning laptop. The mediator should not hold four drivers and four sets of credentials. The agency should decide which tables are visible. The wrapper is the agency's published API.

**How we implement it.**
- `sources/wrapper_template.py` `create_app` serves `GET /health`, `GET /schema` (columns, types, PK/FK, up to 20 sample values) and `POST /query`.
- `guard_sql` enforces a single `SELECT`, no `;`, no comments, a forbidden-keyword list, whitelisted tables only, and `LIMIT 200` appended.
- `_make_engine` opens a read-only session per dialect: `PRAGMA query_only = ON` (SQLite), `default_transaction_read_only=on` plus `statement_timeout` (PostgreSQL), `SET SESSION TRANSACTION READ ONLY` plus `MAX_EXECUTION_TIME` (MySQL). The statement timeout is 3 s.
- `sources/<id>/wrapper.py` calls `from_env`, which reads `<ID>_DB_URL` and `<ID>_TABLES`.

**Trade-off we accept.** Our wrappers are thin. They hide the driver, connection, engine and access control, but the mediator still sends SQL written in the source's table and column names. Pushed-down SQL therefore has to be portable across dialects: MySQL reads `||` as OR, which is why the decomposer avoids it. The guard is textual, not a parser; the read-only session is the real boundary. There is no authentication on the wrapper (see `docs/NETWORK.md` §7).

**Likely question → answer.**
- *Q: If the mediator sends SQL, what does the wrapper actually hide?* A: The engine, driver, credentials, dialect settings and the timeout, and it enforces the agency's whitelist. The table vocabulary comes from the registry, which the matcher built from the wrapper's own `/schema`.
- *Q: Can the mediator run `DROP TABLE` through `/query`?* A: No. The guard rejects it with a 400, and even if the guard had a bug, the database session itself is read-only.

### (f) Heterogeneity types we handle

**Choice.** We deliberately built the sources with the conflict types the Week 3 slide lists, and handle each one in named transforms or registry metadata.

**Concept.** "Why schema matching is hard" lists Synonyms (Naming Conflict), Acronyms, Granularity, Structural mismatch, Derived attributes, Temporal mismatch, Semantic Conflicts and "Representation Conflicts: Date = DD/MM/YYYY vs YYYY-MM-DD" (Week 3, p. 8). Data problems include "Synonyms, conflicting data", "variations of values", "wrong value formats", "misspelt names" (Week 3, p. 6). "Status: source-specific meaning" (Week 3, p. 34). The project brief says: "assume you have at least one column with same value (the column/attribute name may be different)" (Week 2, p. 2).

**Why it fits our problem.** These conflicts are what real agencies look like: an RTO, an insurer and a police station never agreed on a plate format. The matcher and the mappings only earn marks if the heterogeneity is real.

**How we implement it.**

| Our heterogeneity | Slide term | Handled by |
|---|---|---|
| `registration_no` / `vehicle_reg` / `vehicle_number` / `plate_id` / `regn_number` | Synonyms (naming conflict), Week 3, p. 8 | `mediator/matcher.py` `THESAURUS` + name score |
| `DL01AB1234` / `DL-01-AB-1234` / `dl 01 ab 1234` / OCR `DL05GHO258` | Variations of values, wrong value formats, Week 3, p. 6 | `transforms.norm_plate`; SQL `UPPER(REPLACE(REPLACE(...)))` in `decompose_query` |
| ISO `DATE` (REG), `DD/MM/YYYY` text (INS), epoch integer (THEFT), ISO text timestamp (CAM) | Representation conflicts, Week 3, p. 8 | `parse_ddmmyyyy`, `epoch_to_date` in `mediator/transforms.py` |
| `ACTIVE/SUSPENDED/CANCELLED`, `Y/N`, `1/0` | "Status: source-specific meaning", Week 3, p. 34 | `status_map`; `yn_to_bool` applied in `integrate_results` |
| Owner normalised into `OWNERS`, flat elsewhere | Granularity / structural mismatch, Week 3, p. 8 | `join_path` `VEHICLE_REGISTRATION.owner_id=OWNERS.owner_id` |
| Status not stored anywhere | Derived attributes, Week 3, p. 8 | `integrate_results` step 4 |
| Many policies, captures, incidents per plate | Temporal mismatch: current vs historical, Week 3, p. 8 | `aggregate = latest_by:<col>`; `_pick_latest_row` |
| `Hyundia` misspelling | Misspelt names, Week 3, p. 6 | `fix_make` |
| PostgreSQL / MySQL / SQLite | Source-specific query access, Week 2, p. 38 | wrappers, `_make_engine` |
| Official records vs camera guesses | Not a slide term; closest is "untrustworthy, noisy" (Week 3, p. 5) and "Source ownership and provenance" (Week 3, p. 8) | `authority`, `trust_score` in `SOURCE_CATALOG` |

**Trade-off we accept.** The transforms are hand-written, pure functions. The matcher does not synthesise them; a person picks them. `tinyint_to_bool` exists in `mediator/transforms.py`, but no mapping uses it: `is_active` is carried through as a pass-through column and does not drive any decision. The decisions trust dates, not the insurer's own active flag.

**Likely question → answer.**
- *Q: Which heterogeneity was hardest?* A: The join key itself. Four spellings, plus OCR noise on the camera side, mean the join depends on entity resolution. A plain equi-join would find nothing.
- *Q: Where is a semantic conflict, not just a format one?* A: `policy_until` versus "is the vehicle insured": the global attribute is a derived judgement over the source value. The camera's `observed_make` versus REG's `make` is the other one: same concept, different evidential weight.

### (g) Schema matching approach and its evaluation

**Choice.** A hybrid matcher: Score = 0.40·N (name) + 0.20·C (type/constraint) + 0.40·I (instance), threshold θ = 0.55, top-1 global attribute per source column. A person validates the output.

**Concept.** A matcher's output is a set "M = {(ai, bj, sim(ai, bj))}" and you "Match if sim(a,b)≥θ" (Week 3, p. 7). The inputs are synonyms, "Data instances", acronyms, schema structure, attribute names, and "Constraints: data type, keys, nullability" (Week 3, p. 10). Similarity functions come in five families: "Lexical (Linguistic)", "Semantic", "Structural", "Instance-based" and "Hybrid: sim(a,b) = α·simlex + β·simsem + γ·siminst" (Week 3, p. 11). Linguistic matching uses "Tokenization of names", "Expansion of acronyms" and a "Thesaurus" (Week 3, p. 12). The schema-based hybrid algorithm uses "only schema (no instances)" (Week 3, p. 16). The multi-signal ranker is "Score = wN·N + wI·I + wS·S + wC·C + wO·O", where "Weights may be hand-designed or learned" (Week 3, p. 32). "A strong matcher uses names + structure + instances + constraints + context" (Week 3, p. 8). In practice matching is "often semi-automatic + domain expert validation" (Week 3, p. 39).

**Why it fits our problem.** Names alone fail here. `vehicle_number` and `registration_no` share almost no characters, but their sample values (`dl 01 ab 1234`) all fit the plate pattern after normalisation. Instance evidence is what makes plate columns match reliably, which is why N and I carry equal weight.

**How we implement it.**
- **Linguistic (N):** `compute_name_similarity` tokenises on `_` and camelCase, expands tokens through `THESAURUS` (e.g. `until → expiry`, `vehicle_reg → plate`), then averages best Jaro-Winkler token matches in both directions.
- **Type/constraint (C):** `compute_type_compatibility` scores type-family agreement (string, date, boolean), with a bonus toward the key attribute for primary-key columns.
- **Instance (I):** `compute_instance_similarity` takes the up-to-20 samples from `/schema` and returns the fraction that fit a plate regex, a vocabulary (makes, colours, statuses) or a date parser (ISO, `DD/MM/YYYY`, epoch range).
- **Handcrafted rules:** after scoring, `match_source_schema` adds small domain boosts (e.g. `until/expiry/end → insurance_expiry +0.15`, `valid/upto → puc_expiry +0.20`). This is the slide's "Rule-based Matching: handcrafted rules" (Week 3, p. 26).
- **Evaluation:** `mediator/matcher_eval.py` `evaluate` and `score` compute precision = correct/proposed and recall = correct/gold against `data/gold_mapping.json`. The result is 23/23, precision 1.00, recall 1.00, over REG, INS, THEFT, CAM and PUC.

**Trade-off we accept.** Be honest about the taxonomy:
- *Schema-level vs instance-level:* we use both, so we are not the schema-only algorithm of Week 3, p. 16.
- *Constraint-based:* only data types, plus a primary-key bonus that rarely fires, because plate columns are not PKs. Foreign keys (`is_fk`) are read but unused. No keys or nullability reasoning.
- *Structural:* none. There is no tree or neighbour similarity (Week 3, p. 15), and the only context is table-name tokens for `name`/`id` columns.
- *Semantic/ontology (O):* none; a small thesaurus stands in.
- *Cardinality:* top-1 per source column gives 1:1 from each source column. Many source columns across sources map n:1 onto one global attribute. There is no 1:n split and no composite n:1 such as "first_name + last_name → full_name" (the slide's Granularity example). Our data did not need either.
- *Weights:* hand-designed, not learned.

Precision and recall as metrics are not in the provided slides (general II knowledge). The nearest slide line is "Use labelled correspondences for supervised ranking. Evaluate top-1 and top-k performance." (Week 3, p. 32).

**Likely question → answer.**
- *Q: Where is the instance-based part?* A: `compute_instance_similarity`. It reads real sample values from each wrapper's `/schema` and measures how many fit a plate pattern, a vocabulary or a date parser. That is how `vehicle_number` scores as a plate column.
- *Q: Is your matcher constraint-based?* A: Only weakly: it checks data-type compatibility and gives a small primary-key bonus. We did not use foreign keys or structure, because each source has at most two tables and one join.
- *Q: 23 out of 23 — isn't that suspicious?* A: The set is small and we built the gold standard ourselves, so it shows the matcher works on our schemas, not that it generalises. The PUC case even needed a boost added, which is the human-in-the-loop tuning the slides call "semi-automatic".

### (h) Schema mapping and the Clio reading

**Choice.** Matcher output is a *correspondence*. A person turns it into a *mapping* row by adding a transform, a join path and an aggregate. The decomposer compiles those rows to SQL.

**Concept.** "Matching discovers correspondences; mapping operationalizes them into executable integration logic" (Week 3, p. 6). "Matching is discovery; mapping is an executable semantic relationship", and mapping asks "Does it require joins, filters, aggregation or functions?" (Week 3, p. 7). Clio separates "uninterpreted correspondences" from "operational" mappings with "runtime consequences". It distinguishes data exchange (restructuring into a target) from "data integration (or federation)", where "There is no need to materialize a target instance" (Clio, p. 2). Clio's architecture runs Schema Matching → Correspondences → Mapping Generation → Query Generation (SQL/XQuery/XSLT). Through the GUI a user "can view, add and remove correspondences" and "can attach transformation functions to such correspondences" (Clio, p. 3). Mapping generation builds tableaux by following foreign keys ("the chase", Clio, p. 4). It emits logical mappings as source-to-target constraints (∀…∃…), leaves ambiguous interpretations for the user to choose, and uses Skolem functions for target values with no source (Clio, p. 4). Remaining open problems include "mapping adaptation issues that arise when source and target schemas change" (Clio, p. 7). Clio is listed as a traditional tool (Week 3, p. 35–37).

**Why it fits our problem.** Our case is Clio's "data integration (or federation)" case: a virtual target, no materialised instance. The matcher can tell that `policy_until` means `insurance_expiry`. It cannot tell that the text must be parsed as `DD/MM/YYYY`, or that only the latest of several policies counts. Those are exactly the transformation functions and grouping decisions Clio leaves to the user.

**How we implement it.**
- `mediator/matcher.py` `match_source_schema` → proposed `correspondences` (source_table, source_attr, global_attr, score).
- `mediator/catalog.py` `save_mapping(...)` writes the accepted row with `transform_fn`, `aggregate`, `join_path`, `match_score`, `validated_by`, `validated_at`.
- `scripts/seed_mappings.py` states the split plainly: the matcher cannot infer "the transform … and the join path", and "Those are the human validation step". It seeds the validated set (`tests/fixtures.py` `APPROVED_MAPPINGS` plus `EXTRA_MAPPINGS`, 38 rules).
- `mediator/decomposer.py` `decompose_query` is our query generation: `join_path` becomes `LEFT JOIN`, and `aggregate` becomes `ORDER BY … LIMIT 1` where portable. `mediator/integrator.py` applies `transform_fn` to each value.

| Clio | Ours |
|---|---|
| Correspondence | `match_source_schema` output row |
| Transformation function attached in the GUI | `transform_fn` name, resolved by `transforms.get_transform` |
| Join discovered by chasing foreign keys into tableaux | `join_path` typed by a person (one FK join per source at most) |
| Logical mapping (source-to-target tgd) | A `MAPPING_REGISTRY` row, read as a GAV definition |
| Query generation into SQL/XQuery | `decompose_query` into per-source SQL |
| Grouping, deep union | `latest_by:<col>` aggregate, outer combination on plate in `integrate_results` |

**Trade-off we accept.** What Clio does and we do not:
- automatic join discovery by the chase;
- a formal tgd language with ∀/∃;
- enumeration of alternative interpretations;
- Skolem functions;
- nested (XML) targets;
- mapping composition;
- incremental mapping maintenance.

That is defensible for five flat sources with at most one join each. It would not scale to Clio's 63-table GENEX example (Clio, p. 3).

**Likely question → answer.**
- *Q: What is the difference between your matcher output and your mapping?* A: The matcher says "`policy_until` corresponds to `insurance_expiry`, score X". The mapping adds how: parse `DD/MM/YYYY`, join nothing, and keep the latest row by `policy_until`. Only the mapping is executable.
- *Q: Did you implement Clio?* A: No. We implemented the same division of labour at a much smaller scale. Joins are declared by a person instead of derived by chasing foreign keys, and mappings are registry rows instead of tgds.

### (i) Query planning, decomposition, pushdown and parallel execution

**Choice.** Select sources by coverage. Build one SQL statement per source from registry rows only. Push the key predicate down. Call all selected sources in parallel with per-source timeouts.

**Concept.** Source selection asks of each source: "Is it complete? In terms of coverage?" and "Is it up to date?" (Week 3, p. 4). The virtualisation workflow is "Query → decomposition → wrapper invocation → integration" (Week 2, p. 20). The mediator architecture has a "Query Decomposer and Optimizer" (Week 2, p. 22). A listed challenge is "Distributed query processing and optimization" (Week 2, p. 38). Predicate pushdown and parallel dispatch are not named in the provided slides (general II knowledge).

**Why it fits our problem.** "Is this vehicle insured?" does not need the camera network. Asking fewer agencies means lower latency, fewer failure points and less data exposure. Latency matters at the roadside, and parallel calls make the cost the slowest source rather than the sum of all four.

**How we implement it.**
- **Planning:** `mediator/planner.py` `plan_query` selects a source if `SOURCE_CATALOG.covers` intersects the requested attributes, after expanding `derived_from`. If an attribute is flagged `needs_identity_check`, it also selects every `identity_authority` source (REG by default), so "is X insured?" asks INS and REG. `tests/test_planner_metadata.py` asserts the file contains no source id.
- **Decomposition:** `mediator/decomposer.py` `decompose_query` takes the main table from the `plate_number` mapping, adds `LEFT JOIN` from each `join_path`, and selects an explicit column list with `table__col` aliases on name clashes. The key predicate is `UPPER(REPLACE(REPLACE(col,'-',''),' ',''))` compared with the canonical plate. For `OBSERVATIONAL` sources, both sides are also folded O→0 and I→1.
- **Aggregate pushdown:** `ORDER BY <col> DESC LIMIT 1` is added for `latest_by` columns, except when the transform is `parse_ddmmyyyy`, because that text does not sort portably. INS picks its latest policy in the integrator instead.
- **Execution:** `mediator/executor.py` `execute_federated_plan` → `_call_all` runs a `ThreadPoolExecutor` with one thread per source. Each source's `timeout_ms` is 1500 in the catalog, the TCP connect budget is 0.5 s, and a hard backstop at the slowest timeout + 0.25 s marks stragglers `TIMEOUT`. A second, registry-driven path (`execute` + `build_sql` over `mediator/registry_loader.py`) serves the CLI and Challan Guard's sighting lookup. Both paths share the same transport code.

**Trade-off we accept.** There is no cost-based optimiser and no join ordering; the cross-source join happens in Python. Wrapping the plate column in `UPPER(REPLACE(...))` prevents an ordinary index from being used on that column (general DBMS knowledge). That is fine at 3,170 rows, but it would need a normalised or computed column at scale. Having two SQL builders is a duplication we have not removed yet.

**Likely question → answer.**
- *Q: How many sources does an insurance-only question contact?* A: Two: INS because it covers `insurance_expiry`, and REG because it is the identity authority. A full profile contacts all catalogued sources.
- *Q: What exactly is pushed down?* A: The plate predicate, in the source's own spelling rule, and the latest-row ordering where the column sorts correctly as stored. Date parsing, status derivation and conflict checks stay in the mediator.
- *Q: What is the worst-case latency?* A: The slowest source's timeout plus 0.25 s, so 1.75 s with the catalog defaults. A dead wrapper is classified `DOWN` within 0.5 s by the connect budget.

### (j) Integrating results: entity resolution, conflicts, provenance

**Choice.** Join source answers on a canonical plate. Keep the latest row within each source. Tag every value with its provenance. Record REG-versus-CAM disagreements as conflicts instead of overwriting them.

**Concept.** The unifying architecture has "Identity: entity resolution" and "Evidence: provenance • quality • reliability • uncertainty • truth discovery" (Week 2, p. 11). The key challenge is "Determine identity and truth — entity resolution, record linkage, graph evidence, uncertainty, provenance, fusion" (Week 2, p. 38). "Schema matching and entity resolution can share representations, but they solve different integration problems" (Week 3, p. 33). The course's question: "When two sources disagree, how should an intelligent system decide what to believe?" (Week 2, p. 5). "Strong provenance needed" (Week 2, p. 30). Latest-wins as a policy is not in the provided slides (general II knowledge); the nearest slide term is "Temporal mismatch: current vs historical" (Week 3, p. 8).

**Why it fits our problem.** Every source knows the same car under a different string, so identity comes first. A fine must name its evidence ("expiry came from INS, fetched at T, trust 0.90"). A camera seeing a different car than the one registered is not noise to hide. It is the cloned-plate signal.

**How we implement it.**
- **Identity:** `mediator/transforms.py` `norm_plate` keeps upper-case alphanumerics. `decompose_query` applies the same rule in SQL, plus the O/0 and I/1 fold for observational sources. For fines, `mediator/plate_resolve.py` `resolve` tries one-character confusable substitutions and accepts one only when the registered make or colour corroborates it.
- **Within-source aggregation:** `mediator/integrator.py` `_pick_latest_row` reads the source's own `latest_by:<col>`, applies that mapping's transform, and takes the maximum.
- **Provenance:** `integrate_results` step 3 writes `profile["provenance"][attr] = {source, authority, trust, fetched_at}`.
- **Conflicts:** `integrate_results` step 5 compares REG make/model/colour with CAM observed values. On mismatch it appends `{attribute, values_by_source, resolution: "OFFICIAL authority (REG) preferred over OBSERVATIONAL (CAM)", chosen}`. `mediator/decide.py` turns two or more mismatches into `SUSPICIOUS — POSSIBLE CLONED PLATE`.

**Trade-off we accept.** Conflict resolution is a fixed authority rule, not trust-weighted fusion. Trust scores are carried in provenance and used only by `mediator/risk.py` `_trust_factor`. In our registry, no two authoritative sources supply the same global attribute, so the "among equals, prefer the more recent" rule in the design document never has a case to fire, and there is no generic cross-source resolver. The conflict check itself names REG and CAM in the integrator, so it is not metadata-driven. Probabilistic truth discovery is deferred to Part B.

**Likely question → answer.**
- *Q: Isn't this just a join on plate?* A: The join only works after entity resolution: four spellings and OCR errors must reduce to one key first. Within a source we still have to pick the current row, and the output carries provenance and conflicts, which a plain join throws away.
- *Q: How do you use the trust scores?* A: Honestly, lightly. They are recorded on every value and feed the risk score's "low source trust" factor. The actual REG-vs-CAM choice uses authority, which is the deterministic rule Part A asks for.
- *Q: Why keep the losing value?* A: Because the disagreement is evidence. Two mismatching attributes are what raise the cloned-plate suspicion.

### (k) Unavailable sources: refusal instead of guessing

**Choice.** A failed source becomes a status (`DOWN`, `TIMEOUT` or `ERROR`), never an exception and never "no record". If an authoritative source needed for the decision failed, the verdict is `UNDETERMINED` with `LOW` confidence.

**Concept.** "The final challenge: What should an intelligent integration system do when it does not know the answer?" (Week 2, p. 8). The mediation trade-offs include "Source availability dependency" (Week 2, p. 19). In virtual integration "Source availability affects answers", whereas in a warehouse "Source outages need not block queries" (DWH, p. 5). The key challenge is to "Enable the system to perform gracefully with uncertainty" (Week 2, p. 38).

**Why it fits our problem.** If the insurer's server is down, a system that reads "no rows" as "no policy" fines an insured driver. Refusing is the only defensible answer. It is also the honest cost of federation: a warehouse would answer from yesterday's copy without telling anyone.

**How we implement it.**
- `mediator/executor.py` `_call`: connect timeout, refused or unreachable → `DOWN`; read timeout → `TIMEOUT`; non-200 or a contract violation → `ERROR`; HTTP 503 → `DOWN`. It never raises for a source failure, and there are no retries.
- `mediator/integrator.py` `integrate_results`: a failed INS gives `insurance_status = "UNKNOWN"`; an unasked INS gives `None`; only an INS that answered with zero rows gives `NONE`.
- `mediator/decide.py` `evaluate_vehicle_decision` rule 1 returns `UNDETERMINED`/`LOW` when THEFT or INS was asked and failed. A camera sighting with REG failed also returns `UNDETERMINED` rather than `UNREGISTERED`. `CLEAR` is `HIGH` only if every core source was asked and answered and CAM saw the vehicle; otherwise it lists "Sources not checked in this query".
- `mediator/challan_guard.py` `_authoritative_failures` returns `HOLD` ("refusing to fine on partial evidence") if any non-observational source failed.

**Trade-off we accept.** The availability of a full answer is roughly the product of the sources' availabilities (general knowledge). With four laptops on one hotspot, one tired battery blocks decisions. We accept fewer answers in exchange for no wrong accusations.

**Likely question → answer.**
- *Q: Why not retry?* A: A retry doubles the worst-case latency at the roadside and usually hits the same outage. The officer is better served by "undetermined, INS down" in under two seconds.
- *Q: Doesn't a warehouse win here?* A: On availability, yes. The DWH deck says so. But it wins by answering from a copy whose age nobody at the roadside can see, which is exactly the answer we must not act on.

### (l) Data quality and the noisy observational source

**Choice.** CAM is marked `OBSERVATIONAL` with trust 0.60. Its plates get tolerant matching, its values never override official ones, and one mismatching attribute is not enough to accuse.

**Concept.** Before selecting a source, ask "Is it correct? Is it clean?" (Week 3, p. 4). External or sensor data may be "untrustworthy, noisy" (Week 3, p. 5). Per-source data problems include "missing values, incorrect values … misspellings" (Week 3, p. 6). ETL tools do "quality management" (DWH, p. 9). A key challenge is to perform gracefully "on the web/noisy source" (Week 2, p. 38).

**Why it fits our problem.** OCR misreads O as 0 and I as 1, and a camera sees colour under street lighting. REG, INS and THEFT are records of authority. The mediator has to know which is which, or a camera error becomes a cloned-plate alert.

**How we implement it.**
- `mediator/catalog.py` `DEFAULT_SOURCES`: CAM has `authority: "OBSERVATIONAL"` and `trust_score: 0.60`; REG 0.95; INS and THEFT 0.90.
- `decompose_query` applies the O/0, I/1 fold only when authority is `OBSERVATIONAL`. Official sources keep exact matching, so a typo in their data is not silently absorbed.
- `mediator/transforms.py` `fix_make` (`hyundia → Hyundai`) and `title_case` clean values. `scripts/load_source.py` `load_table` stores an uncastable value as NULL instead of crashing.
- `mediator/decide.py` requires `mismatches >= 2` before the cloned-plate verdict. `mediator/travel_check.py` `impossible_legs` flags only legs above 160 km/h on great-circle distance, which is a lower bound.

**Trade-off we accept.** `ocr_confidence` is mapped and shown in the Ministry report (`mediator/report.py`), but it does not affect any decision. The thresholds (two mismatches, 160 km/h) are hand-set. The decomposer's OCR fold covers only O/0 and I/1; wider confusions (8/B, 5/S, 2/Z, 6/G) are handled only in Challan Guard's `plate_resolve`. Using confidence probabilistically is Part B.

**Likely question → answer.**
- *Q: Why fold O/0 only for cameras?* A: A camera's error model is known OCR confusion; an agency's clerical record is not. Folding official plates would hide their data-entry errors instead of exposing them.
- *Q: Why two mismatches, not one?* A: Colour alone varies with light and weather. Two independent attributes disagreeing is much stronger evidence of a different vehicle.

### (m) Writes through wrappers: read-only mediator, separate admin door

**Choice.** The mediator's query path can only read. Each wrapper also offers `/admin/mutate` (named actions) and `/admin/sql` (one guarded `INSERT`, `UPDATE` or `DELETE`) on a separate writable connection. An agency can switch these off with `<SOURCE_ID>_ADMIN=off`.

**Concept.** The course vision mentions "real-time read and write access" (Week 2, p. 5), and the trade-off "Coupling vs autonomy" (Week 2, p. 14). The **view-update problem** is not in the provided slides (general II knowledge). An update expressed against an integrated view, especially one built with joins and aggregation, has no unique translation to source updates, and it would override the source owner's control.

**Why it fits our problem.** A "renew policy" issued against `VEHICLE_PROFILE` would be ambiguous. Which insurer's row changes? Which of several policy rows? Would the insurer even accept writes from a ministry mediator? Real agencies update their own records in their own systems. So writes stay in each source's own vocabulary, on its own endpoint, under its own switch.

**How we implement it.**
- `sources/wrapper_template.py` `guard_sql` limits `/query` to a single `SELECT`. `_make_engine` makes that connection read-only at session level.
- `create_app` builds a second `admin_engine`. `/admin/mutate` dispatches only to `ADMIN_ACTIONS` entries (e.g. INS `renew`, THEFT `steal`, CAM `sight`), each a parameterised transaction. `/admin/sql` uses `write_guard_sql`: one statement, whitelisted tables, no DDL.
- `from_env`: `<SOURCE_ID>_ADMIN=off` leaves `admin_engine` unset, so every `/admin/*` write returns 404.
- The only callers of `/admin/` are GUI tabs acting for an operator (`app/tabs/onboarding.py`, `source_editor.py`, `sql_console.py`). `mediator/executor.py` never calls them.

**Trade-off we accept.** Admin endpoints are **on by default**, not opt-in, so the write demo works without setup. There is no authentication; anyone on the hotspot could write (`docs/NETWORK.md` §7). In a real deployment the admin door would run only on the agency's own network, behind login.

**Likely question → answer.**
- *Q: Doesn't the GUI writing to sources break "the mediator never writes"?* A: The mediator's query path is still read-only by guard and by database session. The GUI is acting as the agency's own console on a separate connection, in that source's own tables, and the agency can switch it off.
- *Q: Why not update through the global schema?* A: That is the view-update problem: a change to a joined, aggregated profile has no unique source translation. It would also let the mediator override an autonomous agency.

### (n) Extensibility: adding PUC as a fifth source

**Choice.** A new agency (PUC, pollution certificates, SQLite) joins by registering a catalog row and accepting mappings. The planner, decomposer, executor and integrator stay unchanged.

**Concept.** LAV "allows to add a source to the system independently of other sources". In GAV "Any change in sources schema, needs change in global schema" (Week 3, p. 18), and GAV is "Not modular – Addition of new sources changes the mediated schema" (Week 3, p. 24). The takeaways list "dynamic schema evolution" as an open problem (Week 3, p. 39).

**Why it fits our problem.** Real enforcement keeps adding agencies; PUC status is already linked to insurance renewal in India (`docs/FIELD_RESEARCH.md` fact 10). Onboarding an agency should be a metadata task, not a code release.

**How we implement it.**
- `mediator/catalog.py` `register_source(...)` inserts the `SOURCE_CATALOG` row (URL, `identifier_attr`, authority, trust, `covers`).
- The matcher proposes `regn_number → plate_number` and `valid_upto → puc_expiry`; `save_mapping` persists them. `scripts/seed_mappings.py` skips PUC rows until the source is catalogued.
- `mediator/planner.py` names no source id, so PUC is planned from `covers` alone.
- `sources/puc/wrapper.py` is the same template with a different whitelist.

**Trade-off we accept.** This is the GAV cost, visible in our own code. `puc_expiry` had to exist in `mediator/schema.py` and in the profile skeleton in `integrate_results`. The matcher needed a `valid_upto` thesaurus entry and a +0.20 boost. The decision rules do not use PUC. So the honest claim is "no change to query-processing code", not "no change anywhere".

**Likely question → answer.**
- *Q: You said zero code changes. Is that true?* A: For planning, decomposition, execution and integration, yes: a test greps the planner for source ids. The global schema did gain `puc_expiry`, which is exactly what the slides predict for GAV.
- *Q: What would LAV change here?* A: With LAV we would describe PUC as a view over an existing global schema and never touch the global side. We would pay for that with query rewriting using views.

### (o) Challan Guard: federation applied at decision time and dispute time

**Choice.** An ANPR sighting becomes a candidate fine. A live, six-step federated verification decides `ISSUE`, `REJECT` or `HOLD`. A citizen's dispute re-runs the same verification live.

**Concept.** STP's principle is "Automate the common path; route uncertainty to human review or richer reasoning", and it is suited to "Fraud / eligibility pre-screening" (Week 2, p. 24). The business-value framework asks about "Trust — Can we explain and defend the answer?" and "Decision value — Did integration improve an outcome?" (Week 2, p. 15). Integration is "constructing defensible knowledge from heterogeneous evidence" (Week 2, p. 8). Cached results "May not always be fresh!" (DWH, p. 23).

**Why it fits our problem.** Wrongful e-challans commonly start with a plate misread or a cloned plate (`docs/FIELD_RESEARCH.md` facts 12–14). Theft indexes also lag (fact 15). A fine should be checked against the agencies at fine time, and a dispute against them at dispute time. Only a federated design reads "the record now" both times.

**How we implement it.**
- `mediator/challan_guard.py` `_verification` runs six steps in order:
  1. authoritative sources reachable, else `HOLD`;
  2. identity via `plate_resolve.resolve` (`RESOLVED`, `AMBIGUOUS` → `HOLD`, `NONE` → `REJECT`);
  3. impossible travel via `travel_check.impossible_legs` over `sightings_for` (registry-driven executor call) → `HOLD` plus a watchlist entry;
  4. stolen before the sighting → `REJECT` and a police alert;
  5. registration not `ACTIVE` → `REJECT`;
  6. insurance on the **sighting date** (`insurance_on`: the policy must be active that day — started on or before it and not yet expired) → `ISSUE` with ₹2,000, or ₹4,000 on repeat (`challan_prior_issued`).
- `dispute` re-runs `_verification`, cancels the fine if the verdict changed, and names the changed fields (`_changes`).
- Cases and events are stored in `CHALLAN_CASES` and `CHALLAN_EVENTS` (`mediator/catalog.py`).
- The policy must be active on the sighting date, checked at both ends: a policy bought after the offence (start later than the sighting) does not clear the fine.

**Trade-off we accept.** The evidence bundle is a snapshot in `meta.db` (profile scalars, statuses, candidates, travel legs) kept for audit; it is never used as a substitute for a live query. Verification is triggered by an operator, not by the event itself. The scoring weights and the 160 km/h threshold are hand-set.

**Likely question → answer.**
- *Q: What makes this integration rather than an app feature?* A: Each step is a federated query across autonomous agencies, joined on a resolved identity. The dispute path only works because we re-query live rather than read a copy.
- *Q: Why is `HOLD` a verdict?* A: That is STP's "route uncertainty to human review". An ambiguous plate, a possible clone or a silent insurer goes to a person instead of becoming a fine.

### (p) Evaluation methodology

**Choice.** We check three things separately: the matcher against a hand-made gold mapping, the whole pipeline's decisions against an independently computed oracle, and component behaviour with unit and integration tests.

**Concept.** Matching aims for a mapping "that passes user validation" (Week 3, p. 9). The slides suggest "Use labelled correspondences for supervised ranking. Evaluate top-1 and top-k performance." (Week 3, p. 32). The value framework's first question is "Accuracy — Did we get the right answer?" (Week 2, p. 15). Ground-truth oracles, precision and recall are not in the provided slides (general II knowledge).

**Why it fits our problem.** A demo that works on six plates proves little. Grading every generated vehicle across every decision class checks that planning, SQL generation, transforms, latest-wins and rules all agree.

**How we implement it.**
- `data/gold_mapping.json` (23 correspondences); `mediator/matcher_eval.py` `evaluate` / `score` (micro-averaged); `scripts/evaluate_matcher.py` → precision 1.00, recall 1.00.
- `mediator/ground_truth.py` `build_ground_truth` reads the raw CSVs directly (no planner, wrappers, mappings or integrator) and applies `expected_decision`. `grade` compares against `mediator.core.run_global_query`. `scripts/evaluate_ground_truth.py --start-wrappers` → 621/621.
- 672 tests pass, including `tests/test_wrapper_guard.py` (injection-style strings rejected), `tests/test_executor.py` `test_sources_are_called_in_parallel_not_in_sequence`, and `tests/test_planner_metadata.py`.

**Trade-off we accept.** The oracle repeats the same rule ordering and imports thresholds and decision strings from `mediator/decide.py`. It therefore tests that the federation delivers the right evidence, not that the rules are the right policy. It skips the two rules that need a live failure or a wholly unknown plate. The gold mapping is small, and the team that tuned the thesaurus also wrote it.

**Likely question → answer.**
- *Q: Isn't 621/621 circular?* A: Partly, and we say so: the rules are shared. What is independent is the data path. The oracle never touches our SQL, wrappers, mappings or transforms, so a bug in any of those shows up as a mismatch.
- *Q: How would you make the matcher evaluation fairer?* A: Use a gold mapping written by someone who did not tune the matcher, on schemas it has not seen, and report top-k as the slides suggest.

---

## 4. Choices we did not make

| Alternative | Where in slides | Why not for this problem | When it would be the right choice |
|---|---|---|---|
| Data warehouse with ETL | Week 2, p. 16; DWH, p. 6 | Stale policy status means wrongful fines; autonomous agencies would not ship copies; duplicated citizen data | Historical reporting, e.g. "uninsured rate by state per year" (Week 2, p. 16 "Regulatory / historical reporting") |
| Hybrid: materialised views for hot queries | Week 2, p. 23; DWH, p. 31 | Our hot data (policy expiry, theft status) is the volatile data; a view of it is stale by definition | If "a virtual query is always slow" (Week 2, p. 37), e.g. registration make/model, which rarely changes |
| Incremental view maintenance / CDC | DWH, p. 33, p. 51 | Needs change capture from each agency, which assumes cooperation beyond autonomy | Sources we own, where triggers or a CDC log exist |
| Result cache with TTL | DWH, p. 23 ("May not always be fresh!") | Same staleness problem, hidden inside the mediator | Health badges only (allowed for 5 s in our brief) |
| LAV mappings | Week 3, p. 18, p. 24; DWH, p. 2 | Reformulation is hard; we have few, known sources | "Many, relatively unknown data sources" with frequent additions |
| Embedding / ML-based matcher | Week 3, p. 26–31; Week 2, p. 27 | "Similarity ≠ equivalence"; harder to explain; Part A asks for deterministic methods | Large, poorly documented schemas; Part C |
| Structural (tree) matching | Week 3, p. 15–16 | Our schemas are one or two flat tables | Nested XML or JSON schemas |
| Collective matching into a mediated schema | Week 3, p. 17 | We designed `VEHICLE_PROFILE` by hand from the use cases | A new domain with many similar sources (e.g. many insurers) |
| Clio-style tgds with chase-derived joins | Clio, p. 3–4 | At most one join per source; a person can state it | Deep schemas with many foreign keys |
| Context-oriented / knowledge-graph integration | Week 2, p. 30 | The entity key (plate) is given; no discovery needed | Linking owners, addresses and vehicles across cases |
| Fully automatic STP fines | Week 2, p. 24 | "False positives/negatives" cost citizens money; we route uncertainty to a person | Low-stakes routing where errors are cheap to undo |
| Trust-weighted or probabilistic truth discovery | Week 2, p. 11 ("truth discovery") | Part A wants deterministic, explainable rules | Part B, "Data Integration under uncertainty" (Week 2, p. 1) |
| Peer-to-peer or federated DBMS integration | Not in the provided slides | Needs shared engines or peer mappings between autonomous agencies | Organisations under one DBMS vendor or one administration |

---

## 5. Hard questions

1. **"Isn't this just a join?"** No. The join key has to be resolved first: four plate spellings plus OCR errors reduce to one canonical key. Each source has several rows per vehicle, so we choose the current one, and every value carries provenance and conflicts. A plain SQL join across four engines is not even possible without the wrappers.

2. **"Why not a warehouse with a five-minute refresh?"** Five minutes is still a window in which a renewed driver is fined. A frequent refresh needs change capture from four autonomous agencies, which the DWH deck calls the hard part. Fresh enough is not good enough when the output is a fine.

3. **"Your matcher found 23/23. Isn't the gold standard biased?"** Yes, partly. We wrote the gold mapping, and we tuned the thesaurus and boosts on the same schemas. The PUC boost was added after a miss. Treat the number as a regression check that the matcher still works on our schemas, not as evidence it generalises.

4. **"What is the latency cost of federation?"** The query waits for the slowest source, capped at its timeout plus 0.25 s: 1.75 s with our 1500 ms defaults. A dead source costs at most the 0.5 s connect budget before it is marked `DOWN`. `docs/NETWORK.md` §5 records the measured figures.

5. **"How do you know the mappings are correct?"** Three checks. A person validates every mapping row (`validated_by`). The matcher is scored against a gold mapping. The 621-plate oracle reads the raw CSVs without our mappings, so a wrong transform or column shows up as a wrong decision.

6. **"What if two plates normalise to the same key?"** Stripping separators is safe, because spaces and hyphens carry no meaning in Indian plates. The O/0 and I/1 fold on camera plates can collide in principle when district and series lengths vary, such as a one-digit Delhi district followed by a series starting with O. We apply it only to the observational source. For fines, `plate_resolve` returns `AMBIGUOUS` and holds the case when more than one registered vehicle fits.

7. **"Is your 'federation' really just REST calls?"** The transport is HTTP, but the architecture is the slides' mediator: wrappers, a global schema, GAV mappings, source selection, decomposition and integration. The HTTP layer is what lets each agency publish an API instead of its database.

8. **"GAV means a new source changes the global schema. Did it?"** Yes: `puc_expiry` had to be added to `mediator/schema.py`. The query-processing code did not change, because the mappings live in the registry.

9. **"If a virtual query is always slow, when should it become materialised?"** (Week 2, p. 37.) When the data is stable and the query is frequent, e.g. registration make, model and colour. We would materialise that, and never insurance or theft status, whose value lies in being current.

10. **"Where is the optimiser?"** There is no cost-based optimiser. Our "optimisation" is coverage-based source selection, predicate pushdown and parallel dispatch. With five sources and one-row answers, join ordering would not change anything measurable.

11. **"Your trust scores barely affect decisions. Why have them?"** They make every value's evidential weight explicit in provenance, and they drive the risk score's low-trust factor. Decisions use authority rules because Part A asks for deterministic ones. The scores are the input Part B's truth discovery will need.

12. **"Your SQL guard is regex. Isn't that insecure?"** The guard catches our own bugs. The security boundary is below it: a read-only database session, and a SELECT-only account on PostgreSQL and MySQL. The real gap is the lack of authentication on the wrapper, which we list as a known limitation.

13. **"You say you never cache, but `meta.db` holds evidence bundles."** They are an audit record of what a source said at issue time, like a filed report. Nothing reads them to answer a new query: a dispute re-queries every source live.

14. **"What does your system do when it does not know the answer?"** (Week 2, p. 8.) It says so. A failed insurer or police source yields `UNDETERMINED` with `LOW` confidence, and names the source. An unasked source makes no claim, and Challan Guard holds the fine.

15. **"Is the matcher constraint-based?"** Only weakly: data-type compatibility plus a primary-key bonus. It does not use foreign keys, nullability or structure. With one or two tables per source, instance evidence carried more signal.

---

## 6. Glossary cheat sheet

| Term | One-line definition | Our example |
|---|---|---|
| Information integration | Middleware that lets applications "access data as though it were in a single database" (Week 2, p. 5) | One plate lookup over five databases |
| Mediator | Holds the global schema and planner; decomposes and integrates (Week 2, p. 19) | `mediator/core.py` `run_global_query` |
| Wrapper | Component that sends queries to a source and converts replies (DWH, p. 8) | `sources/wrapper_template.py` |
| Virtual integration (EII) | Query evaluated over current sources, nothing stored (DWH, p. 5, p. 31) | Every lookup reads live |
| Materialised integration / DWH | Integrated result stored and refreshed by ETL (DWH, p. 5–6) | What we did not build |
| ETL | Extract, transform, load into a warehouse (Week 2, p. 16) | Not used; transforms run at query time |
| Hybrid integration | Materialise the common and expensive, virtualise the volatile (Week 2, p. 23) | `REPORT_LOG` stored, source data virtual |
| STP | Rules/ML automate a decision; uncertainty goes to review (Week 2, p. 24) | Challan Guard `ISSUE` / `HOLD` |
| Source autonomy | Sources stay under their owners' control (Week 2, p. 14, p. 19) | Databases bound to 127.0.0.1; API only |
| Global / mediated schema | The common schema users query (Week 2, p. 5; Week 3, p. 24) | `VEHICLE_PROFILE` in `mediator/schema.py` |
| GAV | Global schema defined as views over sources (Week 3, p. 18) | `MAPPING_REGISTRY` rows |
| LAV | Sources defined as views over the global schema (Week 3, p. 18) | Not used |
| View unfolding | GAV query reformulation by substituting view definitions (Week 3, p. 24) | `decompose_query` |
| Schema matching | Finding correspondences with sim ≥ θ (Week 3, p. 7) | `match_source_schema`, θ = 0.55 |
| Schema mapping | Executable form of correspondences (Week 3, p. 7) | Row with `transform_fn`, `join_path`, `aggregate` |
| Correspondence | Uninterpreted element-to-element link (Clio, p. 2) | `vehicle_reg ↔ plate_number` |
| Linguistic matching | Names tokenised, acronyms expanded, thesaurus (Week 3, p. 12) | `compute_name_similarity` |
| Instance-based matching | Compare value distributions or examples (Week 3, p. 11) | `compute_instance_similarity` |
| Hybrid matching | Weighted sum of several similarity signals (Week 3, p. 11, p. 32) | 0.40·N + 0.20·C + 0.40·I |
| Tableau / chase (Clio) | Join groups built by following foreign keys (Clio, p. 3–4) | Replaced by a hand-typed `join_path` |
| Skolem function (Clio) | Generates target values with no source value (Clio, p. 4) | Not used |
| Naming conflict (synonyms) | Same concept, different names (Week 3, p. 8) | `registration_no`, `vehicle_reg`, `vehicle_number`, `plate_id` |
| Representation conflict | Same value, different encoding (Week 3, p. 8) | `10/06/2026` text vs epoch integer vs ISO |
| Derived attribute | Computed from stored attributes (Week 3, p. 8, p. 19) | `insurance_status` |
| Data source selection | Choosing sources by coverage, correctness, freshness (Week 3, p. 4) | `plan_query` over `covers` |
| Query decomposition | Split a global query into source sub-queries (Week 2, p. 20) | One `SELECT` per source |
| Entity resolution | Deciding records refer to the same entity (Week 2, p. 11) | `norm_plate`, `plate_resolve` |
| Provenance | Where a value came from and how (Week 2, p. 11) | `{source, authority, trust, fetched_at}` |
| Truth discovery | Deciding what to believe when sources disagree (Week 2, p. 11) | Deferred to Part B; authority rule for now |
| Self-maintainable view | View maintainable without accessing base sources (DWH, p. 41) | A warehouse concept we avoid needing |

---

## 7. Numbers to remember

| What | Number |
|---|---|
| Sources | 5 (REG, INS, THEFT, CAM, plus PUC added live) |
| DBMS engines | 3: PostgreSQL (REG, CAM), MySQL (INS), SQLite (THEFT, PUC) |
| Rows per source | REG 605 owners + 605 registrations; INS 5 insurers + 564 policies; THEFT 42 incidents; CAM 7 cameras + 918 captures; PUC 424 certificates |
| Total rows | 3,170 |
| Mapping rules in `MAPPING_REGISTRY` | 38 |
| Matcher weights and threshold | 0.40·N + 0.20·C + 0.40·I, θ = 0.55 |
| Matcher vs gold | 23/23 correspondences, precision 1.00, recall 1.00 |
| Ground truth | 621/621 decisions correct |
| Tests | 672 passing |
| Trust | REG 0.95, INS 0.90, THEFT 0.90, CAM 0.60 |
| Per-source timeout (catalog) | 1500 ms; connect budget 0.5 s; backstop + 0.25 s |
| `/query` guard | one `SELECT`, whitelisted tables, `LIMIT 200`, 3 s statement timeout |
| Cloned-plate rule | 2 or more of make/model/colour mismatch |
| Impossible travel | above 160 km/h, great-circle |
| Challan amounts | ₹2,000 first, ₹4,000 repeat (MV Act §196) |
