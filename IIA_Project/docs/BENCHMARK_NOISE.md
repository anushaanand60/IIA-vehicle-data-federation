# The noisy-plate benchmark (phase 2, workstream A)

Workstreams B, C and D claim one matching method beats another. That claim is empty without
labelled noise, so this is built first: 2 200 rendered plate images with known ground truth, read
by the same EasyOCR pipeline the GUI uses, plus the real noise we already had.

Everything here is reproducible from two commands:

```powershell
python scripts/make_plate_images.py --plates 100 --seed 20261017
python scripts/run_ocr_benchmark.py
```

`data/plate_images/` is **not** committed (2 200 PNGs); `data/plate_images/manifest.csv` and
`data/match_benchmark.csv` are. The generator is deterministic, so the images regenerate byte for
byte from the manifest's `seed` column.

---

## 1. How the images are made

`scripts/make_plate_images.py` renders a 520 x 120 plate: white field, 5 px black border, bold
black characters grouped the way a real plate is painted (`DL01 AB 1234`). The font is
`C:\Windows\Fonts\arialbd.ttf`; if it is missing the script falls back to Pillow's bitmap default
so the benchmark still runs on a laptop without it (the numbers below were produced with Arial
Bold — a different font changes them).

**Plates.** The six story plates (`DL01AB1234`, `DL05CD9876`, `HR26EF4455`, `UP16GH1122`,
`MH12IJ7788`, `DL01AB0002`) are always included, then 94 more are sampled from
`sources/reg/reg.db` `VEHICLE_REGISTRATION.registration_no` with `random.Random(seed)`. The source
database is opened read-only (`file:...?mode=ro`); nothing writes to a source.

**Determinism.** Every random draw comes from a `random.Random` (and, for bulk sensor noise, a
`numpy.random.default_rng`) seeded from `sha256(seed | plate | degradation | severity)`. The
global `random` module is never used, so image *k* does not depend on how many images were made
before it. Two runs at the same seed produce identical bytes; `tests/test_plate_images.py` asserts
exactly that, both in memory and over the files `generate()` writes.

## 2. The degradation ladder

Each plate yields 1 clean image plus 7 degradations x 3 severities = **22 images**, all written as
lossless PNG (the JPEG artefacts live in the pixels; re-encoding the file would degrade it twice).

| Degradation | Mechanism | s1 | s2 | s3 |
|---|---|---|---|---|
| `blur` | Gaussian blur | r = 1.0 | r = 2.0 | r = 3.2 |
| `motion` | mean of copies shifted along one axis, edge-padded | 5 px | 11 px | 19 px |
| `jpeg` | low-quality re-encode, decoded back | q = 25 | q = 12 | q = 5 |
| `rotate` | small rotation + perspective skew | 2°, 6 px | 5°, 14 px | 9°, 24 px |
| `glare` | blurred bright ellipse composited over the plate | 120 px, a = 110 | 200 px, a = 175 | 300 px, a = 235 |
| `dark` | brightness gain + Gaussian sensor noise | x0.55, s = 8 | x0.35, s = 16 | x0.20, s = 26 |
| `occlude` | dirt specks, plus a mud bar from s2 | 25 specks | 70 + 14 px bar | 150 + 28 px bar |

## 3. Measured results

**Run of 17 Oct 2026**, seed `20261017`, 100 plates, 2 200 images, EasyOCR 1.7.2 on CPU
(torch 2.14.0+cpu), 0.38 s per image, **about 14 minutes** end to end.

- Exact-read rate overall: **869 / 2 200 = 39.5 %**
- Exact after `plate_ocr.normalise_candidates` repair: **1 461 / 2 200 = 66.4 %**
- Mean confidence: **0.689**
- Images EasyOCR returned nothing at all for: **47**

`ocr_read` in the CSV is the **raw** reading (detected boxes joined left to right, uppercased
alphanumerics), not `read_plate()`'s output. `read_plate` applies pattern-licensed confusion
repair and returns one box; baking either into the labels would measure the repair that workstream
B and C are supposed to be evaluated against. The "repaired" column below is reported *separately*
for exactly that reason — the gap between the two columns is the headroom the learned matcher has
to beat.

| degradation | sev | n | exact | repaired | mean conf |
|---|---|---|---|---|---|
| clean | 0 | 100 | 52.0 % | 89.0 % | 0.830 |
| blur | 1 | 100 | 53.0 % | 90.0 % | 0.828 |
| blur | 2 | 100 | 56.0 % | 91.0 % | 0.806 |
| blur | 3 | 100 | 56.0 % | 91.0 % | 0.833 |
| motion | 1 | 100 | 59.0 % | 96.0 % | 0.844 |
| motion | 2 | 100 | 63.0 % | 99.0 % | 0.776 |
| motion | 3 | 100 | 0.0 % | 0.0 % | 0.168 |
| jpeg | 1 | 100 | 50.0 % | 87.0 % | 0.823 |
| jpeg | 2 | 100 | 53.0 % | 90.0 % | 0.794 |
| jpeg | 3 | 100 | 54.0 % | 91.0 % | 0.811 |
| rotate | 1 | 100 | 37.0 % | 67.0 % | 0.770 |
| rotate | 2 | 100 | 26.0 % | 45.0 % | 0.743 |
| rotate | 3 | 100 | 5.0 % | 10.0 % | 0.721 |
| glare | 1 | 100 | 51.0 % | 88.0 % | 0.821 |
| glare | 2 | 100 | 54.0 % | 91.0 % | 0.824 |
| glare | 3 | 100 | 47.0 % | 74.0 % | 0.775 |
| dark | 1 | 100 | 51.0 % | 88.0 % | 0.851 |
| dark | 2 | 100 | 50.0 % | 87.0 % | 0.817 |
| dark | 3 | 100 | 6.0 % | 11.0 % | 0.227 |
| occlude | 1 | 100 | 42.0 % | 70.0 % | 0.729 |
| occlude | 2 | 100 | 4.0 % | 6.0 % | 0.259 |
| occlude | 3 | 100 | 0.0 % | 0.0 % | 0.113 |

