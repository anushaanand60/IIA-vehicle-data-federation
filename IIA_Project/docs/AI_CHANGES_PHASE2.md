## Workstream B — `mediator/similarity.py` (string similarity library)

**Prompt (summary).** Implement the Week 6-8 "Data and Text Matching" deck as a pure-function
library: Levenshtein with the DP table and its similarity normalisation (p. 16-19), a
Needleman-Wunsch style weighted edit distance whose substitution cost is a callable, an
OCR-confusion cost built from the existing `plate_resolve.CONFUSABLE` table, Jaro and
Jaro-Winkler (p. 20-22), q-grams with overlap and Jaccard (p. 24-26), TF/IDF cosine over a
corpus (p. 27-31), Ratcliff-Obershelp implemented recursively rather than via `difflib`
(p. 33-34), and Soundex (p. 36-37); plus an `ALL_MEASURES` registry for the evaluation
harness. Cite the slide page in every docstring. Move `matcher.py`'s `jaro_winkler` here so
there is exactly one implementation. Unit tests must encode the deck's own worked examples.

| Commit | What |
|---|---|
| `2974efd` | `generated: mediator/similarity.py (unreviewed AI output)` — first output, committed before it was run |
| `5f3352c` | `reviewed: mediator/similarity.py — ...` — the changes below |

**Changes made in review, and why**

- **Jaro on `("jon", "ojhn")` — the generated test asserted the wrong number.** The first
  version assumed the matching window `max(|x|,|y|)/2 - 1 = 1` could not reach the final `n`
  and predicted 0.722, calling the deck's 0.81 unreproducible. Running it showed the
  implementation returns 0.8056, which *is* the deck's 0.81 (c = 3, t = 2). The test now
  asserts the slide's value. Reason: a wrong hand-calculation would have been written into
  the report as a discrepancy with the course material.
- **Soundex "useless on plates" test asserted a false claim.** The generated test said
  `DL01AB1234` and `DL09KL3321` share a code; they do not (D410 vs D424). Replaced with the
  two real failure modes, both verified: a false positive (`DL01AB1234` and `DL01AB9999` both
  code to D410 because Soundex discards digits) and a false negative (`DL01AB1234` vs
  `DL01AC1234`). Reason: the justification table must report measured behaviour, not expected.
- **TF/IDF violated identity on a degenerate corpus.** With a single-document collection every
  idf is `log(N/N) = 0`, both vectors are zero and the function returned 0.0 for a string
  against itself. Added an explicit identity short-circuit plus a regression test. Reason: the
  evaluation harness compares measures on a common scale and would have scored TF/IDF at 0
  for exact matches on small corpora.
- **Corpus handling tidied.** The document collection was rebuilt and re-tokenised inside a
  membership test (O(n) tokenisation per check); now tokenised once and deduplicated through a
  set. Behaviour unchanged.
- **`_longest_common_substring` return type** annotated `Tuple[int, int, int]` instead of bare
  `tuple`, so the module is fully typed.

**Deliberate deviations from the deck, documented in the code**

- `overlap()` returns the overlap *coefficient* `|Bx ∩ By| / min(|Bx|,|By|)` because the
  harness needs [0,1]; `overlap_count()` keeps p. 25's raw count and is what the slide test
  asserts (`O("dave","dav") = 3`).
- `tfidf_cosine(..., q=None)` tokenises on whitespace, which is what the worked example on
  p. 29-30 does; the default `q=2` is character bigrams, which is what plates need.
- All measures share one empty-string convention (both empty = 1.0, one empty = 0.0) so the
  harness never special-cases a measure.

**Measured effect of the OCR weighting** (the two story misreads):

| Pair | `edit_similarity` | `ocr_edit_similarity` | `jaro_winkler` |
|---|---|---|---|
| `DL05CD9B76` vs `DL05CD9876` (8/B) | 0.900 | 0.980 | 0.960 |
| `DLO1AB1234` vs `DL01AB1234` (O/0) | 0.900 | 0.980 | 0.947 |

---

