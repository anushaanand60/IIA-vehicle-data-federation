"""Plate Match page (rubric 1): a noisy plate read -> the registered vehicles it could be, and back.

Forward: an image (uploaded or one of the benchmark renders) goes through `plate_ocr.read_plate`,
whose raw EasyOCR text is shown next to its pattern-licensed repairs; the chosen read is blocked
(`blocking.candidate_plates`, one call per cumulative filter so the funnel is visible) and scored by
`plate_match.match` against the registry read LIVE from the REG wrapper. Reverse ("vice-versa"):
a registration number -> every CAM capture whose read could be it, via `plate_match.reverse_match`
over captures read live from the CAM wrapper. Nothing here re-implements matching; the shared
helpers live in `scripts/match_plate.py` so the CLI fallback prints exactly what this page shows.
Widget keys carry the `pm_` prefix.
"""
from __future__ import annotations

import csv
import hashlib
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import streamlit as st  # noqa: E402

from components import chip, chip_strip, group_label, kpi_row, page_head, panel, section, styled_table  # noqa: E402
from components import what_this_shows  # noqa: E402  (phase-2 accessibility pass)
from components import how_to  # noqa: E402  (phase-2 simplified GUI)
import html  # noqa: E402  (phase-2 simplified GUI: the results table and the character rows)
from mediator import confusion  # noqa: E402  (phase-2 simplified GUI: colours the character rows)
from mediator import plate_match, plate_ocr  # noqa: E402
from mediator import federated_match, vehicle_vision  # noqa: E402  (PHASE3-ADDED: C1 every database, C2 photo)
from scripts.match_plate import (funnel, live_captures, live_registry, max_edits, reverse_rows,  # noqa: E402
                                 run_ocr)
from tabs import plate_separation  # noqa: E402
from tabs import examiner_toolbox  # noqa: E402  (phase-2 examiner pass)
from theme import TOKENS  # noqa: E402

BENCHMARK = _ROOT / "data" / "match_benchmark.csv"
DECISION_TOKEN = {"ACCEPT": "ok", "REVIEW": "warn", "REJECT": "bad"}
# PHASE3-REPLACED (C2: the labelled photo set is a fourth way to give a plate):
# MODES = ("Type a read", "Benchmark image", "Upload an image")
MODES = ("Type a read", "Benchmark image", "Upload an image", "Photo dataset")
PHOTOS = _ROOT / "data" / "vehicle_photos" / "manifest.csv"  # PHASE3-ADDED (C2)
AGENCIES = ("INS", "THEFT", "PUC", "CAM")  # PHASE3-ADDED (C1): the agencies the examiner can switch off
SHORT = {"edit_similarity": "edit", "ocr_edit_similarity": "ocr-edit", "jaro": "jaro", "jaro_winkler": "j-w",
         "overlap": "overlap", "jaccard": "jaccard", "ratcliff_obershelp": "r-o", "soundex_similarity": "soundex",
         "tfidf": "tf-idf", "learned_ocr_similarity": "learned"}
AGREE = {1.0: "agrees", -1.0: "differs", 0.0: "—"}
EDIT_CHOICES = (1, 2, 3, 5)
# One plain-words line per blocking stage (deck p. 40-65), shown next to the counts.
STAGE_WORDS = {
    "none": "Full scan: every registered plate is a candidate. This is the cost blocking avoids.",
    "index": "Inverted index: keep plates that share at least one 2-character piece (q-gram) with the read.",
    "size": "Size filter: drop plates whose number of q-grams is too different for the allowed errors.",
    "prefix": "Prefix filter: a true match must share one of the read's rarest q-grams; check only those lists.",
    "position": "Position filter: the shared q-grams must also sit at nearly the same place in both strings.",
}


def decision_chip(decision: str, label: Optional[str] = None) -> str:
    return chip(label or decision, TOKENS[DECISION_TOKEN.get(decision, "neutral")])


@st.cache_data(show_spinner=False)
def benchmark_rows() -> List[Dict[str, str]]:
    """The labelled benchmark: a file in the repository, not source data, so caching it is safe."""
    with BENCHMARK.open(encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if r["image_path"]]


def _ocr(data: bytes) -> Dict[str, Any]:
    """OCR once per image per session: an image is not source data, and EasyOCR costs seconds."""
    cache = st.session_state.setdefault("pm_ocr", {})
    key = hashlib.sha1(data).hexdigest()
    if key not in cache:
        try:
            with st.spinner("Reading the plate with EasyOCR…"):
                cache[key] = run_ocr(data)
        except plate_ocr.OCRUnavailable as exc:
            st.error(f"{exc} Type the plate instead.")
            return {"raw": "", "confidence": None, "guesses": []}
    return cache[key]


def _benchmark_input(got: Dict[str, Any]) -> None:
    rows = benchmark_rows()
    c1, c2 = st.columns(2)
    deg = c1.selectbox("Degradation", sorted({r["degradation"] for r in rows}), key="pm_deg")
    sev = c2.selectbox("Severity", sorted({r["severity"] for r in rows if r["degradation"] == deg}), key="pm_sev")
    pool = {r["image_path"]: r for r in rows if r["degradation"] == deg and r["severity"] == sev}
    # options are paths, not positions: a position would point at a different car after a filter change
    path = st.selectbox("Image", list(pool), key="pm_img", format_func=lambda p: Path(p).name)
    row = pool[path]
    got.update(image=(_ROOT / path).read_bytes(), raw=row["ocr_read"], truth=row["true_plate"],
               confidence=float(row["ocr_confidence"] or 0), source="benchmark CSV (EasyOCR, raw)")
    if st.toggle("Run EasyOCR live on this image", key="pm_ocr_live"):
        live = _ocr(got["image"])
        if live["raw"] or live["guesses"]:
            got.update(raw=live["raw"], confidence=live["confidence"], guesses=live["guesses"],
                       source="EasyOCR, live")


