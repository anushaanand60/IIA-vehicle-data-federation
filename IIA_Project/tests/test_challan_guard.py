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
