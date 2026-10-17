# Phase 2 prompts (vibe-coding log)

Every prompt behind the phase-2 work, in order, for rubric 3. Two kinds:

- **User prompts**: typed by the student to Claude Code.
- **Sub-agent task prompts**: the specification Claude Code handed to each coding sub-agent;
  these are the prompts the generated code came from.

The code each prompt produced is in the paired commits `generated: <file>` and
`reviewed: <file>`; the differences between them, and why, are in `docs/AI_CHANGES_PHASE2.md`.
Phase-1 prompts are in `docs/ai/PROMPTS.md`.

### Prompt 1 — 2026-10-17 07:53 UTC — student

```text
<pasted_content id="217b">
Your project phase 2 evaluation will be conducted on Oct 21-23, including in the class on a random selection basis, as we did for phase 1. As discussed earlier, you need to demonstrate the data integration (and entity matching) under noisy data. For example, for your insurance project, if the vehicle registration is not matched exactly, how to find out all vehicles which are very close to the scanned (image) registration number, and then how to cleanse this noisy record with the help of other contextual data (like, model, color etc). Your project will be evaluated based on the following rubric:

1. Given a scanned image of a car registration, find out all vehicles approximately matching with this nosy car plate and vice-versa. Here you need to apply the matching algorithm which gives the best accuracy. You need to justify why you have taken a particular algorithm or a combination of algorithms. (5)
2. Cleansed the recorded data w.r.t. the image data and other contextual information so that there are no anomalies (including the noise) in the recorded data. (5)
3. Show me the prompt (for vibe coding), generated code and highlights the changes you have made in the generated code. (5)

Note: All these tasks I shall be checking by asking you to insert/delete/modify the data records
</pasted_content id="217b">

im adding lecture slides for matching methods that he has taught  us, fformulate a plan on how we are going to start working, we already have some sort of similarity measures i think jaro winkler from the last stage of the project but i want us to make sure all the three rubrics here are fully fullfilled
```

### Prompt 2 — 2026-10-17 08:20 UTC — student

```text
begin i installed scikit in venv
```

### Prompt 3 — 2026-10-17 08:21 UTC — sub-agent task

**Task given to a coding sub-agent** (Build labelled plate-image benchmark, model opus):

Repo: C:\Users\siddh_ygv5bws\IIA-vehicle-data-federation\IIA_Project (git, main, clean). Read docs/superpowers/plans/2026-10-17-phase2-noisy-matching.md first, workstream A. You implement **workstream A only**.

Goal: a reproducible, labelled benchmark of noisy plate reads produced from rendered plate images, so later work can measure matching accuracy honestly.

## Deliverables
1. `scripts/make_plate_images.py`
   - Renders an Indian-style number plate image for a given registration number with Pillow (black text on white/yellow, a bold font; use `C:\Windows\Fonts\arialbd.ttf` with a graceful fallback to `PIL.ImageFont.load_default()` so it still runs on other machines).
   - Applies controlled degradations, each at severities 1..3: `blur` (Gaussian), `motion` (directional blur), `jpeg` (low-quality re-encode), `rotate` (small angle + perspective skew), `glare` (bright patch), `dark` (low light + noise), `occlude` (dirt specks / partial bar).
   - Deterministic: a `--seed` argument, and the same seed must reproduce byte-identical images (use `random.Random(seed)` keyed per image, never global randomness).
   - CLI: `--plates N` (how many registrations to sample from REG), `--out data/plate_images`, `--seed 20261017`, `--manifest data/plate_images/manifest.csv`. Plates come from `sources/reg/reg.db` `VEHICLE_REGISTRATION.registration_no` (read-only; do not modify any source DB). Also always include the story plates DL01AB1234, DL05CD9876, HR26EF4455, UP16GH1122, MH12IJ7788, DL01AB0002.
   - Manifest columns: `image_path,true_plate,degradation,severity,seed`.
2. `scripts/run_ocr_benchmark.py`
   - Runs `mediator/plate_ocr.py` (EasyOCR, offline) over the manifest images and writes `data/match_benchmark.csv` with columns `image_path,true_plate,ocr_read,ocr_confidence,degradation,severity,exact_match`.
   - Caches per image (skip images already in the output unless `--force`), prints a summary: images, exact-read rate overall and per degradation/severity, mean confidence, and the 10 most common character confusions observed (true char -> read char, aligned by position when lengths match).
   - Must not crash when OCR returns nothing: record an empty read.
   - Append rows for noise we already have, clearly marked `degradation=observed`: the camera plates in `sources/cam/cam.db` that are not registered in REG (there are 47), with their `ocr_confidence` and `true_plate` left empty (unknown truth), plus the planted story misreads DL05CD9B76 -> DL05CD9876 and DLO1AB1234 -> DL01AB1234 with truth filled in.
3. `data/match_benchmark.csv` and `data/plate_images/` generated for **100 plates x 7 degradations x 3 severities plus 100 clean = ~2200 images**. If OCR is too slow, reduce to 60 plates and say so in your report; keep all 6 story plates.
4. `docs/BENCHMARK_NOISE.md`: one page. How the images are made, the degradation ladder, the measured exact-read rate per degradation and severity (a real table from your run), the confusion counts observed, and the honest limits (rendered plates are cleaner than real photos; EasyOCR version pinned).
5. Tests `tests/test_plate_images.py`: rendering is deterministic for a fixed seed (same bytes twice); every degradation produces an image of the expected size; the manifest row count matches the files on disk; the benchmark loader parses the CSV. Keep them fast: generate 2-3 images in a tmp dir, never run EasyOCR in tests.

## Rubric-3 workflow — mandatory, this is graded
Work in two commits per deliverable file:
1. Write your **first** version of the module, commit it immediately, unreviewed, even if tests fail, with message `generated: <path> (unreviewed AI output)`.
2. Then test it, fix it, and commit the reviewed version as `reviewed: <path> — <short reason list>`.
3. Append to `docs/AI_CHANGES_PHASE2.md` (create it; add a `## Workstream A` section): for each module, the task prompt summary, the generated commit hash, the reviewed commit hash, and a bullet per change you made with the reason (bug, edge case, course-method compliance, performance).
**NO `Co-Authored-By` line or any trailer in any commit.** Do not push.

