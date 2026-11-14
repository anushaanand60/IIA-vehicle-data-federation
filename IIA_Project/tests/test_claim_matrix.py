"""CONTEXT-ADDED (R3): the claim matrix - who states which fact, the trust-weighted majority, agree / conflict / silent.

Hand-built context and documents shaped like the live MH12EF0248 (recovered vs OPEN) and UP16GH1122 (clone colour)
cases; pure functions, no network. `case()` is shared with tests/test_context_docs.py.
"""
from __future__ import annotations

from typing import List, Tuple

from mediator import claim_matrix as cm
from mediator.context_types import (Conflict, ContextResult, ContextRow, DocResult, Mention, ParsedQuery,
                                    RetrievedDoc)

PLATE = "MH12EF0248"
FIR_BODY = ("Brief facts: On 29/08/2026 the complainant reported at P.S. Sector 14 that the white Hyundai Creta "
            "bearing registration no. MH12EF0248 was stolen. FIR No. FIR00005/2026 registered. The vehicle was "
            "recovered on 22nd September 2026 from an abandoned plot.")
NEWS_BODY = "A silver Hyundai Venue was seen near the toll plaza; the camera read MH12EF0248."
SURVEY_BODY = "Policy No. POL-000203 (National Shield Insurance). Cover valid till 30/09/2027 for MH12EF0248."
OTHER_BODY = "A Blue Hyundai Creta insured with National Shield Insurance was towed from Sector 14."


def _m(body: str, text: str, attr: str, kind: str = "descriptor", value: str = "") -> Mention:
    start = body.index(text)
    return Mention(text, kind, attr, start, start + len(text), value or text, 1.0, 0.5)


def context(colour_cam: str = "White") -> ContextResult:
    rows = [
        ContextRow("THEFT", "CRIME_RECORDS", 5, {"incident_id": 5, "vehicle_number": "mh 12 ef 0248",
                   "fir_no": "FIR00005/2026", "reported_date": 1788028200, "case_status": "OPEN",
                   "police_station": "Sector 14 PS"}, 1.0, ["CRIME_RECORDS#5"], 0),
        ContextRow("REG", "VEHICLE_REGISTRATION", 248, {"registration_no": PLATE, "make": "Hyundai", "model": "Creta",
                   "colour": "White", "registered_on": "2024-06-14"}, 0.9, ["VEHICLE_REGISTRATION#248"], 0),
        ContextRow("INS", "POLICY_RECORDS", 203, {"policy_id": 203, "vehicle_reg": "MH-12-EF-0248", "insurer_id": 1,
                   "policy_until": "16/08/2027"}, 0.8, ["POLICY_RECORDS#203"], 0),
        ContextRow("INS", "INSURERS", 1, {"insurer_id": 1, "insurer_name": "National Shield Insurance"}, 0.3,
                   ["POLICY_RECORDS#203", "INSURERS#1"], 1),
        ContextRow("CAM", "PLATE_CAPTURES", 377, {"plate_id": PLATE, "observed_make": "Hyundai",
                   "observed_model": "Venue" if colour_cam != "White" else "Creta", "observed_colour": colour_cam,
                   "captured_at": "2026-07-13T06:00:00"}, 0.3, ["PLATE_CAPTURES#377"], 0),
        ContextRow("REG", "OWNERS", 248, {"owner_id": 248, "full_name": "Eshana Sanyal", "city": "Gurugram"}, 0.7,
                   ["VEHICLE_REGISTRATION#248", "OWNERS#248"], 1),
    ]
    return ContextResult(query=ParsedQuery(text=PLATE, plates=[PLATE]), rows=rows, stats=[])


def documents() -> List[RetrievedDoc]:
    fir = RetrievedDoc(
        "D00260", "FIR_NARRATIVE", "FIR FIR00005/2026 - P.S. Sector 14", 39.6, FIR_BODY[:120],
        mentions=[_m(FIR_BODY, "29/08/2026", "date", value="2026-08-30"), _m(FIR_BODY, "white", "colour", value="White"),
                  _m(FIR_BODY, "Hyundai", "make"), _m(FIR_BODY, "Creta", "model"),
                  _m(FIR_BODY, "MH12EF0248", "plate", "anchor"), _m(FIR_BODY, "FIR00005/2026", "fir_no", "anchor")],
        linked_rows=[("THEFT", "CRIME_RECORDS", 5), ("REG", "VEHICLE_REGISTRATION", 248)],
        conflicts=[Conflict("recovery", "OPEN", "THEFT", "recovered on 22nd September 2026", "D00260",
                            (FIR_BODY.index("recovered"), FIR_BODY.index("recovered") + 9),
                            "text says the vehicle was recovered; FIR 5 is OPEN")],
        body=FIR_BODY, issuer="P.S. Sector 14", issued_on="2026-08-30",
        why=["names the FIR FIR00005/2026", "names this plate"], relevant=True)
    news = RetrievedDoc(
        "D01009", "NEWS", "Venue seen at the toll plaza", 20.0, NEWS_BODY,
        mentions=[_m(NEWS_BODY, "MH12EF0248", "plate", "anchor"), _m(NEWS_BODY, "Hyundai", "make")],
        linked_rows=[("REG", "VEHICLE_REGISTRATION", 248)],
        conflicts=[Conflict("colour", "White", "REG", "Silver", "D01009", (2, 8), "text: 'silver Hyundai Venue'"),
                   Conflict("model", "Creta", "REG", "Venue", "D01009", (17, 22), "text: 'silver Hyundai Venue'")],
        body=NEWS_BODY, issuer="City Times", issued_on="2026-09-10", why=["names this plate"], relevant=True)
    survey = RetrievedDoc(
        "D00845", "SURVEYOR_REPORT", "Pre-inspection report, POL-000203", 30.0, SURVEY_BODY,
        mentions=[_m(SURVEY_BODY, "POL-000203", "policy_no", "anchor"), _m(SURVEY_BODY, "MH12EF0248", "plate", "anchor")],
        linked_rows=[("INS", "POLICY_RECORDS", 203), ("INS", "INSURERS", 1)],
        body=SURVEY_BODY, issuer="National Shield Insurance", issued_on="2026-01-12",
        why=["names the policy POL-000203"], relevant=True)
    other = RetrievedDoc(
        "D00709", "NEWS", "Blue Hyundai Creta towed", 1.3, OTHER_BODY,
        mentions=[_m(OTHER_BODY, "Hyundai", "make"), _m(OTHER_BODY, "Creta", "model")],
        linked_rows=[("INS", "INSURERS", 1)], body=OTHER_BODY, issuer="Delhi Daily", issued_on="2026-09-01",
        why=["names the insurer National Shield Insurance", "same make and model (Hyundai Creta)"], relevant=False)
    return [fir, news, survey, other]


