# Phase 3 changelog

Three changes, requested after the phase-2 review. Paths are relative to `IIA_Project/`.

| | Change | Why |
|---|---|---|
| **C1** | Match a read against **every** agency's plates, not only REG's | A car missing from REG but held by INS, PUC, THEFT or CAM could never be the answer; a low REG score was the end of the story |
| **C2** | Infer **make, model and colour** from a photo that shows the car | A photo of the car carries the same evidence a road camera records; plate close-ups must keep working |
| **C3** | **Re-evaluate the matcher**: `0L01AB1234` was ACCEPTed as DL01AB1234 at p=1.000 | The learned model only ever compared registered plates with each other, so it could not say "this may be a car we do not hold" |

## 1. How to find every change in the code

Every phase-3 edit is marked inline:

- `# PHASE3-ADDED (Cn: <reason>)` ... `# PHASE3-END`: new code between the two lines. A new module says
  `PHASE3-ADDED` in its docstring instead and is phase-3 throughout.
- `# PHASE3-REPLACED (Cn: <reason>):`: the commented lines directly under it are the original code; the
  live line(s) after them replace it. Nothing was deleted.

```powershell
Select-String -Path mediator\*.py,app\tabs\*.py,scripts\*.py,tests\*.py -Pattern "PHASE3-"
```

```bash
grep -n "PHASE3-" mediator/*.py app/tabs/*.py scripts/*.py tests/*.py
```

## 2. C3: the matcher, re-evaluated

**What was wrong.** `plate_match` (logistic regression + Platt calibration) was trained with negatives that
are only *other registered plates*. Its probability answered "which registered plate is this?", so any
read close to exactly one registered plate scored ~1.0, whatever the read actually said:

| read | before | after |
|---|---|---|
| `0L01AB1234` (examiner) | DL01AB1234, 1.000, ACCEPT | DL01AB1234, 0.82, **REVIEW**: "0->D at position 1 (state code)" |
| `XX01AB1234` (no such state) | 0.999, ACCEPT | 0.12, REVIEW |
| `DL01AB12` (truncated) | 0.972, ACCEPT | 0.026, REJECT |
| `DL01AB1235` (a car one digit away that no source holds) | ACCEPTed as DL01AB1234 | not accepted |
| `DL01AB1234` (exact) | 1.000, ACCEPT | 0.87-0.97 (by OCR confidence), ACCEPT |

**The fix** (`mediator/open_world.py`): a noisy-channel posterior that also weighs "a valid plate no source
holds". The likelihood of a read given a plate uses per-character error rates *measured* on the benchmark by
OCR confidence, and the training split's substitution counts (smoothed over all pairs at once, so an unseen
pair costs the same whatever the true character; documented confusables such as D/0 get a floor). The sum over every unknown valid plate is exact:
the forward algorithm runs over the Indian plate grammar (official state codes, district, series, number;
Bharat series). Then `plate_match`:

1. keeps the learned model as the **ranker** (it puts the right plate first 93.1 % of the time);
2. shows and decides on **p = model p x p(the car is one an agency holds)**. The model was calibrated on held
   plates only, so its p means p(which held plate | the car is held). It still uses make and colour to tell
   twins apart; the open-world factor only adds the question it never saw. REJECT ("no held plate is even
   plausible") is still asked of the model's own p, so a very noisy read of the right plate goes to a person;
3. never auto-accepts a **correction inside the state code** unless the other databases and the observed car
   confirm it (C1 + C2: at least two non-camera agencies hold the plate, and make and colour both agree);
4. lists the **corrections** each match needed.

**Measured** (`python scripts/evaluate_open_world.py`, held-out test vehicles, `docs/OPEN_WORLD.md`):

| case | before: auto-ACCEPT | after: auto-ACCEPT | after: ACCEPT precision |
|---|---|---|---|
| honest reads of registered cars (405) | 78.8 % (precision 0.997) | 55.3 % | 1.000 |
| unknown neighbour: every ACCEPT is the wrong car (19) | **52.6 %** | **0 %** | — |
| state code read as 0 (19) | 84.2 % | 0 % (top candidate still right 100 %) | — |
| unknown state XX (19) | 94.7 % | 0 % | — |
| truncated (19) | 31.6 % (precision 0.17) | 0 % | — |

The cost is honest: on the deliberately degraded benchmark images, a quarter of the reads the old matcher
accepted now go to REVIEW, because in an open world those reads really are ambiguous. The ACCEPT threshold
stays phase 2's "more likely than not" rule, now applied to a probability that means it. `RHO` (prior weight
of an unknown plate) moves honest recall only from 0.565 to 0.519 over 0.01 to 0.2.

One test scenario changed meaning (`mediator/scenarios.py`, *Rotated image, two OCR errors*): a read at OCR
confidence 0.38 with two errors must now rank the right plate first and go to REVIEW or ACCEPT, not ACCEPT
unattended. At that confidence the benchmark misreads 30 % of characters.

Also: `plate_ocr` repairs L->4 and A->4 where the pattern needs a digit (a real photo's `KL L8 S 5665`).

## 3. C1: every database

`mediator/federated_match.live_universe()` reads every catalogued source's plates live through its wrapper (in
parallel, nothing kept) and tags each plate with:

- **who holds it** (`REG, INS, THEFT` / `not registered; known to INS`), shown as *Held by* on Plate Match;
- **a prior**: 1.0 if an authoritative source holds it, 0.3 if only cameras saw it (a camera read may itself be
  a misread; misreads that are not even a valid plate shape are left out);
- **make / model / colour**: REG's, or where REG has none, the majority of confident camera sightings;
- **last seen**: camera and time.

