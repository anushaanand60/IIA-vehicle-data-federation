"""Separation views for the Plate Match page: how far the OCR read is from the real plate, and how
far the right vehicle stands from every other candidate.

`align()` is pure (no Streamlit, no source access) so it can be unit-tested: it aligns the true
plate with a read by the same weighted edit-distance dynamic program the matcher uses (deck p. 17),
with the learned confusion costs of `data/confusion_costs.json` as substitution costs, and labels
every position. The render functions only draw what `plate_match_lab` already computed, plus one
live `plate_match.match()` per benchmark image of the chosen plate (the damage grid).
Widget keys carry the `pm_sep_` prefix.
"""
from __future__ import annotations

import csv
import html
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from components import group_label, kpi_row, section, styled_table  # noqa: E402
from mediator import confusion, plate_match  # noqa: E402
from theme import TOKENS  # noqa: E402

BENCHMARK = _ROOT / "data" / "match_benchmark.csv"

# op -> (label printed under the cell, fill, border). The label is the signal; colour repeats it.
OPS = {
    "match": ("same", "#E6F4EA", TOKENS["ok"]),
    "confusable": ("OCR swap", "#FFF4E0", TOKENS["warn"]),
    "substitute": ("wrong", "#FDECEA", TOKENS["bad"]),
    "insert": ("extra", "#E8F1FB", TOKENS["info"]),
    "delete": ("missing", "#EEF0F2", TOKENS["neutral"]),
}


def align(truth: str, read: str, cost: Optional[Callable[[str, str], float]] = None) -> List[Dict[str, Any]]:
    """Position-by-position alignment of `truth` against `read`.

    Each entry: {"op", "truth", "read", "cost"}; op is match / confusable (a substitution the learned
    table prices below 1) / substitute (a full-cost substitution) / insert (a character only in the
    read) / delete (a character of the plate the read lost). Insert and delete cost 1, as on p. 17.
    Ties in the back-trace prefer the diagonal, so equal-length strings align column for column.
    """
    cost = cost or confusion.learned_confusion_cost
    a, b = str(truth or "").upper(), str(read or "").upper()
    n, m = len(a), len(b)
    d = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        d[i][0] = float(i)
    for j in range(1, m + 1):
        d[0][j] = float(j)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            sub = 0.0 if a[i - 1] == b[j - 1] else cost(a[i - 1], b[j - 1])
            d[i][j] = min(d[i - 1][j - 1] + sub, d[i - 1][j] + 1.0, d[i][j - 1] + 1.0)
    out: List[Dict[str, Any]] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0:
            same = a[i - 1] == b[j - 1]
            sub = 0.0 if same else cost(a[i - 1], b[j - 1])
            if abs(d[i][j] - (d[i - 1][j - 1] + sub)) < 1e-9:
                op = "match" if same else ("confusable" if sub < 1.0 else "substitute")
                out.append({"op": op, "truth": a[i - 1], "read": b[j - 1], "cost": round(sub, 4)})
                i, j = i - 1, j - 1
                continue
        if i > 0 and abs(d[i][j] - (d[i - 1][j] + 1.0)) < 1e-9:
            out.append({"op": "delete", "truth": a[i - 1], "read": "", "cost": 1.0})
            i -= 1
        else:
            out.append({"op": "insert", "truth": "", "read": b[j - 1], "cost": 1.0})
            j -= 1
    return out[::-1]


def summary(cells: List[Dict[str, Any]]) -> str:
    """'2 OCR swaps (0→O 0.10, B→8 0.62), 0 wrong, 0 extra, 0 missing; distance 0.72'."""
    swaps = [c for c in cells if c["op"] == "confusable"]
    count = {op: sum(1 for c in cells if c["op"] == op) for op in ("substitute", "insert", "delete")}
    swap_txt = ", ".join(f"{c['truth']}→{c['read']} {c['cost']:.2f}" for c in swaps)
    total = sum(c["cost"] for c in cells)
    if total == 0:
        return "identical: the read is the plate"
    return (f"{len(swaps)} OCR swap(s){f' ({swap_txt})' if swaps else ''}, {count['substitute']} wrong, "
            f"{count['insert']} extra, {count['delete']} missing; weighted distance {total:.2f}")


