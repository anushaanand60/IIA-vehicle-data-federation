"""The Matching Evaluation page (`app/tabs/matching_eval.py`): the justification, parsed from the doc."""
from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

from app.tabs import matching_eval

DOC = Path(__file__).resolve().parents[1] / "docs" / "MATCHING_EVALUATION.md"


def _page() -> None:
    from app.tabs.matching_eval import render
    render()


def test_parser_reads_every_table_the_page_shows():
    sec = matching_eval.sections(DOC.read_text(encoding="utf-8"))
    scoring = matching_eval.table(sec["2"])
    assert {"contender", "top-1", "ACCEPT P", "ACCEPT R"} <= set(scoring[0])
    best = matching_eval.winner(sec["7"], scoring)
    assert best is not None and best["contender"] in {r["contender"] for r in scoring}
    assert all("*" not in r["contender"] for r in scoring)          # bold markers stripped
    assert matching_eval.table(sec["4"])[0]["variant"] == "full feature set"
    assert {"strategy", "recall test"} <= set(matching_eval.table(sec["1"])[0])


def test_page_renders_chart_tables_and_decision():
    at = AppTest.from_function(_page, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    text = " ".join(str(m.value) for m in at.markdown)
    for heading in ("The decision", "Ablation", "Blocking funnel", "Logistic-regression weights"):
        assert heading in text, heading
    kpis = " ".join(str(m.value) for m in at.markdown if "cv-kpi" in str(m.value))
    sec = matching_eval.sections(DOC.read_text(encoding="utf-8"))
    best = matching_eval.winner(sec["7"], matching_eval.table(sec["2"]))
    assert best["contender"] in kpis and best["top-1"] in kpis
    assert at.get("plotly_chart"), "no chart rendered"


def test_simplified_page_keeps_details_collapsed_and_three_sentence_decision():
    at = AppTest.from_function(_page, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    text = " ".join(str(m.value) for m in at.markdown)
    assert "How to use" in text and "What this shows" not in text
    details = [e for e in at.expander if e.label.startswith("Details")]
    assert len(details) == 1 and not details[0].proto.expanded
    assert len(at.get("plotly_chart")) == 1
    sec = matching_eval.sections(DOC.read_text(encoding="utf-8"))
    decision = matching_eval.decision_text(sec["7"])
    assert decision.startswith("**Use `") and "camera's make" in decision
    assert decision.count(". ") <= 3
