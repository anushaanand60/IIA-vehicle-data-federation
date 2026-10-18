"""Cleansing validation panel (Data Cleansing page): does the rule tree catch and fix noise it was
never shown?

`mediator/cleansing_validation.py::validate` injects known noise of several types into clean camera
records, runs detection and the RDR proposals, and scores them against the known original; its saved
output is `data/cleansing_validation.json`. This panel draws that file (or a fresh live run): overall
figures, a per-noise-type table, a chart of detection recall and correction accuracy per type, and
example rows. Placed on the Data Cleansing page rather than Matching Evaluation because it measures
exactly the rules that page shows and applies (rubric 2); Matching Evaluation is the rubric-1 page.
Keys carry `dc_val_`.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

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
from theme import TOKENS  # noqa: E402

SAVED = _ROOT / "data" / "cleansing_validation.json"


def _fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.3f}"
    if isinstance(v, list) and all(isinstance(x, (str, int, float)) for x in v):
        return ", ".join(str(x) for x in v) or "none"
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str)
    return "—" if v is None else str(v)


def _label(key: str) -> str:
    return key.replace("_", " ")


def _profile(d: Any) -> str:
    """{'vehicle_make': None, 'vehicle_colour': 'Grey'} -> 'make —, colour Grey'."""
    if not isinstance(d, dict):
        return _fmt(d)
    return ", ".join(f"{k.split('_')[-1]} {_fmt(v)}" for k, v in d.items())


# The columns an examiner needs, in reading order; nine columns overflowed the panel, so the per-type
# count goes in the label above the table and detection recall / false-alarm rate share one column (a
# control record has nothing to detect, a noisy one cannot raise a false alarm). Every other metric is
# still in data/cleansing_validation.json and docs/CLEANSING_VALIDATION.md.
COLUMNS = ("expected", "correction_accuracy", "quarantine_rate", "wrong_corrections")
HEAD = {"expected": "expected rule outcome", "correction_accuracy": "corrected right",
        "quarantine_rate": "quarantined", "wrong_corrections": "wrong"}


def _detected(m: Dict[str, Any]) -> str:
    if m.get("detection_recall") is not None:
        return _fmt(m["detection_recall"])
    if m.get("false_alarm_rate") is not None:
        return f"false alarms {_fmt(m['false_alarm_rate'])}"
    return "—"


def per_type_rows(per_type: Dict[str, Dict[str, Any]]) -> List[Dict[str, str]]:
    rows = []
    for t, m in per_type.items():
        known = [k for k in COLUMNS if k in m]
        if not known:  # an unknown shape is shown whole rather than as an empty table
            rows.append({"Noise type": t, **{_label(k): _fmt(v) for k, v in m.items()}})
            continue
        row = {"Noise type": t, "expected rule outcome": _fmt(m.get("expected")), "detected": _detected(m)}
        row.update({HEAD[k]: _fmt(m.get(k)) for k in known if k != "expected"})
        rows.append(row)
    return rows


def chart_frame(per_type: Dict[str, Dict[str, Any]]) -> pd.DataFrame:
    """Long frame of (type, metric, value) for detection recall and correction accuracy only."""
    rows = []
    for t, m in per_type.items():
        for k, v in m.items():
            name = k.lower()
            if isinstance(v, (int, float)) and not isinstance(v, bool) and (
                    ("detect" in name and "recall" in name) or ("correct" in name and "accuracy" in name)):
                rows.append({"type": t, "metric": _label(k), "value": round(float(v), 3)})
    return pd.DataFrame(rows, columns=["type", "metric", "value"])


def _chart(per_type: Dict[str, Dict[str, Any]]) -> None:
    df = chart_frame(per_type)
    if df.empty:
        return
    group_label("Detection recall and correction accuracy per noise type")
    base = alt.Chart(df).encode(
        y=alt.Y("type:N", title=None, axis=alt.Axis(labelFontSize=15)),
        yOffset="metric:N",
        x=alt.X("value:Q", scale=alt.Scale(domain=[0, 1.1]), title="share of injected records",
                axis=alt.Axis(labelFontSize=14, titleFontSize=15)),
        color=alt.Color("metric:N", scale=alt.Scale(range=[TOKENS["brand"], TOKENS["action"]]),
                        legend=alt.Legend(title=None, orient="top", labelFontSize=15)))
    bars = base.mark_bar()
    text = base.mark_text(align="left", dx=4, fontSize=14, color=TOKENS["ink"]).encode(text="value:Q")
    chart = (bars + text).properties(height=max(200, 64 * df["type"].nunique())).configure_axis(
        labelFontSize=15, titleFontSize=15, labelColor=TOKENS["ink"], titleColor=TOKENS["ink"]).configure_legend(
        labelFontSize=15, labelColor=TOKENS["ink"]).configure_view(strokeWidth=0)
    st.altair_chart(chart, key="dc_val_chart", theme=None, width="stretch")  # theme=None keeps the 15 px text


def _live() -> None:
    try:
        from mediator import cleansing_validation
    except Exception as exc:
        st.error(f"Not available yet: mediator/cleansing_validation.py could not be loaded ({exc}).")
        return
    with st.spinner("Injecting noise into 300 records and cleansing them live…"):
        try:
            out = cleansing_validation.validate(n=300)
        except Exception as exc:  # a refusal (e.g. a source down) is shown, never raised
            st.error(f"Refused: {exc}")
            return
    st.session_state["dc_val_live"] = {"data": out, "at": datetime.now().strftime("%H:%M:%S")}


def render() -> None:
    # PHASE2-REPLACED (simplified GUI, 19 Oct 2026: section leads are one short sentence):
    # section("Cleansing validation: noise we injected, and what the rules did with it",
    #         "What this shows: clean camera records are damaged on purpose in known ways; the rules must detect the "
    #         "damage and propose the original value. Because the original is known, every number here is checkable.")
    section("Cleansing validation: noise we injected, and what the rules did with it",
            "Clean records damaged on purpose in known ways, so every number is checkable.")
    if st.button("Re-run validation live (300 records)", key="dc_val_run"):
        _live()
    live = st.session_state.get("dc_val_live")
    if live:
        data, origin = live["data"], f"live run at {live['at']}"
    elif SAVED.exists():
        try:
            data, origin = json.loads(SAVED.read_text(encoding="utf-8")), "saved file data/cleansing_validation.json"
        except ValueError as exc:
            st.error(f"data/cleansing_validation.json is not valid JSON: {exc}")
            return
    else:
        st.info("Not available yet: data/cleansing_validation.json has not been produced. Press Re-run to "
                "compute it live.")
        return
    st.markdown(f"Source: {origin}; seed {data.get('seed', '—')}; records read from "
                f"{data.get('source', 'the live camera source')}"
                + (f", generated {str(data['generated_at'])[:19]}" if data.get("generated_at") else "") + ".")
    overall = data.get("overall") or {}
    if overall:
        kpi_row([(_label(k), _fmt(v)) for k, v in overall.items() if not isinstance(v, (dict, list))])
    per_type = data.get("per_type") or {}
    if per_type:
        counts = sorted({m.get("n") for m in per_type.values() if m.get("n") is not None})
        group_label("Per noise type" + (f" ({', '.join(map(str, counts))} injected records each)" if counts else ""))
        styled_table(per_type_rows(per_type))
        _chart(per_type)
    examples = data.get("examples") or []
    if examples:
        group_label("Examples: original → noisy → what the rules proposed")
        # one "original → noisy" cell: eight columns scrolled the Proposed and Correct? columns off the panel
        styled_table([{"Type": f"{e.get('type')} ({_fmt(e.get('field'))})",
                       "Original → noisy": f"{_fmt(e.get('original'))} → {_fmt(e.get('noisy'))}",
                       "Detected": _fmt(e.get("detected")),
                       "Proposed": _fmt(e.get("proposed")),
                       "Rule path": " → ".join(map(str, e.get("rule_path") or [])) or "none",
                       "Correct?": _fmt(e.get("correct"))} for e in examples], max_rows=30)
    wrong = data.get("wrong_corrections_detail") or []
    if wrong:
        group_label(f"Every wrong correction ({len(wrong)}), listed so it can be checked by hand")
        styled_table([{"Type": w.get("type"), "Original → noisy → set to":
                       f"{_fmt(w.get('original'))} → {_fmt(w.get('noisy'))} → {_fmt(w.get('set_to'))}",
                       "Rule": _fmt(w.get("rule")), "Registered profile of the plate it chose":
                       _profile(w.get("registered_profile_of_set_value")),
                       "What the camera saw": _profile(w.get("observed"))} for w in wrong])
        for w in wrong:
            st.markdown(f"**Why {w.get('rule')} fired:** {w.get('because', '—')}")
