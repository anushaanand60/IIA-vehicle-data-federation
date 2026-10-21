"""PHASE3-ADDED (C1: match the entity with every database): the federated candidate set."""
from __future__ import annotations

import pytest

from mediator import federated_match as fm
from mediator import lexicons
from mediator import plate_match as pm

# Raw rows as each wrapper returns them (the source's own column names); only what the test needs.
ROWS = {
    "REG": [{"registration_id": 1, "registration_no": "DL01AB1234", "make": "Hyundai", "model": "Creta",
             "colour": "White"},
            {"registration_id": 2, "registration_no": "DL05CD9876", "make": "Honda", "model": "City",
             "colour": None}],
    "INS": [{"policy_id": 1, "vehicle_reg": "DL-01-AB-1234"},
            {"policy_id": 2, "vehicle_reg": "DL-01-AB-1235"}],       # insured, never registered
    "THEFT": [{"incident_id": 1, "vehicle_number": "dl 01 ab 1234"}],
    "CAM": [{"capture_id": 1, "plate_id": "DL05CD9876", "observed_colour": "Grey", "ocr_confidence": 0.9},
            {"capture_id": 2, "plate_id": "DL05CD9876", "observed_colour": "Grey", "ocr_confidence": 0.8},
            {"capture_id": 3, "plate_id": "MH12IJ7788", "observed_make": "Kia", "observed_colour": "Red",
             "ocr_confidence": 0.9, "captured_at": "2026-09-01T10:00:00", "location_name": "Toll"},
            {"capture_id": 4, "plate_id": "DLO1AB1Z34", "ocr_confidence": 0.4}],   # a misread
    "PUC": [],
}


@pytest.fixture
def universe(monkeypatch):
    monkeypatch.setattr(fm, "_rows", lambda spec: ROWS.get(spec.source_id, []))
    return fm.live_universe()


def test_every_source_contributes_its_plates(universe):
    u, meta = universe
    assert u["DL01AB1234"]["sources"] == ["REG", "INS", "THEFT"]
    assert meta["identity"] == "REG" and not meta["unavailable"]


def test_a_car_only_insurance_holds_is_a_candidate_but_not_registered(universe):
    u, _ = universe
    assert u["DL01AB1235"]["registered"] is False and u["DL01AB1235"]["sources"] == ["INS"]
    assert u["DL01AB1235"]["prior"] == 1.0
    assert fm.held_by(u["DL01AB1235"]) == "not registered; known to INS"


def test_a_camera_only_plate_is_weaker_evidence_and_misreads_are_left_out(universe):
    u, _ = universe
    assert u["MH12IJ7788"]["prior"] == fm.CAM_ONLY_PRIOR
    assert (u["MH12IJ7788"]["make"], u["MH12IJ7788"]["colour"]) == ("Kia", "Red")
    assert u["MH12IJ7788"]["last_seen"] == {"at": "2026-09-01T10:00:00", "where": "Toll"}
    assert "DLO1AB1Z34" not in u


def test_a_blank_registration_field_is_filled_from_camera_sightings(universe):
    u, _ = universe
    assert u["DL05CD9876"]["colour"] == "Grey" and "colour" in u["DL05CD9876"]["fingerprint"]


def test_the_exact_read_of_an_insured_unregistered_car_stays_itself(universe):
    u, _ = universe
    top = pm.match("DL01AB1235", confidence=0.95, registry=u)[0][0]
    assert top.plate == "DL01AB1235" and top.evidence["sources"] == ["INS"]


def test_registration_down_refuses(monkeypatch):
    def rows(spec):
        if spec.source_id == "REG":
            raise lexicons.SourceUnavailable("REG unreachable")
        return ROWS.get(spec.source_id, [])
    monkeypatch.setattr(fm, "_rows", rows)
    with pytest.raises(lexicons.SourceUnavailable):
        fm.live_universe()


def test_another_source_down_is_named_not_fatal(monkeypatch):
    def rows(spec):
        if spec.source_id == "INS":
            raise lexicons.SourceUnavailable("INS unreachable")
        return ROWS.get(spec.source_id, [])
    monkeypatch.setattr(fm, "_rows", rows)
    u, meta = fm.live_universe()
    assert "INS" in meta["unavailable"] and "DL01AB1235" not in u
