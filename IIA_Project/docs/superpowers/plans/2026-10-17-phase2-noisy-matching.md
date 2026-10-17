# Phase 2 plan — approximate plate matching and record cleansing

**Evaluation:** 21–23 Oct 2026, in class, random selection. Three rubrics, 5 marks each. The
examiner will test by inserting, deleting and modifying records, so everything must work live
against the federation, never from a precomputed table.

**Course material this is built on**, both decks downloaded 17 Oct:

| Deck | What we use |
|---|---|
| Week 6–8, Data and Text Matching (87 slides) | edit distance and its dynamic program (p. 16–19), Jaro and Jaro-Winkler (p. 20–22), set-based overlap, Jaccard and TF/IDF (p. 24–31), Ratcliff-Obershelp (p. 33–34), Soundex (p. 36–37), the two-step framework (p. 44), inverted index, size filtering, prefix filtering, position index (p. 45–65), ML pair classification, feature vectors, gradient-boosted trees, retrieve–rerank, calibration, separate evaluation of retrieval and scoring (p. 67–87) |
| Week 8, RDR (32 slides) | cleansing composite attributes with Ripple-Down Rules: six failure modes of dirty data, lexicons and pattern mining, feature extraction, sub-pattern mining, `if condition then conclusion [because case] except …`, the RDR tree and knowledge-acquisition loop (p. 8–32) |

Copy both files into `docs/slides/` so the repository is self-contained.

---

## 1. What we already have

| Asset | Where | Phase 2 role |
|---|---|---|
| Jaro-Winkler implementation | `mediator/matcher.py::jaro_winkler` | one baseline measure; move into the new similarity library |
| One-edit confusable resolution (O/0/D, 1/I, 8/B, 5/S, 2/Z, 6/G) with make and colour corroboration | `mediator/plate_resolve.py` | becomes one candidate generator among several, and the explainable fallback |
| Plate OCR from a photo | `mediator/plate_ocr.py` (EasyOCR, offline) | the image end of rubric 1 |
| Plate normalisation and OCR folding pushed into source SQL | `mediator/transforms.py::norm_plate`, `mediator/decomposer.py` | blocking predicates pushed to the sources |
| 918 camera captures, 572 distinct plates, 47 of them not registered, confidence 0.554–1.00 | `sources/cam/cam.db` | natural noisy data; the 47 are real candidate mismatches |
| Registration, insurance, police, PUC over five live wrappers | `sources/`, `mediator/` | the contextual evidence for cleansing |
| 621-vehicle ground truth, matcher gold standard, 678 tests | `data/`, `tests/` | the evaluation habit to extend |

Installed and usable: Pillow 12.3, numpy 2.5, EasyOCR. **scikit-learn is missing** and is needed
for the learned matcher; add it to `requirements.txt`.

---

## 2. Architecture: the two-step framework from the slides

```
  image ──EasyOCR──► noisy read + confidence
                          │
            ┌─────────────┴─────────────┐
   STEP 1   │ BLOCKING / FILTERING      │   q-gram inverted index, size filter,
            │ cheap, high recall        │   prefix filter, position index
            └─────────────┬─────────────┘   pushed into source SQL where possible
                          │  small candidate set
            ┌─────────────┴─────────────┐
   STEP 2   │ SCORING / VERIFICATION    │   per-measure scores -> feature vector
            │ exact, explainable        │   -> calibrated decision
            └─────────────┬─────────────┘
                          │  ranked candidates with reasons
            ┌─────────────┴─────────────┐
   RUBRIC 2 │ CLEANSING (RDR)           │   contextual evidence from REG/INS/THEFT/PUC
            │ rules with exceptions     │   propose -> apply through /admin
            └───────────────────────────┘
```

Both directions are the same code path:

- **Image to records:** a noisy read finds all registered vehicles that plausibly match.
- **Record to images:** a registration number finds every camera read that could be it, which is
  how we answer "and vice-versa" and how cleansing finds the sightings to fix.

---

## 3. Workstreams

### A. A labelled noisy benchmark, with images (prerequisite for everything)

Without labels we cannot claim "best accuracy", so this comes first.

1. `scripts/make_plate_images.py`: render a plate image for a registration number with Pillow, then
   degrade it in controlled ways: blur, motion blur, JPEG artefacts, rotation, glare, partial
   occlusion, low light, dirt specks. Each image is written with its true plate in the filename and
   in a manifest.
2. Run EasyOCR over every image to get the read and its confidence. The manifest row becomes
   `(true_plate, ocr_read, confidence, degradation, severity)`. This is a real noise distribution,
   not a guess, and it is reproducible.
3. `data/match_benchmark.csv`: about 400 images across 100 vehicles and 6 degradation levels, plus
   the 47 already-unmatched CAM plates and the planted story misreads.
4. Negative pairs: for each read, the candidates a blocking pass retrieves that are *not* the true
   vehicle. The slides are explicit that training and test pairs must reflect deployment (p. 70, 78).