# PHASE2-HIDDEN (simplified GUI, 19 Oct 2026): the input panel with its long lead; previously called from render() (now _render_before_simplification), pm_input panel.
def _input_panel() -> Dict[str, Any]:
    section("Read a plate", "Type what a camera read, pick a rendered benchmark image, or upload a photo.")
    what_this_shows("the three ways to give the matcher a plate: type a read, pick a damaged test image whose real plate is known, or upload a photo.")
    mode = st.radio("Input", MODES, key="pm_mode", horizontal=True)
    got: Dict[str, Any] = {"image": None, "raw": None, "confidence": None, "truth": None, "source": "typed"}
    if mode == "Type a read":
        c1, c2 = st.columns(2)
        got["raw"] = c1.text_input("Plate as read", "DL05CD9B76", key="pm_typed")
        got["confidence"] = c2.number_input("OCR confidence", 0.0, 1.0, 0.71, 0.01, key="pm_conf")
    elif mode == "Benchmark image":
        _benchmark_input(got)
    else:
        up = st.file_uploader("Plate image (png or jpg)", type=["png", "jpg", "jpeg"], key="pm_upload")
        if up is not None:
            got["image"] = up.getvalue()
            ocr = _ocr(got["image"])
            got.update(raw=ocr["raw"], confidence=ocr["confidence"], guesses=ocr["guesses"], source="EasyOCR, live")
    c1, c2 = st.columns(2)
    got["make"] = c1.text_input("Observed make (optional)", "", key="pm_make").strip() or None
    got["colour"] = c2.text_input("Observed colour (optional)", "", key="pm_colour").strip() or None
    return got


# PHASE2-HIDDEN (simplified GUI, 19 Oct 2026): the 'What the camera read' panel with the repaired-guess list and the max_edits selector; previously called from render() (now _render_before_simplification), pm_read panel.
def _read_panel(got: Dict[str, Any]) -> str:
    section("What the camera read", "The raw read is what the matcher was evaluated on; the repairs are "
            "plate_ocr's pattern-licensed confusion swaps (O→0 where a digit belongs, B→8 …).")
    what_this_shows("the text the OCR engine read and how sure it was; the matcher works on this raw text.")
    raw = plate_ocr.canonical(got["raw"] or "")
    repaired = plate_ocr.normalise_candidates(raw)
    left, right = st.columns([2, 3]) if got["image"] is not None else (None, st.container())
    if left is not None:
        left.image(got["image"], width="stretch")
    with right:
        conf = got["confidence"]
        kpi_row([("Raw read", raw or "—"), ("OCR confidence", f"{conf:.3f}" if conf is not None else "—"),
                 ("True plate", got["truth"] or "not known")])
        st.caption(f"Read from: {got['source']}.")
        group_label("Repaired guesses, best first")
        st.markdown(" · ".join(f"`{g}`" for g in repaired[:8]) or "none (no plate-length text)")
        if got.get("guesses"):
            group_label("read_plate output (repaired per box)")
            st.markdown(" · ".join(f"`{t}` ({c})" for t, c in got["guesses"]))
        use = st.radio("Match on", ["raw read", "best repaired guess"], key="pm_use", horizontal=True)
        validated = max_edits()
        choices = sorted(set(EDIT_CHOICES) | {validated})
        st.selectbox("Errors the blocking filters must tolerate (max_edits)", choices, key="pm_edits",
                     index=choices.index(validated), help=f"{validated} was chosen on validation. Fewer allowed "
                     "errors = far fewer candidates, but a read with more errors than this can lose its plate.")
    got["repaired"] = repaired[0] if repaired else None
    return raw if use == "raw read" or not repaired else repaired[0]


def _forward(read: str, got: Dict[str, Any]) -> None:
    if not read:
        st.warning("There is no read to match: type one, or pick an image EasyOCR could read.")
        return
    try:
        # PHASE3-REPLACED (C1: every catalogued source's plates are candidates, not only REG's):
        # registry, meta = live_registry()
        registry, meta = federated_match.live_universe()
        registry = _only_agencies(registry, st.session_state.get("pm_agencies", list(AGENCIES)))  # PHASE3-ADDED (C1)
    except Exception as exc:  # refuse, never guess: the phase-1 rule for a dead source
        st.session_state.pop("pm_result", None)
        st.error(f"Refused: the registration authority could not be read live ({exc}).")
        return
    # PHASE2-REPLACED (max_edits is now the examiner's choice, default the validated value):
    # me = max_edits()
    # cands, stats = plate_match.match(read, confidence=got["confidence"], observed_make=got["make"],
    #                                  observed_colour=got["colour"], registry=registry)
    me = int(st.session_state.get("pm_edits") or max_edits())
    # PHASE3-REPLACED (C2: the observed model, from the photo or typed, is passed on too):
    # cands, stats = plate_match.match(read, confidence=got["confidence"], observed_make=got["make"],
    #                                  observed_colour=got["colour"], registry=registry, max_edits=me)
    cands, stats = plate_match.match(read, confidence=got["confidence"], observed_make=got["make"],
                                     observed_colour=got["colour"], observed_model=got.get("model"),
                                     registry=registry, max_edits=me)
    truth = plate_ocr.canonical(got["truth"]) if got.get("truth") else None
    target = truth or (cands[0].plate if cands else None)
    st.session_state["pm_result"] = {
        "read": read, "make": got["make"], "colour": got["colour"], "meta": meta, "max_edits": me,
        "stages": funnel(read, sorted(registry), me), "stats": stats, "cands": cands,
        "registry": {c.plate: registry.get(c.plate, {}) for c in cands},
        "at": datetime.now().strftime("%H:%M:%S"),
        "raw": plate_ocr.canonical(got["raw"] or ""), "repaired": got.get("repaired"), "truth": truth,
        "target": target, "plates": sorted(registry),
        "grid": plate_separation.damage_grid(target, registry, me) if target else None}


