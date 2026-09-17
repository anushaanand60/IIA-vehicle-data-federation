"""Challan Guard: case store, identity resolution wiring, and the live verify/dispute workflow.

The store tests run against a throwaway meta.db (monkeypatched `catalog.META_DB_PATH`) so a pytest
run never files demo challans into the real mediator registry. The workflow tests use the
session-scoped `local_cluster` fixture from `tests/conftest.py`, which already redirects
`catalog.META_DB_PATH` at a tmp copy of meta.db pinned to 127.0.0.1.
"""
from __future__ import annotations

import pytest

from mediator import catalog


# =============================================================== B1: the case store ============

@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "META_DB_PATH", str(tmp_path / "meta.db"))
    catalog.init_meta_db()
    return catalog


CASE = {
    "plate_read": "DL05CD9B76",
    "camera_id": "CAM004",
    "location": "Sector 29 Crossing",
    "lat": 28.4601,
    "lon": 77.0648,
    "captured_at": "2026-09-04T11:00:00",
    "observed_make": "Maruti Suzuki",
    "observed_colour": "Silver",
    "ocr_confidence": 0.71,
    "status": "CANDIDATE",
}


class TestStore:
    def test_insert_then_get_round_trips_every_field(self, store):
        case_id = store.challan_insert(CASE)
        assert case_id > 0
        row = store.challan_get(case_id)
        assert row is not None
        assert row["plate_read"] == "DL05CD9B76"
        assert row["status"] == "CANDIDATE"
        assert row["lat"] == pytest.approx(28.4601)
        assert row["created_at"] and row["updated_at"]
        assert row["evidence"] == {}  # JSON decoded for the caller, never raw text

    def test_get_unknown_case_is_none_not_an_error(self, store):
        assert store.challan_get(9999) is None

    def test_list_filters_by_status(self, store):
        a = store.challan_insert(CASE)
        store.challan_insert({**CASE, "plate_read": "HR26EF4455", "status": "ISSUED"})
        assert [c["case_id"] for c in store.challan_list(status="CANDIDATE")] == [a]
        assert len(store.challan_list()) == 2

    def test_update_writes_fields_and_moves_updated_at(self, store):
        case_id = store.challan_insert(CASE)
        before = store.challan_get(case_id)["updated_at"]
        store.challan_update(case_id, status="ISSUED", verdict="ISSUE", amount_inr=2000,
                             evidence={"profile": {"plate_number": "DL05CD9876"}})
        row = store.challan_get(case_id)
        assert (row["status"], row["verdict"], row["amount_inr"]) == ("ISSUED", "ISSUE", 2000)
        assert row["evidence"]["profile"]["plate_number"] == "DL05CD9876"
        assert row["updated_at"] >= before

    def test_update_rejects_an_unknown_column(self, store):
        case_id = store.challan_insert(CASE)
        with pytest.raises(ValueError):
            store.challan_update(case_id, plate_resolved_typo="x")

    def test_events_come_back_in_the_order_they_happened(self, store):
        case_id = store.challan_insert(CASE)
        store.challan_event(case_id, "CREATED", "anpr")
        store.challan_event(case_id, "VERIFIED", "operator", {"verdict": "ISSUE"})
        events = store.challan_events(case_id)
        assert [e["event"] for e in events] == ["CREATED", "VERIFIED"]
        assert events[1]["evidence"] == {"verdict": "ISSUE"}
        assert events[0]["actor"] == "anpr"

    def test_prior_issued_counts_only_issued_and_upheld_for_that_plate(self, store):
        store.challan_insert({**CASE, "plate_resolved": "DL05CD9876", "status": "ISSUED"})
        store.challan_insert({**CASE, "plate_resolved": "DL05CD9876", "status": "UPHELD"})
        store.challan_insert({**CASE, "plate_resolved": "DL05CD9876", "status": "REJECTED"})
        store.challan_insert({**CASE, "plate_resolved": "HR26EF4455", "status": "ISSUED"})
        assert store.challan_prior_issued("DL05CD9876") == 2
        assert store.challan_prior_issued("HR26EF4455") == 1
        assert store.challan_prior_issued("UP16GH1122") == 0

    def test_prior_issued_can_exclude_the_case_being_verified(self, store):
        first = store.challan_insert({**CASE, "plate_resolved": "DL05CD9876", "status": "ISSUED"})
        assert store.challan_prior_issued("DL05CD9876", exclude_case_id=first) == 0

    def test_prior_issued_matches_the_read_when_nothing_was_resolved(self, store):
        store.challan_insert({**CASE, "status": "ISSUED"})  # plate_resolved stays NULL
        assert store.challan_prior_issued("DL05CD9B76") == 1


