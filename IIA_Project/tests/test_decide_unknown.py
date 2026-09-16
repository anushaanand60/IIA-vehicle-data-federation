"""Task 0.5: the UNKNOWN VEHICLE rule -- a plate that exists in no source we asked.

The rule only fires when REG was actually asked and answered OK: "not registered" must be a
statement about a source that spoke, never an artefact of a source we never contacted. These
tests also pin the neighbouring rules (camera-only, source down, the demo plates) so the new
rule cannot silently steal their cases.
"""
from __future__ import annotations

from mediator.decide import UNKNOWN_VEHICLE, evaluate_vehicle_decision

ALL = ["REG", "INS", "THEFT", "CAM"]


def profile(**over) -> dict:
    base = {
        "plate_number": "KA05MN9999",
        "owner_name": None, "vehicle_make": None, "vehicle_model": None, "vehicle_colour": None,
        "registration_date": None, "registration_status": None,
        "insurer_name": None, "policy_type": None, "insurance_start": None,
        "insurance_expiry": None, "insurance_status": "NONE",
        "stolen_status": "NOT_REPORTED", "last_incident_date": None, "case_status": None,
        "last_seen_location": None, "last_seen_time": None,
        "observed_make": None, "observed_model": None, "observed_colour": None,
        "source_availability": {s: "OK" for s in ALL},
    }
    base.update(over)
    return base


def test_plate_absent_from_every_asked_source_is_unknown_vehicle():
    decision, confidence, reasons = evaluate_vehicle_decision(profile(), ALL)
    assert (decision, confidence) == (UNKNOWN_VEHICLE, "MEDIUM")
    assert reasons == ["No registration, insurance, crime or camera record exists for this "
                       "plate in any asked source."]


def test_unknown_vehicle_outranks_the_uninsured_rule():
    # Without the new rule this profile would read UNINSURED -- REPORT, which accuses a vehicle
    # that no authority has ever heard of.
    assert evaluate_vehicle_decision(profile(), ALL)[0] != "UNINSURED — REPORT"


def test_a_camera_sighting_still_means_unregistered_not_unknown():
    p = profile(last_seen_time="2026-09-01T08:14:00", last_seen_location="NH-48 Toll Plaza")
    assert evaluate_vehicle_decision(p, ALL)[:2] == ("UNREGISTERED / SUSPICIOUS", "MEDIUM")


def test_a_theft_record_keeps_the_plate_out_of_the_unknown_rule():
    p = profile(last_incident_date="2026-02-11", case_status="CLOSED", stolen_status="RECOVERED")
    assert evaluate_vehicle_decision(p, ALL)[0] != UNKNOWN_VEHICLE


def test_an_insurance_row_keeps_the_plate_out_of_the_unknown_rule():
    p = profile(insurer_name="Bharti AXA", insurance_expiry="2027-12-31", insurance_status="VALID")
    assert evaluate_vehicle_decision(p, ALL)[0] != UNKNOWN_VEHICLE


def test_reg_not_asked_cannot_produce_unknown_vehicle():
    ins_only = ["INS"]
    p = profile(source_availability={"INS": "OK"})
    assert evaluate_vehicle_decision(p, ins_only)[0] != UNKNOWN_VEHICLE


def test_reg_down_never_claims_the_plate_is_unregistered():
    p = profile(source_availability={"REG": "DOWN", "INS": "OK", "THEFT": "OK", "CAM": "OK"})
    assert evaluate_vehicle_decision(p, ALL)[0] != UNKNOWN_VEHICLE


def test_a_down_cluster_is_still_undetermined():
    p = profile(source_availability={"REG": "DOWN", "INS": "DOWN", "THEFT": "DOWN", "CAM": "DOWN"})
    assert evaluate_vehicle_decision(p, ALL)[:2] == ("UNDETERMINED", "LOW")


# --------------------------------- the demo plates are unchanged -----------------------------

def test_clear_demo_profile_unchanged():
    p = profile(registration_status="ACTIVE", vehicle_make="Hyundai", vehicle_model="Creta",
                vehicle_colour="White", insurance_status="VALID", insurance_expiry="2027-03-10",
                observed_make="Hyundai", observed_model="Creta", observed_colour="White",
                last_seen_time="2026-09-01T08:14:00", last_seen_location="NH-48")
    assert evaluate_vehicle_decision(p, ALL)[:2] == ("CLEAR", "HIGH")


def test_expired_policy_demo_profile_unchanged():
    p = profile(registration_status="ACTIVE", insurance_status="EXPIRED",
                insurance_expiry="2026-07-12")
    assert evaluate_vehicle_decision(p, ALL)[:2] == ("UNINSURED — REPORT", "HIGH")


def test_stolen_demo_profile_unchanged():
    p = profile(registration_status="ACTIVE", stolen_status="STOLEN", case_status="OPEN",
                last_incident_date="2026-05-02")
    assert evaluate_vehicle_decision(p, ALL)[:2] == ("STOLEN — ALERT POLICE", "HIGH")


def test_cloned_plate_demo_profile_unchanged():
    p = profile(registration_status="ACTIVE", insurance_status="VALID",
                vehicle_make="Hyundai", vehicle_model="Creta", vehicle_colour="White",
                observed_make="Hyundai", observed_model="Venue", observed_colour="Silver",
                last_seen_time="2026-09-01T08:14:00")
    assert evaluate_vehicle_decision(p, ALL)[:2] == ("SUSPICIOUS — POSSIBLE CLONED PLATE", "MEDIUM")


def test_scrapped_demo_profile_unchanged():
    p = profile(incident_type="SHREDDING", last_incident_date="2026-01-20",
                case_status="CLOSED", stolen_status="RECOVERED")
    assert evaluate_vehicle_decision(p, ALL)[:2] == ("SCRAPPED — REGISTRATION VOID", "HIGH")