# PHASE2-HIDDEN (simplified GUI, 19 Oct 2026): the blocking funnel (KPI row + per-stage table); previously called from render() (now _render_before_simplification), pm_funnel panel.
def _funnel_panel(res: Dict[str, Any]) -> None:
    section("Blocking funnel", f"{res['meta']['rows']} registrations read live from {res['meta']['source_id']} "
            f"in {res['meta']['elapsed_ms']} ms at {res['at']}; each filter is recall-safe up to "
            f"max_edits = {res['max_edits']} (chosen on validation).")
    kpi_row([(s["stage"], s["candidates"]) for s in res["stages"]]
            + [("scored", min(plate_match.SHORTLIST, res["stats"].candidates_after) or len(res["cands"]))])
    # PHASE2-REPLACED (each stage now says in plain words what it does):
    # styled_table([{"Stage": s["stage"], "Filters applied": s["filters"], "Candidates left": s["candidates"],
    #                "Elapsed ms": s["elapsed_ms"]} for s in res["stages"]])
    st.markdown("**What this shows:** how many registered plates are still in the running after each cheap "
                "filter, before any expensive scoring. Try 1 allowed error to see the set collapse.")
    styled_table([{"Stage": s["stage"], "What the filter does": STAGE_WORDS.get(s["stage"], s["filters"]),
                   "Candidates left": s["candidates"], "Elapsed ms": s["elapsed_ms"]} for s in res["stages"]])
    notes = [r for c in res["cands"][:1] for r in c.reasons if "widened" in r or "full scan" in r]
    if notes:
        st.info("Nothing survived at this setting, so the matcher widened the search: " + "; ".join(notes) + ".")


# PHASE2-HIDDEN (simplified GUI, 19 Oct 2026): the two ranked-candidate tables, decision chips and the 'why' bullet list; previously called from render() (now _render_before_simplification), pm_cands panel.
def _candidates_panel(res: Dict[str, Any]) -> None:
    bundle, err = plate_match.load_bundle()
    model = "logistic regression, Platt-calibrated (data/match_model.joblib)" if bundle else \
        f"hand-weighted fallback — {err}"
    section(f"Ranked candidates for {res['read']}", f"Model: {model}. Each measure's score side by side; "
            "the decision reads the calibrated probability against thresholds chosen on validation.")
    what_this_shows("the registered plates the read could be, best first, with the probability that each is the right car and the decision.")
    # Two tables, one row per candidate in both: one table of 17 columns overflowed the panel and hid
    # the learned score and the context columns behind a horizontal scroll.
    verdict, measures = [], []
    for i, c in enumerate(res["cands"], 1):
        reg = res["registry"].get(c.plate, {})
        verdict.append({"#": i, "Plate": c.plate, "Probability": f"{c.probability:.3f}", "Decision": c.decision,
                        "Make vs registration": AGREE.get(c.features.get("make_agree"), "—"),
                        "Colour vs registration": AGREE.get(c.features.get("colour_agree"), "—"),
                        "Registered as": f"{reg.get('make') or ''} {reg.get('model') or ''} {reg.get('colour') or ''}"})
        measures.append({"#": i, "Plate": c.plate,
                         **{SHORT[n]: f"{c.scores.get(n, 0):.2f}" for n in plate_match.SCORE_NAMES}})
    styled_table(verdict)
    group_label("Every measure's score for the same candidates (deck p. 16-37)")
    styled_table(measures)
    chip_strip([decision_chip(c.decision, f"{c.plate} · {c.decision} · p={c.probability:.3f}")
                for c in res["cands"][:5]])
    if res["cands"]:
        group_label(f"Why {res['cands'][0].plate} is {res['cands'][0].decision}")
        st.markdown("\n".join(f"- {r}" for r in res["cands"][0].reasons))


# PHASE2-HIDDEN (simplified GUI, 19 Oct 2026): the reverse search with make/model/colour columns, chips and the unregistered warning; previously called from render() (now _render_before_simplification), pm_reverse panel.
def _reverse_panel() -> None:
    section("Reverse: camera reads for a registration",
            "Every CAM capture, read live, whose plate read could be this vehicle. Each read is scored by the "
            "same forward matcher, so a read that is really another registered car is never claimed.")
    what_this_shows("the opposite direction: start from a registration and find every camera record that could be that car.")
    c1, c2 = st.columns([3, 1])
    plate = c1.text_input("Registration number", "DL05CD9876", key="pm_rev_plate")
    c2.markdown("<div style='height:34px'></div>", unsafe_allow_html=True)
    if c2.button("Find camera reads", key="pm_rev_run", type="primary", width="stretch"):
        try:
            registry, _ = live_registry()
            captures, meta = live_captures()
            st.session_state["pm_rev"] = {"plate": plate_ocr.canonical(plate), "meta": meta,
                                          "registered": plate_ocr.canonical(plate) in registry,
                                          "rows": reverse_rows(plate, captures, registry),
                                          "at": datetime.now().strftime("%H:%M:%S")}
        except Exception as exc:
            st.session_state.pop("pm_rev", None)
            st.error(f"Refused: a source could not be read live ({exc}).")
    rev = st.session_state.get("pm_rev")
    if not rev:
        return
    group_label(f"{len(rev['rows'])} of {rev['meta']['rows']} captures could be {rev['plate']} "
                f"(read live from {rev['meta']['source_id']} at {rev['at']})")
    if not rev["registered"]:
        st.warning(f"{rev['plate']} is not registered. A capture is only claimed for a plate its forward "
                   "match can rank, so an unregistered plate gets none — search its read forward instead.")
    if rev["rows"]:
        styled_table([{"Capture": r["capture_id"], "Read": r["read"], "Camera": r["camera"], "Time": r["time"],
                       "Make": r["observed_make"], "Model": r["observed_model"], "Colour": r["observed_colour"],
                       "OCR conf": r["ocr_confidence"], "Probability": f"{r['probability']:.3f}",
                       "Decision": r["decision"]} for r in rev["rows"]])
        chip_strip([decision_chip(r["decision"], f"#{r['capture_id']} · {r['decision']}") for r in rev["rows"]])