## Workstream A — labelled noisy benchmark from rendered plate images

**Prompt (summary).** Build a reproducible, labelled benchmark of noisy plate reads so later
workstreams can measure matching accuracy honestly. Render Indian-style plates with Pillow for
registration numbers drawn from `sources/reg/reg.db` (read-only), degrade each with seven
controlled degradations at severities 1-3, run `mediator/plate_ocr.py` (EasyOCR, offline) over
them, and write `data/match_benchmark.csv`. Keep the six story plates in every run; append the
noise we already have (the 47 CAM plates not registered in REG, and the two planted story
misreads). Deterministic for a fixed `--seed`, tests that never call EasyOCR,
`data/plate_images/` out of git, a one-page note in `docs/BENCHMARK_NOISE.md`.

| Module | generated | reviewed |
|---|---|---|
| `scripts/make_plate_images.py` | `efd5479` | `c6d6313` |
| `scripts/run_ocr_benchmark.py` | `9980ca9` | `fea4497` |
| `tests/test_plate_images.py` | `8fd3114` | `52a273e` |

### `scripts/make_plate_images.py` — `efd5479` to `c6d6313`

- **Bug (crash).** `generate()` wrote `path.relative_to(REPO_ROOT)` into the manifest
  unconditionally, so any `--out` outside the repository raised `ValueError` before a single
  image was written — which is every unit-test run, since the tests generate into `tmp_path`.
  Replaced with `_manifest_path()`: repo-relative where possible, absolute otherwise.
- **Correctness (image artefact).** The motion blur averaged `np.roll`-ed copies. `np.roll`
  wraps, so at severity 3 the right-hand plate border was smeared back across the left-hand
  characters — a wrap artefact, not motion. Now pads with `mode="edge"` and crops back.
- **Performance.** The low-light degradation built its sensor noise with a Python list
  comprehension of `rng.gauss()` — 62 400 calls per image, 300 images in a full run. Replaced
  with a `numpy.random.default_rng` seeded from the same per-image key, so determinism is
  unchanged and generating 2 200 images takes 25 s instead of minutes.
- **Correctness (double degradation).** The JPEG degradation decoded a quality-5 re-encode and
  then the writer saved it *again* as JPEG at quality 92, degrading it twice and making the
  severity ladder meaningless. All images are now written as lossless PNG; the JPEG artefacts
  are in the pixels, where they belong.
- **Edge case.** `pick_plates(n)` always prepended all six story plates, so `--plates 2`
  produced six plates and 132 images. A run smaller than the story set now truncates it, which
  is what the fast unit tests need; a real run (`--plates 100`) is unaffected and keeps all six.

### `scripts/run_ocr_benchmark.py` — `9980ca9` to `fea4497`

- **Bug (resource).** Source databases were opened with `with sqlite3.connect(...) as conn`.
  That context manager commits a transaction; it does **not** close the handle. On a read-only
  source DB that is exactly the thing the project promises not to do. Switched to
  `contextlib.closing`.
- **Bug (crash).** `summarise()` divided by `len(labelled)` with no guard, so a run over an
  empty or fully-observed manifest died with `ZeroDivisionError` *after* the expensive OCR pass
  had already completed.
- **Style/correctness.** `import sys` and the `sys.path` insertion sat in the middle of the
  file, after `REPO_ROOT`. Hoisted, and made idempotent.
- **Course-method compliance (the important one).** The generated version reported only the raw
  exact-read rate. EasyOCR misreads the digit `0` in `DL01` as the letter `O` on nearly every
  rendered image, so *every* bucket reported 0.0% and the degradation ladder was invisible. The
  reviewed version reports, per bucket, the raw exact-read rate **and** the rate after
  `plate_ocr.normalise_candidates` repair, plus the count of images OCR returned nothing for.
  The gap between those two columns is precisely the quantity workstreams B and C have to
  improve on, and it is the honest way to show a systematic confusion rather than average it
  away.
- **Design decision kept from the generated version, deliberately.** `ocr_read` is the *raw*
  joined reading, not `read_plate()`'s output. `read_plate` applies pattern-licensed confusion
  repair and returns a single detection box; baking either into the labels would measure the
  repair we are supposed to be evaluating.

