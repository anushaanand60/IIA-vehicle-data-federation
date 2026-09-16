"""Decision honesty for partial questions and unavailable sources (Task 3.1).

A decision may only rest on sources that were asked and answered. Rows use the team's real
source schemas, so these go through the integrator (transform + derive + decide) rather than
hand-built profiles -- the point of the task is that the *derived* statuses stay honest too.
"""
from datetime import datetime, timezone

import pytest

from mediator.decide import evaluate_vehicle_decision
from mediator.integrator import integrate_results

NOW = datetime(2026, 9, 4, tzinfo=timezone.utc).isoformat()
CORE = ["REG", "INS", "THEFT", "CAM"]

REG_ROW = {"registration_id": 1, "registration_no": "DL01AB1234", "owner_id": 1, "full_name": "Ramesh Sharma",
           "make": "Hyundai", "model": "Creta", "colour": "White", "fuel_type": "PETROL",
           "registered_on": "2023-01-15", "reg_status": "ACTIVE", "rto_code": "DL01"}
INS_ROW = {"policy_id": 1, "vehicle_reg": "DL-01-AB-1234", "insurer_id": 1, "insurer_name": "National Shield Insurance",
           "policy_type": "COMPREHENSIVE", "policy_start": "15/01/2026", "policy_until": "14/01/2027",
           "is_active": 1, "premium_inr": 12500.0}
CAM_ROW = {"capture_id": 1, "plate_id": "DL01AB1234", "camera_id": "CAM001", "location_name": "NH8 Toll Plaza",
           "lat": 28.46, "lon": 77.03, "captured_at": "2026-09-04T08:30:00", "observed_make": "Hyundai",
           "observed_model": "Creta", "observed_colour": "White", "ocr_confidence": 0.98}


@pytest.fixture(scope="module", autouse=True)
def validated_mappings() -> None:
    from tests.fixtures import inject_test_mappings
    inject_test_mappings()


def executed(**sources: tuple) -> dict:
    """SOURCE_ID=(status, rows) -> the execution result shape core.py hands the integrator."""
    return {"sources_executed": {sid: {"status": status, "rows": rows, "fetched_at": NOW}
                                 for sid, (status, rows) in sources.items()}}


# --- D1: a partial question claims nothing about sources it did not ask ----------------------

def test_uc1_insurance_question_makes_no_theft_or_camera_claims():
    profile = integrate_results(executed(REG=("OK", [REG_ROW]), INS=("OK", [INS_ROW])), "DL01AB1234", ["REG", "INS"])
    assert (profile["decision"], profile["confidence"]) == ("CLEAR", "MEDIUM")
    assert profile["stolen_status"] is None
    reasons = " ".join(profile["reasons"])
    assert "Insurance is VALID" in reasons
    assert "NOT_REPORTED" not in reasons and "No recent camera capture" not in reasons
    assert "not checked in this query: THEFT, CAM" in reasons


def test_theft_only_question_never_concludes_uninsured():
    profile = integrate_results(executed(THEFT=("OK", [])), "DL01AB1234", ["THEFT"])
    assert profile["insurance_status"] is None
    assert "UNINSURED" not in profile["decision"]
    assert "registration is ACTIVE" not in " ".join(profile["reasons"])


# --- D2: a source that is down never turns into a finding -------------------------------------

def test_registration_down_with_a_sighting_is_undetermined_not_unregistered():
    profile = integrate_results(executed(REG=("DOWN", []), INS=("OK", [INS_ROW]), THEFT=("OK", []), CAM=("OK", [CAM_ROW])),
                                "DL01AB1234", CORE)
    assert (profile["decision"], profile["confidence"]) == ("UNDETERMINED", "LOW")
    assert "REG" in " ".join(profile["reasons"])


# --- unchanged behaviour ------------------------------------------------------------------------

def test_absent_from_a_working_registry_is_still_unregistered():
    profile = integrate_results(executed(REG=("OK", []), INS=("OK", []), THEFT=("OK", []), CAM=("OK", [CAM_ROW])),
                                "DL01AB1234", CORE)
    assert profile["decision"] == "UNREGISTERED / SUSPICIOUS"


def test_full_profile_clear_keeps_high_confidence():
    profile = integrate_results(executed(REG=("OK", [REG_ROW]), INS=("OK", [INS_ROW]), THEFT=("OK", []), CAM=("OK", [CAM_ROW])),
                                "DL01AB1234", CORE)
    assert (profile["decision"], profile["confidence"]) == ("CLEAR", "HIGH")
    assert profile["stolen_status"] == "NOT_REPORTED"


# --- the demo plates still decide exactly as before -------------------------------------------
# Profile-level (no wrappers), in the style of tests/test_decide_unknown.py, so a change to the
# honesty rules that quietly re-labels a demo case fails here first.

