"""CONTEXT-ADDED (R2): cross-source indicators and the fleet leaderboard, over fake SQLite sources."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

import pytest

from mediator import analytics as A
from mediator import evidence as D
from mediator import topk_sql as S
from mediator.context_types import ContextResult, ContextRow, ParsedQuery
from tests.phase3_fakes import FakeClient, use_frozen_registry

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 11, 12, 0)
A_PLATE, NEIGHBOUR, LAPSED = "HR26AB1001", "HR26AB1002", "DL05CD9876"
RING = "9811111111"


def epoch(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=S.IST).timestamp())


def planted() -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    return {
        "REG": {
            "OWNERS": [{"owner_id": 1, "full_name": "Ravi Kumar", "city": "Gurugram"},
                       {"owner_id": 2, "full_name": "Asha Rao", "city": "Delhi"}, {"owner_id": 3, "full_name": "Old Owner", "city": "Delhi"}],
            "VEHICLE_REGISTRATION": [
                {"registration_id": 1, "registration_no": A_PLATE, "owner_id": 1, "make": "Hyundai", "model": "Creta", "colour": "White",
                 "registered_on": "2023-01-01", "reg_status": "ACTIVE"},
                {"registration_id": 2, "registration_no": NEIGHBOUR, "owner_id": 2, "make": "Tata", "model": "Nexon", "colour": "Red",
                 "registered_on": "2023-01-01", "reg_status": "ACTIVE"},
                {"registration_id": 3, "registration_no": LAPSED, "owner_id": 2, "make": "Kia", "model": "Seltos", "colour": "Grey",
                 "registered_on": "2022-01-01", "reg_status": "ACTIVE"}],
            "OWNERSHIP_HISTORY": [{"transfer_id": 1, "registration_id": 1, "prev_owner_id": 3, "new_owner_id": 1,
                                   "transfer_date": "2026-08-15", "transfer_type": "SALE"}],
            "OWNER_CONTACTS": [{"owner_id": 1, "mobile": RING, "pincode": "122001"}, {"owner_id": 2, "mobile": "9822222222"}],
        },
        "INS": {
            "POLICY_RECORDS": [
                {"policy_id": 1, "vehicle_reg": "HR-26-AB-1001", "policy_type": "COMPREHENSIVE", "policy_start": "20/08/2026",
                 "policy_until": "19/08/2027", "is_active": 1},
                {"policy_id": 2, "vehicle_reg": "DL-05-CD-9876", "policy_type": "THIRD_PARTY", "policy_start": "13/07/2025",
                 "policy_until": "12/07/2026", "is_active": 0},
                {"policy_id": 3, "vehicle_reg": "UP-16-GH-1122", "policy_type": "THIRD_PARTY", "policy_start": "01/01/2026",
                 "policy_until": "31/12/2026", "is_active": 1}],
            "CLAIMS": [{"claim_id": 1, "policy_id": 1, "loss_date": "29/08/2026", "intimation_date": "30/08/2026",
                        "loss_type": "THEFT", "amount_claimed": 900000, "claim_status": "INVESTIGATING"}],
            "POLICY_HOLDERS": [{"holder_id": 7, "holder_name": "Rvi Kumaar", "mobile": RING}],
            "POLICY_PARTIES": [{"policy_id": 3, "holder_id": 7}],
        },
        "THEFT": {
            "CRIME_RECORDS": [
                {"incident_id": 1, "vehicle_number": "hr 26 ab 1001", "fir_no": "FIR00001/2026", "reported_date": epoch(2026, 9, 1),
                 "incident_type": "THEFT", "stolen_flag": "Y", "recovered_flag": "N", "case_status": "OPEN", "police_station": "Sector 14 PS"},
                {"incident_id": 2, "vehicle_number": "mh 12 ij 7788", "fir_no": "FIR00002/2026", "reported_date": epoch(2026, 6, 1),
                 "incident_type": "THEFT", "stolen_flag": "Y", "recovered_flag": "Y", "case_status": "CLOSED", "police_station": "Sector 14 PS"}],
            "STATIONS": [{"station_name": "Sector 14 PS", "district": "Gurugram", "zone": "South"}],
            "STOLEN_PROPERTY": [{"incident_id": 2, "recovered_on": epoch(2026, 6, 20)}],
            "PERSONS": [{"person_id": 5, "name": "R. Kumar", "mobile": RING}],
            "INCIDENT_PERSONS": [{"incident_id": 2, "person_id": 5, "role": "ACCUSED"}],
        },
        "CAM": {
            "CAMERAS": [{"camera_id": "C1", "location_name": "NH8 Toll Plaza"}],
            "PLATE_CAPTURES": [
                {"capture_id": 1, "plate_id": "HR26AB1OO1", "camera_id": "C1", "captured_at": "2026-09-12T08:00:00", "ocr_confidence": 0.9},
                {"capture_id": 2, "plate_id": NEIGHBOUR, "camera_id": "C1", "captured_at": "2026-09-13T08:00:00", "ocr_confidence": 0.95},
                {"capture_id": 3, "plate_id": LAPSED, "camera_id": "C1", "captured_at": "2026-08-01T09:30:00", "ocr_confidence": 0.97}],
            "CHALLANS": [{"challan_id": 1, "capture_id": 1, "violation": "SPEEDING", "fine_inr": 2000, "challan_status": "PENDING",
                          "issued_at": "2026-09-12T08:01:00"}],
        },
        "PUC": {
            "POLLUTION_CERT": [{"cert_no": "PUC-1", "regn_number": A_PLATE, "valid_upto": "2027-03-10", "tested_at": "Gurugram Auto Hub"}],
            "TEST_CENTRES": [{"centre_id": 1, "centre_name": "Gurugram Auto Hub", "city": "Gurugram"}],
            "ANALYSERS": [{"analyser_id": 1, "centre_id": 1, "calibrated_on": "2026-01-01"}],
            "CERT_TESTS": [{"test_id": 11, "cert_no": "PUC-1", "analyser_id": 1, "test_ts": "2026-09-10T11:00:00", "result": "PASS"}],
        },
    }


@pytest.fixture(autouse=True)
def _registry(monkeypatch: pytest.MonkeyPatch) -> None:
    use_frozen_registry(monkeypatch)


def codes(found: List[Any]) -> set:
    return {i.code for i in found}


def test_planted_vehicle_raises_every_indicator() -> None:
    found = A.indicators_for(A_PLATE, client=FakeClient(planted()), now=NOW)
    assert codes(found) == {"ALIBI_PUC", "ALIBI_CAMERA", "ALIBI_CHALLAN", "QUICK_CLAIM", "TRANSFER_BEFORE_LOSS", "PERSON_RING"}
    by = {i.code: i for i in found}
    assert "10 Sep 2026" in by["ALIBI_PUC"].explanation and "FIR00001/2026" in by["ALIBI_PUC"].explanation
    assert ("PUC", "CERT_TESTS", 11) in by["ALIBI_PUC"].evidence
    assert by["QUICK_CLAIM"].severity == "high" and "9 day(s)" in by["QUICK_CLAIM"].explanation
    assert "14 day(s) before the claimed loss" in by["TRANSFER_BEFORE_LOSS"].explanation
    assert "3 vehicles" in by["PERSON_RING"].explanation
    assert found[0].severity == "high"


def test_neighbour_plate_is_not_an_alibi() -> None:
    # one character apart and seen inside A's stolen window: must not be counted for A, nor raise anything itself
    assert ("CAM", "PLATE_CAPTURES", 2) not in {e for i in A.indicators_for(A_PLATE, client=FakeClient(planted()), now=NOW)
                                                 for e in i.evidence}
    assert A.indicators_for(NEIGHBOUR, client=FakeClient(planted()), now=NOW) == []


def test_lapsed_cover_seen() -> None:
    found = A.indicators_for("dl-05 cd 9876", client=FakeClient(planted()), now=NOW)
    assert codes(found) == {"LAPSED_COVER_SEEN"}
    assert "12 Jul 2026" in found[0].explanation


def test_recovered_vehicle_interval_ends_at_recovery() -> None:
    rows = D.gather("MH12IJ7788", FakeClient(planted()))
    (start, end, _, how), = A.stolen_intervals(rows, NOW)
    assert end.date().isoformat() == "2026-06-20" and how.startswith("recovered")


def test_context_rows_are_used_without_traffic() -> None:
    client = FakeClient(planted())
    rows = D.gather(A_PLATE, client)
    ctx = ContextResult(query=ParsedQuery(text=A_PLATE), stats=[], rows=[
        ContextRow(source_id=sid, table=t, pk=D.pk_of(sid, t, r), row=r, score=1.0, path=[t], hops=0)
        for (sid, t), rs in rows.items() for r in rs])
    before = len(client.calls)
    found = A.indicators_for(A_PLATE, context=ctx, now=NOW)
    assert len(client.calls) == before
    assert {"ALIBI_PUC", "ALIBI_CAMERA", "QUICK_CLAIM", "TRANSFER_BEFORE_LOSS"} <= codes(found)


def test_dates_parsed_in_each_agency_format() -> None:
    assert D.when("12/07/2026", "INS", "POLICY_RECORDS", "policy_until") == datetime(2026, 7, 12)
    assert D.when("30/08/2026", "INS", "CLAIMS", "intimation_date") == datetime(2026, 8, 30)   # undeclared sibling column
    assert D.when(epoch(2026, 9, 1), "THEFT", "CRIME_RECORDS", "reported_date") == datetime(2026, 9, 1)
    assert D.when("2026-09-10T11:00:00", "PUC", "CERT_TESTS", "test_ts") == datetime(2026, 9, 10, 11)
    assert D.when(None, "PUC", "CERT_TESTS", "test_ts") is None


@pytest.mark.parametrize("method", ["ta", "tput"])
def test_leaderboard_ranks_planted_vehicle_first(method: str) -> None:
    res = A.leaderboard_result(k=3, client=FakeClient(planted()), method=method)
    assert res.vehicles[0].plate == A_PLATE
    assert "PUC test while reported stolen" in res.vehicles[0].explanation
    assert res.baseline_rows > 0 and getattr(res, "round_trips") > 0


def test_leaderboard_methods_agree() -> None:
    ta = [(v.plate, v.score) for v in A.leaderboard(k=4, client=FakeClient(planted()), method="ta")]
    tput = [(v.plate, v.score) for v in A.leaderboard(k=4, client=FakeClient(planted()), method="tput")]
    assert [s for _, s in ta] == [s for _, s in tput]


class LocalFileClient(FakeClient):
    """Read-only SQL on the generated local source files (sources/<id>/<id>.db), through the real guard."""

    def __init__(self) -> None:  # noqa: D107 - no in-memory build: open the real files read-only
        self.dbs, self.down, self.calls, self._stats = {}, set(), [], {}
        for sid in ("REG", "INS", "THEFT", "CAM", "PUC"):
            path = ROOT / "sources" / sid.lower() / f"{sid.lower()}.db"
            if path.exists():
                con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
                con.row_factory = sqlite3.Row
                self.dbs[sid] = con


def test_planted_scenarios_from_generator() -> None:
    path = ROOT / "data" / "phase3_scenarios.json"
    if not path.exists():
        pytest.skip("data/phase3_scenarios.json not generated yet (workstream A)")
    client = LocalFileClient()
    for sid, table in (("PUC", "CERT_TESTS"), ("THEFT", "STOLEN_PROPERTY"), ("INS", "CLAIMS"), ("REG", "OWNERSHIP_HISTORY")):
        if sid not in client.dbs or not client.dbs[sid].execute(
                "SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone():
            pytest.skip(f"local {sid} database has no {table} yet")
    mine = {"ALIBI_PUC", "ALIBI_CAMERA", "ALIBI_CHALLAN", "QUICK_CLAIM", "TRANSFER_BEFORE_LOSS", "LAPSED_COVER_SEEN", "PERSON_RING"}
    misses = []
    for sc in json.loads(path.read_text(encoding="utf-8")):
        expected = set(sc.get("expected_indicators", [])) & mine
        found = {p: A.indicators_for(p, client=client) for p in sc.get("plates", [])}
        got = set().union(*[codes(f) for f in found.values()] or [set()])
        if str(sc.get("id", "")).startswith("HN-TRANSFER") and "TRANSFER_BEFORE_LOSS" in got:
            misses.append((sc["id"], "TRANSFER_BEFORE_LOSS fired on a hard negative"))
        if str(sc.get("id", "")).startswith("HN-PLATE") and sc.get("plates"):
            firs = {tuple(e) for e in sc.get("evidence", []) if e[0] == "THEFT"}
            cited = {(e[0], e[1], e[2]) for i in found[sc["plates"][0]] for e in i.evidence}
            if firs & cited:
                misses.append((sc["id"], "near-miss plate picked up the other vehicle's FIR"))
        if not expected <= got:
            misses.append((sc.get("id"), sorted(expected - got)))
    assert not misses, f"planted scenarios without their indicator: {misses}"


def test_indicator_query_is_refined_not_tie_broken() -> None:
    """'stolen cars tested for PUC after theft': a decoy (stolen, PUC test BEFORE the theft) sorts first by plate,
    so only the lazy cross-source check can put the real alibi vehicle on top."""
    from mediator.entity_topk import topk
    from mediator.query_understanding import parse
    data = planted()
    data["THEFT"]["CRIME_RECORDS"].append({"incident_id": 3, "vehicle_number": "dl 01 aa 0001", "fir_no": "FIR00003/2026",
                                           "reported_date": epoch(2026, 9, 5), "incident_type": "THEFT", "stolen_flag": "Y",
                                           "recovered_flag": "N", "case_status": "OPEN", "police_station": "Sector 14 PS"})
    data["PUC"]["POLLUTION_CERT"].append({"cert_no": "PUC-2", "regn_number": "DL01AA0001", "valid_upto": "2027-01-01"})
    data["PUC"]["CERT_TESTS"].append({"test_id": 12, "cert_no": "PUC-2", "analyser_id": 1, "test_ts": "2026-08-01T10:00:00",
                                      "result": "PASS"})
    res = topk(parse("stolen cars tested for PUC after theft", use_llm=False), k=2, client=FakeClient(data))
    assert [v.plate for v in res.vehicles] == [A_PLATE, "DL01AA0001"]
    assert res.vehicles[0].score > res.vehicles[1].score
    assert any(line.startswith("verified ALIBI_PUC") for line in res.vehicles[0].explanation)
    assert any("not confirmed" in line for line in res.vehicles[1].explanation)
    assert getattr(res, "method") == "ta+refine"