### `tests/test_plate_images.py` — `8fd3114` to `52a273e`

- **Coverage gap.** The determinism test ran at severity 2 only and compared images held in
  memory. The committed artefact is what `generate()` writes to disk, so a second test runs the
  whole generator twice and compares every file byte for byte, and the first is parametrised
  over all three severities.
- **Edge case.** Nothing exercised the `arialbd.ttf` fallback, although three of the four
  laptops may not have that font. Added a test that monkeypatches `FONT_PATH` to a missing file
  and still asserts a non-blank plate, plus a plain "the clean render is not a white rectangle"
  check — a silently blank render would have produced a benchmark of 2 200 unreadable images.
- **Specification gap.** The benchmark-CSV test only counted the 49 observed rows. It now
  asserts the two planted story misreads map to the right truth and are marked `exact_match=0`,
  and that the 47 unregistered CAM plates carry *no* verdict at all (`true_plate` and
  `exact_match` empty) — the "unknown truth" invariant the later workstreams depend on.

**Follow-up fix after the first full run** (`7b7277a`): the per-100-image progress line was not
flushed. A full run is ~14 minutes and stdout is block-buffered when redirected to a log, so the
progress output only appeared once the run had already finished. Added `flush=True`.

**The run that produced the committed artefacts.** 100 plates x (1 clean + 7 degradations x 3
severities) = 2 200 images, seed `20261017`, EasyOCR 1.7.2 on CPU, 0.38 s per image, ~14 minutes.
39.5 % exact raw reads, 66.4 % after confusion repair, 47 images OCR returned nothing for. The
full table and the honest limits are in `docs/BENCHMARK_NOISE.md`.

## Workstream D — `mediator/blocking.py` (candidate generation at scale)

**Prompt summary.** Implement step 1 of the slides' two-step framework (Week 6–8, p. 44) as
`mediator/blocking.py`: a `QGramIndex` with the deck's four FindCands variants — plain inverted
index (p. 47–51), size filtering (p. 53–54), prefix filtering over a global token order
(p. 55–58, proof p. 64) and the position index (p. 65) — each recording a `BlockingStats`;
one entry point `candidate_plates(read, registry, strategy=...)`; and `sql_prefilter`, a
length-and-head predicate to push into a source's SQL reusing `mediator/decomposer.py`'s
normalisation expression. Reuse `mediator/similarity.py` for every measure; never reimplement
one. Tests must encode the deck's own worked example (Week 8 slides 1–7) and must prove recall
is 1.0 over 200 real REG plates and all their single-character `CONFUSABLE` corruptions.

**Commits.** generated `76e5b92` → reviewed `93312c5`.

### What changed between the two commits, and why

- **Correctness, recall-losing bound.** The generated `recommended_k` took
  `k = |Gq(query)| − q·max_edits`, i.e. it sized the prefix from the *query's* token count
  alone. The prefix theorem (p. 57, proof p. 64) only preserves pairs whose true overlap is at
  least `k`, so any indexed plate with fewer distinct q-grams than the query — which happens
  whenever a plate repeats a bigram, e.g. `DL01AB1111` — can have a true overlap below that `k`
  and be silently dropped. The reviewed version takes the minimum token count still inside the
  p. 53 size window (bisect over a sorted size list) and derives `k` from that. On the real
  registry this moves `k` from 9 to 7 and costs nothing measurable.
- **Specification gap: the filters did not compose.** Each strategy ran standalone, so on real
  data `prefix` returned 170 candidates where `size` returned 3 — the pruning table was
  non-monotone and told the examiner nothing. The deck composes them ("Prefix + Inverted Index",
  p. 57–63; the position index as a *further* limit, p. 65), so `candidate_plates` now chains
  them and every `QGramIndex` method grew a `restrict` argument so each filter can still be
  demonstrated alone.