def case(colour_cam: str = "White") -> Tuple[ContextResult, DocResult]:
    return context(colour_cam), DocResult(docs=documents(), mode="context+anchors")


def row(matrix: cm.ClaimMatrix, fact: str) -> cm.FactRow:
    return next(r for r in matrix.rows if r.fact == fact)


def test_recovered_text_contradicts_the_open_fir():
    m = cm.build(*case())
    status = row(m, "case_status")
    assert status.majority == "open"                         # police 0.90 outweighs one document at 0.50
    assert status.cells["THEFT"].status == "agree" and status.cells["D00260"].status == "conflict"
    assert status.cells["D00260"].value == "recovered" and status.disputed
    assert "police case status (police record says open; one police FIR narrative says recovered)" in cm.takeaway(m)


def test_camera_and_text_outvote_the_registration_on_colour():
    ctx, docs = case(colour_cam="Silver")
    m = cm.build(ctx, docs)                                  # the FIR text sides with the registration: white wins
    assert row(m, "colour").majority == "white" and row(m, "colour").cells["CAM"].status == "conflict"
    assert ("colour (registration and one police FIR narrative say white; camera and one news item say silver)"
            in cm.takeaway(m))
    docs.docs = [d for d in docs.docs if d.doc_id != "D00260"]
    colour = row(cm.build(ctx, docs), "colour")
    assert colour.majority == "silver"                       # camera 0.60 + news 0.50 > registration 0.95
    assert colour.cells["REG"].status == "conflict" and colour.cells["CAM"].status == "agree"
    assert colour.cells["D01009"].status == "agree" and colour.cells["D00845"].status == "silent"


def test_irrelevant_documents_get_no_column_and_silent_agencies_none():
    m = cm.build(*case())
    keys = [c.key for c in m.columns]
    assert "D00709" not in keys                              # relevant=False: matched common words only
    assert keys[:4] == ["REG", "INS", "THEFT", "CAM"] and "PUC" not in keys
    assert {c.kind for c in m.columns if c.key.startswith("D")} == {"document"}


def test_dates_compare_only_with_the_record_the_document_is_linked_to():
    m = cm.build(*case())
    policy = row(m, "policy_until")
    assert policy.cells["INS"].value == "16/08/2027" and policy.cells["INS"].norm == "2027-08-16"
    assert policy.cells["D00845"].norm == "2027-09-30" and policy.cells["D00845"].status == "conflict"
    theft = row(m, "incident_date")                          # 29 Aug (text) vs 30 Aug (epoch): within 3 days
    assert theft.cells["THEFT"].status == theft.cells["D00260"].status == "agree"
    assert cm.shown("incident_date", theft.cells["THEFT"]) == "30 Aug 2026"
    assert row(m, "recovered_on").cells["D00260"].norm == "2026-09-22"   # no agency row: a relevant doc may state it


def test_owner_comes_from_owner_tables_only():
    m = cm.build(*case())
    assert row(m, "owner").cells["REG"].value == "Eshana Sanyal"
    assert all(r.cells.get("THEFT", cm.Cell()).norm is None for r in m.rows if r.fact == "owner")


def test_normalisation_rules():
    assert cm.norm("colour", "Grey") == cm.norm("colour", "gray")
    assert cm.norm("make", "Maruti Suzuki") == cm.norm("make", "MARUTI")
    assert cm.norm("model", "XUV 700") == cm.norm("model", "xuv700")
    assert cm.same("loss_date", "2026-08-30", "2026-08-31") and not cm.same("loss_date", "2026-08-30", "2026-09-02")
    assert cm.norm("owner", "  ") is None


def test_no_context_is_an_empty_matrix():
    m = cm.build(None, None)
    assert m.rows == [] and m.columns == [] and cm.takeaway(m) == ""
    ctx, _ = case()
    only_agencies = cm.build(ctx, None)
    assert all(c.kind == "agency" for c in only_agencies.columns)
    assert cm.takeaway(only_agencies).startswith("All ")
