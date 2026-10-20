# Phase 2 guide — noisy plate matching and data cleansing

Everything in phase 2, explained so you can learn it, run it and explain it in the viva. Read it top
to bottom once; after that, use the headings to jump. Every number here was measured on our system.

| Part | |
|---|---|
| [1. The scope we define](#1-the-scope-we-define) | what problem we solve, and what we deliberately do not |
| [2. The data](#2-the-data) | what is noisy, what is the reference, how we tested |
| [3. Rubric 1 — matching, and how it works](#3-rubric-1--matching-and-how-it-works) | from an image to a registered car, step by step, including logistic regression |
| [4. Rubric 2 — cleansing, and how it works](#4-rubric-2--cleansing-and-how-it-works) | finding and fixing anomalies with Ripple-Down Rules |
| [5. Rubric 3 — prompts, generated code, our changes](#5-rubric-3--prompts-generated-code-our-changes) | where the evidence is and how to show it |
| [6. The pages, and how to use them](#6-the-pages-and-how-to-use-them) | each page, step by step, and which rubric it serves |
| [7. A 10-minute demo](#7-a-10-minute-demo) | the order to show things in class |
| [8. Questions you may be asked](#8-questions-you-may-be-asked) | short answers |

---

## 1. The scope we define

The professor accepts our interpretation as long as the scope is stated clearly. This is it.

**The problem.** A camera photographs a number plate. OCR (optical character recognition) turns the
photo into text, and OCR makes mistakes: it reads `0` as `O`, `8` as `B`, `1` as `I`. The recorded
camera data therefore contains wrong plates and other errors. We must:

1. find which registered vehicle a noisy plate really is, and the reverse;
2. clean the noisy recorded data, using the image and context such as make, model and colour.

**Our definitions.**

| Term | In our project |
|---|---|
| **Noisy data** | the camera records: the plate as OCR read it, plus the make, model and colour the camera observed, the OCR confidence, camera and time |
| **Reference data** | the registration records (the RTO database): the true plate, make, model, colour and owner of every registered vehicle |
| **Noise** | OCR errors in the plate text |
| **Anomaly** | any inconsistency in a camera record: a misread or unregistered plate, a plate that breaks the Indian format, a misspelt or conflicting make, model or colour, a duplicate record, the same plate at two distant cameras too close in time, a timestamp that cannot be read |
| **Forward matching** | noisy plate (from an image) → the registered vehicle(s) it could be |
| **Reverse matching ("vice-versa")** | a registered vehicle → every noisy camera record that could be it |
| **Cleansing** | correcting a camera record when we are sure, sending it to a person when we are not, and flagging it when the camera genuinely saw a different car |

**Out of scope, on purpose.**

- Deep-learning matchers (Siamese networks, cross-encoders). Plates are 10 characters long with a known
  set of OCR confusions; a weighted edit distance plus logistic regression is accurate and every
  decision can be explained.
- Real street photos. Our test images are rendered and then damaged in controlled ways, so we know the
  true plate of every image and can measure accuracy.
- Overwriting what a camera observed when it contradicts the registration. That could hide a cloned
  plate, so we flag it instead.

---

## 2. The data

| Data | Size | Role |
|---|---|---|
| Registration (REG) | 605 vehicles | the reference: true plate, make, model, colour |
| Camera captures (CAM) | 918 records | the noisy data being matched and cleansed |
| Natural noise in CAM | 47 plates match no registration | real misreads, for example `DL01ABO001`, `DL05CD9B76` |
| Test images | 2,200 | 100 plates, each rendered clean and with 7 kinds of damage at 3 strengths |

**The test images.** `scripts/make_plate_images.py` draws a plate image for a real registration, then
damages it: blur, motion blur, heavy JPEG compression, rotation, glare, darkness, and dirt or a bar
across it, each at severity 1, 2 and 3. `scripts/run_ocr_benchmark.py` runs EasyOCR on every image and
saves the true plate next to what OCR read, in `data/match_benchmark.csv`. The images are in
`data/plate_images/`; each file name starts with its true plate, for example
`HR26EF4455__rotate_s2.png`.

What OCR did on them:

| Measured | Result |
|---|---|
| plates read exactly right | 39.5% |
| after the pattern repair (below) | 66.4% |
| most common mistake | `0` read as `O`: 62% of all wrong characters |
| other frequent mistakes | `5`→`S`, `1`→`4`, `H`→`4`, `1`→`I` |

So exact matching fails most of the time, which is why phase 2 exists.

---

## 3. Rubric 1 — matching, and how it works

> *Given a scanned image of a car registration, find all vehicles approximately matching this noisy
> plate and vice-versa. Apply the most accurate algorithm and justify why.*

### 3.1 The pipeline in one picture

```
 image ─► EasyOCR ─► raw read + confidence ─► pattern repair (optional)
                                   │
                                   ▼
 STEP 1  find candidates      q-gram index, size filter, prefix filter      (slides 44–65)
                                   │  a short list of registered plates
                                   ▼
 STEP 2  describe each pair   10 string scores + make/model/colour agreement + OCR confidence
                                   │  one row of numbers per (read, candidate) pair
                                   ▼
 STEP 3  logistic regression  one probability per candidate                (slides 69–71)
                                   ▼
 STEP 4  calibrate & decide   ACCEPT  /  REVIEW  /  REJECT                  (slide 81)
```

This is the **two-step framework** from the Week 6–8 deck (slide 44): a cheap step that finds
candidates, then an exact step that scores them.

### 3.2 Reading the image

- **EasyOCR read (raw)** is exactly the text EasyOCR returns, with its confidence (0 to 1).
- **Pattern-repaired read** is a simple rule, *not* the learned model. Indian plates follow a fixed
  pattern: 2 letters, 2 digits, 1–3 letters, 4 digits. Where the pattern needs a digit but OCR gave a
  look-alike letter, we swap it back (`O→0`, `I→1`, `B→8`, `S→5`, `Z→2`, `G→6`), and the reverse where
  a letter is needed. You choose which read to match on.

### 3.3 Step 1 — finding candidates (blocking)

Comparing a read with all 605 registrations in detail would be slow, and with millions of vehicles
impossible. So we first narrow the list, using the indexing methods from slides 45–65.

- **q-grams.** Split a plate into overlapping pairs of characters: `DL01` → `DL`, `L0`, `01`.
- **Inverted index** (slides 47–51). For every pair, the list of registered plates containing it. A
  read is compared only with plates that share some pairs with it.
- **Size filter** (slide 53). A plate with far more or far fewer pairs than the read cannot be within a
  few errors of it, so it is dropped.
- **Prefix filter** (slides 55–58). Only the read's rarest pairs need to be looked up, which cuts the
  work further without losing true matches.

Measured over all 605 registrations, with every possible one-character misread:

| Filter | Candidates left | True plate kept |
|---|---|---|
| none | 605 | always |
| inverted index | 373 | always |
| + size filter | 1.1 | always |
| + prefix filter | 1.1 | always |

In the live matcher we allow up to 5 wrong characters, because real OCR often makes several mistakes.
That keeps more candidates; we then shortlist the closest 30 for the detailed step.

### 3.4 Step 2 — describing each pair with numbers

For each candidate, we compute a row of numbers called a **feature vector** (slide 71).

**Ten string similarity scores**, each between 0 (nothing alike) and 1 (identical):

| Measure | Slides | Idea | Example: `DL05CD9B76` vs `DL05CD9876` |
|---|---|---|---|
| edit distance similarity | 16–19 | how many single-character changes turn one into the other | 0.90 (one change) |
| OCR-weighted edit distance | 17 | the same, but a look-alike swap such as `8`↔`B` costs less | 0.98 |
| learned OCR distance | 17 | the same idea, with swap costs learned from our test images | 0.91 |
| Jaro | 20–21 | matching characters near the same position | 0.93 |
| Jaro-Winkler | 22 | Jaro, plus a bonus for a common start | 0.96 |
| overlap | 24–25 | shared character pairs, out of the smaller set | — |
| Jaccard | 26 | shared character pairs, out of all pairs | 0.69 |
| TF/IDF | 27–31 | shared pairs, where rare pairs count more | — |
| Ratcliff-Obershelp | 33–34 | longest common pieces | 0.90 |
| Soundex | 36–37 | do they sound alike when spoken | useless on plates (it ignores digits) |

**Context from the camera record:** does the observed make agree with the candidate's registration?
the model? the colour? Plus the OCR confidence, whether the first two letters (the state code) agree,
the length difference, and whether the read itself is a registered plate.

### 3.5 Step 3 — logistic regression, in plain words

Logistic regression turns that row of numbers into **one probability**: how likely is it that this
candidate is the true vehicle?

1. **Weighted sum.** Each feature gets a weight. Multiply each feature by its weight and add them up,
   plus a constant: `z = w1·x1 + w2·x2 + … + b`. A large `z` means "looks like the right car".
2. **Squash into a probability.** The sigmoid function turns any `z` into a number between 0 and 1:
   `p = 1 / (1 + e^(−z))`. Big positive `z` gives `p` near 1; big negative gives `p` near 0.
3. **Learning the weights.** We show it thousands of training pairs where we know the answer: the true
   plate is labelled 1 and every wrong candidate 0. It adjusts the weights until its probabilities
   agree with the labels as closely as possible. That is all "training" means.
4. **Fair testing.** We split the 100 test vehicles into 60 for training, 20 for tuning and 20 for the
   final test, **by vehicle**, so no car appears in both training and testing (slide 83, leakage).

Why logistic regression and not just one string score? A single score cannot weigh different kinds of
evidence against each other. Logistic regression learns, from data, how much a close spelling should
count versus a matching colour versus a low OCR confidence.

**Worked example**, the camera read `DL05CD9B76`, measured live:

| What the matcher is given | Best match | Decision |
|---|---|---|
| plate only | `DL05CD9876`, p = 0.926 | ACCEPT |
| plate + make and colour that agree (Maruti Suzuki, Silver) | `DL05CD9876`, p = 0.999 | ACCEPT, more sure |
| plate + make and colour that contradict (Mahindra, White) | best p = 0.022 | REJECT |

The plate evidence is strong on its own: only one character differs, and the nearest other registered
plate (`DL05CD0276`) is two characters away. The context raises confidence when it agrees and blocks
the match when it contradicts.

### 3.6 Step 4 — calibration and the decision

**Calibration** (slide 81) adjusts the probabilities so that "0.9" really means right about 9 times in
10 (Platt scaling). Then two thresholds, chosen on the tuning set, turn the top probability into a
decision:

| Decision | Top probability | Meaning |
|---|---|---|
| **ACCEPT** | at least 0.51, **and** at least 0.10 ahead of the runner-up | sure enough to act on |
| **REVIEW** | between 0.05 and 0.51, or two candidates too close together | a plausible candidate, but a person should check |
| **REJECT** | below 0.05 | no registered vehicle plausibly matches |

The thresholds were chosen on the tuning vehicles so that ACCEPT is right at least 98% of the time. The
margin rule is why two near-identical registered plates go to REVIEW instead of a coin toss. For
Jaro-Winkler alone, no threshold reached that precision, which is why it never accepts.

### 3.7 Why this combination — the justification

Measured on the 20 test vehicles the models never saw:

| Approach | Top-1 correct | Accepts correctly | Precision of ACCEPT | Sent to review |
|---|---|---|---|---|
| **logistic regression, strings + context** | **90.2%** | **84%** | **99.7%** | **3.9%** |
| gradient-boosted trees, same features (slide 72) | 89.5% | 83% | 99.4% | 5.5% |
| hand-weighted sum of the same features | 89.5% | 70% | 98.6% | 17% |
| best single measure (learned OCR distance) | 86.8% | 60% | 100% | 19% |
| other single measures | 84–86% | 42–61% | ~100% | 20–42% |
| phase-1 one-character rule | 60.8% | 57% | 100% | 4% |
| Soundex | 4.5% | 0% | — | — |

- **Top-1 correct:** how often the highest-ranked candidate is the true plate.
- **Accepts correctly:** of all reads, how many were confidently accepted with the right answer.
- **Precision of ACCEPT:** when it says ACCEPT, how often it is right.
- **Sent to review:** how often it asks a person.

**What to say:**

1. Every single measure ranks the right plate first about 85% of the time, but cannot be sure enough
   to accept most reads. The combination accepts 84% of reads and is right 99.7% of the time.
2. The gain comes from **context**, not from stacking string measures. Removing make, model and colour
   costs 4.5 points; strings alone score 85.2%, no better than one measure, because the string measures
   say nearly the same thing.
3. Logistic regression and boosted trees are tied; we chose logistic regression because its weights
   can be read out and explained.
4. Soundex fails because it throws digits away. Jaro-Winkler alone never accepts safely, because its
   bonus for a common start rewards every wrong plate that shares the state code, such as all `DL01…`.
5. About 5% of images are damaged beyond recovery by any method; for those the system says REVIEW or
   REJECT instead of guessing.

### 3.8 The reverse ("vice-versa")

Forward: a noisy plate → which registered car is it (one answer, or none).
Reverse: a registered car → every noisy camera record that is that car (a list, because a car is seen
many times).

1. Take a registration, say `DL05CD9876`.
2. Find camera reads close to it (the same blocking as step 1).
3. Match each of those reads **forward** against all 605 registrations, using its recorded make, model,
   colour and confidence.
4. Keep a read only if that forward match points back to `DL05CD9876`.

| Capture | Camera read | Camera saw | Decision | Why |
|---|---|---|---|---|
| 911 | `DL05CD9876` | Maruti Swift, Silver | ACCEPT, p = 1.000 | read correctly |
| 916 | `DL05CD9B76` | Maruti Swift, Silver | ACCEPT, p = 0.999 | `8` misread as `B`, car agrees |
| 502–504 | `DL05CD0276` | Mahindra XUV700, White | REJECT, p = 0.002 | a different registered car that looks similar |

Step 4 is what stops similar-looking plates of other cars from being claimed.

---

## 4. Rubric 2 — cleansing, and how it works

> *Cleanse the recorded data with respect to the image data and other contextual information so that
> there are no anomalies (including the noise) in the recorded data.*

### 4.1 The loop

```
 camera record ─► compare with live registration ─► anomalies found
                                                        │
                                                        ▼
                     Ripple-Down Rules decide, rule by rule (Week 8 deck)
                                                        │
                       ┌────────────────┬───────────────┴───────────────┐
                       ▼                ▼                               ▼
                   FIX it          SEND to a person             FLAG as clone evidence
                       │                │                               │
                       ▼                ▼                               ▼
     write to the camera agency's DB   Resolve: accept a candidate,   kept, never overwritten
     through its API, logged, undoable    confirm clone, delete, dismiss
```

### 4.2 What counts as an anomaly

| Anomaly | Example | Usual outcome |
|---|---|---|
| plate not registered | `DL05CD9B76` | fix if the matcher ACCEPTs, else send to a person |
| plate breaks the Indian format | `DL05CD9B76` has a letter where a digit belongs | as above |
| make, model or colour misspelt | `Gry`, `Maruthi` | fix to the dictionary form |
| make, model or colour conflicts with registration | camera saw a Kia, plate registered to a Hyundai | flag as clone evidence |
| same plate at two distant cameras too close in time | Gurgaon to Agra in 30 minutes | flag as clone evidence |
| low OCR confidence with nothing backing it up | confidence under 0.70, make and colour unknown | send to a person |
| duplicate record | same plate, camera and time twice | delete the copy |
| timestamp unreadable or in the future | — | send to a person |

The dictionaries of makes, models and colours are built live from the registration data, not typed in.

### 4.3 Ripple-Down Rules, in plain words

A Ripple-Down Rules (RDR) tree is a list of rules of the form
**if *condition* then *conclusion*, because of *this real case*.**

- Each rule was created from a real record, its **cornerstone case**, which shows why the rule exists.
- When a rule gets a case wrong, you never edit it. You add an **exception** underneath it ("…except
  when this, then that"). That is how the knowledge grows safely.
- Before an exception is accepted, the system checks it would not change the conclusion for any earlier
  rule's cornerstone case. If it would, it refuses. This keeps old correct behaviour intact.

Our tree, 13 rules:

| Rule | If | Then |
|---|---|---|
| R0 | default | keep the value |
| R1 | a spelling variant of a known make, model or colour | standardise it (`Gry` → `Grey`) |
| R2 | ↳ exception: that guess contradicts the registration | send to a person |
| R3 | a word no dictionary knows (`Maruthi`) | use the registered value |
| R4 | valid value that conflicts with registration, or impossible travel | flag as clone evidence |
| R5 | unregistered plate, matcher ACCEPTs | correct it to the matcher's plate |
| **R12** | ↳ exception: the candidate's registered colour or model contradicts the camera | **send to a person** |
| R6 | unregistered plate, matcher not sure | send to a person |
| R7 | registered plate, confidence below 0.70 | send to a person |
| R8 | ↳ exception: make or colour backs the read up | keep it |
| R9 | timestamp in another format | rewrite in the source's format |
| R10 | timestamp unreadable or in the future | send to a person |
| R11 | duplicate record | delete the copy |

**R12 is a good viva story.** Our validation found one wrong correction: a doubly misread plate was
corrected to a car registered Grey while the camera saw Red. That failed case became the cornerstone of
R12, added as an exception under R5, exactly as the method prescribes. Wrong plate corrections went from
1 to 0.

### 4.4 Results

On the 918 real camera records:

| Count | |
|---|---|
| anomalies found | 216 |
| fixed automatically | 79 |
| sent to a person | 21 |
| flagged as clone evidence | 116 |
| **unhandled** | **0** |

The 116 flags are deliberate: our data contains vehicles where the camera saw a different car than the
one registered. Overwriting them would make the data look clean and hide the clones. A person resolves
them on the Data Cleansing page, and each resolution is logged with a reason.

**Validation, to prove it works and not only that it runs.** We took 300 clean camera records, planted
known errors of every type, plus 25 untouched controls, and ran cleansing:

| Measured | Result |
|---|---|
| planted errors detected | 100% |
| false alarms on untouched records | 0 |
| corrected back to the original value | 95.2% |
| single-character plate misreads corrected | 100% |
| double misreads corrected | 76% (the rest sent to a person) |
| wrong plate corrections | **0** |

### 4.5 Why it is safe

- Nothing is guessed: unsure cases go to a person.
- Fixes are written to the camera agency's own database through its API, never by the mediator's
  read-only query path.
- Every write is logged with the exact SQL, and can be undone.

---

## 5. Rubric 3 — prompts, generated code, our changes

> *Show the prompt, the generated code, and highlight the changes you made.*

| Evidence | Where |
|---|---|
| every prompt, yours and the task given to each coding agent | `docs/ai/PROMPTS_PHASE2.md` |
| for each file: the AI's first version, the reviewed version, and why each change was made | `docs/AI_CHANGES_PHASE2.md` |
| every file changed in phase 2, with commits, and where the original code still sits | `docs/CHANGELOG_PHASE2.md` |
| phase-1 code that was replaced or hidden, still in the file | search for `PHASE2-REPLACED` and `PHASE2-HIDDEN` |

**How each file was built.** The AI's first draft was committed untouched as `generated: <file>`. After
review and testing, the corrected version was committed as `reviewed: <file> — <reasons>`. The
difference between the two commits is exactly "the changes we made".

```powershell
git log --oneline --grep="generated:\|reviewed:"
git diff <generated-commit> <reviewed-commit> -- mediator/blocking.py
Select-String -Path app\*.py,app\tabs\*.py,mediator\*.py,tests\*.py -Pattern "PHASE2-REPLACED|PHASE2-HIDDEN"
```

**Three changes worth showing:**

1. `mediator/blocking.py`: the AI sized the prefix filter from the query alone, which silently dropped
   true matches for plates with a repeated pair such as `DL01AB1111`.
2. `mediator/similarity.py`: the AI's test claimed the slide's Jaro example (0.81) could not be
   reproduced; its own hand calculation was wrong, and the code does give 0.81.
3. `mediator/cleansing.py`: the first version read the matcher's output in the wrong format, so every
   unregistered plate came back with no decision.

---

## 6. The pages, and how to use them

Start the system on one laptop:

```powershell
cd C:\Users\siddh_ygv5bws\IIA-vehicle-data-federation\IIA_Project
.venv\Scripts\Activate.ps1
python scripts\configure_cluster.py --local
python run_system.py
```

Open http://localhost:8501 and press Ctrl+F5. The phase-2 pages are under **Data quality** in the
sidebar. The sidebar's **Sources** chips must all say UP; if not, press **Restart local sources**.

### How it works

Two diagrams (matching pipeline, cleansing loop) and a box naming the combination with its numbers. Use
it for the first minute of the demo to show the architecture. **Rubric:** 1 (the justification, briefly).

### Plate Match — rubric 1

1. **Give a plate.** Upload an image, pick a test image (it shows the true plate so you can check), or
   type a read.
2. **See what OCR read.** The EasyOCR read and its confidence, the pattern-repaired read, and a row of
   letter boxes comparing true plate, OCR read and repaired read: green same, amber a common OCR swap,
   red wrong. Choose which read to match on and press **Match against the live registry**.
3. **Read the matches.** One table: rank, registered plate, probability, decision, whether make and
   colour agree. The top row is the answer, with one summary line such as
   `DL01AB1234 — ACCEPT (p=0.998), next best p=0.002`.

**Details**, collapsed: every measure's score for each candidate, and the same plate under every damage
level.

**Reverse**, at the bottom: type a registration, press **Find camera reads**, and see every camera record
that is that car.

| Mechanic on the page | Rubric 1 requirement |
|---|---|
| upload or pick an image, OCR read | "given a scanned image" |
| ranked list of registered plates | "find all vehicles approximately matching" |
| probability and decision | "the matching algorithm with the best accuracy" |
| reverse box | "and vice-versa" |

### Data Cleansing — rubric 2

1. **Scan.** Checks all camera records live; six counts appear, including unhandled = 0.
2. **Pick a capture.** See its anomalies and, for each field, `old → new, by rule R5 because …`.
3. **Apply** the fix, **Undo** it, or **Resolve** it yourself: accept a candidate, confirm it is a clone
   (adds it to the watchlist), delete it, or dismiss it with a note.

**Examiner toolbox**: insert, modify or delete a camera record; the page re-scans straight away.
**Watch the data change** opens after a write: the database row before and after, and the exact SQL.
**Advanced**, collapsed: the rule tree, teaching a new rule, fixing everything at once, and the
validation results.

| Mechanic on the page | Rubric 2 requirement |
|---|---|
| scan against live registration | "with respect to … other contextual information" |
| plate correction using the matcher | "with respect to the image data" |
| fix / send to a person / flag; unhandled = 0 | "no anomalies (including the noise)" |
| toolbox insert, modify, delete | "checking by asking you to insert/delete/modify the data records" |
| validation in Advanced | evidence that it works |

### Matching Evaluation — rubric 1 justification

The decision in three sentences, the comparison table from section 3.7, and one chart. Show it when asked
"why this algorithm?".

### Test scenarios — everything, end to end

Press **Run all**. Each line is one complete test with PASS or FAIL; details are collapsed. Any test that
changes data puts it back.

| Scenario | Checks | Rubric |
|---|---|---|
| clean image | a clear photo is matched | 1 |
| `0` read as `O`; `8` read as `B` | the commonest OCR mistakes are fixed | 1 |
| rotated photo, two errors | still matched | 1 |
| unreadable image | says "not sure" instead of guessing | 1 |
| unregistered plate | rejected | 1 |
| ambiguous twins | two registered plates one look-alike apart: asks a person | 1 |
| context breaks the tie | same twins, make and colour pick the right one | 1 |
| reverse search | finds the camera records for a registration | 1 |
| examiner insert / modify / delete | a dirty record is caught, fixed, edited, removed | 2 |
| misread plate cleansed | a misread plate in a record is corrected | 2 |
| source down | an agency goes offline; the system refuses to decide | 1 + 2 |
| batch stress | 100 images: 93% top-1, 100% ACCEPT precision, 29 ms typical | 1 |
| rule refusal | a rule that would break earlier cases is refused | 2 |

All 16 pass.

---

## 7. A 10-minute demo

| Minute | Show | Say |
|---|---|---|
| 0–1 | How it works | "Image → OCR → find candidates → score each with strings and context → logistic regression → ACCEPT, REVIEW or REJECT." |
| 1–4 | Plate Match: test image `HR26EF4455`, rotation, severity 2 | "OCR read `HR2GEF44551`, two characters wrong; we still match `HR26EF4455` at 0.999." Then type `DL09QQ9009`: "unregistered, so REJECT, never a confident wrong answer." |
| 4–5 | Plate Match, reverse: `DL05CD9876` | "Captures 911 and 916 are this car, including a misread; 502–504 look similar but are a different registered car." |
| 5–6 | Matching Evaluation | "Logistic regression over strings plus context: 90% top-1, 99.7% right when it accepts. Context is the biggest gain." |
| 6–9 | Data Cleansing: Scan, pick 916, Apply, Watch the data change, Undo; toolbox insert with colour `Blu` | "The rule fired, the fix went to the camera agency's database, here is the SQL, and it can be undone." |
| 9–10 | Test scenarios: Run all | "Every rubric scenario, end to end, all passing, data restored." |

---

## 8. Questions you may be asked

| Question | Answer |
|---|---|
| Why not just edit distance? | It ranks almost as well, but cannot be sure enough to accept: 60% versus 84% accepted, at the same precision. Context makes the difference. |
| What exactly does logistic regression learn? | A weight for each feature, from training pairs labelled right or wrong. The weighted sum goes through a sigmoid to become a probability. |
| Why logistic regression and not boosted trees? | They are tied on our test set; logistic regression's weights can be explained. |
| Why not deep learning? | Ten-character strings with known OCR confusions do not need it, and every decision here can be checked by hand. |
| How do you know it isn't overfitting? | We split by vehicle: the 20 test vehicles were never seen in training or tuning. |
| What does calibration do? | Makes the probabilities honest, so a threshold on them means something. |
| What if two registered plates are both close? | It answers REVIEW, unless make or colour separates them. The "ambiguous twins" scenario shows both. |
| Why not correct a colour that contradicts the registration? | It may be a cloned plate; overwriting it would destroy the evidence. It is flagged and a person resolves it. |
| How do you know the cleansing is right? | Validation with planted errors: 100% detected, 0 false alarms, 95% corrected, 0 wrong plate corrections. |
| What is a cornerstone case? | The real record that justifies a rule. New exceptions may never change a cornerstone's outcome. |
| Does cleansing write to the mediator? | No. It writes to the camera agency's own database through its API, logs the SQL, and can undo it. |
| What is "vice-versa" in your project? | From a registered car to all noisy camera records that are that car, each confirmed by matching it forward. |
| How much of this did AI write? | All phase-2 code was generated with an AI assistant; we reviewed and corrected it. Each file's generated and reviewed versions are separate commits, and the changes with reasons are in `docs/AI_CHANGES_PHASE2.md`. |