- **Efficiency.** The chained pipeline probed the same inverted lists twice (a full
  `candidates()` pass whose result the size filter immediately recomputed). Removing the
  redundant probe and memoising the query's ordered token set per index cut the test file from
  6.5 s to 3.9 s. The memo caches a derivation of the *query*, never a source row, so the
  project's no-cache invariant is intact.
- **Course method ignored.** The generated size filter used only `|nx − ny| ≤ q·delta`. The
  reviewed one also applies the p. 45 inequality `ov_q(s,t) ≥ max(|Gq(s)|,|Gq(t)|) − q·k`, which
  is what actually does the work: it is the difference between 373 candidates and 1.1. The
  Jaccard form of the p. 53 bound (`|x|·t ≤ |y| ≤ |x|/t`) is exposed as
  `jaccard_size_window` and tested against Week 8 slide 3.
- **Injection and dialect bugs in `sql_prefilter`.** The generated version interpolated the
  caller's column name unchecked and silently fell back to `LENGTH` for any unknown dialect —
  `LENGTH` counts *bytes* in MySQL. The reviewed version validates the column against an
  identifier pattern, rejects unknown dialects, uses `CHAR_LENGTH` for MySQL/MariaDB, and
  returns `1=1` rather than a vacuous `LIKE '%'` when the read is shorter than two characters.
- **Documented bound we chose to keep.** `sql_prefilter`'s two-character head is *not*
  recall-safe against an OCR confusion in position 0 or 1 (`DL` read as `0L`). The docstring
  states this and names the alternatives; it is accepted because the state code is the most
  legible part of the plate and because the local q-gram path, which has no such hole, is the
  one used whenever the registry is already in hand.
- **Test bugs found while reviewing.** (a) The deck test asserted slide 4's candidate set using
  the *globally ordered* prefix; slide 4 precedes the ordering introduced on slide 5 and uses
  the tokens as listed, so the expected set was wrong. (b) The pruning test chained
  `candidates_after` monotonically across strategies that were not yet chained, and used the
  story misread `DLO1AB1Z34` — a **two**-confusion read — as if it were one edit. It now uses a
  genuine one-edit read, and a separate test asserts the honest behaviour at the boundary: at
  `max_edits=1` the two-edit read is legitimately excluded, and `max_edits=2` brings it back.

### Measured result (all 605 REG plates, 5 079 single-confusion reads)

| strategy | mean candidates | reduction | recall |
|---|---|---|---|
| `none` (p. 40–43 naive) | 605.0 | 1× | 1.000 |
| `index` (p. 47–51) | 372.6 | 1.6× | 1.000 |
| `size` (p. 53) | 1.1 | 551× | 1.000 |
| `prefix` (p. 55–58) | 1.1 | 551× | 1.000 |
| `position` (p. 65) | 1.1 | 551× | 1.000 |

Standalone (unchained), prefix and position each cut 605 → 85.9 with recall 1.000; on plates the
position bound adds nothing over the prefix bound, which we report rather than hide.

---

## Workstream E — cleansing camera records with Ripple-Down Rules (rubric 2)

**Prompt (summary).** Implement workstream E against the Week 8 RDR deck (slides 8-32): a general
single-classification RDR engine (`if condition then conclusion [because case] except … else …`,
cornerstone cases, knowledge acquisition that only ever adds a rule under the rule that fired
wrongly or on the false branch, and refuses a rule that would change another rule's cornerstone
conclusion); lexicons and plate shape/sub-pattern mining from live REG data through the federation;
anomaly detection on CAM captures (unregistered plate, invalid shape, make/model/colour not in the
lexicon or disagreeing with registration, low OCR confidence, impossible travel, duplicate capture,
unparseable or future timestamp); proposals from the RDR tree that cite the plate matcher's
candidate and probability and quarantine on REVIEW; apply and undo through the CAM wrapper's own
`/admin/sql` with an audit row in `meta.db`; a live scan report; a seed tree with real cornerstones;
a non-interactive CLI; tests including the deck's worked example and the examiner's
insert / modify / delete loop on the `local_cluster` fixture. No edits to the matcher, similarity,
blocking or GUI modules.