ALL = ["REG", "INS", "THEFT", "CAM"]


def demo_profile(**over) -> dict:
    base = {
        "plate_number": "DL01AB1234",
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


DEMO_CASES = [
    ("DL01AB1234 clear", ("CLEAR", "HIGH"), dict(
        registration_status="ACTIVE", insurance_status="VALID", insurance_expiry="2027-03-10",
        vehicle_make="Hyundai", vehicle_model="Creta", vehicle_colour="White",
        observed_make="Hyundai", observed_model="Creta", observed_colour="White",
        last_seen_time="2026-09-01T08:14:00", last_seen_location="NH-48")),
    ("DL05CD9876 expired policy", ("UNINSURED — REPORT", "HIGH"), dict(
        registration_status="ACTIVE", insurance_status="EXPIRED", insurance_expiry="2026-07-12")),
    ("HR26EF4455 stolen", ("STOLEN — ALERT POLICE", "HIGH"), dict(
        registration_status="ACTIVE", stolen_status="STOLEN", case_status="OPEN",
        last_incident_date="2026-05-02")),
    ("UP16GH1122 cloned", ("SUSPICIOUS — POSSIBLE CLONED PLATE", "MEDIUM"), dict(
        registration_status="ACTIVE", insurance_status="VALID",
        vehicle_make="Hyundai", vehicle_model="Creta", vehicle_colour="White",
        observed_make="Hyundai", observed_model="Venue", observed_colour="Silver",
        last_seen_time="2026-09-01T08:14:00")),
    ("MH12IJ7788 camera only", ("UNREGISTERED / SUSPICIOUS", "MEDIUM"), dict(
        last_seen_time="2026-09-01T08:14:00", last_seen_location="NH-48 Toll Plaza")),
    ("scrapped", ("SCRAPPED — REGISTRATION VOID", "HIGH"), dict(
        incident_type="SHREDDING", last_incident_date="2026-01-20",
        case_status="CLOSED", stolen_status="RECOVERED")),
]


@pytest.mark.parametrize("name,expected,over", DEMO_CASES, ids=[c[0] for c in DEMO_CASES])
def test_demo_decisions_unchanged(name, expected, over):
    assert evaluate_vehicle_decision(demo_profile(**over), ALL)[:2] == expected


# --- Task 2.5: grace-period / escalation ladder for lapsed insurance ---------------------------
# REFERENCE_TODAY is 2026-09-04. Mirrors UK Continuous Insurance Enforcement's
# advisory -> penalty -> impound staging (docs/FIELD_RESEARCH.md #6).

LAPSE_CASES = [
    ("10 days lapsed -> advisory", ("UNINSURED — ADVISORY", "MEDIUM"), dict(
        registration_status="ACTIVE", insurance_status="EXPIRED", insurance_expiry="2026-08-25")),
    ("25 days lapsed -> warning", ("UNINSURED — WARNING", "HIGH"), dict(
        registration_status="ACTIVE", insurance_status="EXPIRED", insurance_expiry="2026-08-10")),
    ("86 days lapsed -> report", ("UNINSURED — REPORT", "HIGH"), dict(
        registration_status="ACTIVE", insurance_status="EXPIRED", insurance_expiry="2026-06-10")),
    ("no policy at all -> report", ("UNINSURED — REPORT", "HIGH"), dict(
        registration_status="ACTIVE", insurance_status="NONE")),
]


@pytest.mark.parametrize("name,expected,over", LAPSE_CASES, ids=[c[0] for c in LAPSE_CASES])
def test_lapsed_insurance_escalation_ladder(name, expected, over):
    assert evaluate_vehicle_decision(demo_profile(**over), ALL)[:2] == expected


def test_lapse_advisory_reason_names_the_days_and_the_window():
    profile = demo_profile(registration_status="ACTIVE", insurance_status="EXPIRED", insurance_expiry="2026-08-25")
    _, _, reasons = evaluate_vehicle_decision(profile, ALL)
    assert any("lapsed 10 days ago (advisory window" in r for r in reasons)


def test_lapse_warning_reason_names_the_days_and_the_window():
    profile = demo_profile(registration_status="ACTIVE", insurance_status="EXPIRED", insurance_expiry="2026-08-10")
    _, _, reasons = evaluate_vehicle_decision(profile, ALL)
    assert any("lapsed 25 days ago (warning window" in r for r in reasons)


def test_demo_plate_dl05cd9876_still_reports_at_86_days_lapsed():
    # DL05CD9876 expired 10/06/2026 -> 86 days before REFERENCE_TODAY 2026-09-04: still REPORT.
    profile = demo_profile(registration_status="ACTIVE", insurance_status="EXPIRED", insurance_expiry="2026-06-10")
    assert evaluate_vehicle_decision(profile, ALL)[:2] == ("UNINSURED — REPORT", "HIGH")
