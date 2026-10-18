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
from scripts.match_plate import (funnel, live_captures, live_registry, max_edits, reverse_rows,  # noqa: E402
                                 run_ocr)
from tabs import plate_separation  # noqa: E402
from theme import TOKENS  # noqa: E402

BENCHMARK = _ROOT / "data" / "match_benchmark.csv"
DECISION_TOKEN = {"ACCEPT": "ok", "REVIEW": "warn", "REJECT": "bad"}
MODES = ("Type a read", "Benchmark image", "Upload an image")
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
        registry, meta = live_registry()
    except Exception as exc:  # refuse, never guess: the phase-1 rule for a dead source
        st.session_state.pop("pm_result", None)
        st.error(f"Refused: the registration authority could not be read live ({exc}).")
        return
    # PHASE2-REPLACED (max_edits is now the examiner's choice, default the validated value):
    # me = max_edits()
    # cands, stats = plate_match.match(read, confidence=got["confidence"], observed_make=got["make"],
    #                                  observed_colour=got["colour"], registry=registry)
    me = int(st.session_state.get("pm_edits") or max_edits())
    cands, stats = plate_match.match(read, confidence=got["confidence"], observed_make=got["make"],
                                     observed_colour=got["colour"], registry=registry, max_edits=me)
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
    cols = ["#", "Registered plate", "Probability", "Decision", "Make agrees", "Colour agrees", "Registered as"]
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
        rows.append({"#": i, "Registered plate": c.plate, "Probability": f"{c.probability:.3f}", "Decision": c.decision,
                     "Make agrees": AGREE.get(c.features.get("make_agree"), "—"),
                     "Colour agrees": AGREE.get(c.features.get("colour_agree"), "—"),
                     "Registered as": f"{reg.get('make') or ''} {reg.get('model') or ''} "
                                      f"{reg.get('colour') or ''}".strip() or "—"})
    st.markdown(matches_html(rows), unsafe_allow_html=True)
    nxt = f"next best p={cands[1].probability:.3f}" if len(cands) > 1 else "no other candidate"
    st.markdown(f"**{cands[0].plate} — {cands[0].decision} (p={cands[0].probability:.3f}), {nxt}.**")
    with st.expander("Details: every measure's score and the same plate under every damage level", expanded=False):
        group_label("Every measure's score for the same candidates")
        styled_table([{"#": i, "Plate": c.plate,
                       **{SHORT[n]: f"{c.scores.get(n, 0):.2f}" for n in plate_match.SCORE_NAMES}}
                      for i, c in enumerate(cands, 1)])
        target = res.get("target")
        if target:
            plate_separation.render_distance(res["read"], target, cands, res.get("plates") or [])
        if res.get("grid"):
            plate_separation.render_grid(res["grid"])


def _reverse_simple() -> None:
    section("Reverse: camera reads for a registration",
            "Finds every camera capture whose read could be this registered plate. Each read is matched forward "
            "against all registrations and is listed only if that match points back to this plate.")
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
    if not rev["rows"]:
        st.info(f"No camera capture could be {rev['plate']}" + ("" if rev["registered"] else " (it is not registered)") + ".")
        return
    styled_table([{"Capture": r["capture_id"], "Read": r["read"], "Camera": r["camera"], "Time": r["time"],
                   "Probability": f"{r['probability']:.3f}", "Decision": r["decision"]} for r in rev["rows"]])


def render() -> None:
    page_head("Plate Match", "Match a noisy plate read against every registered vehicle, and find the camera "
              "reads of one registration.")
    how_to(["Give a plate: upload an image, pick a benchmark image, or type a read.",
            "Check what OCR read, then press Match.",
            "Read the matches: the highlighted top row is the answer."])
    with panel("pm_input"):
        got = _input_simple()
    with panel("pm_read"):
        read = _read_simple(got)
        if st.button("Match against the live registry", key="pm_run", type="primary"):
            _forward(read, got)
    res = st.session_state.get("pm_result")
    if res:
        with panel("pm_cands"):
            _matches(res)
    with panel("pm_reverse"):
        _reverse_simple()