# =============================================================== B5: demo fixtures =============

def test_seed_is_idempotent(store):
    from scripts.seed_challan_cases import load_candidates, seed

    expected = len(load_candidates())
    assert expected >= 5
    assert seed() == expected
    assert seed() == 0  # a second run tops up nothing
    assert len(store.challan_list()) == expected


def test_seeded_candidates_carry_camera_coordinates(store):
    from scripts.seed_challan_cases import seed

    seed()
    for case in store.challan_list():
        assert case["lat"] is not None and case["lon"] is not None
        assert case["status"] == "CANDIDATE" and case["verdict"] is None


def test_reset_empties_the_queue(store):
    from scripts.seed_challan_cases import reset, seed

    seed()
    assert reset() > 0
    assert store.challan_list() == []


STORY_PLATES = ["DL05CD9B76", "DLO1AB1234", "UP16GH1122", "HR26EF4455", "DL01AB0002"]


def test_story_candidates_are_cases_1_to_5_in_order_after_a_reset(store):
    """The demo script says "case 5 = DL01AB0002"; that must hold after any number of resets."""
    from scripts.seed_challan_cases import reset, seed

    for _ in range(2):  # the second pass proves reset() also restarts the case_id sequence
        reset()
        seed()
        cases = store.challan_list()
        assert [(c["case_id"], c["plate_read"]) for c in cases[:5]] == list(enumerate(STORY_PLATES, 1))
        assert len(cases) >= 12


def test_the_extra_candidates_are_real_captures_of_registered_vehicles(store):
    """Beyond the five stories, every candidate is a row of the camera source, joined to its camera,
    of a vehicle the registration authority knows - so the same plates exist on every laptop."""
    import sqlite3
    from pathlib import Path

    from mediator.transforms import norm_plate
    from scripts.seed_challan_cases import load_candidates

    root = Path(__file__).resolve().parents[1]
    extras = load_candidates()[5:]
    assert len(extras) >= 6
    with sqlite3.connect(root / "sources" / "cam" / "cam.db") as cam,             sqlite3.connect(root / "sources" / "reg" / "reg.db") as reg:
        registered = {norm_plate(r[0]) for r in reg.execute(
            "SELECT registration_no FROM VEHICLE_REGISTRATION")}
        for c in extras:
            row = cam.execute(
                "SELECT 1 FROM PLATE_CAPTURES p JOIN CAMERAS k ON k.camera_id = p.camera_id "
                "WHERE p.plate_id = ? AND p.camera_id = ? AND k.location_name = ? AND k.lat = ? "
                "AND k.lon = ? AND p.captured_at = ? AND p.observed_make = ? "
                "AND p.observed_colour = ? AND p.ocr_confidence = ?",
                tuple(c[f] for f in ("plate_read", "camera_id", "location", "lat", "lon",
                                     "captured_at", "observed_make", "observed_colour",
                                     "ocr_confidence"))).fetchone()
            assert row, f"not a real capture: {c}"
            assert norm_plate(c["plate_read"]) in registered, c
            assert norm_plate(c["plate_read"]) not in {norm_plate(p) for p in STORY_PLATES}
            assert c.get("story"), c


# =============================================================== B4: the live workflow =========
# These run against the session-scoped local cluster. `catalog.META_DB_PATH` already points at a
# tmp copy of meta.db (see tests/conftest.py), so cases and events never touch the real registry.

INS_BASE = "http://127.0.0.1:8002"
CAPTURED = "2026-09-04T11:00:00"
CAM004 = {"camera_id": "CAM004", "location": "Sector 29 Crossing", "lat": 28.4601, "lon": 77.0648}
CAM006 = {"camera_id": "CAM006", "location": "Yamuna Expressway Toll, Agra",
          "lat": 27.1767, "lon": 78.0081}
CAM003 = {"camera_id": "CAM003", "location": "Cyber City Entrance", "lat": 28.4949, "lon": 77.087}
CAM001 = {"camera_id": "CAM001", "location": "NH8 Toll Plaza", "lat": 28.4595, "lon": 77.0266}


@pytest.fixture(scope="module")
def guard(local_cluster):
    """The live cluster plus the validated mappings (camera_lat/camera_lon included) in the
    session's tmp meta.db, so `sightings_for` can reach camera coordinates through the registry."""
    from scripts.seed_challan_cases import reset
    from scripts.seed_mappings import seed as seed_mappings

    seed_mappings()
    reset()  # the tmp meta.db is a copy of the developer's: start this module from an empty queue
    from mediator import challan_guard
    return challan_guard


