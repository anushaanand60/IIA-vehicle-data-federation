"""Ground truth for every generated vehicle (design PDF section 4).

mediator/ground_truth.py applies the same ordered decision rules as mediator/decide.py directly to
the generated CSVs, independently of the federated pipeline, so the pipeline can be graded against it.
"""
import csv
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pytest

from mediator.ground_truth import build_ground_truth, expected_decision
from sources.server_manager import get_cluster
from tests.fixtures import inject_test_mappings

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 9, 4)
REG = {"make": "Hyundai", "model": "Creta", "colour": "White", "reg_status": "ACTIVE"}
VALID = [{"policy_until": "14/01/2027"}]
SEEN = [{"captured_at": "2026-09-04T08:30:00", "observed_make": "Hyundai", "observed_model": "Creta", "observed_colour": "White"}]
STOLEN = [{"reported_date": 1786768200, "stolen_flag": "Y", "recovered_flag": "N", "case_status": "OPEN"}]
SHREDDED = [{"reported_date": 1721041200, "stolen_flag": "N", "recovered_flag": "N", "case_status": "CLOSED", "incident_type": "SHREDDING"}]


def decide(reg=REG, policies=VALID, incidents=(), captures=SEEN) -> tuple[str, str]:
    return expected_decision(reg, list(policies), list(incidents), list(captures), TODAY)[:2]


# --- the rules, in order (mediator/decide.py) ---------------------------------------------------

def test_clear_vehicle_seen_by_a_camera_is_high_confidence():
    assert decide() == ("CLEAR", "HIGH")


def test_clear_vehicle_never_seen_is_medium_confidence():
    assert decide(captures=()) == ("CLEAR", "MEDIUM")


def test_open_theft_outranks_everything_else():
    assert decide(policies=(), incidents=STOLEN) == ("STOLEN — ALERT POLICE", "HIGH")


def test_recovered_theft_is_not_stolen():
    assert decide(incidents=[{**STOLEN[0], "recovered_flag": "Y", "case_status": "CLOSED"}]) == ("CLEAR", "HIGH")


def test_only_the_latest_incident_counts():
    older_open = {**STOLEN[0], "reported_date": 1700000000}
    newer_closed = {**STOLEN[0], "recovered_flag": "Y", "case_status": "CLOSED"}
    assert decide(incidents=[newer_closed, older_open])[0] == "CLEAR"


def test_shredded_and_sighted_again_is_scrapped_alert_police():
    assert decide(reg={**REG, "reg_status": "CANCELLED"}, policies=(), incidents=SHREDDED) == ("SCRAPPED — ALERT POLICE", "HIGH")


def test_shredded_and_never_sighted_again_is_registration_void():
    assert decide(reg={**REG, "reg_status": "CANCELLED"}, policies=(), incidents=SHREDDED, captures=()) == \
        ("SCRAPPED — REGISTRATION VOID", "HIGH")


def test_camera_only_plate_is_unregistered():
    assert decide(reg=None, policies=()) == ("UNREGISTERED / SUSPICIOUS", "MEDIUM")


def test_two_visual_mismatches_suggest_a_cloned_plate():
    seen = [{**SEEN[0], "observed_model": "Seltos", "observed_colour": "Blue"}]
    assert decide(captures=seen) == ("SUSPICIOUS — POSSIBLE CLONED PLATE", "MEDIUM")


def test_one_mismatch_or_a_misspelt_make_is_not_a_conflict():
    assert decide(captures=[{**SEEN[0], "observed_colour": "Blue"}])[0] == "CLEAR"
    assert decide(reg={**REG, "make": "Hyundia", "colour": "Blue"})[0] == "CLEAR"


def test_empty_values_are_left_out_of_the_comparison():
    assert decide(reg={**REG, "make": "", "colour": ""}, captures=[{**SEEN[0], "observed_make": "Kia", "observed_colour": "Red"}])[0] == "CLEAR"