**What if the car is not in REG but is in the other tables?** It is a candidate like any other. A read that
exactly matches it stays itself (it is no longer pulled to a registered neighbour), and the result says
"not registered; known to INS, PUC". If REG is down the match is refused, as in phase 1. Any other source
that is down is named and contributes nothing.

On Plate Match, *What the other databases say* lists, for the top candidates, who holds each one, its record,
camera sightings, last seen, and the model and open-world probabilities side by side.

## 4. C2: the photo

`mediator/vehicle_vision.analyse(image, plate_box)`:

1. **Close-up gate.** If the plate box fills at least 12 % of the photo, or at least 60 % of its width, it is a
   plate close-up: only the plate is read, exactly as before, and the vision model is never loaded.
2. **Zero-shot CLIP** (open_clip ViT-B-32) scores the photo against prompts over REG's own makes and models,
   plus common Indian makes REG does not hold, and 11 colours.
3. **Abstain.** A guess is reported only at p >= 0.5, and a model only under a make it agrees with. "Not sure"
   counts as no evidence.

The inferred make, model and colour pre-fill Plate Match's *Observed* fields (still editable). From there they
feed the matcher's existing context features and the phase-2 clone veto. On Investigate, the photo's car is
compared with the record of the plate queried (match / one difference / possible clone).

`scripts/match_plate.run_ocr` now joins only the boxes on the plate's own line(s). It picks the box with a
real state code first, and drops the `IND` emblem, so a badge or dealer sticker is not read into the plate. A
plate-only photo keeps every box, so benchmark rows reproduce unchanged.

Example, a front photo of a white Hyundai i20, plate `KL 48 S 5665`: OCR `KLL8S5665` at 0.95, repaired to
`KL48S5665`; CLIP says white (0.99), Hyundai (0.99), i20 (0.99). No source holds a KL plate, so the matcher
correctly finds no registered car.

## 5. Every file changed

| File | New / changed | What |
|---|---|---|
| `mediator/open_world.py` | new | C3 noisy-channel open-world posterior, corrections, plate validity |
| `mediator/federated_match.py` | new | C1 live candidate set from every source, priors, camera fingerprint |
| `mediator/vehicle_vision.py` | new | C2 close-up gate, zero-shot CLIP make/model/colour, abstention |
| `mediator/plate_match.py` | changed | C3 `apply_open_world`, state-code rule, REJECT on the model's p, `Candidate.evidence`, `open_world_check=` |
| `mediator/scenarios.py` | changed | C3 the rotated two-error scenario expects REVIEW or ACCEPT of the right plate |
| `mediator/plate_ocr.py` | changed | C3 L->4, A->4 repairs; explicit digit->letter map |
| `scripts/match_plate.py` | changed | C2 `plate_line`, `union_box`; `run_ocr` returns the plate box |
| `scripts/evaluate_open_world.py` | new | C3 before/after evaluation, writes `docs/OPEN_WORLD.md` |
| `app/tabs/plate_match_lab.py` | changed | C1 federated candidates, *Held by*, *Corrections*, other-databases panel; C2 photo fills make/model/colour |
| `app/tabs/ocr_upload.py` | changed | C2 caption of what the photo shows; photo-vs-record check |
| `app/tabs/investigate.py` | changed | C2 shows the photo-vs-record check under the decision |
| `requirements.txt` | changed | `open_clip_torch` (optional) |
| `tests/test_open_world.py` | new | C3 |
| `tests/test_federated_match.py` | new | C1 (fake wrappers) |
| `tests/test_vehicle_vision.py` | new | C2 (fake CLIP) |
| `tests/test_photo_plate_line.py` | new | C2 plate line, L->4 repair, photo-vs-record |
| `docs/OPEN_WORLD.md` | new | generated evaluation |
| `docs/CHANGELOG_PHASE3.md` | new | this file |

## 6. C3 follow-up: learned extra / missing characters, and plate shapes

**What was wrong.** `HR26AB05501` (the right plate HR26AB0550, with the plate's right border read as a trailing
1) scored 0.010. An extra character was modelled as rare and equally likely to be any of 36 characters at any
place. "An unknown HR26ABO5501 (three-letter series, O read as 0)" therefore won 70 to 1, although none of the
605 plates the sources hold has a three-letter series.

**The fix** (`mediator/open_world.py`, marked `PHASE3-REPLACED (C3 follow-up ...)`):

- **Where and which characters OCR adds or drops**, learned from the training split only. 96 of 1,298 rendered
  reads gain a character at the end (71 of them a 1: the right border) and 18 at the start (17 an I: the left
  border); 85 gain one and 34 lose one in the middle; none loses one at either end. Rates are per opportunity
  and scaled by OCR confidence.
- **Unknown plates take the shapes of the held plates** (`shape_weights`, read live from the registry the matcher
  is given, Laplace-smoothed): a three-letter series is possible but rare when no held plate has one.
- A character added **before** the plate (the left border as I) is no longer counted as a state-code correction.
- Corrections now say "extra 1 at the end" / "extra I at the start".

| read | before | after |
|---|---|---|
| `HR26AB05501` | 0.010 REVIEW | **0.931 ACCEPT**, "extra 1 at the end" |
| `IDL01AB1234` | REVIEW (counted as a state-code fix) | 0.955 ACCEPT, "extra I at the start" |
| `0L01AB1234` | 0.857 REVIEW | 0.864 REVIEW (a state-code fix still needs confirming) |

**Measured** (`docs/OPEN_WORLD.md`): honest reads auto-accepted 55.3 % -> **69.1 %** at precision 1.000 (phase 2:
78.8 % at 0.997, in a closed world). Unknown neighbours, state-code-0, XX and truncated reads all stay at 0 %.
Test suite 1008 passed; ground truth 621/621.