| Module | generated | reviewed |
|---|---|---|
| `mediator/rdr.py` | `71d88aa` | `2331b58` |
| `mediator/lexicons.py` | `ec6dd4d` | `e8d91df` |
| `mediator/cleansing.py` | `d65cfba` | `601c44c` |
| `scripts/cleanse.py` | `13d4c09` | `7e0679a` |

Seed tree and tests: `2702f6e` (`data/rdr_rules.json`, `tests/test_cleansing.py`); `tests/test_rdr.py`
was committed with the reviewed engine.

### `mediator/rdr.py` — `71d88aa` to `2331b58`

- **Knowledge acquisition could hang a rule where the case never goes.** The generated
  `add_exception` never checked that the named rule was on the case's own evaluation path, or that
  it *fired* (for an except branch) or *failed* (for an else branch). Slide 26 makes that
  distinction the whole rule ("fires but wrong: except; fails to fire: else"), and slide 29 says the
  trace is what guides where the rule goes. Now refused with a message naming the path.
- **A shadowed rule was silently accepted.** Appending to an existing except chain can put the new
  rule behind an earlier sibling that already catches the case; the rule was saved and never fired.
  The reviewed version re-evaluates the case and refuses unless the new rule is the one concluding.
- **Validation was not atomic.** Any failure during the cornerstone check (or an exception from a
  bad condition) could leave the new node attached; it is now detached on every failure path, and
  a test asserts the serialised tree is identical after a refusal.
- **Malformed conditions raised `KeyError`**; they now raise `ValueError` naming the condition.
- **Efficiency:** lexicon membership rebuilt a lower-cased set on every test of every rule; the
  tree now normalises its lexicons once.

### `mediator/lexicons.py` — `ec6dd4d` to `e8d91df`

- **Resource leak:** an `httpx.Client` was opened per request and never closed.
- **SQL literal bug:** the keyset cursor was rendered with Python `repr`, which is not SQL quoting
  for text keys; replaced by a proper literal (shared with the cleansing writes).
- **A duplicated column name lost its value**; the executor's `table__column` alias rule is reused.
- **Overloaded numbers (slide 12):** edit/prefix guessing mapped model `i10` onto `i20`. Tokens
  containing digits are now never guessed; two-letter tokens (`Re` to Red) are not guessed either,
  and the mined-variant radius now follows token length as the lookup does.
- **Curated aliases overstated the dictionary.** The generated table listed `Gry`, `Slvr`, `Gray`,
  `Maruti`, `Hyundia`, all of which the miner, prefix, abbreviation or edit lookup already derive,
  hiding how much is actually mined. Only non-derivable aliases remain (`Pearl White`, `MSIL`, ...).
- **Sub-pattern frequencies were mined but never used.** `ZZ01AB1234` has a real shape and passed;
  validity now needs both the shape and the slide-21 pattern (`S^+^`) to be attested.

### `mediator/cleansing.py` — `d65cfba` to `601c44c`

- **The matcher adapter was wrong for the real interface.** Workstream C's `plate_match.match`
  returns `(ranked candidates, blocking stats)`; the generated adapter read it as an object with a
  `decision` attribute, so every unregistered read came back with no decision. It now reads the top
  candidate's calibrated decision, and hands the matcher the live REG snapshot (`registry=`) so it
  never opens `reg.db` itself (that file is not on the mediator laptop in the 4-laptop deployment).
  Results are memoised per snapshot, since one scan meets the same misread several times.
- **Future timestamps vs the wrapper's own inserts.** "Future relative to `REFERENCE_TODAY`" flagged
  every capture the CAM wrapper's `sight` admin action inserts (it stamps the real clock), i.e.
  every row the examiner adds through the GUI. The horizon is now the later of the two; the examiner
  loop test asserts a "seen just now" row is not flagged.
- **Duplicates compared ids as text** (`"10" < "9"`), so the wrong copy would have been deleted.
- **A junk `ocr_confidence` crashed `detect`**; values are parsed defensively.
- **Low confidence was double-counted.** It stayed an anomaly after the registration corroborated
  the read (make or colour agree), so "remaining after" never reached zero for corrected plates.
  It is now flagged only for uncorroborated reads, mirroring rules R7/R8.
