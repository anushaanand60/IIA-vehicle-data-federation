"""Matching Evaluation page (rubric 1, "justify why"): the measured comparison, on screen.

Reads `docs/MATCHING_EVALUATION.md`, which `scripts/evaluate_matching.py` writes deterministically,
and parses its markdown tables, so the page can never show a number the document does not contain
(and re-running the evaluation updates both at once). Keys carry the `me_` prefix.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from components import kpi_row, page_head, panel, section, styled_table  # noqa: E402
from theme import TOKENS  # noqa: E402

DOC = _ROOT / "docs" / "MATCHING_EVALUATION.md"


def sections(text: str) -> Dict[str, str]:
    """'## 2. Scoring, ...' -> {'2': body}."""
    out = {}
    for block in re.split(r"^## ", text, flags=re.M)[1:]:
        title, _, body = block.partition("\n")
        out[title.split(".")[0].strip()] = body
    return out


def table(body: str) -> List[Dict[str, str]]:
    lines = [l.strip() for l in body.splitlines() if l.strip().startswith("|")]
    if len(lines) < 2:
        return []
    cells = lambda l: [c.strip().strip("*") for c in l.strip("|").split("|")]
    head = cells(lines[0])
    return [dict(zip(head, cells(l))) for l in lines[2:]]


def prose(body: str) -> str:
    return "\n".join(l for l in body.splitlines() if not l.strip().startswith("|")).strip()


def _chart(rows: List[Dict[str, str]]) -> None:
    names = [r["contender"] for r in rows]
    fig = go.Figure([go.Bar(name="top-1", x=names, y=[float(r["top-1"]) for r in rows], marker_color=TOKENS["brand"]),
                     go.Bar(name="ACCEPT recall", x=names, y=[float(r["ACCEPT R"]) for r in rows],
                            marker_color=TOKENS["action"])])
    fig.update_layout(barmode="group", height=380, margin=dict(l=10, r=10, t=10, b=10),
                      paper_bgcolor=TOKENS["panel"], plot_bgcolor=TOKENS["panel"],
                      font=dict(color=TOKENS["ink"]), yaxis=dict(range=[0, 1]))
    st.plotly_chart(fig, key="me_chart")


def render() -> None:
    page_head("Matching Evaluation", "Why the matcher is logistic regression: every contender scored on 20 held-out "
              "vehicles, with the retrieval step and the scoring step evaluated separately.")
    if not DOC.exists():
        st.error("docs/MATCHING_EVALUATION.md is missing: run python scripts/evaluate_matching.py")
        return
    sec = sections(DOC.read_text(encoding="utf-8"))
    scoring = table(sec.get("2", ""))
    best = scoring[0] if scoring else {}
    with panel("me_head"):
        section("The decision")
        kpi_row([("Chosen model", best.get("contender", "—")), ("Top-1 (test)", best.get("top-1", "—")),
                 ("ACCEPT precision", best.get("ACCEPT P", "—")), ("ACCEPT recall", best.get("ACCEPT R", "—"))])
        st.markdown(prose(sec.get("7", "")))
    with panel("me_chart"):
        section("Top-1 and ACCEPT recall per contender", "Test split; every contender ranks the same blocked candidates.")
        _chart(scoring)
    with panel("me_scoring"):
        section("Scoring, evaluated on its own")
        styled_table(scoring)
    with panel("me_ablation"):
        section("Ablation: what the combination adds")
        styled_table(table(sec.get("4", "")))
    with panel("me_blocking"):
        section("Blocking funnel: retrieval recall per filter", prose(sec.get("1", "")).split("\n")[0])
        styled_table(table(sec.get("1", "")))
    with panel("me_weights"):
        section("Logistic-regression weights")
        styled_table(table(sec.get("5", "")))
    with panel("me_degradation"):
        section("By degradation and severity")
        styled_table(table(sec.get("3", "")), max_rows=40)