**What the table says, and what it does not.**

- Mild blur, mild motion and JPEG artefacts barely matter — modern OCR is trained on exactly this.
  A *clean* rendering only reads exactly 52 % of the time, which means the floor is set by
  character confusion, not by the degradation (see §4). Mild degradations score at or above clean
  because slight blurring smooths the glyph edges Arial Bold renders aliased.
- **Three degradations actually break OCR**: heavy motion blur (19 px), heavy occlusion (a mud bar
  across the characters) and heavy low light. All three collapse to near 0 %, and the mean
  confidence collapses with them (0.11-0.23). Confidence is therefore a usable feature for the
  accept / review / reject decision in workstream C.
- **Rotation degrades gradually** (37 / 26 / 5 %) while keeping confidence high (0.72-0.77). This
  is the dangerous case: a skewed plate yields a *confident wrong read*. Confidence alone will not
  catch it; string similarity against the registry has to.
- EasyOCR returned nothing for 47 images, all at the top of the occlude / motion / dark ladders.
  The benchmark records these as an empty `ocr_read`, never as a crash.

## 4. Character confusions observed

Counted by position, only where the read and the truth have the same length (a positional
comparison cannot align an insertion, so unequal lengths contribute nothing rather than a
misaligned guess).

| true -> read | count | note |
|---|---|---|
| `0` -> `O` | 612 | overwhelmingly dominant; EasyOCR prefers the letter in a letter-led run |
| `5` -> `S` | 20 | in `CONFUSABLE` already |
| `1` -> `4` | 17 | **not** in `CONFUSABLE` |
| `H` -> `4` | 17 | **not** in `CONFUSABLE` |
| `1` -> `I` | 16 | in `CONFUSABLE` |
| `1` -> `A` | 9 | **not** in `CONFUSABLE` |
| `D` -> `U` | 6 | **not** in `CONFUSABLE` |
| `M` -> `I` | 6 | a dropped-stroke read, not a glyph confusion |
| `G` -> `6` | 6 | in `CONFUSABLE` |
| `1` -> `T` | 5 | **not** in `CONFUSABLE` |

Two findings worth saying out loud in the viva:

1. The `0`/`O` pair is not one confusion among many — it is 612 of the 981 aligned substitutions,
   **62 %** of them, with the next most common at 20. Any
   weighted edit distance that does not make that substitution nearly free is mis-specified. This
   is the empirical justification for the `ocr_edit_similarity` measure in workstream B, and it is
   measured, not assumed.
2. `plate_resolve.CONFUSABLE` is incomplete: `1`/`4`, `H`/`4`, `1`/`A`, `D`/`U` and `1`/`T` all
   occur and none are in the table. That is a concrete, evidence-backed change for workstream C
   rather than a guess.

## 5. The rows that are not rendered images

`data/match_benchmark.csv` also carries 49 rows with `degradation=observed`:

- **47 rows** — the camera plates in `sources/cam/cam.db` that match no registration in REG. This
  is real, unlabelled OCR noise from the synthetic-but-plausible CAM source. `true_plate` and
  `exact_match` are **empty**: nobody knows what these should have been, and inventing a truth
  would silently turn the benchmark into a self-fulfilling test. Their `ocr_confidence` is the
  value the source itself recorded (the maximum over that plate's captures).
- **2 rows** — the planted story misreads the demo relies on: `DL05CD9B76` -> `DL05CD9876` (8/B)
  and `DLO1AB1234` -> `DL01AB1234` (O/0). Truth known, `exact_match = 0`.

## 6. Honest limits

- **Rendered plates are cleaner than photographs.** One font, one plate size, no motion of the
  camera itself, no number-plate retro-reflective sheeting, no IND emblem or state hologram, no
  background clutter, no non-frontal lighting, flat colour. Absolute rates here are an *upper
  bound* on what the same pipeline would do on real ANPR frames; only the *relative* ordering of
  degradations and the *shape* of the confusion distribution should be carried forward.
- **One font.** Every confusion count is Arial Bold's. A real plate uses the Charles Wright style
  typeface, whose `0` and `O` differ more than Arial's, so the `0`/`O` rate here is probably
  pessimistic — but every Indian state paints plates differently, and some are hand-painted.
- **One OCR engine, pinned.** EasyOCR 1.7.2, torch 2.14.0+cpu, Pillow 12.3.0, numpy 2.5.3, CPU
  only. A different version will move every number in §3; re-run both scripts if the environment
  changes.
- **The degradation severities are chosen, not calibrated** against a measured distribution of
  real camera conditions. They are a ladder, not a simulation.
- **Positional confusion counting** undercounts: any read with a dropped or inserted character
  contributes nothing to §4. The true substitution counts are therefore lower bounds.
- **100 vehicles, not 605.** Enough to characterise the noise, deliberately small enough that the
  learned matcher in workstream C must be split by vehicle to avoid leakage.