def candidate(guard_mod, plate_read: str, camera: dict, captured_at: str,
              make: str | None, colour: str | None, confidence: float = 0.9) -> int:
    return guard_mod.new_candidate(plate_read=plate_read, camera_id=camera["camera_id"],
                                   location=camera["location"], lat=camera["lat"],
                                   lon=camera["lon"], captured_at=captured_at,
                                   observed_make=make, observed_colour=colour,
                                   ocr_confidence=confidence)


def step_titles(case: dict) -> list[str]:
    return [s["title"] for s in case["steps"]]


def test_a_misread_plate_is_corrected_and_the_right_vehicle_is_fined(guard):
    case_id = candidate(guard, "DL05CD9B76", CAM004, CAPTURED, "Maruti Suzuki", "Silver", 0.71)
    case = guard.verify(case_id)
    assert case["verdict"] == guard.ISSUE, case["reason"]
    assert case["status"] == "ISSUED"
    assert case["plate_resolved"] == "DL05CD9876"
    assert case["amount_inr"] == guard.FINE_FIRST_INR
    assert "Identity" in step_titles(case)
    assert any("misread corrected" in s["detail"] for s in case["steps"]), case["steps"]
    assert [e["event"] for e in guard.events(case_id)][:2] == ["CREATED", "VERIFY_STARTED"]


def test_a_misread_of_an_insured_vehicle_never_becomes_a_fine(guard):
    case_id = candidate(guard, "DLO1AB1234", {"camera_id": "CAM002",
                                              "location": "Ring Road Junction",
                                              "lat": 28.6139, "lon": 77.209},
                        "2026-09-04T08:30:00", "Hyundai", "White", 0.76)
    case = guard.verify(case_id)
    assert case["verdict"] == guard.REJECT, case["reason"]
    assert case["status"] == "REJECTED"
    assert case["plate_resolved"] == "DL01AB1234"
    assert "insured" in case["reason"].lower()
    assert case["amount_inr"] in (None, 0)


def test_a_stolen_vehicle_goes_to_the_police_not_to_the_owner(guard):
    case_id = candidate(guard, "HR26EF4455", CAM003, "2026-09-02T19:45:00", "Mahindra", "Black",
                        0.94)
    case = guard.verify(case_id)
    assert case["verdict"] == guard.REJECT, case["reason"]
    assert "stolen" in case["reason"].lower()


def test_impossible_travel_holds_the_fine_and_flags_a_clone(guard):
    from mediator import watchlist

    case_id = candidate(guard, "UP16GH1122", CAM006, "2026-09-04T11:30:00", "Kia", "Blue", 0.93)
    case = guard.verify(case_id)
    assert case["verdict"] == guard.HOLD, case["reason"]
    assert case["status"] == "HOLD"
    assert "impossible travel" in case["reason"].lower()
    assert watchlist.is_watched("UP16GH1122")


def test_an_unknown_plate_is_rejected_rather_than_guessed(guard):
    case_id = candidate(guard, "KA05MN9999", CAM001, CAPTURED, "Kia", "Blue")
    case = guard.verify(case_id)
    assert case["verdict"] == guard.REJECT
    assert "no registered vehicle" in case["reason"].lower()


def test_an_unreachable_authoritative_source_refuses_to_decide(guard, monkeypatch):
    from mediator import challan_guard

    real = challan_guard.run_global_query

    def ins_down(plate, attrs=None):
        result = real(plate, attrs)
        result["profile"].setdefault("source_availability", {})["INS"] = "DOWN"
        return result

    monkeypatch.setattr(challan_guard, "run_global_query", ins_down)
    case_id = candidate(guard, "DL01AB0002", CAM001, "2026-09-04T07:10:00", "Tata", "Grey")
    case = guard.verify(case_id)
    assert case["verdict"] == guard.HOLD
    assert "unreachable" in case["reason"] and "INS" in case["reason"]


def test_sightings_for_reads_camera_coordinates_through_the_registry(guard):
    sightings = guard.sightings_for("UP16GH1122")
    assert sightings, "expected at least one CAM capture for UP16GH1122"
    assert all({"location", "lat", "lon", "at"} <= set(s) for s in sightings)
    assert any(s["lat"] is not None for s in sightings)


