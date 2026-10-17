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
