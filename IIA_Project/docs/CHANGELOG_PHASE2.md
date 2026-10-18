# Phase 2 changelog (since `ee797b2`, the phase-2 plan)

A reference for the viva: where every phase-2 change is, and how to show it. Hashes come from
`git log ee797b2..HEAD`; nothing here is guessed. Paths are relative to `IIA_Project/`.

---

## 1. How to view any change

| To see | Command (PowerShell or bash, run in `IIA_Project/`) |
|---|---|
| every phase-2 commit touching a file | `git log --oneline ee797b2..HEAD -- app/tabs/plate_match_lab.py` |
| what the human review changed in the AI's first version | `git diff <generated> <reviewed> -- <file>` e.g. `git diff 83ef041 b92d7dd -- app/tabs/plate_match_lab.py` |
| one commit in full (message + diff) | `git show <commit>` e.g. `git show 498d6b4` |
| the whole phase-2 diff of one file | `git diff ee797b2 HEAD -- <file>` |
| a file as it was before phase 2 | `git show ee797b2:IIA_Project/<file>` |
| every inline marker of replaced / hidden code | `Select-String -Path app\tabs\*.py,mediator\*.py -Pattern "PHASE2-REPLACED\|PHASE2-HIDDEN"` (bash: `grep -n "PHASE2-REPLACED\|PHASE2-HIDDEN" app/tabs/*.py mediator/*.py`) |
| markers in the pre-existing files too | add `app\app.py,app\components.py,app\theme.py,sources\*.py,tests\*.py` to `-Path` |

Marker meanings:

- `# PHASE2-REPLACED (<reason>):` — the commented lines directly under it are the original code; the
  live line(s) after them replace it.
- `# PHASE2-HIDDEN (simplified GUI, 19 Oct 2026): <what it rendered>; previously called from <where>.` —
  the function under it is kept intact but no longer called (the simplified page does not draw it).

Commit message convention (AI-disclosure workflow): `generated: <path> (unreviewed AI output)` is the
AI's first version, `reviewed: <path> — <reasons>` is the version after the human review; the diff
between the two is the review. `docs/AI_CHANGES_PHASE2.md` explains each workstream.

---

## 2. Every file changed in phase 2

"new" = created in phase 2; "pre-existing" = a phase-1 file changed (its markers are in section 3).
Several files were reviewed more than once; every generated -> reviewed pair is listed.

### Matching

| File | New / pre-existing | What changed | generated -> reviewed | Other commits |
|---|---|---|---|---|
| `mediator/similarity.py` | new | 10 string measures from the Week 6-8 deck (edit, OCR-weighted edit, Jaro, Jaro-Winkler, overlap, Jaccard, TF/IDF, Ratcliff-Obershelp, Soundex) | `2974efd` -> `5f3352c` | — |
| `mediator/blocking.py` | new | q-gram inverted index with size, prefix and position filters; SQL pre-filter | `76e5b92` -> `93312c5` | `219337f` (empty index rebuilt) |
| `mediator/confusion.py` | new | OCR confusion costs learned from the training split | `65beee4` -> `dff6b33` | `9d4eacf` |
| `mediator/plate_match.py` | new | retrieve -> shortlist -> feature vector -> calibrated combiner -> ACCEPT/REVIEW/REJECT; reverse match | `d1c773e` -> `25e5bd8`, -> `9d4eacf` (second pass) | — |
| `mediator/matcher.py` | pre-existing | phase-1 Jaro-Winkler moved to `similarity.py` (original kept as PHASE2-REPLACED) | (in `2974efd`) | `3d533b9` |
| `scripts/evaluate_matching.py` | new | seeded evaluation of every contender; writes `docs/MATCHING_EVALUATION.md`, `data/match_model.joblib`, `data/confusion_costs.json` | `f800bff` -> `c7fc071` | — |
| `scripts/match_plate.py` | new | CLI fallback + helpers the Plate Match page shares (live registry, funnel, reverse rows) | `27b4e97` -> `77d0ffc` | — |
| `docs/MATCHING_EVALUATION.md`, `data/match_model.joblib`, `data/confusion_costs.json` | new | generated outputs of the evaluation | — | `c7fc071` |

