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
from scripts.match_plate import funnel, live_captures, live_registry, max_edits, reverse_rows  # noqa: E402
from theme import TOKENS  # noqa: E402

BENCHMARK = _ROOT / "data" / "match_benchmark.csv"
DECISION_TOKEN = {"ACCEPT": "ok", "REVIEW": "warn", "REJECT": "bad"}
MODES = ("Type a read", "Benchmark image", "Upload an image")


def decision_chip(decision: str, label: Optional[str] = None) -> str:
    return chip(label or decision, TOKENS[DECISION_TOKEN.get(decision, "neutral")])


@st.cache_data(show_spinner=False)
def benchmark_rows() -> List[Dict[str, str]]:
    """The labelled benchmark (a file in the repo, not source data), rendered rows only."""
    with BENCHMARK.open(encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if r["image_path"]]


class _Recorder:
    """Wraps the EasyOCR reader so the raw text read_plate repairs is kept for display."""

    def __init__(self, inner: Any) -> None:
        self.inner, self.raw = inner, []

    def readtext(self, *args: Any, **kwargs: Any) -> Any:
        self.raw = self.inner.readtext(*args, **kwargs)
        return self.raw


def run_ocr(image: bytes) -> Dict[str, Any]:
    rec = _Recorder(plate_ocr._get_reader())
    guesses = plate_ocr.read_plate(image, reader=rec)
    parts = sorted((min(p[0] for p in e[0]), plate_ocr.canonical(e[1]), float(e[2])) for e in rec.raw)
    raw = "".join(p[1] for p in parts)
    conf = sum(p[2] for p in parts) / len(parts) if parts else 0.0
    return {"raw": raw, "confidence": round(conf, 3),
            "guesses": [(g.text, round(g.confidence, 3)) for g in guesses]}


def _input_panel() -> Dict[str, Any]:
    section("Read a plate", "Type what a camera read, pick a rendered benchmark image, or upload a photo.")
    mode = st.radio("Input", MODES, key="pm_mode", horizontal=True)
    got: Dict[str, Any] = {"mode": mode, "image": None, "raw": None, "confidence": None, "truth": None}
    if mode == "Type a read":
        got["raw"] = st.text_input("Plate as read", "DL05CD9B76", key="pm_typed")
        got["confidence"] = st.number_input("OCR confidence", 0.0, 1.0, 0.71, 0.01, key="pm_conf")
    elif mode == "Benchmark image":
        rows = benchmark_rows()
        c1, c2 = st.columns(2)
        deg = c1.selectbox("Degradation", sorted({r["degradation"] for r in rows}), key="pm_deg")
        sev = c2.selectbox("Severity", sorted({r["severity"] for r in rows if r["degradation"] == deg}),
                           key="pm_sev")
        pool = [r for r in rows if r["degradation"] == deg and r["severity"] == sev]
        pick = st.selectbox("Image", range(len(pool)), key="pm_img",
                            format_func=lambda i: Path(pool[i]["image_path"]).name)
        row = pool[pick]
        got.update(image=(_ROOT / row["image_path"]).read_bytes(), raw=row["ocr_read"],
                   confidence=float(row["ocr_confidence"] or 0), truth=row["true_plate"])
    else:
        up = st.file_uploader("Plate image", type=["png", "jpg", "jpeg"], key="pm_upload")
        if up is not None:
            data = up.getvalue()
            key = hashlib.sha1(data).hexdigest()
            cache = st.session_state.setdefault("pm_ocr", {})
            if key not in cache:
                try:
                    with st.spinner("Reading the plate with EasyOCR…"):
                        cache[key] = run_ocr(data)
                except plate_ocr.OCRUnavailable as exc:
                    st.error(str(exc))
                    cache[key] = {"raw": "", "confidence": 0.0, "guesses": []}
            got.update(image=data, raw=cache[key]["raw"], confidence=cache[key]["confidence"],
                       guesses=cache[key]["guesses"])
    c1, c2 = st.columns(2)
    got["make"] = c1.text_input("Observed make (optional)", "", key="pm_make") or None
    got["colour"] = c2.text_input("Observed colour (optional)", "", key="pm_colour") or None
    return got


def _read_panel(got: Dict[str, Any]) -> Optional[str]:
    section("What the camera read", "The raw OCR text is what the matcher was evaluated on; the repairs "
            "are plate_ocr's pattern-licensed confusion swaps (O->0, B->8 ...).")
    left, right = st.columns([2, 3])
    if got["image"] is not None:
        left.image(got["image"], width="stretch")
    raw = plate_ocr.canonical(got["raw"] or "")
    repaired = plate_ocr.normalise_candidates(raw)
    with right:
        kpi_row([("Raw read", raw or "—"), ("OCR confidence", got["confidence"] if got["confidence"] is not None else "—"),
                 ("True plate", got["truth"] or "unknown")])
        group_label("Repaired guesses (best first)")
        st.markdown(" · ".join(f"`{g}`" for g in repaired[:8]) or "none")
        if got.get("guesses"):
            group_label("read_plate output")
            st.markdown(" · ".join(f"`{t}` ({c})" for t, c in got["guesses"]))
        use = st.radio("Match on", ["raw read", "best repaired guess"], key="pm_use", horizontal=True)
    return raw if use == "raw read" else (repaired[0] if repaired else raw)


