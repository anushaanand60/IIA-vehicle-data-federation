"""Matcher precision/recall against the hand-made gold mapping (design PDF section 6)."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from mediator.matcher_eval import evaluate, load_gold, score

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {"REG", "INS", "THEFT", "CAM", "PUC"}


# --- the arithmetic -----------------------------------------------------------------------------

def test_score_counts_hits_false_alarms_and_misses():
    gold = {("t", "a", "x"), ("t", "b", "y"), ("t", "c", "z")}
    predicted = {("t", "a", "x"), ("t", "b", "w"), ("t", "d", "v")}
    result = score(predicted, gold)
    assert (result["tp"], result["fp"], result["fn"]) == (1, 2, 2)
    assert result["precision"] == pytest.approx(1 / 3) and result["recall"] == pytest.approx(1 / 3)
    assert result["f1"] == pytest.approx(1 / 3)


def test_empty_prediction_scores_zero_instead_of_dividing_by_zero():
    result = score(set(), {("t", "a", "x")})
    assert (result["precision"], result["recall"], result["f1"]) == (0.0, 0.0, 0.0)


def test_table_names_compare_case_insensitively():
    # PostgreSQL reports vehicle_registration where SQLite reports VEHICLE_REGISTRATION.
    assert score({("vehicle_registration", "make", "vehicle_make")},
                 {("VEHICLE_REGISTRATION", "make", "vehicle_make")})["tp"] == 1


# --- the gold mapping ------------------------------------------------------------------------------

def test_gold_mapping_uses_only_stored_global_attributes():
    from mediator.schema import GLOBAL_SCHEMA_ATTRIBUTES
    gold = load_gold()
    assert set(gold) == SOURCES
    for source, rows in gold.items():
        for _, _, attr in rows:
            assert attr in GLOBAL_SCHEMA_ATTRIBUTES and not GLOBAL_SCHEMA_ATTRIBUTES[attr].get("is_derived"), (source, attr)


# --- against the team's live schemas -----------------------------------------------------------------

@pytest.fixture(scope="module")
def team_dbs() -> None:
    if not (ROOT / "sources" / "reg" / "reg.db").exists():
        pytest.skip("team databases not built yet: python scripts/load_source.py REG (and INS/THEFT/CAM/PUC)")


def test_evaluation_reports_every_source_and_an_overall_score(team_dbs):
    report = evaluate()
    assert set(report["per_source"]) == SOURCES
    overall = report["overall"]
    assert 0.0 <= overall["precision"] <= 1.0 and 0.0 <= overall["recall"] <= 1.0
    assert overall["tp"] + overall["fn"] == sum(len(rows) for rows in load_gold().values())


def test_matcher_maps_the_uc6_pollution_source_plate_column_correctly(team_dbs):
    # The UC6 demo runs the matcher on PUC; regn_number -> plate_number must always be found.
    puc = evaluate(sources=["PUC"])["per_source"]["PUC"]
    assert (puc["tp"], puc["fp"], puc["fn"]) == (2, 0, 0), puc  # regn_number -> plate_number, valid_upto -> puc_expiry


def test_cli_prints_a_table_and_json(team_dbs):
    human = subprocess.run([sys.executable, "scripts/evaluate_matcher.py"], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert human.returncode == 0, human.stderr
    assert "precision" in human.stdout and "OVERALL" in human.stdout
    machine = subprocess.run([sys.executable, "scripts/evaluate_matcher.py", "--json"], cwd=ROOT,
                             capture_output=True, text=True, timeout=120)
    assert json.loads(machine.stdout)["overall"]["tp"] >= 1