### Cleansing

| File | New / pre-existing | What changed | generated -> reviewed | Other commits |
|---|---|---|---|---|
| `mediator/rdr.py` | new | Ripple-Down Rules tree with cornerstone cases; knowledge acquisition refuses rules that change a cornerstone | `71d88aa` -> `2331b58` | — |
| `mediator/lexicons.py` | new | make/model/colour lexicons mined live from REG; plate patterns | `ec6dd4d` -> `e8d91df` | — |
| `mediator/cleansing.py` | new | detect / propose / apply / undo / teach over CAM, through the wrapper's `/admin/sql` | `d65cfba` -> `601c44c` | resolve step `c324000` -> `19484fc`, -> `2aee597`; ADMIN_LOG `28c41ce` -> `2285df9`; colour/model veto `f19ce97` -> `955006b` |
| `mediator/cleansing_validation.py` | new | injects known noise into clean records and scores detection / correction | `14c79ac` -> `7710059` | — |
| `mediator/change_audit.py` | new | raw before/after source rows and the exact SQL sent (ADMIN_LOG) | `28c41ce` -> `2285df9` | — |
| `scripts/cleanse.py` | new | CLI for scan / propose / apply / undo / teach | `13d4c09` -> `7e0679a` | — |
| `scripts/validate_cleansing.py` | new | runs the validation, writes `docs/CLEANSING_VALIDATION.md` + `data/cleansing_validation.json` | `14c79ac` -> `7710059` | `640edb1` |
| `scripts/add_colour_veto_rule.py` | new | adds R12 under R5 through `RDRTree.add_exception` from the validation case | — | `640edb1` |
| `data/rdr_rules.json` | new | seed tree (12 rules, real CAM cornerstones), then R12 | — | `2702f6e`, `4681db2` |
| `data/cleansing_validation.json`, `docs/CLEANSING_VALIDATION.md` | new | validation outputs (seed 20261017, n=300) | — | `7710059`, `4681db2` |

### Benchmark

| File | New / pre-existing | What changed | generated -> reviewed | Other commits |
|---|---|---|---|---|
| `scripts/make_plate_images.py` | new | renders ~2200 plate images under 7 degradations x 3 severities (seeded) | `efd5479` -> `c6d6313` | — |
| `scripts/run_ocr_benchmark.py` | new | runs EasyOCR over every image -> `data/match_benchmark.csv` | `9980ca9` -> `fea4497` | `7b7277a` |
| `data/match_benchmark.csv`, `docs/BENCHMARK_NOISE.md` | new | the labelled noisy-plate benchmark | — | `7b7277a` |

### GUI

| File | New / pre-existing | What changed | generated -> reviewed | Other commits |
|---|---|---|---|---|
| `app/tabs/plate_match_lab.py` | new | Plate Match page; simplified on 19 Oct to input -> OCR read -> matches, details collapsed | `129ce05` -> `f940034`, -> `0fc549b`; `bb9c521` -> `2d5a56f`; `83ef041` -> `b92d7dd` | — |
| `app/tabs/plate_separation.py` | new | alignment of the read with the true plate, margins, damage grid | `1acb3c1` -> `3922aa0`; `83ef041` -> `b92d7dd` | — |
| `app/tabs/cleansing_tab.py` | new | Data Cleansing page; simplified to scan -> pick -> fix / undo / resolve, advanced tools collapsed | `d6ba3f3` -> `67ad084`, -> `a9c9942`; `0cd34ca` -> `7a76fe9`; `7eecaa9` -> `d4926c7` | — |
| `app/tabs/cleansing_toolbox.py` | new | examiner insert / modify / delete of a capture; compact layout | `d6ba3f3` -> `67ad084`; `7eecaa9` -> `d4926c7` | `ee7462d` |
| `app/tabs/change_watch.py` | new | "Watch the data change": before/after row + SQL; compact expander | `0cd34ca` -> `7a76fe9`, -> `5364c8c`; `7eecaa9` (unchanged in review `d4926c7`) | — |
| `app/tabs/cleansing_validation_panel.py` | new | validation results panel | `0cd34ca` -> `7a76fe9`, -> `5364c8c`; `7eecaa9` (unchanged in review) | — |
| `app/tabs/how_it_works.py` | new | two diagrams + "Combination we use" box | `0fd1b22` -> `798b513`; `55cea90` -> `498d6b4` | — |
| `app/tabs/matching_eval.py` | new | Matching Evaluation page parsed from the evaluation document | `88dae79` -> `149d50a`; `c152ed3` -> `913f78e` | `ee7462d` |
| `app/tabs/scenarios_tab.py` | new | Test scenarios: one line per scenario, Run all, details collapsed | `eaf88a5` -> `87eb853`, -> `2244aae`; `2a76bfe` -> `a68dcb8` | — |
| `app/tabs/source_status.py` | new | sidebar source status + Restart button | `c68ae42` -> `1f40735` | — |
| `app/app.py` | pre-existing | Data quality menu group (5 pages), per-rerun healer call | `0300d26` -> `9c40031`; `e6f9647` -> `6dda5d8` | `1d52a16`, `bff7110` |
| `app/theme.py` | pre-existing | Data quality nav heading, larger captions, how-to box, best-row highlight, six-tile KPI rows | `0300d26` -> `9c40031`; `2792719` -> `7ea2773` | `ee7462d` |
| `app/components.py` | pre-existing | `what_this_shows()` added, then silenced; `how_to()` steps primitive | `2792719` -> `7ea2773` | `ee7462d` |

