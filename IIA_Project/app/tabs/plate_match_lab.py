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
from mediator import plate_match, plate_ocr  # noqa: E402
from scripts.match_plate import (funnel, live_captures, live_registry, max_edits, reverse_rows,  # noqa: E402
                                 run_ocr)
from theme import TOKENS  # noqa: E402

BENCHMARK = _ROOT / "data" / "match_benchmark.csv"
DECISION_TOKEN = {"ACCEPT": "ok", "REVIEW": "warn", "REJECT": "bad"}
MODES = ("Type a read", "Benchmark image", "Upload an image")
SHORT = {"edit_similarity": "edit", "ocr_edit_similarity": "ocr-edit", "jaro": "jaro", "jaro_winkler": "j-w",
         "overlap": "overlap", "jaccard": "jaccard", "ratcliff_obershelp": "r-o", "soundex_similarity": "soundex",
         "tfidf": "tf-idf", "learned_ocr_similarity": "learned"}
AGREE = {1.0: "agrees", -1.0: "differs", 0.0: "—"}


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


def _input_panel() -> Dict[str, Any]:
    section("Read a plate", "Type what a camera read, pick a rendered benchmark image, or upload a photo.")
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


def _read_panel(got: Dict[str, Any]) -> str:
    section("What the camera read", "The raw read is what the matcher was evaluated on; the repairs are "
            "plate_ocr's pattern-licensed confusion swaps (O→0 where a digit belongs, B→8 …).")
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
    me = max_edits()
    cands, stats = plate_match.match(read, confidence=got["confidence"], observed_make=got["make"],
                                     observed_colour=got["colour"], registry=registry)
    st.session_state["pm_result"] = {
        "read": read, "make": got["make"], "colour": got["colour"], "meta": meta, "max_edits": me,
        "stages": funnel(read, sorted(registry), me), "stats": stats, "cands": cands,
        "registry": {c.plate: registry.get(c.plate, {}) for c in cands},
        "at": datetime.now().strftime("%H:%M:%S")}


def _funnel_panel(res: Dict[str, Any]) -> None:
    section("Blocking funnel", f"{res['meta']['rows']} registrations read live from {res['meta']['source_id']} "
            f"in {res['meta']['elapsed_ms']} ms at {res['at']}; each filter is recall-safe up to "
            f"max_edits = {res['max_edits']} (chosen on validation).")
    kpi_row([(s["stage"], s["candidates"]) for s in res["stages"]]
            + [("scored", min(plate_match.SHORTLIST, res["stats"].candidates_after) or len(res["cands"]))])
    styled_table([{"Stage": s["stage"], "Filters applied": s["filters"], "Candidates left": s["candidates"],
                   "Elapsed ms": s["elapsed_ms"]} for s in res["stages"]])


def _candidates_panel(res: Dict[str, Any]) -> None:
    bundle, err = plate_match.load_bundle()
    model = "logistic regression, Platt-calibrated (data/match_model.joblib)" if bundle else \
        f"hand-weighted fallback — {err}"
    section(f"Ranked candidates for {res['read']}", f"Model: {model}. Each measure's score side by side; "
            "the decision reads the calibrated probability against thresholds chosen on validation.")
    rows = []
    for i, c in enumerate(res["cands"], 1):
        reg = res["registry"].get(c.plate, {})
        rows.append({"#": i, "Plate": c.plate, "Probability": f"{c.probability:.3f}", "Decision": c.decision,
                     **{SHORT[n]: f"{c.scores.get(n, 0):.2f}" for n in plate_match.SCORE_NAMES},
                     "Make": AGREE.get(c.features.get("make_agree"), "—"),
                     "Colour": AGREE.get(c.features.get("colour_agree"), "—"),
                     "Registered as": f"{reg.get('make') or ''} {reg.get('model') or ''} {reg.get('colour') or ''}"})
    styled_table(rows)
    chip_strip([decision_chip(c.decision, f"{c.plate} · {c.decision} · p={c.probability:.3f}")
                for c in res["cands"][:5]])
    if res["cands"]:
        group_label(f"Why {res['cands'][0].plate} is {res['cands'][0].decision}")
        st.markdown("\n".join(f"- {r}" for r in res["cands"][0].reasons))


def _reverse_panel() -> None:
    section("Reverse: camera reads for a registration",
            "Every CAM capture, read live, whose plate read could be this vehicle. Each read is scored by the "
            "same forward matcher, so a read that is really another registered car is never claimed.")
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


def render() -> None:
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
    with panel("pm_why"):
        section("Why this algorithm")
        st.markdown("Logistic regression over ten string measures plus the camera's make, model and colour ranked "
                    "the right vehicle first most often on held-out vehicles, and at the precision target it accepts the most "
                    "reads unattended — the numbers, and why the others lost, are on "
                    "[Matching Evaluation](?page=Matching%20Evaluation).")
    with panel("pm_reverse"):
        _reverse_panel()