## Constraints
- Python 3.12 in `.venv`. Pillow, numpy, EasyOCR, scikit-learn are installed.
- Do not modify any existing module except adding to `requirements.txt` if truly needed.
- Do not touch `mediator/`, `app/`, or the source databases' contents.
- `data/plate_images/` must be added to `.gitignore` (too big for git) but `data/match_benchmark.csv` and `data/plate_images/manifest.csv` ARE committed.
- Ports 8001-8005 may be used by other work; your scripts must not start wrappers.
- Run `python -m pytest -q tests/test_plate_images.py` plus the existing fast suites you touch.

Final report (<=250 words): commit hashes (generated and reviewed per file), the real exact-read-rate table, top confusions, how many images and how long OCR took, and anything you could not do.

### Prompt 4 — 2026-10-17 08:21 UTC — sub-agent task

**Task given to a coding sub-agent** (Build similarity library + evaluation, model opus):

Repo: C:\Users\siddh_ygv5bws\IIA-vehicle-data-federation\IIA_Project (git, main, clean). Read docs/superpowers/plans/2026-10-17-phase2-noisy-matching.md first, workstream B. You implement **workstream B only**: the string-similarity library and its unit tests. Another agent is building the benchmark data (workstream A) in parallel — do NOT create or edit anything under `data/`, `scripts/make_plate_images.py`, `scripts/run_ocr_benchmark.py`, `docs/BENCHMARK_NOISE.md`, or `tests/test_plate_images.py`.

Course slides are the specification. Read the extracted text first:
`C:\Users\SIDDH_~1\AppData\Local\Temp\claude\c--Users-siddh-ygv5bws-OneDrive-Desktop-iia-proj1\845c9096-9404-4111-8dbd-b140ad188a46\scratchpad\slides\Week-6-8_Data_String_Matching.txt`
Pages that matter: edit distance and the DP table p.16-19, Jaro p.20-21, Jaro-Winkler p.22, set-based overlap/Jaccard p.24-26, TF/IDF p.27-31, Ratcliff-Obershelp p.33-34, Soundex p.36-37. Implement what the slides define, with the slides' own notation in the docstrings, and cite the page number in each function's docstring.

## Deliverable: `mediator/similarity.py`
Pure functions, no I/O, type hints, each returning a float in [0,1] unless stated:
- `levenshtein(s1, s2) -> int` and `edit_similarity(s1, s2) -> float` (1 - dist/max_len), DP table, O(len1*len2).
- `weighted_edit_distance(s1, s2, sub_cost: Callable[[str,str],float] | None = None) -> float` — generalised DP where substitution cost comes from a callable; default 1.0 for any substitution, 1.0 insert/delete.
- `ocr_confusion_cost(a, b) -> float` — 0.2 if the pair is in the documented ANPR confusion set, else 1.0. Import the pairs from `mediator/plate_resolve.py::CONFUSABLE` (do not redefine them) and treat the relation as symmetric.
- `ocr_edit_similarity(s1, s2) -> float` — `weighted_edit_distance` with `ocr_confusion_cost`, normalised.
- `jaro(s1, s2) -> float` and `jaro_winkler(s1, s2, p=0.1, max_prefix=4) -> float` — implement per the slides. `mediator/matcher.py` already has a `jaro_winkler`; move/replace it so there is ONE implementation: import it in matcher.py from here and keep matcher.py's behaviour identical (its tests must still pass).
- `qgrams(s, q=2, pad=True) -> list[str]`, `overlap(s1, s2, q=2) -> float`, `jaccard(s1, s2, q=2) -> float`.
- `tfidf_cosine(s1, s2, corpus: Sequence[str], q=2) -> float` — IDF computed over the given corpus of strings, as in the slides' worked example; document that the corpus is the registration list.
- `ratcliff_obershelp(s1, s2) -> float` — implement the recursive longest-common-substring definition from the slides; do not just call difflib, but you may assert agreement with `difflib.SequenceMatcher().ratio()` in a test.
- `soundex(s) -> str` and `soundex_similarity(s1, s2) -> float` (1.0 if codes equal else 0.0), per the slides' table.
- `ALL_MEASURES: dict[str, Callable[[str, str], float]]` — name -> callable, for the evaluation harness to iterate. `tfidf_cosine` needs a corpus, so expose a factory `tfidf_measure(corpus)` that returns a 2-arg callable.

Behaviour rules: empty-vs-empty = 1.0; empty-vs-nonempty = 0.0; all functions case-fold and work on the canonical plate form (A-Z0-9) but must not assume plates — they take arbitrary strings.