### Infrastructure

| File | New / pre-existing | What changed | generated -> reviewed | Other commits |
|---|---|---|---|---|
| `sources/server_manager.py` | pre-existing | self-healing: restarts a dead local wrapper on the next rerun (`heal_local_sources`) | `63d7247` -> `6dda5d8` | — |
| `tests/conftest.py` | pre-existing | `local_cluster` refuses to start when ports 8001-8005 are in use (never writes into a live source) | — | `788fd71` |
| `tests/test_query_log.py` | pre-existing | counts past the 100-row default of `get_query_log` | — | `788fd71` |
| `tests/test_self_check.py`, `tests/test_tabs_render.py` | pre-existing | nav counts 10 -> 13 -> 15, `app.py` line limit 150 -> 160 | (in `9c40031`) | `4247208`, `bff7110` |
| `requirements.txt` | pre-existing | scikit-learn, joblib | — | `c7fc071` |
| `.gitignore` | pre-existing | rendered plate images out of git; manifest and benchmark CSV in | — | `c17f453` |
| new tests | new | `test_similarity`, `test_blocking`, `test_confusion`, `test_plate_match`, `test_plate_images`, `test_match_plate_cli`, `test_rdr`, `test_cleansing`, `test_cleansing_validation`, `test_change_audit`, `test_scenarios`, `test_server_manager_heal`, `test_plate_match_lab`, `test_cleansing_tab`, `test_matching_eval_tab`, `test_gui_refinement` | see `git log --oneline ee797b2..HEAD -- tests/<file>` | `f4aa468` (simplified GUI) |
| `mediator/scenarios.py` | new | one-click scenario runner that restores what it changes | `1408ec5` -> `5550ca0` | — |
| `docs/AI_CHANGES_PHASE2.md` | new | AI change disclosure per workstream (A-I) | — | see `git log -- docs/AI_CHANGES_PHASE2.md` |

---

## 3. Pre-existing (phase-1) files changed in phase 2, with marker locations

Line numbers are as of this changelog's commit.