# PHASE2-HIDDEN (simplified GUI, 19 Oct 2026): the three Separation panels (diff, distance, damage grid) as open panels; previously called from render() (now _render_before_simplification).
def _separation(res: Dict[str, Any]) -> None:
    """The three Separation panels; the real plate is the benchmark label, else the matcher's winner."""
    target = res.get("target")
    if not target:
        return
    if not res.get("truth"):
        st.info(f"The real plate of a typed or uploaded read is not known, so the Separation panels compare "
                f"against the matcher's winner {target}. Pick a benchmark image to compare against the truth.")
    with panel("pm_sep_diff"):
        plate_separation.render_diff(target, res.get("raw") or res["read"], res.get("repaired"))
    with panel("pm_sep_dist"):
        plate_separation.render_distance(res["read"], target, res["cands"], res.get("plates") or [])
    if res.get("grid"):
        with panel("pm_sep_grid"):
            plate_separation.render_grid(res["grid"])


# PHASE2-HIDDEN (simplified GUI, 19 Oct 2026): the whole previous page: input, read panel with max_edits selector, blocking funnel, two candidate tables, three open Separation panels, 'Why this algorithm' and the reverse search; previously called from app/app.py as render_plate_match.
def _render_before_simplification() -> None:
    page_head("Plate Match", "A noisy plate read, matched approximately against every registered vehicle "
              "— and the reverse: every camera read that could be one registration.")
    with panel("pm_input"):
        got = _input_panel()
    with panel("pm_read"):
        read = _read_panel(got)
        if st.button("Match against the live registry", key="pm_run", type="primary"):
            _forward(read, got)
    res = st.session_state.get("pm_result")
    if res:
        with panel("pm_funnel"):
            _funnel_panel(res)
        with panel("pm_cands"):
            _candidates_panel(res)
        _separation(res)
    with panel("pm_why"):
        section("Why this algorithm")
        what_this_shows("one sentence on why this combination was chosen; the full comparison is on Matching Evaluation.")
        st.markdown("Logistic regression over ten string measures plus the camera's make, model and colour ranked "
                    "the right vehicle first most often on held-out vehicles, and at the precision target it accepts the most "
                    "reads unattended — the numbers, and why the others lost, are on "
                    "[Matching Evaluation](?page=Matching%20Evaluation).")
        st.markdown("How the whole pipeline fits together, and which slide each method comes from: "
                    "[How it works](?page=How%20it%20works).")
    with panel("pm_reverse"):
        _reverse_panel()


# ------------------------------------------------------------------ phase-2 simplified page

REPAIR_HELP = ("Letters and digits swapped where the Indian plate pattern requires (O→0, I→1, B→8, S→5, Z→2, G→6). "
               "Done before matching; not the learned model.")
USE = ("EasyOCR read (raw)", "Pattern-repaired read")


def _input_simple() -> Dict[str, Any]:
    section("1. Give a plate")
    mode = st.radio("Input", MODES, key="pm_mode", horizontal=True)
    got: Dict[str, Any] = {"image": None, "raw": None, "confidence": None, "truth": None, "source": "typed"}
    if mode == "Type a read":
        c1, c2 = st.columns(2)
        # PHASE2-REPLACED (examiner pass: the toolbox fills this box, and a widget given both a default and a
        # Session State value draws a warning; the default now lives in Session State only):
        # got["raw"] = c1.text_input("Plate as read", "DL05CD9B76", key="pm_typed")
        st.session_state.setdefault("pm_typed", "DL05CD9B76")
        got["raw"] = c1.text_input("Plate as read", key="pm_typed")
        got["confidence"] = c2.number_input("OCR confidence", 0.0, 1.0, 0.71, 0.01, key="pm_conf")
    elif mode == "Benchmark image":
        _benchmark_input(got)
    elif mode == "Photo dataset":  # PHASE3-ADDED (C2)
        _photo_dataset_input(got)
    else:
        # PHASE3-REPLACED (C2: a photo of the whole car is welcome now, not only a plate close-up):
        # up = st.file_uploader("Plate image (png or jpg)", type=["png", "jpg", "jpeg"], key="pm_upload")
        up = st.file_uploader("Plate close-up, or a photo of the car showing its plate (png or jpg)",
                              type=["png", "jpg", "jpeg"], key="pm_upload")
        if up is not None:
            got["image"] = up.getvalue()
            ocr = _ocr(got["image"])
            got.update(raw=ocr["raw"], confidence=ocr["confidence"], guesses=ocr["guesses"], source="EasyOCR, live")
            _photo_vehicle(got["image"], ocr.get("box") or [])  # PHASE3-ADDED (C2)
    # PHASE3-REPLACED (C2: the photo can fill these, so they are Session State keys without a widget default, as
    # pm_typed already is; and the observed model is asked for too, which the matcher has always accepted):
    # c1, c2 = st.columns(2)
    # got["make"] = c1.text_input("Observed make (optional)", "", key="pm_make").strip() or None
    # got["colour"] = c2.text_input("Observed colour (optional)", "", key="pm_colour").strip() or None
    c1, c2, c3 = st.columns(3)
    for key in ("pm_make", "pm_model", "pm_colour"):
        st.session_state.setdefault(key, "")
    got["make"] = c1.text_input("Observed make (optional)", key="pm_make").strip() or None
    got["model"] = c2.text_input("Observed model (optional)", key="pm_model").strip() or None
    got["colour"] = c3.text_input("Observed colour (optional)", key="pm_colour").strip() or None
    return got