## Tests: `tests/test_similarity.py`
- Worked examples **from the slides** (use the exact strings and expected values in the deck's examples for edit distance, Jaro, Jaro-Winkler, Jaccard, TF/IDF, Ratcliff-Obershelp and Soundex; quote the page in a comment). If a slide gives a number, assert it to a sensible tolerance.
- Properties: identity = 1.0, symmetry, range [0,1], empty-string rules.
- OCR cases: `DL05CD9B76` vs `DL05CD9876` must score higher under `ocr_edit_similarity` than under plain `edit_similarity`; `DLO1AB1234` vs `DL01AB1234` likewise.
- A test asserting `mediator.matcher.jaro_winkler` is the function from this module (one implementation).

## Rubric-3 workflow — mandatory, this is graded
1. Commit your **first** version immediately, unreviewed, even if tests fail: `generated: mediator/similarity.py (unreviewed AI output)`.
2. Then fix and commit: `reviewed: mediator/similarity.py — <short reason list>`.
3. Append to `docs/AI_CHANGES_PHASE2.md` a `## Workstream B` section (the file may be created by the other agent; if it exists, append, do not overwrite): the task prompt summary, generated and reviewed commit hashes, and a bullet per change with its reason.
**NO `Co-Authored-By` line or any trailer.** Do not push.

Finish: `python -m pytest -q tests/test_similarity.py tests/test_matcher.py tests/test_matcher_evaluation.py` all green, and `python scripts\evaluate_matcher.py` still prints OVERALL 23 0 0 1.00 1.00 1.00 (the schema matcher must not regress).

Final report (<=250 words): commit hashes, which slide examples you encoded and any whose numbers you could not reproduce exactly (say why), the OCR-vs-plain similarity numbers for the two story misreads, and anything left undone.

### Prompt 5 — 2026-10-17 08:30 UTC — sub-agent task

**Task given to a coding sub-agent** (Build blocking and filtering, model opus):

Repo: C:\Users\siddh_ygv5bws\IIA-vehicle-data-federation\IIA_Project (git, main). Read docs/superpowers/plans/2026-10-17-phase2-noisy-matching.md, workstream D. You implement **workstream D only**: candidate generation at scale (the first step of the slides' two-step framework).

Another agent is finishing workstream A (benchmark images and `data/match_benchmark.csv`). Do NOT touch `data/`, `scripts/make_plate_images.py`, `scripts/run_ocr_benchmark.py`, `docs/BENCHMARK_NOISE.md`, `tests/test_plate_images.py`. Workstream B is done: use `mediator/similarity.py` (levenshtein, jaccard, qgrams, overlap, ocr_edit_similarity, ...) and never reimplement a measure.

Specification is the course deck; read the extracted text first:
`C:\Users\SIDDH_~1\AppData\Local\Temp\claude\c--Users-siddh-ygv5bws-OneDrive-Desktop-iia-proj1\845c9096-9404-4111-8dbd-b140ad984a66\scratchpad\slides\Week-6-8_Data_String_Matching.txt` — if that path does not exist, use
`C:\Users\SIDDH_~1\AppData\Local\Temp\claude\c--Users-siddh-ygv5bws-OneDrive-Desktop-iia-proj1\845c9096-9404-4111-8dbd-b140ad896a46\scratchpad\slides\Week-6-8_Data_String_Matching.txt`; if neither exists, find it with: Get-ChildItem "C:\Users\SIDDH_~1\AppData\Local\Temp\claude" -Recurse -Filter "Week-6-8_Data_String_Matching.txt" | Select -First 1 -ExpandProperty FullName
Pages that matter: the naive solution and its cost p.40-43, the 2-step framework p.44, string variants p.45-46, inverted index p.47-51 and its limitations p.52, size filtering p.53-54, prefix filtering p.55-64, position index p.65, classical summary p.66. Also Week 8 slides 1-7 recap the same filters with a worked example.

## Deliverable: `mediator/blocking.py`
Typed, no I/O beyond what is passed in, docstrings citing slide pages:
- `class QGramIndex`: build from an iterable of strings (`build(strings, q=2)`); an inverted index mapping q-gram -> set of string ids, as in the deck.
  - `candidates(query, min_overlap=1) -> set[str]`: plain inverted-index lookup (p.47-51).
  - `candidates_size_filtered(query, delta)`: size/length filtering (p.53) — only strings whose length is within the bound implied by the overlap requirement.
  - `candidates_prefix_filtered(query, k)`: prefix filtering (p.55-58) using a global token order (document the order you choose: by increasing document frequency, as in the deck).
  - `candidates_position_filtered(query, k)`: position index (p.65), using stored positions to prune by the remaining-overlap bound.
  - Each returns a set of candidate ids and records statistics.
- `@dataclass BlockingStats`: `queried`, `candidates_before`, `candidates_after`, `index_lookups`, `elapsed_ms`, and `filters_applied: list[str]`. Every method populates one; `QGramIndex.last_stats` exposes the most recent.
- `def candidate_plates(read: str, registry: Sequence[str], *, strategy: str = "position", **kw) -> tuple[list[str], BlockingStats]` — the one entry point the rest of the code calls; `strategy` is one of `none` (full scan), `index`, `size`, `prefix`, `position`.
- `def sql_prefilter(read: str, column: str, dialect: str) -> str` — a cheap predicate to push into a source's SQL so a wrapper returns tens of rows instead of all of them: same length-or-near-length and same first two characters, written with `UPPER(REPLACE(REPLACE(col,'-',''),' ',''))` the way `mediator/decomposer.py` already normalises plates. Read decomposer.py first and reuse its normalisation expression rather than inventing another. This function only builds the string; it must not execute anything.

## Tests: `tests/test_blocking.py`
- The deck's worked example (Week 8 slides 1-7, the `x1: {Block, Jeevan, Cinema, Old, Agra}` style sets) encoded as a test for prefix and position filtering, with the page/slide cited in a comment.
- **Recall safety**: for every plate in a 200-plate sample of `sources/reg/reg.db` (read-only) and for each of its single-character OCR corruptions from `mediator/plate_resolve.CONFUSABLE`, the true plate must be in the candidate set returned by `index`, `size`, `prefix` and `position` strategies. A filter that loses true matches is a bug — if a bound genuinely cannot keep them, make the test assert the documented bound and say so in your report.
- **Pruning works**: candidates_after < candidates_before for the filtered strategies on that sample, and `none` returns everything.
- Determinism, empty input, a query with no candidates, and a query longer than anything indexed.
- Keep the whole file under ~3 seconds; no wrappers, no network.

## Rubric-3 workflow — mandatory, graded
1. Commit your first version unreviewed, even if tests fail: `generated: mediator/blocking.py (unreviewed AI output)`.
2. Then fix and commit: `reviewed: mediator/blocking.py — <reasons>`.
3. Append a `## Workstream D` section to `docs/AI_CHANGES_PHASE2.md` (it exists; append, never overwrite): prompt summary, both commit hashes, and a bullet per change with its reason.
**No `Co-Authored-By` or any trailer. Do not push.**

Also report, in your final message, a small table from a real run over all 605 REG plates: for a typical noisy read, candidates scanned under each strategy and the recall each keeps. This is the scalability evidence for the viva.

Final report (<=250 words): commit hashes, the table, which deck example you encoded, any filter whose bound loses true matches, and anything undone.

### Prompt 6 — 2026-10-17 08:48 UTC — sub-agent task

**Task given to a coding sub-agent** (Evaluate measures and build combiner, model opus):

Repo: C:\Users\siddh_ygv5bws\IIA-vehicle-data-federation\IIA_Project (git, main). Read docs/superpowers/plans/2026-10-17-phase2-noisy-matching.md workstreams B and C, docs/BENCHMARK_NOISE.md, and docs/AI_CHANGES_PHASE2.md. You implement **workstream C plus the evaluation harness from B**. This produces the evidence for rubric 1: "apply the matching algorithm which gives the best accuracy and justify why".

Inputs that already exist — use them, never reimplement:
- `mediator/similarity.py` — every string measure (`ALL_MEASURES`, `tfidf_measure(corpus)`, `weighted_edit_distance`, `ocr_confusion_cost`, `ocr_edit_similarity`, ...).
- `mediator/blocking.py` — `candidate_plates(read, registry, strategy=...)` and `BlockingStats` (another agent may still be wrapping up its report, but the code is committed; do not edit blocking.py — if you find a bug in it, describe it in your report).
- `mediator/plate_resolve.py` — the rule-based resolver with make/colour corroboration; it is a contender in the comparison.
- `data/match_benchmark.csv` — 2,202 labelled rows (100 true plates, 7 degradations x 3 severities + clean, raw EasyOCR reads and confidences) plus 47 unlabelled observed CAM plates. Registry = all 605 plates in `sources/reg/reg.db` `VEHICLE_REGISTRATION.registration_no` (open read-only). Make/model/colour per registered plate are in the same table; camera-observed make/colour for the benchmark rows can be taken as the registration's true values with controlled noise (see below).

Course deck text (spec), find it with:
`Get-ChildItem "C:\Users\SIDDH_~1\AppData\Local\Temp\claude" -Recurse -Filter "Week-6-8_Data_String_Matching.txt" | Select -First 1 -ExpandProperty FullName`
Relevant: accuracy challenges p.13, ML pair classification p.69, training pairs must reflect deployment p.70, feature vectors p.71, gradient-boosted trees p.72, retrieve-rerank p.76, hard negatives p.78, calibration p.81, evaluate retrieval/scoring/clustering separately p.82, leakage p.83.

## 1. Data-driven confusion costs — `mediator/confusion.py`
The benchmark found 0->O is 62% of substitutions and `plate_resolve.CONFUSABLE` misses 1/4, H/4, 1/A, D/U, 1/T. Estimate substitution costs from observed confusions on the **training split only**: cost(a,b) = 1 - normalised frequency, floored (e.g. at 0.1), Laplace-smoothed, symmetric, saved to `data/confusion_costs.json` with the split seed and counts. Provide `learned_confusion_cost(a, b) -> float` and `learned_ocr_similarity(s1, s2) -> float` (via `similarity.weighted_edit_distance`). Fall back to the hand table for unseen pairs. Document the leakage rule in the docstring.

## 2. Matcher — `mediator/plate_match.py`
- `@dataclass Candidate`: plate, scores (dict measure->float), features (dict), probability, decision (`ACCEPT` | `REVIEW` | `REJECT`), reasons (list[str]).
- `features(read, candidate, *, confidence, observed_make, observed_colour, registry_row, corpus) -> dict` — every measure from `ALL_MEASURES`, TF/IDF, learned_ocr_similarity, hand ocr_edit_similarity, length difference, state-code (first two chars) agreement, OCR confidence, make agreement, model agreement, colour agreement, and `read_is_registered`.
- `match(read, *, confidence=None, observed_make=None, observed_colour=None, registry=None, strategy="position", model="logreg", top_k=10) -> tuple[list[Candidate], BlockingStats]` — retrieve via `blocking.candidate_plates`, score, rerank with the chosen combiner, calibrate, decide.
- `reverse_match(plate, captures, ...) -> list[Candidate]` — the "vice-versa" direction: given a registered plate, rank camera reads (strings from CAM) that could be it, same scoring.
- Combiners, chosen by `model=`: `rule` (the existing plate_resolve logic, for comparison), each single measure, `weighted` (a fixed hand-weighted sum you justify), `logreg` (scikit-learn LogisticRegression; expose coefficients so they can be read aloud), `gbt` (GradientBoostingClassifier). Train both learned models in `scripts/evaluate_matching.py` and save to `data/match_model.joblib` with the feature list, split seed and train metrics; `plate_match` loads it lazily and degrades to `weighted` with a clear reason if it is missing or scikit-learn is absent.
- Calibration (p.81): fit isotonic or Platt on a validation fold; choose two thresholds from validation: ACCEPT only above a precision target (>= 0.98), REJECT below a recall-safe floor, REVIEW in between. Save thresholds with the model.

## 3. Evaluation — `scripts/evaluate_matching.py` -> `docs/MATCHING_EVALUATION.md`
Split **by true plate** (no vehicle in both train and test), fixed seed, 60/20/20 train/validation/test. Hard negatives (p.78) = the non-true candidates blocking retrieves. Simulate camera context for labelled rows from the registration with documented noise: make correct 85%, colour correct 75% (perceived colour drifts), missing 10%; fixed seed.
Report on the **tes

[… 2450 characters omitted …]

### Prompt 7 — 2026-10-17 08:48 UTC — sub-agent task

**Task given to a coding sub-agent** (Build RDR cleansing engine, model opus):

Repo: C:\Users\siddh_ygv5bws\IIA-vehicle-data-federation\IIA_Project (git, main). Read docs/superpowers/plans/2026-10-17-phase2-noisy-matching.md workstream E, and docs/AI_CHANGES_PHASE2.md. You implement **workstream E**: cleansing noisy recorded data with Ripple-Down Rules, for rubric 2: "Cleanse the recorded data w.r.t. the image data and other contextual information so that there are no anomalies (including the noise) in the recorded data." The examiner will test it by inserting, deleting and modifying records live.

Spec is the Week 8 RDR deck. Find its extracted text with:
`Get-ChildItem "C:\Users\SIDDH_~1\AppData\Local\Temp\claude" -Recurse -Filter "Week-8-RDR.txt" | Select -First 1 -ExpandProperty FullName`
Read slides 8-32 fully: the six failure modes of dirty data, lexicons and pattern mining, feature extraction (surface + shape), sub-pattern mining, "from pattern frequency to a decision rule", `if condition then conclusion [because case] except …`, the knowledge-acquisition process, rule organisation, and the RDR tree summary ("each node will have three steps"). Implement what the deck describes and cite slide numbers in docstrings.

Other agents are working on `mediator/confusion.py`, `mediator/plate_match.py`, `scripts/evaluate_matching.py` right now — do NOT edit those, `mediator/similarity.py`, `mediator/blocking.py`, or `app/`. For plate correction, call `mediator.plate_match.match(read, confidence=..., observed_make=..., observed_colour=...)` if it imports, and fall back to `mediator.plate_resolve.resolve` if it does not (lazy import inside a function; no hard dependency).

Understand the data and plumbing before coding: `sources/cam/schema.sql`, `sources/reg/schema.sql`, `mediator/core.py::run_global_query` and the profile it returns, `mediator/executor.py::execute_federated_plan`, `mediator/catalog.py`, `sources/wrapper_template.py` ADMIN_ACTIONS and `/admin/sql`, `mediator/challan_guard.py` (it already reads CAM sightings through the mappings via `sightings_for`, reuse that pattern), `mediator/transforms.py`.

## Deliverables
1. `mediator/rdr.py` — a general RDR engine:
   - `@dataclass Rule`: id, condition (a small declarative predicate over a case dict, serialisable to JSON — e.g. `{"field": "observed_colour", "op": "in_lexicon", "value": "colour_variants"}` and boolean `all`/`any`), conclusion (dict of field -> corrected value or action such as `quarantine`), `cornerstone` (the case that created it), `because` (one-sentence reason), `except_` (child rules), `if_false` (sibling for the false branch) — whichever structure the deck uses (single-classification RDR: true-branch exception, false-branch alternative). Match the deck.
   - `RDRTree.evaluate(case) -> (conclusion, path)` returning the conclusion of the last satisfied rule and the full path of rules tried, so the GUI can show *why*.
   - `RDRTree.add_exception(case, wrong_rule_id, new_condition, new_conclusion, because)` — knowledge acquisition: a new rule is only ever added as an exception under the rule that fired wrongly (or on the false branch), never by editing an existing rule; it must not change the conclusion for the cornerstone cases of other rules — check that and refuse with a clear message if it would (the deck's validation step).
   - Load/save to `data/rdr_rules.json`.
2. `mediator/lexicons.py` — lexicons and pattern mining from live registration data: canonical makes, models, colours from REG (via the federation, not by opening the DB), variant maps (e.g. `Grey/Gray/Gry`, `Maruti/Maruti Suzuki/MARUTI-SUZUKI`, `Silver/Silvr`), plate shape patterns (`LLDDLLDDDD` etc.) and their frequencies (sub-pattern mining, slides 21-23). Deterministic.
3. `mediator/cleansing.py` — the workflow:
   - `detect(record, profile) -> list[Anomaly]` for a camera capture: plate not registered; plate shape invalid; make/model/colour not in lexicon or disagreeing with registration; OCR confidence below floor; impossible travel (reuse `mediator/travel_check.py`); duplicate capture (same plate, camera, time); timestamp unparseable or in the future relative to `REFERENCE_TODAY`. Each `Anomaly` has a code, field, observed value, evidence, and severity.
   - `propose(record) -> Proposal` runs the RDR tree on the record enriched with features and contextual evidence (registered make/model/colour, matched plate candidates with probabilities, confidence), giving per-field corrected values, the rule path, and the evidence. A plate correction must cite the matcher's candidate and probability; if the matcher says REVIEW, the proposal is `quarantine`, never a guess.
   - `apply(proposal, actor)` writes the correction **through the CAM wrapper's `/admin/sql`** (an UPDATE by capture_id, building SQL from the mapping registry the way the rest of the code does — no hard-coded table/column names in cleansing.py if the registry can supply them; if you must name a column, put it i

[… 2330 characters omitted …]

### Prompt 8 — 2026-10-17 09:31 UTC — student

```text
Did we have to reproduce evrything from the sldie go through only what the rubric demands, tell me what the plan is what we are planning to do for our data and what works and how we are going to show it
```

### Prompt 9 — 2026-10-17 09:50 UTC — student

```text
so what rules worked and what combinations worked, how did you implement them, and what were the differences from the prior that you refined
```

### Prompt 10 — 2026-10-17 10:10 UTC — student

```text
So is the project done, how do we test it as a demo?
```

### Prompt 11 — 2026-10-17 10:11 UTC — sub-agent task

**Task given to a coding sub-agent** (Build phase-2 GUI pages, model opus):

Repo: C:\Users\siddh_ygv5bws\IIA-vehicle-data-federation\IIA_Project (git, main). Read docs/superpowers/plans/2026-10-17-phase2-noisy-matching.md (workstream F and section 5 "What the examiner will try"), docs/MATCHING_EVALUATION.md, and docs/AI_CHANGES_PHASE2.md. Phase-2 evaluation is in class on 21 Oct: the examiner uploads a plate image, asks for approximate matches both ways, and then inserts, deletes and modifies records to test cleansing live. You build the **GUI for all three rubrics**, plus two small backend pieces.

Backend you must reuse, never reimplement: `mediator/plate_ocr.py::read_plate(image_bytes) -> list[PlateGuess]` (inspect PlateGuess fields; note the benchmark found its built-in repair step matters, and `data/match_benchmark.csv` uses the RAW read — show both raw read and repaired guesses), `mediator/plate_match.py::match(read, confidence=, observed_make=, observed_colour=, registry=, ...) -> (list[Candidate], BlockingStats)` and `reverse_match(plate, captures, ...)`, `mediator/blocking.py::candidate_plates` and `BlockingStats`, `mediator/cleansing.py` (`scan`, `detect`, `propose`, `apply`, `undo`), `mediator/rdr.py` (`RDRTree.evaluate`, `add_exception`, load/save `data/rdr_rules.json`), `mediator/lexicons.py`, `scripts/cleanse.py` (read its functions for the call patterns). The registry for matching must come LIVE from the REG wrapper through the federation (as cleansing does), never by opening reg.db, so examiner inserts/deletes show up.

The GUI is the Streamlit app in `app/`: read `app/app.py` (sidebar navigation `PAGES`, `?page=` deep links), `app/theme.py`, `app/components.py` (`page_head`, `panel`, `section`, `group_label`, `kpi_row`, `styled_table`, `chip`, `stepper`), and two existing pages as style reference (`app/tabs/challan_guard.py`, `app/tabs/investigate.py`). Follow the established rules: import tabs as `from tabs.x import …` (never `from app.tabs`), every widget has a unique `key=` with a page prefix, forced-light civic theme, one `section()` per block, no `st.subheader`/caption-as-heading (`tests/test_tabs_render.py` enforces this), status colours via `chip()`.

## Pages (add to `PAGES` in a new sidebar group "Data quality", after the existing groups; update the group-heading positions in theme.py accordingly)
1. **Plate Match** (`app/tabs/plate_match_lab.py`, keys `pm_`) — rubric 1.
   - Input: upload a plate image (png/jpg) OR pick one of the benchmark images from `data/plate_images/` via a selectbox (filter by degradation and severity; show the true plate for benchmark images so the result can be checked) OR type a read. Optional observed make and colour.
   - Show: the image, OCR raw read + confidence and the repaired guesses; the blocking funnel (`BlockingStats`: registry size -> candidates after each filter, elapsed ms); the ranked candidates table with every measure's score side by side, the calibrated probability, decision chip (ACCEPT/REVIEW/REJECT) and reasons; the chosen model name. A one-line "why this algorithm" panel linking to the Evaluation page.
   - Reverse direction ("vice-versa"): input a registration number -> every CAM capture whose read could be it (`reverse_match` over live CAM captures fetched through the federation), ranked with probability and decision, showing capture id, camera, time, observed make/colour.
2. **Data Cleansing** (`app/tabs/cleansing_tab.py`, keys `dc_`) — rubric 2.
   - KPI row: records scanned, anomalies before, auto-fixed, quarantined, flagged as clone evidence, **unhandled (must be 0)**, and after-resolution remaining.
   - Anomaly table from a live `scan()` with a Re-scan button (no caching of source data; a fresh scan reflects examiner inserts/deletes/edits).
   - Per-record panel (pick capture id): anomalies, the proposal per field with the RDR rule path rendered with `stepper()` and the `because` text and cornerstone case; buttons Apply, Undo (from the cleansing log), and **Resolve** (see backend 2).
   - Bulk: "Apply all automatic fixes" with confirmation via a checkbox (no browser dialogs).
   - Teach panel: add an RDR exception under a chosen rule for the current case (condition/conclusion as simple form fields that build the JSON), showing refusals verbatim (cornerstone protection).
   - The RDR tree rendered readably (indented rules with ids, condition, conclusion, cornerstone id).
   - **Examiner toolbox** panel: insert a camera capture (plate, camera, time, make, model, colour, confidence) through the CAM wrapper's `/admin/sql` or the existing `sight` admin action; modify a field of a capture; delete a capture — each followed by an automatic re-scan, so the effect is visible immediately. Reuse the existing admin-call helpers in `app/tabs/source_editor.py` / `sql_console.py` rather than writing new HTTP code.
3. **Matching Evaluation** (`app/tabs/matching_eval.py`, keys `me_`) — the justification on screen: render `docs/MATCHING_EVALUAT

[… 4020 characters omitted …]

### Prompt 12 — 2026-10-17 10:33 UTC — student

```text
can you ensure that all the code that you are changing with respect to the working of the project since the last time is commented and not deleted pls
```

### Prompt 13 — 2026-10-17 10:35 UTC — student

```text
in the meanwhile, give me an overall overview of our project, what have we tried to do, whats different since last time, a small demo to see it, what combinations are we using, start with the problem statement, go over the rubrics atleast 1 and 2, tell me what the functionalities are with respect to this assignment, from the slides what methods have we implemented and how do I cross-check them?
```

### Prompt 14 — 2026-10-17 10:43 UTC — student

```text
what is top-1 correct, accepts safely and sent to review? so after using string match we do logistic regression, and create a probability which an engine then decides to accepts as a match, match being a noisy car plate to an actual car plate?
```

### Prompt 15 — 2026-10-17 14:20 UTC — student

```text
Ok can you refine the gui to make it as accessible? Make it so that it is clear what combination or method is used, how the architecture works is clear, i want to see how modifying data would work, stress test the system using different scenarios, i could t really see the matching happen worh our database because they were all down, need to fix that, need to see for a given number playe, what the separation between ocr and real measuring is, explainer on how it works, different test scenarios in the gui, cleaning vlaidation all showed and as many details to make it clear that the thing works as possible
```

### Prompt 16 — 2026-10-17 14:22 UTC — sub-agent task

**Task given to a coding sub-agent** (Backend: self-heal, scenarios, validation, model opus):

Repo: C:\Users\siddh_ygv5bws\IIA-vehicle-data-federation\IIA_Project (git, main). Phase-2 evaluation is in class on 21 Oct (approximate plate matching from images; cleansing noisy records; examiner inserts/deletes/modifies data). Read docs/superpowers/plans/2026-10-17-phase2-noisy-matching.md, docs/AI_CHANGES_PHASE2.md, docs/MATCHING_EVALUATION.md first. You build the BACKEND for a GUI refinement; a second agent is building the GUI pages in parallel and will call exactly the interfaces below. Do NOT edit anything under `app/tabs/` or `app/theme.py` or `app/components.py` (theirs). You own `app/app.py` only for item 1.

## 1. Self-healing local sources (real bug, fix first)
Root cause (verified): `app/app.py::ensure_cluster_running` is `@st.cache_resource`, so it runs once per server; `sources/server_manager.py::start_all` skips any port already in use at that moment. When the website was started while another process (a test run) held ports 8001-8005, it skipped all five; when that process exited, the website was left with no wrappers and every source showed DOWN forever.
Fix:
- In `sources/server_manager.py` add `ensure_local_sources(catalog: dict, include_puc=True) -> dict[str, str]`: for every source whose catalog base_url host is 127.0.0.1/localhost, if its port is not answering `/health`, (re)start it in-process; never start anything for a source pointed at another machine. Return per-source status: `running`, `started`, `remote`, `failed: <reason>`. Also drop dead `WrapperServerThread` entries from `self.servers` so a restart is possible.
- In `app/app.py`: keep the cached cluster object, but call `ensure_local_sources(get_source_catalog())` on every rerun (cheap: a 0.2 s TCP check per local port, skip /health if port open). Expose the status in `st.session_state["source_status"]` for the GUI agent's sidebar.
- Test: start the app's cluster logic with a port held by a dummy socket, release it, call ensure again -> the wrapper is started. Tests in `tests/test_server_manager_heal.py`; use free high ports or the existing `local_cluster` fixture pattern; never leave servers running.

## 2. Scenario runner — `mediator/scenarios.py`
A stress-test / demo harness the GUI renders as a "Test scenarios" page. Interface (the GUI agent codes against this, keep it exact):
```python
@dataclass
class Step: title: str; detail: str; ok: bool | None   # None = informational
@dataclass
class ScenarioResult:
    key: str; title: str; rubric: str            # "1", "2" or "1+2"
    expected: str; actual: str; passed: bool
    steps: list[Step]; data: dict                # data: tables/numbers for the GUI (json-serialisable)
    elapsed_ms: float; restored: bool            # True once any data change was undone
SCENARIOS: dict[str, dict]   # key -> {"title","rubric","what","why"} metadata for listing
def run_scenario(key: str, *, registry=None) -> ScenarioResult
def run_all(keys=None) -> list[ScenarioResult]
```
Scenarios (each must restore any data it changes, through the same admin path, and set `restored=True`; read the registry and captures LIVE through the federation like `mediator/cleansing.py` does):
1. `clean_image` — a clean benchmark image -> OCR -> ACCEPT of the true plate.
2. `ocr_zero_o` — `DLO1AB1234` -> DL01AB1234 ACCEPT; show the char diff.
3. `ocr_b8` — `DL05CD9B76` -> DL05CD9876 ACCEPT.
4. `two_errors_rotated` — benchmark `HR26EF4455__rotate_s2.png` (OCR reads 2 wrong chars) -> still ACCEPT.
5. `unreadable_image` — a severity-3 occlude/motion image -> REVIEW or REJECT, never a wrong ACCEPT.
6. `unregistered_plate` — `DL09QQ9009` -> REJECT.
7. `ambiguous_twins` — insert, via REG admin, a second registered plate one confusable character away from an existing one (e.g. the 0/O twin), then match the ambiguous read -> REVIEW (not ACCEPT); then delete the inserted plate; restored.
8. `context_breaks_tie` — same twins, but with observed make/colour matching only one -> ACCEPT of that one; restored.
9. `reverse_search` — DL05CD9876 -> captures 911 and 916 ACCEPT.
10. `examiner_insert_dirty` — insert a CAM capture with colour `Blu` -> scan detects it, R1 proposes Blue -> apply -> rescan clean -> undo -> delete; restored.
11. `examiner_modify_conflict` — modify that capture's colour to contradict REG -> flagged as clone evidence -> restore.
12. `examiner_delete` — delete the inserted capture -> gone from scan.
13. `misread_plate_cleansed` — a CAM capture with a misread plate (insert one, e.g. `DL05CD9B76`-style for a real registered vehicle) -> proposal corrects it citing matcher probability -> apply -> undo -> delete.
14. `source_down_refusal` — stop the local INS wrapper (only if it is local; otherwise skip with passed=None-equivalent explained) -> Challan-Guard-style decision refuses (UNDETERMINED/HOLD) -> restart it; restored.
15. `batch_stress` — N (default 100, parameter) random benchmark rows (raw OCR reads, fixed se

[… 4057 characters omitted …]

### Prompt 17 — 2026-10-17 14:22 UTC — sub-agent task

**Task given to a coding sub-agent** (GUI: explainers, separation, scenarios, model opus):

Repo: C:\Users\siddh_ygv5bws\IIA-vehicle-data-federation\IIA_Project (git, main). Phase-2 evaluation is in class on 21 Oct. The user (a student) tried the new pages and said: make the GUI as accessible as possible; make it obvious which combination of methods is used and how the architecture works; show how modifying data works; stress-test the system with different scenarios in the GUI; for a given number plate show the separation between the OCR read and the real plate (and how well the matcher separates the right vehicle from the rest); an explainer on how it works; cleansing validation shown; as many details as possible to make it clear that it works.

Read first: docs/superpowers/plans/2026-10-17-phase2-noisy-matching.md, docs/MATCHING_EVALUATION.md, docs/BENCHMARK_NOISE.md, docs/AI_CHANGES_PHASE2.md (Workstream F describes the existing pages), the existing pages `app/tabs/plate_match_lab.py`, `app/tabs/cleansing_tab.py`, `app/tabs/matching_eval.py`, `app/app.py` (PAGES, nav groups), `app/theme.py`, `app/components.py`. Look at the current screenshots `docs/screenshots/plate-match.png`, `cleansing.png`, `matching-eval.png` with the Read tool.

A backend agent is working IN PARALLEL and will deliver these interfaces; code against them, import lazily, and show a clear "not available yet" panel if an import fails so your pages never crash:
- `mediator/scenarios.py`: `SCENARIOS: dict[key, {"title","rubric","what","why"}]`, `run_scenario(key, *, registry=None) -> ScenarioResult`, `run_all(keys=None)`. `ScenarioResult` fields: key, title, rubric, expected, actual, passed, steps (list of Step: title, detail, ok: bool|None), data (json-serialisable dict of tables/numbers), elapsed_ms, restored. Keys: clean_image, ocr_zero_o, ocr_b8, two_errors_rotated, unreadable_image, unregistered_plate, ambiguous_twins, context_breaks_tie, reverse_search, examiner_insert_dirty, examiner_modify_conflict, examiner_delete, misread_plate_cleansed, source_down_refusal, batch_stress, rdr_teach_refusal.
- `mediator/cleansing_validation.py::validate(n, seed, local_db) -> dict` and its saved output `data/cleansing_validation.json` with keys overall, per_type (type -> detection recall/precision, correction accuracy, quarantine rate, wrong corrections), examples (type, original, noisy, detected, proposed, rule_path, correct), seed.
- `mediator/change_audit.py`: `capture_row(capture_id)`, `registration_row(plate)` (raw source rows read live), `last_admin_statements(limit)` (the exact SQL the mediator sent to sources).
- `st.session_state["source_status"]`: per-source `running | started | remote | failed: <reason>` set by app.py on every rerun (the backend agent owns app.py's cluster logic; you may only edit `PAGES`/nav entries in app.py, and must coordinate by touching only those lines).
Do NOT edit mediator/, sources/, scripts/ (backend's), except reading.

## What to build (all forced-light civic theme, existing primitives, unique `key=` per widget, one `section()` per block, no st.subheader / caption-as-heading — tests/test_tabs_render.py enforces this)
1. **"How it works" page** (`app/tabs/how_it_works.py`, keys `hw_`), first page of the "Data quality" group. A clear architecture explainer:
   - A pipeline diagram drawn with Graphviz via `st.graphviz_chart` (built-in, no new dependency): Image -> OCR (EasyOCR) -> raw read + confidence -> Blocking (q-gram inverted index -> size filter -> prefix filter -> position filter; slide pages) -> Candidates -> Feature vector (10 string measures + make/model/colour agreement + OCR confidence) -> Logistic regression (Platt-calibrated) -> probability -> ACCEPT/REVIEW/REJECT; and a second diagram for cleansing: CAM record -> detect anomalies vs live REG -> RDR tree (rules with cornerstones) -> proposal -> apply via CAM wrapper /admin/sql -> audit log -> undo/resolve. Show where the federation (wrappers on 5 laptops) sits.
   - "The combination we use, and why" in plain sentences with the real numbers read from docs/MATCHING_EVALUATION.md (parse it; do not hard-code numbers): winner, its top-1, ACCEPT precision/recall, REVIEW rate, the best single measure, and the ablation (context is the biggest gain).
   - A table "Method -> slide page -> where in the code -> what it contributes", covering every implemented method (edit distance p.16-19, Jaro/JW p.20-22, overlap/Jaccard p.24-26, TF/IDF p.27-31, Ratcliff-Obershelp p.33-34, Soundex p.36-37, 2-step framework p.44, inverted index/size/prefix/position p.45-65, pair classification/feature vector/GBT/calibration/separate evaluation p.69-82, Week 8 RDR slides 8-32).
   - A glossary of terms a non-expert needs (top-1, ACCEPT/REVIEW/REJECT, calibration, blocking, q-gram, cornerstone case, precision, recall).
2. **Plate Match page — add a "Separation" section** (edit `plate_match_lab.py`):
   - For the chosen plate/image: the true plate (benchmark) vs the raw OCR re

[… 4964 characters omitted …]

### Prompt 18 — 2026-10-17 15:01 UTC — sub-agent task

**Task given to a coding sub-agent** (RDR colour veto exception, model opus):

Repo: C:\Users\siddh_ygv5bws\IIA-vehicle-data-federation\IIA_Project (git, main). Small, precise fix for rubric 2 (cleansing). Read docs/CLEANSING_VALIDATION.md (section "Every wrong correction"), mediator/cleansing.py, mediator/rdr.py, data/rdr_rules.json, mediator/cleansing_validation.py, docs/AI_CHANGES_PHASE2.md (Workstream E and G sections).

## The defect (found by the validation)
Noise type `plate_misread_double`: noisy read `UP16ABO524` (truth `UP16AB0521`) was corrected by rule R5 to `UP16AB0524` because the matcher ACCEPTed it (p=0.948). That candidate is registered colour Grey (make not on record); the camera saw Red. The proposal context only carries `match_best_make_agrees`, so a contradicting colour could not veto the correction. Wrong plate corrections must be 0.

## The fix — RDR knowledge acquisition, exactly as the Week 8 deck prescribes
1. In `mediator/cleansing.py`, add the missing context features next to `match_best_make_agrees`: `match_best_colour_agrees` and `match_best_model_agrees` (True / False / None when either side is unknown; colour compared through the lexicon's canonical colour form so `Gray`/`Grey` agree). Additive change only.
2. Add an RDR exception under R5, created through the engine's own `RDRTree.add_exception` (not by hand-editing JSON), so the cornerstone protection check runs: condition "the accepted candidate's registered colour disagrees with the observed colour, or its registered model disagrees" (use `False` explicitly, not None, so unknowns do not veto), conclusion `quarantine`, because "a plate correction is only made when nothing the camera saw contradicts the candidate's registration", cornerstone = the validation case above (store it as a full case dict with its provenance `cleansing_validation seed 20261017`). Write a tiny script or test fixture that performs the add and saves `data/rdr_rules.json`; commit the resulting rules file. If the engine refuses (because it would change another rule's cornerstone conclusion), report the refusal verbatim and stop rather than force it.
3. Re-run `python scripts\validate_cleansing.py` (offline `--local-db` is fine) and update `docs/CLEANSING_VALIDATION.md` + `data/cleansing_validation.json`. Required: wrong plate corrections = 0, detection recall unchanged, no new false alarms on controls. Report the before/after table (correction accuracy and quarantine rate per type will move; say exactly how).
4. Check the 918 real CAM rows: `python scripts\cleanse.py --scan` (needs wrappers; if ports 8001-8005 are held by the user's running website, use `mediator.cleansing.scan` against the `local_cluster` fixture or the GUI agent's spare-port approach used in `mediator/scenarios.py`). Report how the proposal counts changed (set / quarantine / flag) and confirm capture 916 (`DL05CD9B76` -> `DL05CD9876`, a registered Silver Maruti Swift seen as Silver Maruti Swift) is still corrected.
5. Tests: a unit test reproducing the exact wrong-correction case now yields `quarantine` with the new rule id on the path; capture-916-like case still `set`; `match_best_colour_agrees` canonicalises Gray/Grey; None does not veto. Run `tests/test_rdr.py tests/test_cleansing.py tests/test_cleansing_validation.py` plus `tests/test_scenarios*.py` if it exists (scenarios `misread_plate_cleansed` and `examiner_insert_dirty` must still pass; run them only on free ports — check `Get-NetTCPConnection -State Listen -LocalPort 8001,8002,8003,8004,8005` first; never kill processes you did not start).

## Rules
- Rubric-3 workflow: commit your first version unreviewed (`generated: mediator/cleansing.py — colour/model veto features (unreviewed AI output)`), then the reviewed one (`reviewed: ... — <reasons>`); the rules file and validation outputs in a separate commit. Append a `## Workstream H — colour veto (RDR exception from a validation case)` section to docs/AI_CHANGES_PHASE2.md (append only): the defect, the rule added with its cornerstone, before/after numbers, commit hashes.
- Never delete pre-existing code; any line you replace stays as a comment above with `# PHASE2-REPLACED (<reason>):`.
- Do not edit app/ (another agent is working there), mediator/plate_match.py, or mediator/meta.db's SOURCE_CATALOG.
- No `Co-Authored-By` or any trailer. Do not push.
Final report (<=200 words): commits, the new rule (id, condition, conclusion, cornerstone), before/after validation table, real-scan proposal counts before/after, tests run.