def _forward(read: str, got: Dict[str, Any]) -> None:
    try:
        registry, meta = live_registry()
    except Exception as exc:
        st.error(f"Refused: the registration authority could not be read live ({exc}).")
        return
    me = max_edits()
    stages = funnel(read, sorted(registry), me)
    cands, stats = plate_match.match(read, confidence=got["confidence"], observed_make=got["make"],
                                     observed_colour=got["colour"], registry=registry)
    st.session_state["pm_result"] = {"read": read, "meta": meta, "stages": stages, "stats": stats,
                                     "cands": cands, "registry": registry, "max_edits": me}


def _funnel_panel(res: Dict[str, Any]) -> None:
    section("Blocking funnel", f"Registry read live from {res['meta']['source_id']}: {res['meta']['rows']} rows "
            f"in {res['meta']['elapsed_ms']} ms. Each filter is recall-safe up to max_edits={res['max_edits']}.")
    rows = [{"Stage": s["stage"], "Filters": s["filters"], "Candidates": s["candidates"],
             "Elapsed ms": s["elapsed_ms"]} for s in res["stages"]]
    rows.append({"Stage": "shortlist", "Filters": "Jaccard, then OCR edit similarity",
                 "Candidates": min(plate_match.SHORTLIST, res["stats"].candidates_after), "Elapsed ms": "—"})
    styled_table(rows)


def _candidates_panel(res: Dict[str, Any]) -> None:
    bundle, err = plate_match.load_bundle()
    model = "logreg (Platt-calibrated)" if bundle else f"weighted fallback ({err})"
    section("Ranked candidates", f"Model: {model}. Every measure's score side by side; the decision uses the "
            "calibrated probability and thresholds chosen on validation.")
    rows = []
    for i, c in enumerate(res["cands"], 1):
        reg = res["registry"].get(c.plate, {})
        rows.append({"#": i, "Plate": c.plate, "p": f"{c.probability:.3f}", "Decision": c.decision,
                     **{n: f"{c.scores.get(n, 0):.2f}" for n in plate_match.SCORE_NAMES},
                     "Registered": f"{reg.get('make')} {reg.get('model')} {reg.get('colour')}"})
    styled_table(rows)
    chip_strip([decision_chip(c.decision, f"{c.plate} · {c.decision}") for c in res["cands"]])
    if res["cands"]:
        group_label("Why the top candidate")
        for r in res["cands"][0].reasons:
            st.markdown(f"- {r}")


def _reverse_panel() -> None:
    section("Reverse: camera reads for a registration",
            "Every CAM capture, read live, whose plate read could be this vehicle — each scored by the same "
            "forward matcher, so a read that is really another registered car is never claimed.")
    plate = st.text_input("Registration number", "DL05CD9876", key="pm_rev_plate")
    if st.button("Find camera reads", key="pm_rev_run", type="primary"):
        try:
            registry, _ = live_registry()
            captures, meta = live_captures()
            st.session_state["pm_rev"] = {"plate": plate, "rows": reverse_rows(plate, captures, registry),
                                          "meta": meta}
        except Exception as exc:
            st.error(f"Refused: a source could not be read live ({exc}).")
    rev = st.session_state.get("pm_rev")
    if rev:
        rows = [{"Capture": r["capture_id"], "Read": r["read"], "Camera": r["camera"], "Time": r["time"],
                 "Make": r["observed_make"], "Model": r["observed_model"], "Colour": r["observed_colour"],
                 "OCR conf": r["ocr_confidence"], "p": f"{r['probability']:.3f}", "Decision": r["decision"]}
                for r in rev["rows"]]
        if rows:
            styled_table(rows)
            chip_strip([decision_chip(r["decision"], f"#{r['capture_id']} · {r['decision']}") for r in rev["rows"]])
        else:
            st.info(f"No camera read in {rev['meta']['rows']} captures could be {rev['plate']}.")


def render() -> None:
    page_head("Plate Match", "A noisy plate read, matched approximately against every registered vehicle "
              "— and the reverse, every camera read that could be one registration.")
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
        st.markdown("Logistic regression over ten string measures plus the camera's make, model and colour won "
                    "on the held-out vehicles — see [Matching Evaluation](?page=Matching+Evaluation).")
    with panel("pm_reverse"):
        _reverse_panel()
