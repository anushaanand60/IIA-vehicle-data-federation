"""CONTEXT-ADDED (R2: verdict robustness): source knock-out on a hand-made ContextResult, no network."""
from __future__ import annotations

from datetime import datetime

import pytest

from mediator.context_types import ContextResult, ContextRow, ParsedQuery, SourceStats
from mediator.robustness import knock_out, summary_line

PLATE = "MH12EF0248"
NOW = datetime(2026, 6, 1)


def _row(sid: str, table: str, pk: int, row: dict) -> ContextRow:
    return ContextRow(sid, table, pk, row, 0.5, [f"{table}#{pk}"], 0)


def _ctx(with_signs: bool = True) -> ContextResult:
    rows = [_row("REG", "VEHICLE_REGISTRATION", 248, {"registration_id": 248, "registration_no": PLATE})]
    if with_signs:
        rows += [_row("THEFT", "CRIME_RECORDS", 5, {"incident_id": 5, "fir_no": "FIR-5", "stolen_flag": "Y",
                                                   "recovered_flag": "N",
                                                   "reported_date": int(datetime(2026, 3, 1).timestamp())}),
                 _row("PUC", "CERT_TESTS", 455, {"test_id": 455, "test_ts": "2026-03-10T10:00:00"}),
                 _row("INS", "POLICY_RECORDS", 203, {"policy_id": 203, "policy_until": "01/01/2026"}),
                 _row("CAM", "PLATE_CAPTURES", 377, {"capture_id": 377, "plate_id": PLATE, "ocr_confidence": 0.95,
                                                     "captured_at": "2026-04-01T10:00:00"})]
    stats = [SourceStats(s) for s in ("REG", "INS", "THEFT", "CAM", "PUC")]
    return ContextResult(query=ParsedQuery(text=PLATE, plates=[PLATE]), rows=rows, stats=stats)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    import mediator.source_client as sc

    def refuse(*a, **k):
        raise AssertionError("robustness must not contact a source")
    monkeypatch.setattr(sc, "SourceClient", refuse)


def test_each_sign_names_the_smallest_set_of_agencies_it_needs():
    ko = knock_out(PLATE, _ctx(), now=NOW, asked=["REG", "INS", "THEFT", "CAM", "PUC", "DOCS"])
    assert ko["indicators"] == ["ALIBI_CAMERA", "ALIBI_PUC", "LAPSED_COVER_SEEN"]
    assert ko["minimal"] == {"ALIBI_CAMERA": ["CAM", "THEFT"], "ALIBI_PUC": ["PUC", "THEFT"],
                             "LAPSED_COVER_SEEN": ["CAM", "INS"]}


def test_knock_out_lists_what_falls_without_each_agency():
    ko = knock_out(PLATE, _ctx(), now=NOW, asked=["REG", "INS", "THEFT", "CAM", "PUC", "DOCS"])
    assert ko["falls_without"]["THEFT"] == ["ALIBI_CAMERA", "ALIBI_PUC"]
    assert ko["falls_without"]["CAM"] == ["ALIBI_CAMERA", "LAPSED_COVER_SEEN"]
    assert ko["holds_without"] == ["REG", "DOCS"] and ko["depends"] == ["INS", "THEFT", "CAM", "PUC"]
    assert summary_line(ko) == "Holds without REG, DOCS · Depends on INS, THEFT, CAM and PUC"


def test_a_clear_verdict_says_it_uses_every_agency():
    ko = knock_out(PLATE, _ctx(with_signs=False), now=NOW, asked=["REG", "INS", "THEFT", "CAM", "PUC", "DOCS"])
    assert ko["indicators"] == [] and ko["depends"] == []
    assert summary_line(ko) == "Clear verdict uses all 6 agencies; losing any one would make it a lower bound"


def test_no_context_is_an_empty_clear_answer():
    ko = knock_out(PLATE, None, now=NOW)
    assert ko["indicators"] == [] and ko["asked"] == []
