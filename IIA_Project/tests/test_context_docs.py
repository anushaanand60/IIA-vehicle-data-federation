"""CONTEXT-ADDED (R3): the case file's document cards, reader and claim matrix render - and never raise.

Hand-built `res` (the shape context_flow.run_query returns) from tests/test_claim_matrix.case(); no port, no cluster.
"""
from __future__ import annotations

import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "app"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from tabs import context_docs as cd  # noqa: E402
from tests.test_claim_matrix import case  # noqa: E402


def res_for(**kw):
    ctx, docs = case(**kw)
    return {"query": "MH12EF0248", "plate": "MH12EF0248", "context": ctx, "docs": docs, "indicators": [],
            "summary": None, "missing": [], "problems": []}


def _page() -> None:
    import os
    from tabs.context_docs import render_claims, render_documents
    from tests.test_context_docs import res_for
    res = {"none": lambda: {"context": None, "docs": None}, "broken": lambda: {"context": object(), "docs": object()}
           }.get(os.environ.get("CXD_CASE", ""), res_for)()
    render_documents(res)
    render_claims(res)


def run(monkeypatch, which: str = "") -> AppTest:
    monkeypatch.setenv("CXD_CASE", which)
    at = AppTest.from_function(_page, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def text(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)


def test_cards_show_relevant_documents_in_plain_words_and_hide_the_rest(monkeypatch):
    at = run(monkeypatch)
    page = text(at)
    assert "Police FIR narrative" in page and "P.S. Sector 14" in page and "30 Aug 2026" in page
    assert "Says the car was recovered, but the police case is still open." in page
    assert "names the FIR FIR00005/2026" in page
    assert "1 other document matched only common words" in page
    for raw in ("FIR_NARRATIVE", "SURVEYOR_REPORT", "INSURERS #", "score "):
        assert raw not in page
    assert page.index("Police FIR narrative") < page.index("Surveyor report")   # contradiction first
    assert {b.key for b in at.button} >= {"cxd_open_card_D00260", "cxd_open_hidden_D00709"}


def test_open_shows_the_reader_with_numbered_records(monkeypatch):
    at = run(monkeypatch)
    at.button(key="cxd_open_card_D00260").click().run()
    assert not at.exception, [e.value for e in at.exception]
    page = text(at)
    assert "records say OPEN" in page                        # the contradicted span, with the database value
    assert "[1]" in page and "Police FIR FIR00005/2026" in page and "reported 30 Aug 2026" in page
    assert "Registration MH12EF0248" in page
    assert "Issued by P.S. Sector 14" in page


def test_claim_matrix_renders_with_takeaway(monkeypatch):
    page = text(run(monkeypatch))
    assert "fact disputed" in page or "facts disputed" in page
    assert "police case status" in page and "✗" in page and "✓" in page


def test_nothing_harmful_without_context_or_with_garbage(monkeypatch):
    assert "Documents are not available" in text(run(monkeypatch, "none"))
    assert "could not be shown" in text(run(monkeypatch, "broken"))


def test_reader_numbers_mentions_by_the_record_they_support():
    res = res_for()
    fir = next(d for d in res["docs"].docs if d.doc_id == "D00260")
    body, records = cd.reader_html(fir, res)
    assert [n for n, _, _ in records] == [1, 2]
    assert records[0][1] == ("THEFT", "CRIME_RECORDS", "5") and "recovered" in records[0][2]
    assert body.count("<mark") >= 3 and "records say OPEN" in body


def test_describe_uses_lookup_rows_and_time_words():
    res = res_for()
    assert cd.describe(("INS", "POLICY_RECORDS", 203), res) == \
        "Insurance policy POL-000203 — National Shield Insurance, valid till 16 Aug 2027"
    assert cd.describe(("REG", "OWNERS", 248), res).startswith("Owner — Eshana Sanyal")
