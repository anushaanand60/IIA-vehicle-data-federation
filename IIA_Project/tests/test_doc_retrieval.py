"""CONTEXT-ADDED (R3): context-driven retrieval, EROCS-style linking, conflicts and the grounded summary.

A hand-built ContextResult for DL05CD9876 and a FAKE client that serves DOCS in-process over a temp SQLite file.
No port is bound and no cluster is started.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from fastapi.testclient import TestClient

from mediator.context_types import ContextResult, ContextRow, DocResult, ParsedQuery, RetrievedDoc
from mediator.doc_eval import ndcg_at, reciprocal_rank
from mediator.doc_retrieval import retrieve
from mediator.grounded_summary import summarise, validate
from sources.docs.wrapper import add_search_route
from sources.wrapper_template import create_app
from tests.test_docs_source import make_db

FILLER = [(f"F{i}", "NEWS", f"Traffic drive {i}", f"Traffic police ran a helmet drive at junction {i} in Noida; "
           f"{i * 7} two-wheelers were fined.", "City Times", "2026-02-01") for i in range(1, 7)]
DOCS = [
    ("R1", "FIR_NARRATIVE", "FIR FIR00028/2026 - P.S. Karol Bagh",
     "Brief facts: On 12th July 2026 the complainant Priya Singh reported at P.S. Karol Bagh that her silver Maruti "
     "Suzuki Swift bearing registration no. DLO5CD9B76 was stolen. FIR No. FIR00028/2026 registered. The vehicle was "
     "recovered on 20/07/2026 from an abandoned plot.", "P.S. Karol Bagh", "2026-07-12"),
    ("R2", "NEWS", "Swift stolen from Karol Bagh",
     "A silver Maruti Suzuki Swift belonging to Priya Singh was stolen from near Karol Bagh. Police at Karol Bagh PS "
     "are scanning CCTV footage.", "City Times", "2026-07-13"),
    ("R3", "NEWS", "Swift towed", "A white Maruti Suzuki Swift bearing No. DL05CD9877 owned by Anil Mehta was towed.",
     "Delhi Daily", "2026-03-01"),
    ("R4", "ACCIDENT_REPORT", "Accident report: Ring Road",
     "On 03-09-2026 a red Maruti Suzuki Swift bearing No. DL-05-CD-9876 driven by Priya Singh hit a divider.",
     "Traffic Police", "2026-09-03"),
    ("R5", "SURVEYOR_REPORT", "Pre-inspection report, POL-000562",
     "Policy No. POL-000562 (TrustLine Insurers). Vehicle: DL 05 CD 9876. Cover valid from 01/01/2025 till 30/09/2027.",
     "TrustLine Insurers", "2025-01-01"),
    ("R6", "NEWS", "Maruti sales up", "Maruti Suzuki reported higher sales of the Swift this quarter.", "Biz", "2026-04-01"),
] + FILLER
GRADES = {"R1": 2, "R2": 2, "R4": 2, "R5": 2}


class FakeClient:
    """Same post() as SourceClient; DOCS answered by the wrapper app in-process."""

    def __init__(self, http: Optional[TestClient]) -> None:
        self.http, self.calls = http, []

    def post(self, source_id: str, path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        self.calls.append((source_id, path, payload))
        if self.http is None:
            return None                     # DOCS down
        resp = self.http.post(path, json=payload)
        return resp.json() if resp.status_code == 200 else None

    def query(self, source_id: str, sql: str) -> List[Dict[str, Any]]:
        return []

    def stats(self) -> list:
        return []


@pytest.fixture()
def client(tmp_path: Path) -> FakeClient:
    app = create_app(make_db(tmp_path / "docs.db", DOCS), source_id="DOCS", dbms="SQLite", tables=["DOCUMENTS"],
                     admin_enabled=False)
    add_search_route(app)
    return FakeClient(TestClient(app))


def context(text: str = "DL05CD9876") -> ContextResult:
    rows = [
        ContextRow("REG", "VEHICLE_REGISTRATION", 602, {"registration_id": 602, "registration_no": "DL05CD9876",
                   "owner_id": 602, "make": "Maruti Suzuki", "model": "Swift", "colour": "Silver", "fuel_type": "PETROL",
                   "registered_on": "2021-05-10", "reg_status": "ACTIVE", "rto_code": "DL05"}, 1.0, ["VEHICLE_REGISTRATION#602"], 0),
        ContextRow("REG", "OWNERS", 602, {"owner_id": 602, "full_name": "Priya Singh", "address_line": "45 Mall Road",
                   "city": "Delhi"}, 0.8, ["VEHICLE_REGISTRATION#602", "OWNERS#602"], 1),
        ContextRow("THEFT", "CRIME_RECORDS", 7, {"incident_id": 7, "vehicle_number": "dl 05 cd 9876",
                   "fir_no": "FIR00028/2026", "reported_date": 1783881000, "incident_type": "THEFT", "stolen_flag": "Y",
                   "recovered_flag": "N", "case_status": "OPEN", "police_station": "Karol Bagh PS"}, 0.9,
                   ["CRIME_RECORDS#7"], 0, "plate"),
        ContextRow("INS", "POLICY_RECORDS", 562, {"policy_id": 562, "vehicle_reg": "DL-05-CD-9876", "insurer_id": 4,
                   "policy_type": "THIRD_PARTY", "policy_start": "01/01/2025", "policy_until": "10/06/2026",
                   "is_active": 0}, 0.9, ["POLICY_RECORDS#562"], 0, "plate"),
    ]
    return ContextResult(query=ParsedQuery(text=text, plates=["DL05CD9876"]), rows=rows, stats=[])


def ranked(res: DocResult) -> List[str]:
    return [d.doc_id for d in res.docs]


def doc(res: DocResult, doc_id: str) -> RetrievedDoc:
    return next(d for d in res.docs if d.doc_id == doc_id)


def test_context_beats_query_only(client: FakeClient):
    base = retrieve(context(), k=5, mode="query_only", client=client)
    ctx = retrieve(context(), k=5, mode="context", client=client)
    full = retrieve(context(), k=5, mode="context+anchors", client=client)
    assert "R2" not in ranked(base) and "R1" not in ranked(base)   # no plate in R2, OCR'd plate in R1
    assert "R2" in ranked(ctx)                                       # found through owner name + station
    assert "R1" in ranked(full)                                      # found through the OCR-tolerant anchor
    scores = [ndcg_at(ranked(r), GRADES, 5) for r in (base, ctx, full)]
    assert scores[0] < scores[1] <= scores[2]
    assert all(c[0] == "DOCS" and c[1] == "/search" for c in client.calls)
    assert full.rows_shipped == len(full.docs) <= 5


def test_mention_spans_point_at_the_text(client: FakeClient):
    r1 = doc(retrieve(context(), k=5, client=client), "R1")
    body = dict((d[0], d[3]) for d in DOCS)["R1"]
    for m in r1.mentions:
        assert body[m.start:m.end] == m.text
    plate = next(m for m in r1.mentions if m.attr == "plate")
    assert plate.text == "DLO5CD9B76" and plate.kind == "anchor" and 0.92 <= plate.similarity < 1
    assert {m.attr for m in r1.mentions} >= {"plate", "fir_no", "person", "make", "model", "colour", "date"}
    assert "DLO5CD9B76" in r1.snippet


def test_linking_threshold_and_key_rule(client: FakeClient):
    res = retrieve(context(), k=8, mode="context+anchors", client=client)
    r1, r2 = doc(res, "R1"), doc(res, "R2")
    assert ("THEFT", "CRIME_RECORDS", 7) in r1.linked_rows and ("REG", "VEHICLE_REGISTRATION", 602) in r1.linked_rows
    # R2 names the owner and the station but no identifier: the owner row links, the FIR row (which has a
    # FIR number and a plate in the context) does not
    assert ("REG", "OWNERS", 602) in r2.linked_rows
    assert ("THEFT", "CRIME_RECORDS", 7) not in r2.linked_rows
    for d in res.docs:
        if d.doc_id in ("R3", "R6"):   # one-character-off plate; make/model only: not about this vehicle
            assert d.linked_rows == []


def test_conflicts_detected(client: FakeClient):
    res = retrieve(context(), k=8, client=client)
    attrs = {(d.doc_id, c.attr) for d in res.docs for c in d.conflicts}
    assert ("R1", "recovery") in attrs        # "recovered on 20/07/2026" while the FIR is OPEN
    assert ("R4", "colour") in attrs          # "red ... Swift" vs REG silver
    assert ("R5", "policy_until") in attrs    # "till 30/09/2027" vs policy_until 10/06/2026
    assert not any(c.attr == "incident_date" for c in doc(res, "R1").conflicts)  # 12 July matches the epoch day
    assert all(c.doc_id in ("R1", "R4", "R5") for d in res.docs for c in d.conflicts)


def test_docs_down_degrades_to_empty_result():
    res = retrieve(context(), k=5, client=FakeClient(None))
    assert res.docs == [] and res.rows_shipped == 0


def test_metrics():
    assert ndcg_at(["a", "b"], {"a": 2, "b": 1}, 10) == pytest.approx(1.0)
    assert ndcg_at(["b", "a"], {"a": 2, "b": 1}, 10) < 1.0
    assert reciprocal_rank(["x", "y", "a"], {"a": 1}) == pytest.approx(1 / 3)
    assert reciprocal_rank(["x"], {"a": 1}) == 0.0


# ------------------------------------------------------------------ grounded summary

def test_summary_keeps_only_cited_and_supported_sentences(client: FakeClient):
    ctx, docs = context(), retrieve(context(), k=5, client=client)
    reply = ("[REG:VEHICLE_REGISTRATION#602] DL05CD9876 is a silver Maruti Suzuki Swift.\n"
             "The owner is Priya Singh. [REG:OWNERS#602]\n"
             "The car was sold on 01/02/2026. [REG:VEHICLE_REGISTRATION#602]\n"     # date not in the cited fact
             "The FIR FIR99999/2026 is open. [THEFT:CRIME_RECORDS#7]\n"               # invented FIR number
             "It was seen in Agra. [CAM:PLATE_CAPTURES#1]\n"                         # citation we never gave
             "No citation here at all.\n"
             "[D:R1]\nThe FIR text says the car was recovered on 20/07/2026.")
    out = summarise(ctx, docs, llm=lambda prompt: reply)
    sentences = [s["sentence"] for s in out]
    assert "DL05CD9876 is a silver Maruti Suzuki Swift." in sentences
    assert any(s["citations"] == ["REG:OWNERS#602"] for s in out)
    assert not any("01/02/2026" in s or "FIR99999" in s or "Agra" in s or "No citation" in s for s in sentences)
    assert any(s["citations"] == ["D:R1"] and "20/07/2026" in s["sentence"] for s in out)
    assert all(s["citations"] for s in out) and len(out) <= 5


def test_summary_falls_back_to_templates(client: FakeClient):
    ctx, docs = context(), retrieve(context(), k=5, client=client)
    for llm in (lambda p: None, lambda p: "nonsense without citations", lambda p: 1 / 0):
        out = summarise(ctx, docs, llm=llm)
        assert out and all(s["citations"] for s in out)
        assert any("contradicts" in s["sentence"] for s in out)    # the flagged conflict always surfaces
    assert summarise(ctx, docs, use_llm=False) == summarise(ctx, docs, llm=lambda p: None)
    assert summarise(ContextResult(ParsedQuery("x"), [], []), DocResult([], "context"), use_llm=False) == []


def test_validate_reworded_dates():
    known = {"PUC:POLLUTION_CERT#X": "PUC POLLUTION_CERT: valid_upto=2027-02-28, tested_at=Noida"}
    assert validate("Valid until 28th February 2027. [PUC:POLLUTION_CERT#X]", known)
    assert validate("Valid until 27th February 2027. [PUC:POLLUTION_CERT#X]", known) is None