| File | Marker (file:line) | What the original was |
|---|---|---|
| `app/app.py` | `app/app.py:26` PHASE2-REPLACED | import of `get_cluster` only (healer added) |
| | `app/app.py:73` PHASE2-REPLACED | the phase-1 PAGES comment (Enforcement / Citizens / Mediator only) |
| `app/components.py` | `app/components.py:172` PHASE2-REPLACED | the body of `what_this_shows()` (the "What this shows:" line, silenced 19 Oct) |
| `app/theme.py` | `app/theme.py:203` PHASE2-REPLACED | sidebar group-heading rule without the Data quality heading |
| | `app/theme.py:210` PHASE2-REPLACED | heading space for items 1, 5, 6 only |
| | `app/theme.py:225` PHASE2-REPLACED | divider for items 5, 6 only |
| `mediator/matcher.py` | `mediator/matcher.py:91` PHASE2-REPLACED | phase-1 `jaro_winkler` implementation |
| `sources/server_manager.py` | `sources/server_manager.py:11` PHASE2-REPLACED | `typing` import without `Set` |
| `tests/test_query_log.py` | `tests/test_query_log.py:117`, `:124` PHASE2-REPLACED | the two log reads with the default 100-row cap |
| `tests/test_self_check.py` | `tests/test_self_check.py:243`, `:245` PHASE2-REPLACED | nav count 10, then 13 |
| `tests/test_tabs_render.py` | `tests/test_tabs_render.py:273` PHASE2-REPLACED | `app.py` under 150 lines |
| | `tests/test_tabs_render.py:285`, `:292` PHASE2-REPLACED | nav lists of 10, then 13 pages |
| `tests/conftest.py`, `requirements.txt`, `.gitignore` | none (pure additions) | `git diff ee797b2 HEAD -- <file>` shows only added lines |

---

## 4. The GUI simplification round (19 Oct 2026)

Each page now starts with its title, one sentence, and a numbered "How to use" list (`components.how_to`).
"What this shows" sentences are silenced in one place (`app/components.py:172`); their call sites stay.
On every page the previous `render()` is kept, renamed `_render_before_simplification()` and headed by a
PHASE2-HIDDEN marker; the new `render()` is at the end of the file.

| Page | What was hidden from the page | Where it now lives |
|---|---|---|
| How it works | the long leads, the six-step explanation, the method / slide / code table, the glossary, the "where to see it" links | `how_it_works.py:197` `_combination` (HIDDEN); `:227` `_render_before_simplification` (HIDDEN, contains the method table, glossary and links); `METHODS` / `GLOSSARY` constants unchanged |
| Plate Match | blocking funnel and the max_edits selector; two-row diff with cost table; two candidate tables and chips; "Why this algorithm"; reverse make/model/colour columns and chips | `plate_match_lab.py:102` `_input_panel`, `:126` `_read_panel` (max_edits selector), `:185` `_funnel_panel`, `:204` `_candidates_panel`, `:233` `_reverse_panel`, `:269` `_separation`, `:287` `_render_before_simplification` ("Why this algorithm"); `plate_separation.py:156` `render_diff`. Per-measure scores, probability chart, margins and the 22-image grid moved into one collapsed "Details" expander (`_matches`) |
| Data Cleansing | eight KPIs; long table columns; JSON evidence table; open rule paths and cornerstone lines; the separate Resolve panel; full watch table + registration row; stacked toolbox; open rule tree / teach / bulk / validation | `cleansing_tab.py:69` `_kpis`, `:85` `_table`, `:129` `_fix_view`, `:141` `_record_panel`, `:186` `_resolve_panel`, `:288` `_render_before_simplification`; `change_watch.py:114` `render`; `cleansing_toolbox.py:107` `render`. Rule tree, teach, bulk apply and validation now sit in the collapsed "Advanced" expander; rule path in a small "Rule path" expander |
| Matching Evaluation | six KPIs, the full "Why" prose, and the scoring / ablation / blocking / weights / degradation / real-read panels all open | `matching_eval.py:93` `_render_before_simplification`; the new page keeps the comparison table, one chart and a three-sentence decision (`decision_text`), the rest in a collapsed "Details" expander |
| Test scenarios | rubric-named groups, "What it does / Why it matters" prose, results drawn open | `scenarios_tab.py:147` `_result`, `:163` `_scenario`, `:180` `_render_before_simplification`; now one "title — what it checks" line (`CHECKS`), inline PASS/FAIL chip, details collapsed |

Commits of this round: `2792719`/`7ea2773` (components + theme), `55cea90`/`498d6b4` (How it works),
`83ef041`/`b92d7dd` (Plate Match), `7eecaa9`/`d4926c7` (Data Cleansing), `c152ed3`/`913f78e` (Matching
Evaluation), `2a76bfe`/`a68dcb8` (Test scenarios), `f4aa468` (tests), `f4a51a7` (screenshots).