# PHASE3-ADDED (C2: make, model and colour from the photo)
def _photo_vehicle(image: bytes, box: List[Any]) -> None:
    """Infer make / model / colour once per image and pre-fill the observed fields (still editable)."""
    key = hashlib.sha1(image).hexdigest()
    cache = st.session_state.setdefault("pm_vision", {})
    if key not in cache:
        try:
            with st.spinner("Looking at the car…"):
                cache[key] = vehicle_vision.analyse(image, box)
        except vehicle_vision.VisionUnavailable as exc:
            cache[key] = exc
    view = cache[key]
    if isinstance(view, Exception):
        st.caption(f"Make and colour are not inferred on this machine ({view}). "
                   f"Install with `{vehicle_vision.INSTALL_HINT}`, or type them below.")
        return
    if st.session_state.get("pm_vision_for") != key:  # a new photo: fill once, then the examiner's edits stand
        st.session_state["pm_vision_for"] = key
        st.session_state.update(pm_make=view.make or "", pm_model=view.model or "", pm_colour=view.colour or "")
    if view.kind == "vehicle":
        sure = ", ".join(f"{k} {view.p[k]:.0%}" for k in ("make", "model", "colour") if getattr(view, k))
        st.info(f"The photo shows a **{view.words()}**" + (f" (CLIP: {sure})" if sure else "") +
                ". Filled in below; correct it if wrong. The matcher checks it against each candidate's record.")
    else:
        st.caption(view.words().capitalize() + ".")
    for note in view.notes:
        st.caption(note)
# PHASE3-END


def char_rows(truth: str, raw: str, repaired: Optional[str]) -> List[Dict[str, str]]:
    """Columns of (true, OCR, repaired) characters, aligned on the true plate by the matcher's own
    weighted edit distance. The repair only substitutes, so the repaired read reuses the raw read's columns."""
    cells = plate_separation.align(truth, raw)
    rep = list(repaired) if repaired and len(repaired) == len(raw) else None
    out, j = [], 0
    for c in cells:
        r = ""
        if c["read"]:
            r = rep[j] if rep else ""
            j += 1
        out.append({"truth": c["truth"], "raw": c["read"], "repaired": r})
    return out


def _cell(ch: str, truth: str) -> str:
    if not ch:
        fill, border = "#EEF0F2", TOKENS["neutral"]
    elif ch == truth:
        fill, border = "#E6F4EA", TOKENS["ok"]
    elif truth and confusion.learned_confusion_cost(truth, ch) < 1.0:
        fill, border = "#FFF4E0", TOKENS["warn"]
    else:
        fill, border = "#FDECEA", TOKENS["bad"]
    return (f'<span style="display:inline-flex;align-items:center;justify-content:center;width:34px;height:36px;'
            f'background:{fill};border:2px solid {border};border-radius:6px;font-family:var(--cv-font-mono);'
            f'font-size:1.2rem;font-weight:700;color:{TOKENS["ink"]};">{html.escape(ch) or "·"}</span>')


def char_compare_html(cols: List[Dict[str, str]], with_repaired: bool) -> str:
    rows = [("True plate", "truth"), ("OCR", "raw")] + ([("Repaired", "repaired")] if with_repaired else [])
    lines = []
    for label, k in rows:
        boxes = "".join(_cell(c[k], c["truth"] if k != "truth" else c[k]) for c in cols)
        lines.append(f'<div style="display:flex;gap:4px;align-items:center;margin:3px 0;">'
                     f'<span style="width:96px;font-weight:600;color:{TOKENS["ink"]};">{label}</span>{boxes}</div>')
    legend = (f'<div style="font-size:0.95rem;color:{TOKENS["ink2"]};margin-top:4px;">green = same as the true '
              "plate, amber = a common OCR swap, red = wrong, grey · = missing</div>")
    return f'<div role="img" aria-label="character comparison with the true plate">{"".join(lines)}{legend}</div>'


def _read_simple(got: Dict[str, Any]) -> str:
    section("2. What OCR read")
    raw = plate_ocr.canonical(got["raw"] or "")
    repaired_all = plate_ocr.normalise_candidates(raw)
    repaired = repaired_all[0] if repaired_all else None
    got["repaired"] = repaired
    left, right = st.columns([2, 3]) if got["image"] is not None else (None, st.container())
    if left is not None:
        left.image(got["image"], width="stretch")
    with right:
        conf = got["confidence"]
        c1, c2, c3 = st.columns(3)
        c1.metric("EasyOCR read (raw)", raw or "—", help=f"Read from: {got['source']}.")
        c2.metric("OCR confidence", f"{conf:.3f}" if conf is not None else "—")
        c3.metric("Pattern-repaired read", repaired or "—", help=REPAIR_HELP)
        if got.get("truth"):
            st.markdown(f"**True plate:** `{got['truth']}`")
        use = st.radio("Match on", USE, key="pm_use_v2", horizontal=True)
    truth = plate_ocr.canonical(got["truth"]) if got.get("truth") else None
    if truth and raw:
        st.markdown(char_compare_html(char_rows(truth, raw, repaired), bool(repaired and repaired != raw)),
                    unsafe_allow_html=True)
    return raw if use == USE[0] or not repaired else repaired


