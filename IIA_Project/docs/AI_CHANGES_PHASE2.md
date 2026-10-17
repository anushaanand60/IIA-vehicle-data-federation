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