**Deliverable:** `data/match_benchmark.csv`, `data/plate_images/`, a one-page note on the noise
distribution.

### B. The similarity library and the measured comparison (rubric 1, the justification)

`mediator/similarity.py`, one function per measure, each with its own unit tests:

| Measure | Slide | Why it is a candidate here |
|---|---|---|
| Levenshtein edit distance, DP table | p. 16–19 | substitutions and the occasional dropped character |
| Needleman-Wunsch style weighted edit distance | p. 17 | lets us weight OCR-confusable substitutions cheaper than arbitrary ones |
| Jaro, Jaro-Winkler | p. 20–22 | transpositions and a prefix bonus; the state code prefix is informative |
| Overlap, Jaccard on character q-grams | p. 24–26 | robust to a single bad character, and it is what the blocking index uses |
| TF/IDF over q-grams | p. 27–31 | rare q-grams such as an unusual series should count more than common ones |
| Ratcliff-Obershelp | p. 33–34 | longest-common-subsequence flavour, good on insertions |
| Soundex | p. 36–37 | **expected to lose** on plates, which are not phonetic; we report it anyway, because showing a measure that does not fit is part of the justification |
| OCR-confusion-weighted distance | our `CONFUSABLE` table | domain knowledge: `0`→`O` must cost far less than `0`→`7` |

`scripts/evaluate_matching.py` runs every measure over the benchmark and reports precision, recall,
F1 and mean reciprocal rank at the top 1 and top 5, plus timing, per degradation level. Output is a
table in `docs/MATCHING_EVALUATION.md` and a chart in the GUI.

**This table is the answer to "justify why you took a particular algorithm."** We expect the
confusion-weighted distance and Jaro-Winkler to lead on plates, Jaccard on q-grams to give the best
recall for blocking, and Soundex to fail. We report what we measure, not what we expect.

### C. Combining measures, and the learned matcher (rubric 1, "combination of algorithms")

1. **Feature vector per candidate pair** (slide 71): every measure from B, plus length difference,
   state-code agreement, OCR confidence, make agreement, model agreement, colour agreement, and
   whether the read itself is registered.
2. **Two combiners**, both trained on the benchmark with a clean train and test split by vehicle to
   avoid leakage (p. 83):
   - **Logistic regression**, which gives per-feature weights we can read out loud in the viva;
   - **Gradient-boosted trees**, the strong tabular baseline the slides name (p. 72).
3. **Calibration** (p. 81): convert the score to a probability and pick the threshold from the
   benchmark, with three decisions: accept, review, reject. No silent auto-accept below the
   threshold.
4. **Retrieve then rerank** (p. 76): blocking retrieves, the combiner reranks. Report retrieval
   recall and reranking accuracy separately, as slide 82 requires.
5. Keep the rule-based resolver as the explainable path and compare it in the same table, so we can
   say precisely what the learned model buys.

We deliberately stop before Siamese and cross-encoder models (p. 73–75). Reason to give: 10-character
plates with a known confusion alphabet do not justify a learned text encoder, and an examiner can
check a weighted edit distance by hand. We will name them as the next step for 50-million-record
scale (p. 86).

### D. Scale: blocking and filtering (rubric 1, the "all vehicles" part)

`mediator/blocking.py`:

- q-gram **inverted index** over registration numbers (p. 47–51);
- **size filtering** (p. 53) and **prefix filtering** (p. 55–58) to cut candidates before scoring;
- **position index** (p. 65) as the final refinement;
- for the federated path, push a cheap predicate into source SQL, for example a prefix and length
  filter, so the source returns tens of rows, not 605.

Report candidates scanned per query with and without each filter, and the recall each filter keeps.
That is the scalability evidence, and it answers "find out **all** vehicles approximately matching".

### E. Cleansing with Ripple-Down Rules (rubric 2)

`mediator/rdr.py`, following the Week 8 deck exactly:

1. **Anomaly detection first.** For a camera record, compare against the live federated profile and
   flag: plate not registered, make or colour disagreeing with registration, impossible travel,
   confidence below a floor, malformed plate shape, duplicate sighting, timestamp out of range.
2. **Feature extraction** (slide 20): surface and shape features of each field, for example plate
   shape `LL DD LL DDDD`, token type, dictionary membership for make and colour.
3. **Lexicons and pattern mining** (slides 19–23): build make, model and colour lexicons from REG,
   mine sub-patterns, and turn frequent patterns into candidate predicates.
4. **The RDR tree** (slides 24–29): `if condition then conclusion [because case] except …`. Each
   node stores the cornerstone case that created it. A new wrong case adds an exception under the
   rule that fired, never an edit to an existing rule. Rules live in `data/rdr_rules.json`, so the
   examiner can watch the tree grow when they insert a record that breaks it.
