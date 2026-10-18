"""Matching Evaluation page (rubric 1, "justify why"): the measured comparison, on screen.

Reads `docs/MATCHING_EVALUATION.md`, which `scripts/evaluate_matching.py` writes deterministically,
and parses its markdown tables, so the page can never show a number the document does not contain,
and re-running the evaluation updates both at once. (Trade-off: parsing markdown is looser than a
JSON side-file, but it needs no change to the evaluation script and no second artefact that could
drift from the document the viva cites.) Keys carry the `me_` prefix.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from components import kpi_row, page_head, panel, section, styled_table  # noqa: E402
from components import what_this_shows  # noqa: E402  (phase-2 accessibility pass)
from components import how_to  # noqa: E402  (phase-2 simplified GUI)
from theme import FONTS, TOKENS  # noqa: E402

DOC = _ROOT / "docs" / "MATCHING_EVALUATION.md"


def sections(text: str) -> Dict[str, str]:
    """'## 2. Scoring, evaluated on its own' -> {'2': body}; keyed on the number the generator writes."""
    out = {}
    for block in re.split(r"^## ", text, flags=re.M)[1:]:
        title, _, body = block.partition("\n")
        out[title.split(".")[0].strip()] = body
    return out


def _cells(line: str) -> List[str]:
    return [c.strip().replace("**", "").replace("`", "") for c in line.strip().strip("|").split("|")]


def table(body: str) -> List[Dict[str, str]]:
    """The first markdown table in a section, as rows keyed by its header."""
    lines = [l for l in body.splitlines() if l.strip().startswith("|")]
    if len(lines) < 3:
        return []
    head = _cells(lines[0])
    return [dict(zip(head, _cells(l))) for l in lines[2:]]


def prose(body: str) -> str:
    return "\n".join(l for l in body.splitlines() if not l.strip().startswith("|")).strip()


def winner(decision: str, scoring: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    """The contender the Decision section names ("**Use `logreg`.**"), not whatever row is first."""
    m = re.search(r"Use `([^`]+)`", decision)
    name = m.group(1) if m else None
    return next((r for r in scoring if r.get("contender") == name), None)


def _num(value: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _chart(rows: List[Dict[str, str]], best: Optional[str]) -> None:
    names = [r["contender"] + (" ★" if r["contender"] == best else "") for r in rows]
    bars = [("top-1", "top-1", TOKENS["brand"]), ("ACCEPT recall", "ACCEPT R", TOKENS["action"])]
    # PHASE2-REPLACED (accessibility pass: 11 px value labels are below the 0.9 rem floor):
    # fig = go.Figure([go.Bar(name=label, x=names, y=[_num(r[col]) for r in rows], marker_color=colour,
    #                         text=[r[col] for r in rows], textposition="outside", textfont=dict(size=11))
    #                  for label, col, colour in bars])
    fig = go.Figure([go.Bar(name=label, x=names, y=[_num(r[col]) for r in rows], marker_color=colour,
                            text=[r[col] for r in rows], textposition="outside", textfont=dict(size=14))
                     for label, col, colour in bars])
    fig.update_layout(barmode="group", height=420, margin=dict(l=10, r=10, t=30, b=10),
                      paper_bgcolor=TOKENS["panel"], plot_bgcolor=TOKENS["panel"],
                      font=dict(color=TOKENS["ink"], family=FONTS["body"], size=14),
                      legend=dict(orientation="h", y=1.08, x=0),
                      yaxis=dict(range=[0, 1.1], gridcolor=TOKENS["line"], title="share of test reads"),
                      xaxis=dict(tickangle=-35))
    st.plotly_chart(fig, key="me_chart", config={"displayModeBar": False})


# PHASE2-HIDDEN (simplified GUI, 19 Oct 2026): the whole previous page: six KPIs, chart, the full 'Why' prose, and the scoring, ablation, blocking, weights, degradation and real-read panels all open; previously called from app/app.py as render_matching_eval.
def _render_before_simplification() -> None:
    page_head("Matching Evaluation", "Why the matcher is what it is: every contender measured on vehicles it "
              "never saw in training, with retrieval and scoring evaluated separately.")
    if not DOC.exists():
        st.error("docs/MATCHING_EVALUATION.md is missing: run python scripts/evaluate_matching.py")
        return
    sec = sections(DOC.read_text(encoding="utf-8"))
    scoring = table(sec.get("2", ""))
    best = winner(sec.get("7", ""), scoring) or {}
    with panel("me_head"):
        section("The decision", "From docs/MATCHING_EVALUATION.md, written by scripts/evaluate_matching.py "
                "(seeded, deterministic).")
        what_this_shows("the model the system uses and how often it is right on 20 cars it never saw while learning.")
        kpi_row([("Chosen model", best.get("contender", "—")), ("Top-1, test", best.get("top-1", "—")),
                 ("MRR, test", best.get("MRR", "—")), ("ACCEPT precision", best.get("ACCEPT P", "—")),
                 ("ACCEPT recall", best.get("ACCEPT R", "—")), ("Sent to review", best.get("REVIEW rate", "—"))])
    with panel("me_chart_panel"):
        section("Top-1 and unattended ACCEPT recall per contender",
                "Test split. Every contender ranks the same blocked candidates, so the gaps are scoring, not retrieval.")
        what_this_shows("for each method we tried, how often the right car came first (navy) and how often it was accepted without a person (green). The star marks the method in use.")
        if scoring:
            _chart(scoring, best.get("contender"))
    with panel("me_why"):
        section("Why, in plain sentences")
        what_this_shows("the reasons for the choice, written as sentences, with the methods that lost and why.")
        st.markdown(prose(sec.get("7", "")) or "The Decision section is missing from the document.")
    with panel("me_scoring"):
        section("Scoring, evaluated on its own", "Calibrated, with thresholds chosen on validation per contender.")
        what_this_shows("every method ranks the same candidate plates; this table compares only how well each ranks them.")
        styled_table(scoring)
    with panel("me_ablation"):
        section("Ablation: what each part of the combination is worth",
                "Each row retrains the combiner without the named features.")
        what_this_shows("the combined model retrained with one kind of evidence removed; the bigger the drop, the more that evidence is worth.")
        styled_table(table(sec.get("4", "")))
    with panel("me_blocking"):
        section("Blocking funnel: retrieval recall per filter",
                "Recall = the true plate survives into the candidate set; mean candidates = work left for scoring.")
        what_this_shows("how many plates each cheap filter keeps, and how often the right plate survives it.")
        styled_table(table(sec.get("1", "")))
        st.markdown(prose(sec.get("1", "")))
    with panel("me_weights"):
        section("Logistic-regression weights", "Standardised features: a positive weight pushes towards the same vehicle.")
        what_this_shows("how much each piece of evidence pushes the model towards 'same car' (positive) or away (negative).")
        styled_table(table(sec.get("5", "")))
    with panel("me_degradation"):
        section("By degradation and severity", "Top-1 on test, one read per test vehicle per bucket.")
        what_this_shows("accuracy for each kind and strength of image damage, so you can see where OCR and matching break.")
        styled_table(table(sec.get("3", "")), max_rows=40)
    with panel("me_observed"):
        section("Real camera reads with no ground truth")
        what_this_shows("what the matcher would do with the real camera records whose plate matches no registration.")
        st.markdown(prose(sec.get("6", "")))


# ------------------------------------------------------------------ phase-2 simplified page

def decision_text(decision: str) -> str:
    """Two or three sentences from the Decision section: its first paragraph (the choice and its numbers)
    and the bullet that names the camera context as the reason a combination wins."""
    body = prose(decision)
    first = body.split("\n\n")[0].strip()
    why = next((l.strip()[2:] for l in body.splitlines() if l.strip().startswith("- ") and "camera's make" in l), "")
    return " ".join(x for x in (first, why) if x)


def render() -> None:
    page_head("Matching Evaluation", "Every matching method measured on vehicles it never saw in training, "
              "and the one we chose.")
    how_to(["Read the decision: the method in use and why.",
            "Compare the methods in the table and the chart.",
            "Open Details for the ablation and the other measurements."])
    if not DOC.exists():
        st.error("docs/MATCHING_EVALUATION.md is missing: run python scripts/evaluate_matching.py")
        return
    sec = sections(DOC.read_text(encoding="utf-8"))
    scoring = table(sec.get("2", ""))
    best = winner(sec.get("7", ""), scoring) or {}
    with panel("me_head"):
        section("The decision")
        # PHASE2-REPLACED (review: the same REVIEW rate is labelled 'Sent to a person (REVIEW)' on How it works; one name
        # for one number across pages):
        # kpi_row([("Chosen model", best.get("contender", "—")), ("Top-1, test", best.get("top-1", "—")),
        #          ("ACCEPT precision", best.get("ACCEPT P", "—")), ("Sent to review", best.get("REVIEW rate", "—"))])
        kpi_row([("Chosen model", best.get("contender", "—")), ("Top-1, test", best.get("top-1", "—")),
                 ("ACCEPT precision", best.get("ACCEPT P", "—")), ("Sent to a person (REVIEW)", best.get("REVIEW rate", "—"))])
        st.markdown(decision_text(sec.get("7", "")) or "The Decision section is missing from the document.")
    with panel("me_scoring"):
        section("Every method compared", "Test split: vehicles never seen in training; the star marks the method in use.")
        styled_table(scoring)
        if scoring:
            _chart(scoring, best.get("contender"))
    with panel("me_more"):
        with st.expander("Details: ablation, blocking funnel, weights, degradation, real camera reads", expanded=False):
            section("Ablation: what each part of the combination is worth", "Each row retrains the combiner without the named features.")
            styled_table(table(sec.get("4", "")))
            section("Blocking funnel: retrieval recall per filter", "Recall = the true plate survives into the candidate set.")
            styled_table(table(sec.get("1", "")))
            section("Logistic-regression weights", "A positive weight pushes towards the same vehicle.")
            styled_table(table(sec.get("5", "")))
            section("By degradation and severity", "Top-1 on test, one read per test vehicle per bucket.")
            styled_table(table(sec.get("3", "")), max_rows=40)
            section("Real camera reads with no ground truth")
            st.markdown(prose(sec.get("6", "")))