def test_only_the_latest_capture_is_compared():
    old_conflict = {**SEEN[0], "captured_at": "2026-08-01T08:00:00", "observed_model": "Seltos", "observed_colour": "Blue"}
    assert decide(captures=[SEEN[0], old_conflict])[0] == "CLEAR"


def test_missing_or_expired_policy_is_uninsured():
    assert decide(policies=()) == ("UNINSURED — REPORT", "HIGH")
    assert decide(policies=[{"policy_until": "10/06/2026"}]) == ("UNINSURED — REPORT", "HIGH")


def test_the_latest_policy_decides_whatever_its_position():
    assert decide(policies=[{"policy_until": "01/01/2020"}, {"policy_until": "14/01/2027"}])[0] == "CLEAR"


def test_inactive_registration_is_reported_after_insurance():
    assert decide(reg={**REG, "reg_status": "SUSPENDED"}) == ("REGISTRATION INVALID — REPORT", "HIGH")
    assert decide(reg={**REG, "reg_status": "SUSPENDED"}, policies=())[0] == "UNINSURED — REPORT"


# --- the generated population ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def ground_truth() -> list[dict]:
    if not (ROOT / "reg_vehicle_registration.csv").exists():
        pytest.skip("generated CSVs missing: python reg.py; python ins.py; python theft.py; python cam.py")
    return build_ground_truth(ROOT)


def test_every_registered_and_camera_only_plate_has_exactly_one_row(ground_truth):
    plates = [row["plate_number"] for row in ground_truth]
    assert len(plates) == len(set(plates))
    with open(ROOT / "reg_vehicle_registration.csv", newline="", encoding="utf-8") as f:
        registered = {row["registration_no"] for row in csv.DictReader(f)}
    assert registered <= set(plates) and "MH12IJ7788" in plates


def test_oracle_agrees_with_the_team_demo_expectations(ground_truth):
    by_plate = {row["plate_number"]: row for row in ground_truth}
    with open(ROOT / "data" / "mediator_test_cases.csv", newline="", encoding="utf-8") as f:
        for case in csv.DictReader(f):
            row = by_plate[case["plate_number"]]
            assert (row["expected_decision"], row["expected_confidence"]) == (case["expected_decision"], case["expected_confidence"])


def test_every_decision_class_appears_in_the_population(ground_truth):
    assert {row["expected_decision"] for row in ground_truth} >= {
        "CLEAR", "UNINSURED — REPORT", "STOLEN — ALERT POLICE", "UNREGISTERED / SUSPICIOUS",
        "SUSPICIOUS — POSSIBLE CLONED PLATE", "REGISTRATION INVALID — REPORT"}


@pytest.fixture(scope="module")
def team_cluster(ground_truth):
    cluster = get_cluster()
    cluster.start_all(include_puc=True)
    inject_test_mappings()
    time.sleep(1)
    yield cluster
    cluster.stop_all()


def test_federated_pipeline_matches_the_oracle_for_every_decision_class(ground_truth, team_cluster):
    from mediator.core import run_global_query
    sample, per_class = [], {}
    for row in ground_truth:
        if per_class.setdefault((row["expected_decision"], row["expected_confidence"]), 0) < 4:
            per_class[(row["expected_decision"], row["expected_confidence"])] += 1
            sample.append(row)
    wrong = []
    for row in sample:
        profile = run_global_query(row["plate_number"])["profile"]
        if (profile["decision"], profile["confidence"]) != (row["expected_decision"], row["expected_confidence"]):
            wrong.append((row["plate_number"], profile["decision"], profile["confidence"], row["basis"]))
    assert not wrong, wrong


def test_evaluation_script_reports_accuracy(ground_truth, team_cluster):
    proc = subprocess.run([sys.executable, "scripts/evaluate_ground_truth.py", "--limit", "15"], cwd=ROOT,
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "accuracy" in proc.stdout