5. **Conclusions are proposals**: corrected plate, standardised make, model and colour, normalised
   timestamp, or "quarantine". Nothing is written until an operator applies it, and applying goes
   through the owning source's `/admin` endpoint, so the mediator still never writes to a source.
6. **Provenance**: every cleansed value records the rule that produced it, its cornerstone case and
   the contextual evidence used. That is what makes "no anomalies remain" checkable.

**Live behaviour matters for the exam.** The examiner inserts a dirty row, presses Re-check, and the
anomaly appears; they teach the system a rule and the anomaly clears for that case and every similar
one; they delete the row and it disappears from the report. No batch step anywhere.

### F. GUI: one page for each rubric

- **Plate Match Lab**: upload or pick a plate image, see the OCR read with confidence, the blocking
  candidate set with counts, the ranked matches with every measure's score side by side, the
  combiner's probability and decision, and the reverse direction, registration to camera reads.
- **Cleansing**: the anomaly list over live data, the proposed fix per record with the rule that
  fired and the cornerstone case, Apply and Undo, the RDR tree, and a before-and-after count.
- **Evaluation**: the comparison table and chart from B and C, so the justification is on screen.

### G. Rubric 3: prompt, generated code, and our changes

Our existing disclosure covers phase 1. For phase 2, from the first commit:

1. **Prompt log per task.** Keep the phase-2 prompts in `docs/ai/PROMPTS_PHASE2.md`, numbered, with
   the task each belongs to.
2. **Two commits per module.** Commit the AI's first output verbatim as
   `generated: <module> (unreviewed AI output)`, then commit the reviewed version as
   `reviewed: <module> — <what changed and why>`. The diff between the two **is** the highlight the
   rubric asks for; no reconstruction after the fact.
3. **`docs/AI_CHANGES_PHASE2.md`**: per module, the prompt, a link to the generated commit, the diff
   of our changes, and one line on why each change was needed, for example a correctness bug, a
   boundary case the model missed, or a course method it ignored.
4. Expect real examples to show: off-by-one in the edit-distance DP table, Jaro transposition
   counting, a prefix filter that drops true matches, and a threshold chosen without calibration.

---

## 4. Schedule

| Day | Work | Done when |
|---|---|---|
| **Fri 17 Oct** | this plan; copy slides into the repository; add scikit-learn; workstream A | benchmark CSV and images exist |
| **Sat 18 Oct** | workstream B, all measures plus the evaluation table | `MATCHING_EVALUATION.md` has real numbers |
| **Sun 19 Oct** | workstream D blocking, then C combiner and calibration | retrieval recall and reranking accuracy reported separately |
| **Mon 20 Oct** | workstream E, RDR cleansing end to end | examiner script runs: insert dirty row, detect, teach, clear |
| **Tue 21 Oct** | workstream F GUI, rehearsal, workstream G write-up | all three rubrics demonstrable in 10 minutes |

Phase-1 habits stay: test-first, every claim measured, the full suite green before any push, and no
AI co-author line in commits.

---

## 5. What the examiner will try, and our answer

| They do | We show |
|---|---|
| "Here is a scanned plate, find the vehicle" | upload, OCR read with confidence, candidate set, ranked matches with per-measure scores and the decision |
| "Why this algorithm?" | the comparison table, including the measures that lost and why, and the ablation showing what the combination adds |
| "Find the camera reads for this registration" | the reverse direction on the same page |
| **Insert** a dirty record | it appears as an anomaly within one refresh, with the rule that caught it |
| **Modify** make or colour so it contradicts the registration | conflict flagged, cleansing proposal changes, and the match re-ranks |
| **Delete** a record | anomaly and sighting both disappear; no stale cache anywhere |
| "Make it fail" | an unregistered plate yields a review decision, never a confident wrong answer; a source going down yields refusal, as in phase 1 |

## 6. Risks

| Risk | Mitigation |
|---|---|
| EasyOCR is slow or its model is missing on the day | pre-generate reads for the benchmark; cache per image; keep manual plate entry as the path that never depends on OCR |
| The learned matcher overfits 100 vehicles | split by vehicle, report the gap between train and test, and keep the rule-based path as the default if the gain is not real |
| RDR rule explosion during the demo | cornerstone cases keep rules ordered; cap the demo to a handful of taught rules |
| New dependency, scikit-learn, on four laptops | pin it in `requirements.txt` and make the import lazy, so the rest of the app runs without it |
| Scope creep into deep learning | explicitly out of scope, with the reason above; name it as future work |

## 7. First three commands

```powershell
cd C:\Users\siddh_ygv5bws\IIA-vehicle-data-federation\IIA_Project
mkdir docs\slides; copy "C:\Users\siddh_ygv5bws\Downloads\Week-6-8_Data_String_Matching (1).pdf" docs\slides\; copy "C:\Users\siddh_ygv5bws\Downloads\Week 8 - RDR.pptx" docs\slides\
.venv\Scripts\Activate.ps1; python -m pip install scikit-learn
```