def test_a_policy_added_at_the_insurer_cancels_the_challan_on_dispute(guard):
    import httpx

    plate = "DL01AB0002"
    case_id = candidate(guard, plate, CAM001, "2026-09-04T07:10:00", "Tata", "Grey")
    issued = guard.verify(case_id)
    assert issued["verdict"] == guard.ISSUE, issued["reason"]
    assert "no policy on record" in issued["reason"]

    try:
        r = httpx.post(f"{INS_BASE}/admin/mutate",
                       json={"action": "add_policy", "plate": plate,
                             "params": {"until": "01/01/2027", "start": "01/01/2026"}}, timeout=10.0)
        assert r.status_code == 200, r.text
        disputed = guard.dispute(case_id, "I renewed my policy before that date")
        assert disputed["status"] == "CANCELLED", disputed["reason"]
        assert "insurance_expiry" in disputed["reason"]
        assert [e["event"] for e in guard.events(case_id)][-1] == "CANCELLED"
    finally:
        httpx.post(f"{INS_BASE}/admin/mutate",
                   json={"action": "delete_policies", "plate": plate, "params": {}}, timeout=10.0)


def test_a_dispute_that_changes_nothing_upholds_the_challan(guard):
    case_id = candidate(guard, "DL05CD9B76", CAM004, CAPTURED, "Maruti Suzuki", "Silver", 0.71)
    guard.verify(case_id)
    upheld = guard.dispute(case_id, "I believe this is not my vehicle")
    assert upheld["status"] == "UPHELD", upheld["reason"]
    assert "re-verified live" in upheld["reason"]


def test_dispute_refuses_a_case_that_was_never_issued(guard):
    case_id = candidate(guard, "HR26EF4455", CAM003, "2026-09-02T19:45:00", "Mahindra", "Black")
    guard.verify(case_id)  # REJECTED
    with pytest.raises(ValueError):
        guard.dispute(case_id, "not mine")


def test_operator_overrides_are_recorded_as_events(guard):
    case_id = candidate(guard, "UP16GH1122", CAM006, "2026-09-04T11:30:00", "Kia", "Blue")
    guard.verify(case_id)
    case = guard.reject(case_id, "operator", "manually cleared after phone call")
    assert case["status"] == "REJECTED" and case["verdict"] == guard.REJECT
    assert "REJECTED" in [e["event"] for e in guard.events(case_id)]


def test_a_repeat_offender_pays_the_higher_amount(guard):
    first = candidate(guard, "DL05CD9B76", CAM004, "2026-09-01T09:00:00", "Maruti Suzuki", "Silver")
    guard.verify(first)
    second = candidate(guard, "DL05CD9B76", CAM004, "2026-09-02T09:00:00", "Maruti Suzuki", "Silver")
    case = guard.verify(second)
    assert case["amount_inr"] == guard.FINE_REPEAT_INR, case


def test_verify_all_runs_every_candidate_live_and_counts_the_verdicts(guard):
    """One click for the whole demo queue: every CANDIDATE is verified, nothing else is touched."""
    from scripts.seed_challan_cases import reset, seed

    reset()
    seed()
    already = guard.new_candidate(plate_read="DL07ZZ4444", camera_id="CAM001",
                                  location="NH8 Toll Plaza", lat=28.4595, lon=77.0266,
                                  captured_at="2026-09-04T12:00:00")
    guard.hold(already, "operator", "parked before the batch")
    seen: list[tuple[int, int]] = []

    counts = guard.verify_all("operator", on_progress=lambda done, total, case: seen.append((done, total)))

    assert counts == {"verified": 12, "issued": 6, "rejected": 5, "held": 1}, counts
    assert seen[-1] == (12, 12)
    assert guard.queue(status="CANDIDATE") == []
    assert guard.get(already)["reason"] == "parked before the batch"  # not re-verified
    story = {c["plate_read"]: c["verdict"] for c in guard.queue()[:5]}
    assert story == {"DL05CD9B76": "ISSUE", "DLO1AB1234": "REJECT", "UP16GH1122": "HOLD",
                     "HR26EF4455": "REJECT", "DL01AB0002": "ISSUE"}
    summary = guard.summary()
    assert (summary["ISSUED"], summary["REJECTED"], summary["HOLD"]) == (6, 5, 2)
    assert guard.verify_all("operator") == {"verified": 0, "issued": 0, "rejected": 0, "held": 0}
    reset()


def test_the_seed_cli_can_reset_and_verify_the_whole_queue(guard, capsys):
    """`seed_challan_cases.py --reset --verify` gives a clean, fully verified queue and says why."""
    from scripts.seed_challan_cases import main

    assert main(["--reset", "--verify"]) == 0
    out = capsys.readouterr().out
    assert guard.queue(status="CANDIDATE") == []
    assert len(guard.queue()) == 12
    assert "case 5" in out and "DL01AB0002" in out and "ISSUE" in out
    assert "impossible travel" in out and "reported stolen" in out
    assert "12 verified: 6 issued, 5 rejected, 1 held" in out
    main(["--reset"])
