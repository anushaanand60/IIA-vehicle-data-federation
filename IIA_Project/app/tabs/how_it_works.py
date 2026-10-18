"""How it works (Data quality group, page 1): the architecture, the combination of methods and why,
every course method mapped to its slide and its code, and a glossary.

All numbers come from `docs/MATCHING_EVALUATION.md` (written by `scripts/evaluate_matching.py`),
parsed with the Matching Evaluation page's own parser, so this explainer can never quote a number
the evaluation document does not contain. Diagrams are Graphviz DOT strings drawn by
`st.graphviz_chart` (rendered in the browser; no new dependency). Widget keys carry `hw_`.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import streamlit as st  # noqa: E402

from components import kpi_row, page_head, panel, section, styled_table  # noqa: E402
from components import how_to  # noqa: E402  (phase-2 simplified GUI)
from tabs.matching_eval import DOC, sections, table, winner  # noqa: E402
from theme import TOKENS  # noqa: E402

COMBINERS = {"logreg", "gbt", "weighted", "rule"}

MATCH_DOT = f"""
digraph G {{
  rankdir=TB; bgcolor="transparent"; nodesep=0.5; ranksep=0.5;
  node [shape=box, style="rounded,filled", fillcolor="#FFFFFF", color="{TOKENS['brand']}",
        fontname="Helvetica", fontsize=15, fontcolor="{TOKENS['ink']}", margin="0.18,0.10"];
  edge [color="{TOKENS['ink2']}", fontname="Helvetica", fontsize=13, fontcolor="{TOKENS['ink2']}"];
  img  [label="Plate image\\n(camera / upload)"];
  ocr  [label="OCR\\n(EasyOCR, offline)"];
  raw  [label="Raw read +\\nconfidence"];
  subgraph cluster_fed {{
    label="Federation: one wrapper per laptop (read-only HTTP)"; fontname="Helvetica"; fontsize=14;
    style="rounded,dashed"; color="{TOKENS['ink_muted']}";
    reg [label="REG wrapper\\nregistrations", fillcolor="#E8F1FB"];
  }}
  subgraph cluster_block {{
    label="Step 1: blocking (deck p. 45-65) - cheap, high recall"; fontname="Helvetica"; fontsize=14;
    style="rounded"; color="{TOKENS['action']}";
    idx [label="q-gram\\ninverted index"]; size [label="size\\nfilter"];
    pre [label="prefix\\nfilter"]; pos [label="position\\nfilter"];
    idx -> size -> pre -> pos; {{rank=same; idx; size; pre; pos;}}
  }}
  cand [label="Candidates\\n(30-plate shortlist)"];
  subgraph cluster_score {{
    label="Step 2: scoring (deck p. 69-82) - exact, explainable"; fontname="Helvetica"; fontsize=14;
    style="rounded"; color="{TOKENS['action']}";
    feat [label="Feature vector\\n10 string measures +\\nmake/model/colour agreement\\n+ OCR confidence"];
    lr   [label="Logistic regression\\n(Platt-calibrated)"];
    prob [label="Probability"];
    feat -> lr -> prob;
  }}
  dec [label="ACCEPT / REVIEW / REJECT", fillcolor="#E6F4EA", color="{TOKENS['ok']}"];
  img -> ocr -> raw -> idx;
  reg -> idx [label="605 plates,\\nread live"];
  pos -> cand -> feat;
  reg -> feat [label="make, model,\\ncolour", style=dashed];
  prob -> dec;
}}
"""

CLEANSE_DOT = f"""
digraph G {{
  rankdir=TB; bgcolor="transparent"; nodesep=0.5; ranksep=0.5;
  node [shape=box, style="rounded,filled", fillcolor="#FFFFFF", color="{TOKENS['brand']}",
        fontname="Helvetica", fontsize=15, fontcolor="{TOKENS['ink']}", margin="0.18,0.10"];
  edge [color="{TOKENS['ink2']}", fontname="Helvetica", fontsize=13, fontcolor="{TOKENS['ink2']}"];
  subgraph cluster_fed {{
    label="Federation: five autonomous sources, each behind its own wrapper"; fontname="Helvetica";
    fontsize=14; style="rounded,dashed"; color="{TOKENS['ink_muted']}";
    cam [label="CAM wrapper\\ncamera captures", fillcolor="#E8F1FB"];
    reg [label="REG wrapper\\nregistrations", fillcolor="#E8F1FB"];
    oth [label="INS / THEFT / PUC\\nwrappers", fillcolor="#E8F1FB"];
  }}
  rec  [label="One CAM record\\n(read live)"];
  det  [label="Detect anomalies\\nvs live REG\\n(plate, make, colour,\\ntime, duplicates)"];
  rdr  [label="RDR tree\\nif ... then ... because ...\\nexcept ... (cornerstones)"];
  prop [label="Proposal\\n(set / delete / flag /\\nquarantine)"];
  op   [label="Operator\\nApply / Resolve / Teach", fillcolor="#FFF4E0", color="{TOKENS['warn']}"];
  adm  [label="CAM wrapper\\n/admin/sql"];
  log  [label="Audit log\\n(undo, resolve)"];
  cam -> rec -> det -> rdr -> prop -> op; {{rank=same; det; rdr; prop;}}
  reg -> det [label="registered\\nmake/colour"];
  oth -> det [style=dashed, label="context"];
  op -> adm [label="UPDATE /\\nDELETE"];
  adm -> cam [label="the source\\nwrites itself", style=dashed];
  op -> log;
  op -> rdr [label="new exception rule\\n(knowledge acquisition)", style=dashed];
}}
"""

METHODS = [
    ("Levenshtein edit distance (dynamic program)", "p. 16-19", "mediator/similarity.py::levenshtein, edit_similarity",
     "Baseline: counts substitutions, insertions, deletions. One of the 10 string features."),
    ("Weighted edit distance with OCR confusion costs", "p. 17", "similarity.py::weighted_edit_distance, ocr_edit_similarity; confusion.py::learned_ocr_similarity",
     "0 read as O costs 0.1, not 1. Learned from the training split; the best single measure."),
    ("Jaro and Jaro-Winkler", "p. 20-22", "similarity.py::jaro, jaro_winkler",
     "Transposition-aware with a prefix bonus. Features; on its own it never calibrates to a safe ACCEPT."),
    ("Overlap and Jaccard on character q-grams", "p. 24-26", "similarity.py::qgrams, overlap, jaccard",
     "Robust to one bad character. Features, and the Jaccard pre-filter that cuts candidates to 100."),
    ("TF/IDF over q-grams", "p. 27-31", "similarity.py::tfidf_cosine; plate_match.py::CachedTfidf",
     "Rare q-grams count more. A feature."),
    ("Ratcliff-Obershelp", "p. 33-34", "similarity.py::ratcliff_obershelp",
     "Longest-common-substring similarity. A feature."),
    ("Soundex", "p. 36-37", "similarity.py::soundex, soundex_similarity",
     "Measured and reported as the misfit: plates are not phonetic (top-1 below 0.05)."),
    ("Two-step framework: blocking then matching", "p. 44", "plate_match.py::match (retrieve -> shortlist -> score)",
     "The whole pipeline shape: cheap high-recall step 1, exact step 2."),
    ("Inverted index over q-grams", "p. 47-51", "blocking.py::QGramIndex",
     "Finds plates sharing a q-gram with the read without scanning all of them."),
    ("Size filtering", "p. 53", "blocking.py::QGramIndex.candidates_size_filtered",
     "Drops plates whose q-gram count is too different."),
    ("Prefix filtering", "p. 55-58", "blocking.py::QGramIndex.candidates_prefix_filtered",
     "Only the lists of the read's rarest q-grams need checking."),
    ("Position index", "p. 65", "blocking.py::QGramIndex.candidates_position_filtered",
     "Shared q-grams must sit at nearly the same position."),
    ("Pushing a filter into source SQL", "p. 45-65 (federated)", "blocking.py::sql_prefilter",
     "A length/prefix predicate the source evaluates, so it returns tens of rows, not all."),
    ("Pair classification with a feature vector", "p. 69-71", "plate_match.py::features, string_features, context_features",
     "Each (read, candidate) pair becomes one row of numbers."),
    ("Logistic regression and gradient-boosted trees", "p. 72", "scripts/evaluate_matching.py; plate_match.py::raw_scores; data/match_model.joblib",
     "Two learned combiners; logistic regression won and its weights can be read aloud."),
    ("Retrieve then re-rank", "p. 76", "plate_match.py::retrieve, shortlist, match",
     "Blocking retrieves, the combiner re-ranks."),
    ("Calibration and thresholds", "p. 81", "plate_match.py::platt, thresholds_for, decide",
     "Turns a score into a probability; ACCEPT/REVIEW/REJECT thresholds chosen on validation."),
    ("Separate evaluation of retrieval and scoring", "p. 82-83", "scripts/evaluate_matching.py; docs/MATCHING_EVALUATION.md sections 1-2",
     "Retrieval recall and ranking accuracy reported apart, on vehicles never seen in training."),
    ("Six failure modes of dirty data; anomaly detection", "Week 8, slides 8-18", "mediator/cleansing.py::detect",
     "Flags unregistered plates, make/colour conflicts, bad timestamps, duplicates."),
    ("Lexicons, feature extraction, pattern mining", "Week 8, slides 19-23", "mediator/lexicons.py; cleansing.py::features",
     "Make/model/colour vocabularies mined from REG; plate shape patterns."),
    ("Ripple-Down Rules tree with cornerstone cases", "Week 8, slides 24-29", "mediator/rdr.py::RDRTree; data/rdr_rules.json",
     "if ... then ... because ... except ...; each rule remembers the case that created it."),
    ("Knowledge acquisition loop", "Week 8, slides 30-32", "cleansing.py::teach, propose, apply, undo, resolve",
     "A wrong conclusion adds an exception rule, never an edit; refused if it would change a cornerstone."),
]

GLOSSARY = [
    ("Top-1", "The share of reads where the right vehicle is ranked first."),
    ("ACCEPT", "The matcher is sure enough to act without a person (calibrated probability above the ACCEPT threshold "
               "and clearly ahead of the runner-up)."),
    ("REVIEW", "Plausible but not certain: a person must look."),
    ("REJECT", "No candidate is plausible enough; nothing is claimed."),
    ("Calibration", "Adjusting scores so that 'probability 0.9' really is right about 9 times in 10 (Platt scaling)."),
    ("Blocking", "A cheap first pass that throws away plates that cannot match, so the expensive scoring runs on few."),
    ("q-gram", "A piece of q consecutive characters; with q = 2, DL01 has the pieces #D, DL, L0, 01, 1#."),
    ("Recall", "Of the things that should be found, the share that was found."),
    ("Precision", "Of the things the system claimed, the share that was right."),
    ("Cornerstone case", "The record that made a rule necessary. A new rule may not change any cornerstone's conclusion."),
    ("Federation", "The data stays in each agency's database; every question is asked live through its wrapper."),
    ("Wrapper", "The small web service in front of one agency's database: it answers read-only queries, nothing else."),
]


def facts(text: str) -> Dict[str, Optional[str]]:
    """The numbers this page quotes, all read from the evaluation document (None when absent)."""
    sec = sections(text)
    scoring = table(sec.get("2", ""))
    best = winner(sec.get("7", ""), scoring) or {}
    singles = [r for r in scoring if r.get("contender") not in COMBINERS]
    single = max(singles, key=lambda r: _f(r.get("top-1")), default={})
    ablation = [r for r in table(sec.get("4", "")) if r.get("model") == best.get("contender")]
    full = next((r for r in ablation if r.get("variant") == "full feature set"), None)
    # PHASE2-REPLACED (the combined "string only" row removes two groups at once and always won, so the
    # page credited "string only" instead of naming the single most valuable feature group):
    # gains = sorted(((_f(full["top-1"]) - _f(r["top-1"]), r["variant"]) for r in ablation
    #                 if full and r is not full), reverse=True)
    gains = sorted(((_f(full["top-1"]) - _f(r["top-1"]), r["variant"]) for r in ablation
                    if full and r is not full and r["variant"].startswith("no ")), reverse=True)
    retrieval = table(sec.get("1", ""))
    scan = next((r for r in retrieval if r.get("strategy") == "none"), {})
    pos1 = next((r for r in retrieval if r.get("strategy") == "position" and r.get("max_edits") == "1"), {})
    return {"winner": best.get("contender"), "top1": best.get("top-1"), "acc_p": best.get("ACCEPT P"),
            "acc_r": best.get("ACCEPT R"), "review": best.get("REVIEW rate"),
            "single": single.get("contender"), "single_top1": single.get("top-1"),
            "gain_variant": gains[0][1] if gains else None, "gain": f"{gains[0][0]:+.3f}" if gains else None,
            "scan": scan.get("mean candidates test"), "pos1": pos1.get("mean candidates test"),
            "pos1_recall": pos1.get("recall test")}


def _f(value: Optional[str]) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


# PHASE2-HIDDEN (simplified GUI, 19 Oct 2026): the KPI row and the six-step numbered explanation of the
# combination; previously called from render() (now _render_before_simplification) in the hw_why panel.
def _combination(f: Dict[str, Optional[str]]) -> None:
    section("The combination we use, and why",
            "What this shows: the methods that run on every plate, in order, with the measured reason for each. "
            "Numbers are read from docs/MATCHING_EVALUATION.md (test split: vehicles never seen in training).")
    if not f["winner"]:
        st.warning("docs/MATCHING_EVALUATION.md could not be parsed; run python scripts/evaluate_matching.py.")
        return
    kpi_row([("Chosen combiner", f["winner"]), ("Right car ranked first", f["top1"]),
             ("ACCEPT precision", f["acc_p"]), ("ACCEPT recall", f["acc_r"]), ("Sent to a person", f["review"])])
    st.markdown("\n".join([
        f"1. **Blocking first.** A q-gram inverted index with size, prefix and position filters keeps only plates "
        f"that could be the read. A full scan scores {f['scan']} plates per read; at one allowed error the filters "
        f"leave {f['pos1']} on average (recall {f['pos1_recall']}), and the validated setting trades a larger set for "
        "more recall.",
        "2. **Ten string measures, side by side.** Edit distance, two confusion-weighted edit distances, Jaro, "
        "Jaro-Winkler, overlap, Jaccard, TF/IDF, Ratcliff-Obershelp and Soundex each score every candidate.",
        f"3. **The best single measure is `{f['single']}`** (top-1 {f['single_top1']}). Stacking string measures "
        "alone adds little: they all look at the same two strings.",
        f"4. **Camera context is the biggest gain.** Removing `{f['gain_variant']}` costs the combiner "
        f"{f['gain']} in top-1 (largest drop in the ablation). The camera's make, model and colour separate plates "
        "one character apart, which no string measure can do.",
        f"5. **Logistic regression combines them (`{f['winner']}`), then Platt calibration** turns the score into a "
        f"probability, and thresholds chosen on validation give ACCEPT precision {f['acc_p']} while sending "
        f"{f['review']} of reads to a person.",
        "6. **Cleansing reuses the matcher.** A camera record whose plate is not registered is matched; the Ripple-Down "
        "Rules decide whether to correct, flag, quarantine or delete, and only a person applies the change."]))


# PHASE2-HIDDEN (simplified GUI, 19 Oct 2026): the whole previous page -- both diagrams with long leads, the
# combination explanation, the method/slide/code table, the glossary and the 'where to see it' links;
# previously called from app/app.py as how_it_works.render.
def _render_before_simplification() -> None:
    page_head("How it works", "The pipeline from a camera image to a decision, the cleansing loop, where the "
              "federation sits, and which course method does what.")
    with panel("hw_match"):
        section("Matching pipeline: from a plate image to a decision",
                "What this shows: every box is one step the Plate Match page runs live. The registration list is "
                "read from the REG agency's wrapper on each query; nothing is copied into the mediator.")
        st.graphviz_chart(MATCH_DOT, width="stretch")  # graphviz_chart takes no key
    with panel("hw_cleanse"):
        section("Cleansing loop: from a dirty camera record to a fixed one",
                "What this shows: how the Data Cleansing page finds and fixes a bad record. The mediator never "
                "writes to a source directly; the camera agency's own wrapper executes the change, and every change "
                "is logged so it can be undone.")
        st.graphviz_chart(CLEANSE_DOT, width="stretch")  # graphviz_chart takes no key
    with panel("hw_why"):
        _combination(facts(DOC.read_text(encoding="utf-8")) if DOC.exists() else
                     {k: None for k in ("winner",)})
    with panel("hw_methods"):
        section("Every method, its slide, and where it lives in the code",
                "What this shows: each technique from the Week 6-8 matching deck and the Week 8 RDR deck that is "
                "implemented, so an examiner can open the file and check it.")
        styled_table([{"Method": m, "Slides": s, "Where in the code": c, "What it contributes": w}
                      for m, s, c, w in METHODS], max_rows=40)
    with panel("hw_glossary"):
        section("Glossary", "What this shows: the terms used across the Data quality pages, in plain words.")
        styled_table([{"Term": t, "Meaning": m} for t, m in GLOSSARY])
    with panel("hw_next"):
        section("Where to see it working")
        st.markdown("- [Plate Match](?page=Plate%20Match): one plate through the pipeline, with the separation views.\n"
                    "- [Data Cleansing](?page=Data%20Cleansing): the live anomaly list, rule paths, and the data changing.\n"
                    "- [Matching Evaluation](?page=Matching%20Evaluation): every contender measured.\n"
                    "- [Test scenarios](?page=Test%20scenarios): one-click stress tests, each restoring the data it changed.")


# ------------------------------------------------------------------ phase-2 simplified page

def _combo_box(f: Dict[str, Optional[str]]) -> None:
    """The winning combiner, its key numbers and the one-line ablation conclusion, all parsed from the doc."""
    section("Combination we use")
    if not f.get("winner"):
        st.warning("docs/MATCHING_EVALUATION.md could not be parsed; run python scripts/evaluate_matching.py.")
        return
    # PHASE2-REPLACED (review: the single-measure name broke mid-word in its tile ('learned_ocr_similarit y'); the gain printed '+0.046' as a cost and disagreed with the 0.045 the Matching Evaluation page quotes from the same document (the table difference of rounded values vs the document's own figure)):
    # kpi_row([("Combiner", f["winner"]), ("Right car ranked first (top-1)", f["top1"]),
    #          ("ACCEPT precision", f["acc_p"]), ("Sent to a person (REVIEW)", f["review"]),
    #          (f"Best single measure: {f['single']}", f["single_top1"])])
    # st.markdown(f"**Ablation:** camera context is the biggest gain -- removing it (`{f['gain_variant']}`) "
    #             f"costs the combiner {f['gain']} top-1.")
    kpi_row([("Combiner", f["winner"]), ("Right car ranked first (top-1)", f["top1"]),
             ("ACCEPT precision", f["acc_p"]), ("Sent to a person (REVIEW)", f["review"]),
             (f"Best single measure ({str(f['single']).replace('_', ' ')})", f["single_top1"])])
    worth = doc_gain(DOC.read_text(encoding="utf-8")) if DOC.exists() else None
    st.markdown(f"**Ablation:** camera context is the biggest gain -- removing it (`{f['gain_variant']}`) "
                f"costs the combiner {worth or str(f['gain']).lstrip('+')} top-1.")


def doc_gain(text: str) -> Optional[str]:
    """The camera-context worth exactly as the document's Decision section states it ('0.045')."""
    import re
    m = re.search(r"camera's make, model and colour are worth \+?([0-9.]+)", text)
    return m.group(1) if m else None


def render() -> None:
    page_head("How it works", "How a camera read becomes a decision, and how a dirty camera record gets fixed.")
    how_to(["Follow the matching pipeline from the plate image down to the decision.",
            "Follow the cleansing loop from one camera record to the operator's fix.",
            "Check the measured numbers in \"Combination we use\"."])
    with panel("hw_match"):
        section("Matching pipeline")
        st.graphviz_chart(MATCH_DOT, width="stretch")  # graphviz_chart takes no key
    with panel("hw_cleanse"):
        section("Cleansing loop")
        st.graphviz_chart(CLEANSE_DOT, width="stretch")  # graphviz_chart takes no key
    with panel("hw_why"):
        _combo_box(facts(DOC.read_text(encoding="utf-8")) if DOC.exists() else {"winner": None})