- **Taught rules could break the two invariants.** A rule could rewrite a plate when the matcher
  said REVIEW, or `set` a NULL; both are now downgraded to `quarantine` in code, whatever the tree
  says (tested by teaching exactly such a rule).
- **Undo was unsafe:** no optimistic check (it would overwrite an operator's later edit) and it could
  run twice; both now refuse. **Delete** logged only the mapped columns, so undo lost `camera_id`;
  the full row is fetched before deleting. Apply logs the value the source actually stores (its own
  plate rendering), which is what undo must match.
- **meta.db connections were never closed** (`with sqlite3.connect()` only commits).
- **A dead source crashed the scan;** it now returns `UNAVAILABLE` with the reason (the phase-1
  refuse-don't-crash rule).
- **Added:** each anomaly left after the proposals carries a disposition: `flagged` (clone evidence
  that cleansing must not erase), `quarantined` (a person decides) or `unhandled` (the number that
  must be 0).

### `scripts/cleanse.py` — `13d4c09` to `7e0679a`

- Unknown capture ids, an unreachable source, an RDR refusal or bad JSON printed a traceback; now
  one `error:` line and exit code 2.
- `--teach` with missing flags crashed in `json.loads(None)`; it now names every missing flag.
- `--scan` printed raw JSON only; it now prints the counts before / proposed / remaining / by
  disposition and a per-record line, and a refused scan exits non-zero.

### Deliberate choices, documented in the code

- **One tree, evaluated once per field** (plate, make, model, colour, timestamp, record), the way
  slide 30 labels each token by walking the same chain.
- **Clone evidence is flagged, never "cleansed".** A valid make or colour that contradicts the
  registration is the phase-1 cloned-plate signal; overwriting it with REG's value would destroy the
  evidence (slide 23: context, not the pattern, decides).
- **Seed cornerstones.** R0, R4, R5 and R6 are unmodified CAM captures (77, 918, 916, 914). CAM's
  text fields contain no natural spelling noise, so R1-R3 and R7-R11 use a real capture with one
  field perturbed exactly as the examiner would; each cornerstone records its provenance.
- **Confidence floor 0.70**, from the benchmark: the exact-read rate is at most 32 % below it and at
  least 42 % above it.

### Measured on the local CAM data (918 captures, live through the wrappers)

| | count |
|---|---|
| anomalies before | 216 (52 unregistered plate, 40 colour / 39 model / 35 make disagreeing with REG, 36 invalid shape, 12 low confidence, 2 impossible travel) |
| proposals | 34 set (plate corrections the matcher ACCEPTs), 18 quarantine, 116 flag |
| remaining after applying | 137 = 116 flagged clone evidence + 21 quarantined + **0 unhandled** |

Known limit: a full scan calls `plate_match.match` once per distinct unregistered read; at roughly
0.5 s a call (it rebuilds its TF/IDF corpus each time) a full scan takes 10-25 s on this laptop.


## Workstream C — learned confusion costs, the combined matcher, and the evaluation

**Prompt summary.** Implement workstream C plus the evaluation harness from B (rubric 1: "apply
the matching algorithm which gives the best accuracy and justify why"). Reuse
`mediator/similarity.py`, `mediator/blocking.py` and `mediator/plate_resolve.py`, never
reimplement them. (1) `mediator/confusion.py`: substitution costs estimated from the benchmark's
**training split only** (1 − normalised frequency, floored, Laplace-smoothed, symmetric), saved to
`data/confusion_costs.json`, hand table for unseen pairs. (2) `mediator/plate_match.py`: a
`Candidate` dataclass, the p. 71 feature vector, `match()` = blocking retrieve → score → rerank
with a chosen combiner (`rule`, any single measure, `weighted`, `logreg`, `gbt`) → calibrate
(p. 81) → ACCEPT / REVIEW / REJECT, and `reverse_match()` for registration → camera reads; lazy
model loading with a fallback to `weighted`. (3) `scripts/evaluate_matching.py`: split by true
plate 60/20/20 with a fixed seed, hard negatives from blocking (p. 78), simulated camera context,
retrieval and scoring evaluated separately (p. 82), per-degradation table, ablation, observed
rows, and a viva-ready Decision section in `docs/MATCHING_EVALUATION.md`. (4) Fast tests. Mid-task
the coordinator added: report retrieval recall at max_edits 1–3 and choose max_edits on
validation for recall ≥ 0.99; widen blocking when nothing survives; then, from the cleansing
workstream, near-zero probabilities coming back REVIEW and ~0.5 s per `match()` call.

**Commits.**

| file | generated | reviewed |
|---|---|---|
| `mediator/confusion.py` | `65beee4` | `dff6b33` |
| `mediator/plate_match.py` | `d1c773e` | `25e5bd8`, second pass `9d4eacf` |
| `scripts/evaluate_matching.py` | `f800bff` | `c7fc071` (with the doc, model and costs) |

### `mediator/confusion.py` — what changed and why

- **Wrong formula, measured.** The generated estimate divided `n(a,b)+1` by `n(b)+36` (Laplace
  over the whole alphabet). With 16 observations of 5/S that gave cost 0.71 — *more* expensive
  than the hand table's 0.2 — and left every real confusion except 0/O at ~0.9, i.e. barely
  cheaper than an arbitrary substitution. Replaced by an add-one, log-normalised frequency,
  `1 − log(1+n)/log(1+n_max)`, floored at 0.1; log because the counts are heavy-tailed (0/O alone
  is 62 %). 5/S now costs ~0.52, H/4 ~0.61, 0/O 0.1.
- **Leakage of demo fixtures.** The two planted story misreads (`degradation=observed`) were
  counted as noise samples when their plate fell in the training split. Excluded.
- **Stale cache.** Costs were cached on first use and never invalidated, so a process that
  re-estimated them kept scoring with the old (or empty) table. `save_costs` now calls
  `reload_costs()`; a version counter lets callers drop memoised scores.
- **Default bound at import.** `COSTS_PATH` was a default argument, so tests could not redirect
  it; resolved at call time now.
- **Counts and costs returned together**, so the JSON cannot record counts made under a
  different filter from the costs it stores.

### `mediator/plate_match.py` — what changed and why

First review (`25e5bd8`):

- **Silent REJECT on noisy reads.** When nothing survived blocking (a read with more errors than
  `max_edits`) the generated code returned no candidates. Now `retrieve()` widens to
  `max_edits+1`, then a full scan, and says so in the reasons (coordinator's request).
- **A failed model load was cached forever**, so a bundle written later in the same process was
  never used. `reload_bundle()` added; the evaluation calls it after saving.
- The `rule` contender ran blocking it ignored and dropped the fallback notes; `reverse_match`
  ignored the validated `max_edits` and could fall back to a forward match per capture; empty and
  one-character reads were not handled; the reason text did not say when a close rival forced
  REVIEW. String and context features split so the pair half can be memoised.

Second pass (`9d4eacf`, prompted by the cleansing workstream):

- **~0.5 s per call.** Profiling showed TF/IDF dominating: `similarity.tfidf_cosine` re-tokenises
  all 605 plates and recounts document frequencies on *every pair* (2–5 ms a pair), the q-gram
  index was rebuilt per call, and the shortlist ran the 120 µs confusion distance over ~320
  candidates. Fixes: `CachedTfidf` computes the corpus statistics once and is asserted equal to
  `tfidf_cosine` (to 1e-12, including its quirk of appending the two strings to the collection);
  the index and IDF are cached under a SHA-1 of the registry's plate list, so an examiner's insert
  or delete changes the key and forces a rebuild (no stale source data; make/colour are never
  cached); a pair-feature memo keyed on that hash and on `confusion.costs_version()`; a Jaccard
  pre-filter to 100 before the distance shortlist (same shortlist recall on test within one read).
  Timing, same 10 reads, idle machine: **38.6 ms → 17.6 ms** per call with cold pair memo,
  **9.7 ms** warm (CPU time 34.4 → 17.2 / 9.4 ms); the coordinator's 0.5 s was measured while the
  machine was saturated by another process, where the old code took 529 ms.
- **REVIEW for p ≈ 0.002.** Root cause was in the evaluation's threshold choice (below); the
  matcher now gets a REJECT floor ≥ 0.05 from the bundle, and a test asserts that reads with no
  plausible candidate are REJECT and that every REVIEW candidate clears the floor.

### `scripts/evaluate_matching.py` — what changed and why

- **Degenerate thresholds (correctness).** The saved logistic-regression and GBT thresholds were
  `accept 0.0, reject 0.0`: the "winner 0.1 clear of the runner-up" rule alone already met the
  0.98 precision target at t = 0, so ACCEPT ignored the probability and REJECT could never fire —
  a threshold "chosen without calibration" in all but name, exactly the failure the plan predicted.
  ACCEPT now also needs calibrated p ≥ 0.5; REJECT is below max(1st percentile of correctly ranked
  reads' p, 0.05). The cost is reported per contender as `right lost` (2.7 % for logreg).
- **Spec items silently missing.** The observed CAM rows were featurised but never reported; the
  logistic-regression coefficients were not in the doc; no train/test gap.
- **Selection leakage made explicit.** The winner is chosen on validation, and the doc now says
  so; the margin is given as paired read counts on test, not only a percentage.
- **Dishonest ceiling.** An empty read "retrieved" the whole registry under the full scan and
  counted as a hit; empty reads are misses everywhere now.
- **Unreachable target handled out loud.** Recall ≥ 0.99 is impossible with q-gram blocking here
  (best 0.95 at max_edits 5; the remaining reads share almost no bigram with their plate). The
  generated code returned the last grid value silently; now the rule is "smallest k within 0.005
  of the best", the doc says the target was not met, and a rescue check shows a full scan lets
  logreg recover **0** of the reads blocking lost.
- Shortlist recall and the reads it drops are reported; logistic-regression `C` is chosen on
  validation from {0.01, 0.1, 1, 10} (ties to the more regularised, more readable model); the
  coefficient table explains collinearity among the string measures; the Decision text is
  generated from the ablation, so it cannot claim a feature helps when the numbers say it does
  not (learned costs +0.002, confidence −0.002 inside the combination); the elapsed time was
  removed from the doc so two runs produce identical files (checked with `diff`).
- **Runtime.** 1 163 s on a loaded machine → 75 s by reusing the matcher's cached TF/IDF and memo.

### Result (test split, 20 vehicles never seen in training)

| contender | top-1 | MRR | ACCEPT P | ACCEPT R | REVIEW |
|---|---|---|---|---|---|
| **logreg** | 0.902 | 0.911 | 0.997 | 0.842 | 0.039 |
| gbt | 0.895 | 0.907 | 0.994 | 0.828 | 0.055 |
| weighted | 0.895 | 0.907 | 0.986 | 0.699 | 0.173 |
| learned_ocr_similarity | 0.868 | 0.891 | 1.000 | 0.603 | 0.189 |
| ocr_edit_similarity | 0.859 | 0.884 | 1.000 | 0.612 | 0.205 |
| rule (phase-1 plate_resolve) | 0.608 | 0.608 | 1.000 | 0.565 | 0.041 |
| soundex_similarity | 0.045 | 0.116 | 0.000 | 0.000 | 0.000 |

Full table, per-degradation breakdown, ablation and the viva text: `docs/MATCHING_EVALUATION.md`.

**Problems found in files we must not edit (reported, not changed).**
`similarity.tfidf_cosine` recomputes corpus statistics per pair (O(N) per call); worked around by
`plate_match.CachedTfidf`, which is tested equal to it. `blocking.py`'s bounds are only
recall-safe to `max_edits`, and on real EasyOCR reads even max_edits 5 keeps 0.95; this is a
property of the reads, documented, not a code bug.