def diff_html(cells: List[Dict[str, Any]], top: str, bottom: str) -> str:
    """Two rows of character boxes with a word under each column; colour never carries meaning alone."""
    cols = []
    for c in cells:
        word, fill, border = OPS[c["op"]]
        if c["op"] == "confusable":
            word = f"swap {c['cost']:.2f}"
        cols.append(
            f'<div style="display:flex;flex-direction:column;align-items:center;gap:2px;">'
            f'<span style="{_box(fill, border)}">{html.escape(c["truth"]) or "·"}</span>'
            f'<span style="{_box(fill, border)}">{html.escape(c["read"]) or "·"}</span>'
            f'<span style="font-size:0.9rem;color:{TOKENS["ink2"]};white-space:nowrap;">{html.escape(word)}</span></div>')
    labels = (f'<div style="display:flex;flex-direction:column;gap:2px;margin-right:10px;font-size:0.95rem;'
              f'color:{TOKENS["ink"]};font-weight:600;"><span style="line-height:44px;">{html.escape(top)}</span>'
              f'<span style="line-height:44px;">{html.escape(bottom)}</span></div>')
    return (f'<div role="img" aria-label="{html.escape(top)} versus {html.escape(bottom)}: '
            f'{html.escape(summary(cells))}" style="display:flex;flex-wrap:wrap;gap:6px;align-items:flex-start;'
            f'margin:8px 0 4px;">{labels}{"".join(cols)}</div>')


def _box(fill: str, border: str) -> str:
    return (f"display:inline-flex;align-items:center;justify-content:center;width:40px;height:42px;"
            f"background:{fill};border:2px solid {border};border-radius:6px;font-family:var(--cv-font-mono);"
            f"font-size:1.35rem;font-weight:700;color:{TOKENS['ink']};")


def legend() -> None:
    st.markdown(" ".join(
        f'<span style="display:inline-block;margin:0 10px 6px 0;padding:2px 10px;background:{fill};'
        f'border:2px solid {border};border-radius:6px;font-size:0.95rem;color:{TOKENS["ink"]};">{word}</span>'
        for word, fill, border in OPS.values()) + f'<span style="font-size:0.95rem;color:{TOKENS["ink2"]};">'
        "· = no character on that side</span>", unsafe_allow_html=True)


@st.cache_data(show_spinner=False)
def benchmark_for(plate: str) -> List[Dict[str, str]]:
    """The 22 benchmark rows of one plate. A labelled repository file, not source data."""
    with BENCHMARK.open(encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if r["true_plate"] == plate and r["image_path"]]


def damage_grid(plate: str, registry: Dict[str, Any], max_edits: int) -> Dict[str, Any]:
    """Match every benchmark read of `plate` live against `registry` (the REG rows just read)."""
    rows = []
    for r in sorted(benchmark_for(plate), key=lambda r: (r["degradation"] != "clean", r["degradation"], r["severity"])):
        read = r["ocr_read"]
        conf = float(r["ocr_confidence"] or 0) or None
        cands = plate_match.match(read, confidence=conf, registry=registry, max_edits=max_edits)[0] if read else []
        top = cands[0] if cands else None
        rows.append({"path": r["image_path"], "damage": f"{r['degradation']} s{r['severity']}", "read": read,
                     "exact": read == plate, "top": top.plate if top else None,
                     "decision": top.decision if top else "REJECT", "p": top.probability if top else 0.0,
                     "right": bool(top and top.plate == plate)})
    return {"plate": plate, "rows": rows}


# ------------------------------------------------------------------ render

def render_diff(truth: str, raw: str, repaired: Optional[str]) -> None:
    section("Separation: the OCR read against the real plate",
            "What this shows: each character of the real plate above the character the camera read. "
            "An OCR swap is a confusion the camera makes often (0 and O), priced by the learned cost table; "
            "wrong, extra and missing characters cost a full edit.")
    legend()
    cells = align(truth, raw)
    st.markdown(diff_html(cells, "Real plate", "Raw OCR"), unsafe_allow_html=True)
    st.markdown(f"**Raw OCR read `{raw or '—'}`:** {summary(cells)}.")
    if repaired and repaired != raw:
        rep = align(truth, repaired)
        st.markdown(diff_html(rep, "Real plate", "Repaired"), unsafe_allow_html=True)
        st.markdown(f"**Repaired guess `{repaired}`:** {summary(rep)}.")
    swaps = [c for c in cells if c["op"] in ("confusable", "substitute")]
    if swaps:
        group_label("Cost of each substitution (data/confusion_costs.json; 1.00 = an arbitrary error)")
        styled_table([{"Real": c["truth"], "Read as": c["read"], "Cost": f"{c['cost']:.2f}",
                       "Kind": "OCR swap" if c["op"] == "confusable" else "arbitrary substitution"} for c in swaps])