def matches_html(rows: List[Dict[str, Any]]) -> str:
    """The results table with a decision chip per row and the top row highlighted."""
    # PHASE3-REPLACED (C1 + C3: two columns added, shown only when the rows carry them):
    # cols = ["#", "Registered plate", "Probability", "Decision", "Make agrees", "Colour agrees", "Registered as"]
    cols = [c for c in ["#", "Registered plate", "Probability", "Decision", "Corrections", "Held by", "Make agrees",
                        "Colour agrees", "Registered as"] if not rows or c in rows[0]]
    head = "".join(f"<th>{c}</th>" for c in cols)
    body = []
    for i, r in enumerate(rows):
        cells = [decision_chip(r[c]) if c == "Decision" else html.escape(str(r[c])) for c in cols]
        cls = ' class="cv-best"' if i == 0 else ""
        body.append(f"<tr{cls}>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    return (f'<div class="cv-tablewrap"><table class="cv-table"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def _matches(res: Dict[str, Any]) -> None:
    section(f"3. Matches for {res['read']}")
    cands = res["cands"]
    if not cands:
        st.info("No registered plate is close enough to this read.")
        return
    rows = []
    for i, c in enumerate(cands, 1):
        reg = res["registry"].get(c.plate, {})
        # PHASE3-REPLACED (C1 + C3: a candidate may be held by any agency, so the table says which; and the
        # characters the match had to change are shown next to the probability):
        # rows.append({"#": i, "Registered plate": c.plate, "Probability": f"{c.probability:.3f}", "Decision": c.decision,
        #              "Make agrees": AGREE.get(c.features.get("make_agree"), "—"),
        #              "Colour agrees": AGREE.get(c.features.get("colour_agree"), "—"),
        #              "Registered as": f"{reg.get('make') or ''} {reg.get('model') or ''} "
        #                               f"{reg.get('colour') or ''}".strip() or "—"})
        rows.append({"#": i, "Registered plate": c.plate, "Probability": f"{c.probability:.3f}", "Decision": c.decision,
                     "Corrections": "; ".join(c.evidence.get("corrections") or []) or "none",
                     "Held by": reg.get("held_by") or "REG",
                     "Make agrees": AGREE.get(c.features.get("make_agree"), "—"),
                     "Colour agrees": AGREE.get(c.features.get("colour_agree"), "—"),
                     "Registered as": f"{reg.get('make') or ''} {reg.get('model') or ''} "
                                      f"{reg.get('colour') or ''}".strip() or "—"})
    st.markdown(matches_html(rows), unsafe_allow_html=True)
    nxt = f"next best p={cands[1].probability:.3f}" if len(cands) > 1 else "no other candidate"
    st.markdown(f"**{cands[0].plate} — {cands[0].decision} (p={cands[0].probability:.3f}), {nxt}.**")
    _why(cands[0])  # PHASE3-ADDED (C3: the evidence, group by group)
    _other_databases(res)  # PHASE3-ADDED (C1)
    with st.expander("Details: every measure's score and the same plate under every damage level", expanded=False):
        group_label("Every measure's score for the same candidates")
        styled_table([{"#": i, "Plate": c.plate,
                       **{SHORT[n]: f"{c.scores.get(n, 0):.2f}" for n in plate_match.SCORE_NAMES}}
                      for i, c in enumerate(cands, 1)])
        # PHASE2-REPLACED (review: for a typed or uploaded read the separation views silently compared against the matcher's own winner as if it were the truth (the hidden _separation said so; the new expander did not)):
        # target = res.get("target")
        # if target:
        #     plate_separation.render_distance(res["read"], target, cands, res.get("plates") or [])
        target = res.get("target")
        if target and not res.get("truth"):
            st.info(f"No true plate is known for a typed or uploaded read, so these views use the matcher's winner {target}.")
        if target:
            plate_separation.render_distance(res["read"], target, cands, res.get("plates") or [])
        if res.get("grid"):
            plate_separation.render_grid(res["grid"])


# PHASE3-ADDED (C2: the labelled photo set as an input; C1: agency toggles; C3: the evidence behind a match)
@st.cache_data(show_spinner=False)
def photo_rows() -> List[Dict[str, str]]:
    """The labelled photo set: a file in the repository, not source data, so caching it is safe."""
    if not PHOTOS.exists():
        return []
    with PHOTOS.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _photo_dataset_input(got: Dict[str, Any]) -> None:
    rows = photo_rows()
    if not rows:
        st.warning("No photo set: run python scripts/make_vehicle_photos.py")
        return
    kinds = {"full": "whole car", "partial": "part of the car", "hard": "night / blur", "plate_only": "plate close-up",
             "clone": "clone: another car's plate"}
    c1, c2 = st.columns([1, 2])
    kind = c1.selectbox("Kind", list(kinds), format_func=kinds.get, key="pm_ph_kind")
    pool = {r["image"]: r for r in rows if r["variant"] == kind}
    path = c2.selectbox("Photo", list(pool), key="pm_ph_img", format_func=lambda x: Path(x).name)
    row = pool[path]
    got["image"] = (_ROOT / path).read_bytes()
    ocr = _ocr(got["image"])
    got.update(raw=ocr["raw"], confidence=ocr["confidence"], guesses=ocr["guesses"], truth=row["true_plate"],
               source="photo dataset, EasyOCR live")
    car = f"{row['colour'].lower()} {row['make']} {row['model']}"
    if row["is_clone"] == "1":
        st.info(f"Truth: the photo shows a **{car}**, but the plate **{row['true_plate']}** is registered to another "
                "car. The plate should match; the car should not.")
    else:
        st.info(f"Truth: a **{car}** with plate **{row['true_plate']}** (registered as exactly that car).")
    st.caption(f"Photo: {row['author']}, {row['license']} ({row['source_url']}); its own plate is covered by a "
               "rendered one.")
    _photo_vehicle(got["image"], ocr.get("box") or [])


def _only_agencies(universe: Dict[str, Dict[str, Any]], keep: List[str]) -> Dict[str, Dict[str, Any]]:
    """The candidate set as if only REG and the kept agencies were asked: a plate no kept agency holds drops out."""
    out = {}
    for plate, row in universe.items():
        sources = [s for s in row.get("sources") or [] if s == "REG" or s in keep]
        if sources:
            out[plate] = {**row, "sources": sources, "held_by": federated_match.held_by({**row, "sources": sources})}
    return out


def _why(top: Any) -> None:
    parts = plate_match.explain(top)
    if not parts:
        return
    section(f"Why {top.plate}: the evidence, group by group",
            "What each kind of evidence adds to the stacked model's log-odds (positive = 'this is the car'). Change the "
            "observed make or colour, or switch an agency off below, and press Match again.")
    biggest = max(abs(v) for _, v in parts) or 1.0
    lines = []
    for name, v in parts:
        width = int(100 * abs(v) / biggest)
        colour = TOKENS["ok"] if v >= 0 else TOKENS["bad"]
        lines.append(f'<div style="display:flex;align-items:center;gap:8px;margin:2px 0;">'
                     f'<span style="width:260px;">{html.escape(name)}</span>'
                     f'<span style="display:inline-block;height:14px;width:{width * 3}px;background:{colour};'
                     f'border-radius:3px;"></span><span>{v:+.2f}</span></div>')
    st.markdown("".join(lines), unsafe_allow_html=True)
    model_p, shown = top.evidence.get("model_p"), top.probability
    if model_p is not None and shown < model_p - 1e-6:
        st.caption(f"The model alone said p={model_p:.3f}; the probability shown ({shown:.3f}) is that times the chance "
                   f"the car is one an agency holds ({1 - top.evidence.get('p_unknown', 0):.3f}).")


# PHASE3-ADDED (C1: when the registration alone is not enough, what every other agency says about the candidates)
def _other_databases(res: Dict[str, Any]) -> None:
    cands, universe = res["cands"], res["registry"]
    top = cands[0]
    p_unknown = top.evidence.get("p_unknown")
    if p_unknown is not None:
        st.markdown(f"Chance the car is **one no agency holds** (out-of-state, unregistered): **{p_unknown:.3f}**. "
                    f"The learned model alone said p={top.evidence.get('model_p', top.probability):.3f} for "
                    f"{top.plate}; it only ever compared registered plates with each other, so the probability shown and "
                    "decided on is limited by the chance the car is one an agency holds.")
    meta = res.get("meta") or {}
    if meta.get("unavailable"):
        st.warning("Not consulted (down): " + ", ".join(meta["unavailable"]) + ".")
    with st.expander("What the other databases say about the top candidates", expanded=top.decision != "ACCEPT"):
        table = []
        for c in cands[:3]:
            row = universe.get(c.plate, {})
            seen = row.get("last_seen") or {}
            table.append({"Plate": c.plate, "Held by": row.get("held_by") or "REG",
                          "On record": f"{row.get('make') or ''} {row.get('model') or ''} {row.get('colour') or ''}".strip()
                                       or "—",
                          "Filled from cameras": row.get("fingerprint") or "—",
                          "Camera sightings": row.get("sightings", 0),
                          "Last seen": f"{seen.get('where') or ''} {seen.get('at') or ''}".strip() or "—",
                          "Model p": f"{c.evidence.get('model_p', c.probability):.3f}",
                          "Shown p": f"{c.probability:.3f}"})
        styled_table(table)
        st.caption("A plate counts as corroborated when at least two agencies other than the cameras hold it and the "
                   "observed make and colour both match its record. Only then can a correction inside the state code "
                   "(the first two letters) be accepted without a person.")
# PHASE3-END


def _reverse_simple() -> None:
    section("Reverse: camera reads for a registration",
            "Finds every camera capture whose read could be this registered plate. Each read is matched forward "
            "against all registrations and is listed only if that match points back to this plate.")
    c1, c2 = st.columns([3, 1])
    # PHASE2-REPLACED (examiner pass: same reason as pm_typed):
    # plate = c1.text_input("Registration number", "DL05CD9876", key="pm_rev_plate")
    st.session_state.setdefault("pm_rev_plate", "DL05CD9876")
    plate = c1.text_input("Registration number", key="pm_rev_plate")
    c2.markdown("<div style='height:34px'></div>", unsafe_allow_html=True)
    if c2.button("Find camera reads", key="pm_rev_run", type="primary", width="stretch"):
        try:
            registry, _ = live_registry()
            captures, meta = live_captures()
            st.session_state["pm_rev"] = {"plate": plate_ocr.canonical(plate), "meta": meta,
                                          "registered": plate_ocr.canonical(plate) in registry,
                                          "profile": registry.get(plate_ocr.canonical(plate), {}),
                                          "rows": reverse_rows(plate, captures, registry),
                                          "at": datetime.now().strftime("%H:%M:%S")}
        except Exception as exc:
            st.session_state.pop("pm_rev", None)
            st.error(f"Refused: a source could not be read live ({exc}).")
    rev = st.session_state.get("pm_rev")
    if not rev:
        return
    # PHASE2-REPLACED (examiner pass: REJECT rows were listed as this car's reads although they are another car's
    # (DL05CD0276 is itself registered); the read column gave no hint why a misread such as DL05CD9B76 is accepted):
    # if not rev["rows"]:
    #     st.info(f"No camera capture could be {rev['plate']}" + ("" if rev["registered"] else " (it is not registered)") + ".")
    #     return
    # styled_table([{"Capture": r["capture_id"], "Read": r["read"], "Camera": r["camera"], "Time": r["time"],
    #                "Probability": f"{r['probability']:.3f}", "Decision": r["decision"]} for r in rev["rows"]])
    rows = [r for r in rev["rows"] if r["decision"] != "REJECT"]
    rejected = len(rev["rows"]) - len(rows)
    if not rows:
        st.info(f"No camera capture is {rev['plate']}" + ("" if rev["registered"] else " (it is not registered)") + ".")
    else:
        styled_table([{"Capture": r["capture_id"], "Read (as stored)": r["read"], "Pattern-repaired": repaired(r["read"]),
                       "Make & colour vs registration": context_check(r, rev.get("profile") or {}),
                       "Camera": r["camera"], "Time": r["time"], "Probability": f"{r['probability']:.3f}",
                       "Decision": r["decision"]} for r in rows])
        st.markdown("The read is what the camera database holds; nothing is rewritten here. ACCEPT means the "
                    "matcher, from the read's characters plus the camera's make and colour, is sure this capture is "
                    f"{rev['plate']}. Correcting the stored read is Data Cleansing's job (rule R5).")
    if rejected:
        st.markdown(f"{rejected} other nearby read(s) were checked and are not this car: their own best match is "
                    "another registered plate, or nothing.")


def repaired(read: Any) -> str:
    """The pattern-repaired read, or 'same' when the read already fits the plate pattern."""
    raw = plate_ocr.canonical(read or "")
    fixed = (plate_ocr.normalise_candidates(raw) or [raw])[0]
    return "same" if fixed == raw else fixed


def context_check(capture: Dict[str, Any], profile: Dict[str, Any]) -> str:
    """'agree', 'differ' or 'unknown' for the camera's make and colour against the registration."""
    pairs = [(capture.get("observed_make"), profile.get("make")), (capture.get("observed_colour"), profile.get("colour"))]
    known = [(a, b) for a, b in pairs if a and b]
    if not known:
        return "unknown"
    return "agree" if all(str(a).strip().lower() == str(b).strip().lower() for a, b in known) else "differ"


def render() -> None:
    page_head("Plate Match", "Match a noisy plate read against every registered vehicle, and find the camera "
              "reads of one registration.")
    # PHASE2-REPLACED (examiner pass: the steps did not say how to change the data the matcher reads):
    # how_to(["Give a plate: upload an image, pick a benchmark image, or type a read.",
    #         "Check what OCR read, then press Match.",
    #         "Read the matches: the highlighted top row is the answer."])
    how_to(["Give a plate: upload an image, pick a benchmark image, or type a read.",
            "Check what OCR read, then press Match.",
            "Read the matches: the highlighted top row is the answer.",
            "To test: insert, modify or delete a registration or camera record in the Examiner toolbox at the "
            "bottom; the next Match reads the changed data live."])
    _apply_prefill()
    with panel("pm_input"):
        got = _input_simple()
    with panel("pm_read"):
        read = _read_simple(got)
        # PHASE3-ADDED (C1: switch agencies off to see what each one contributes to the match)
        st.multiselect("Agencies asked besides the registration authority (REG)", AGENCIES, default=list(AGENCIES),
                       key="pm_agencies", help="A plate held only by agencies you switch off stops being a candidate; "
                       "a camera-only plate needs CAM. Press Match again after a change.")
        # PHASE3-END
        if st.button("Match against the live registry", key="pm_run", type="primary"):
            _forward(read, got)
    res = st.session_state.get("pm_result")
    if res:
        with panel("pm_cands"):
            _matches(res)
    with panel("pm_reverse"):
        _reverse_simple()
    with panel("pm_toolbox"):
        examiner_toolbox.render(_after_examiner_write, "pm_tb")


def _after_examiner_write(message: str, capture: Any = None) -> None:
    """A toolbox write makes the shown results stale: drop them, and put the changed plate in the read and
    reverse boxes so the next Match / Find runs on the new data. Widget keys can only be set before the
    widgets exist, so the values wait in `pm_prefill` until the rerun."""
    change = st.session_state.pop("ex_last", None) or {}
    st.session_state.pop("pm_result", None)
    st.session_state.pop("pm_rev", None)
    st.session_state["pm_prefill"] = {"message": message, "next": change.get("match"), "read": change.get("read"),
                                      "make": change.get("make"), "colour": change.get("colour"),
                                      "plate": change.get("plate")}
    st.rerun()


def _apply_prefill() -> None:
    pre = st.session_state.pop("pm_prefill", None)
    if not pre:
        return
    st.success(pre["message"])
    if pre.get("next"):
        st.info(pre["next"])
    if pre.get("read"):
        st.session_state.update(pm_mode="Type a read", pm_typed=pre["read"], pm_make=pre.get("make") or "",
                                pm_colour=pre.get("colour") or "")
    if pre.get("plate"):
        st.session_state["pm_rev_plate"] = pre["plate"]
