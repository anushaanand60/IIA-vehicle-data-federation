"""Workstream G item 3: labelled-noise validation of cleansing. Offline: reads the local SQLite copies."""
from __future__ import annotations

import json
import random

import pytest

from mediator import cleansing, cleansing_validation as cv
from mediator.transforms import norm_plate


@pytest.fixture(scope="module")
def report():
    return cv.validate(n=48, seed=11, local_db=True)


def test_report_shape_and_json(report):
    assert set(report) >= {"overall", "per_type", "examples", "seed", "wrong_corrections_detail"}
    assert report["seed"] == 11 and report["overall"]["records"] == 48
    assert set(report["per_type"]) <= set(cv.EXPECT) and "control" in report["per_type"]
    for e in report["examples"]:
        assert set(e) >= {"type", "original", "noisy", "detected", "proposed", "rule_path", "correct"}
    json.dumps(report)


def test_core_guarantees(report):
    t = report["per_type"]
    assert t["control"]["false_alarm_rate"] == 0.0
    for kind in ("colour_misspelling", "make_misspelling", "duplicate", "timestamp_unparseable",
                 "timestamp_future", "low_confidence", "clone_swap"):
        assert t[kind]["detection_recall"] == 1.0, kind
    assert t["clone_swap"]["wrong_corrections"] == 0 and t["clone_swap"]["flag_rate"] == 1.0
    assert report["overall"]["plate_wrong_corrections"] == len(
        [w for w in report["wrong_corrections_detail"] if w["field"] == "plate_number"])


def test_deterministic_for_a_seed(report):
    again = cv.validate(n=48, seed=11, local_db=True)
    assert again["overall"] == report["overall"] and again["per_type"] == report["per_type"]


def test_misread_uses_measured_confusions_and_avoids_registered_plates():
    conf = cv._confusions()
    rng = random.Random(1)
    for k in (1, 2):
        read = cv.misread("DL01AB1234", k, rng, conf, {"DLO1AB1234"})
        assert read and len(read) == 10 and read != "DLO1AB1234"
        diffs = [(a, b) for a, b in zip("DL01AB1234", read) if a != b]
        assert len(diffs) == k and all(b in dict(conf[a]) for a, b in diffs)


def test_overwriting_clone_evidence_scores_as_wrong():
    original = {"plate_number": "DL01AB1234", "observed_make": "Hyundai", "observed_colour": "White"}
    fixes = [cleansing.FieldFix("observed_colour", "set", "Red", "White", "R3", "", []),
             cleansing.FieldFix("observed_make", "keep", "Kia", "Kia", None, "", [])]
    prop = cleansing.Proposal("CAM", 1, {}, [cleansing.Anomaly("COLOUR_DISAGREES_REGISTRATION", "observed_colour",
                                                                "Red", {}, "high")], fixes)
    s = cv.score("clone_swap", {**original, "observed_colour": "Red"}, prop)
    assert s["detected"] and not s["correct"] and s["wrong"] == ["observed_colour"]
    assert norm_plate("dl 01 ab 1234") == "DL01AB1234"


def test_script_writes_json_and_markdown(tmp_path, capsys):
    from scripts import validate_cleansing
    out_json, out_md = tmp_path / "v.json", tmp_path / "v.md"
    assert validate_cleansing.main(["--n", "24", "--local-db", "--json", str(out_json), "--md", str(out_md)]) == 0
    data = json.loads(out_json.read_text(encoding="utf-8"))
    md = out_md.read_text(encoding="utf-8")
    assert data["overall"]["records"] == 24 and "## Per noise type" in md and "wrong plate corrections" in md