def render_distance(read: str, truth: str, cands: List[Any], registry_plates: List[str]) -> None:
    section("How far apart: the right plate against the runner-up",
            "What this shows: every measure scores the read against the real plate and against the closest "
            "wrong plate. A positive margin means that measure prefers the right vehicle; the probability gap "
            "is what the decision is based on.")
    _, _, tfidf = plate_match.registry_artefacts(sorted(registry_plates))
    wrong = next((c for c in cands if c.plate != truth), None)
    true_c = next((c for c in cands if c.plate == truth), None)
    if wrong is None:
        st.info("No other candidate survived blocking, so there is no runner-up to separate from.")
        return
    t_scores = true_c.scores if true_c else plate_match.string_features(read, truth, tfidf)
    rows = [{"Measure": n, f"read vs {truth}": f"{t_scores.get(n, 0):.3f}",
             f"read vs {wrong.plate}": f"{wrong.scores.get(n, 0):.3f}",
             "Margin": f"{t_scores.get(n, 0) - wrong.scores.get(n, 0):+.3f}"} for n in plate_match.SCORE_NAMES]
    p_true = true_c.probability if true_c else 0.0
    kpi_row([("P(real plate)", f"{p_true:.3f}"), (f"P(runner-up {wrong.plate})", f"{wrong.probability:.3f}"),
             ("Probability gap", f"{p_true - wrong.probability:+.3f}"),
             ("Measures preferring the right plate", f"{sum(1 for r in rows if r['Margin'].startswith('+') and r['Margin'] != '+0.000')} of {len(rows)}")])
    if true_c is None:
        st.warning(f"{truth} is not among the ranked candidates: blocking or the shortlist dropped it, so its "
                   "string scores are shown but it had no probability.")
    styled_table(rows)
    group_label("Calibrated probability of the top 10 candidates")
    df = pd.DataFrame([{"plate": c.plate, "probability": round(c.probability, 3),
                        "kind": "real plate" if c.plate == truth else "other candidate"} for c in cands[:10]])
    chart = alt.Chart(df).mark_bar().encode(
        y=alt.Y("plate:N", sort=None, title=None, axis=alt.Axis(labelFontSize=15, labelFont="monospace")),
        x=alt.X("probability:Q", scale=alt.Scale(domain=[0, 1]), title="calibrated probability",
                axis=alt.Axis(labelFontSize=14, titleFontSize=15)),
        color=alt.Color("kind:N", scale=alt.Scale(domain=["real plate", "other candidate"],
                                                  range=[TOKENS["ok"], TOKENS["neutral"]]),
                        legend=alt.Legend(title=None, orient="top", labelFontSize=15)))
    text = chart.mark_text(align="left", dx=4, fontSize=14, color=TOKENS["ink"]).encode(text="probability:Q")
    st.altair_chart((chart + text).properties(height=34 * len(df) + 40), key="pm_sep_chart")


def render_grid(grid: Dict[str, Any]) -> None:
    plate, rows = grid["plate"], grid["rows"]
    section(f"The same plate under every damage level: {plate}",
            "What this shows: all 22 benchmark images of this one car, the raw text EasyOCR read from each "
            "(from data/match_benchmark.csv), and the live match of that read against the registry. OCR is "
            "often wrong; the matcher usually still finds the car, and says REVIEW or REJECT when it cannot.")
    if not rows:
        st.info(f"{plate} is not one of the 100 benchmark vehicles, so there are no rendered images of it.")
        return
    n = len(rows)
    kpi_row([("Images", n), ("OCR read exactly right", f"{sum(r['exact'] for r in rows)} of {n}"),
             ("Matcher ranked the car first", f"{sum(r['right'] for r in rows)} of {n}"),
             ("ACCEPTed and right", f"{sum(r['right'] and r['decision'] == 'ACCEPT' for r in rows)} of {n}"),
             ("ACCEPTed and wrong", f"{sum((not r['right']) and r['decision'] == 'ACCEPT' for r in rows)} of {n}")])
    cols = st.columns(4)
    for i, r in enumerate(rows):
        verdict = f"{r['decision']} {r['top'] or '—'} (p={r['p']:.2f})" + ("" if r["right"] or not r["top"] else " — wrong car")
        cap = (f"{r['damage']} · OCR read {r['read'] or '(nothing)'} · "
               f"{'exact' if r['exact'] else 'not exact'} · {verdict}")
        path = _ROOT / r["path"]
        with cols[i % 4]:
            if path.exists():
                st.image(str(path), caption=cap, width="stretch")
            else:
                st.markdown(f"_{cap} (image file not generated on this laptop)_")
    group_label("The same results as a table")
    styled_table([{"Damage": r["damage"], "Raw OCR read": r["read"] or "(nothing)",
                   "OCR exact": "yes" if r["exact"] else "no", "Matcher's top plate": r["top"],
                   "Right car": "yes" if r["right"] else "no", "Decision": r["decision"],
                   "Probability": f"{r['p']:.3f}"} for r in rows])
