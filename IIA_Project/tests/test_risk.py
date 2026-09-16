"""Task 2.3: the explainable risk score (`mediator/risk.py`).

The score is a *presentation* of the decision, never a second opinion: it must be deterministic,
it must order the verdicts the way an enforcement officer would triage them, and every point on
screen must be attributable to a named factor. These tests pin exactly that — ordering, the
factors adding up to the number shown, and the clamp at both ends.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from mediator.decide import REFERENCE_TODAY
from mediator.risk import LEVELS, RiskFactor, RiskScore, score


def profile(decision: str = "CLEAR", **extra) -> dict:
    """A minimal profile: every core source asked and answering, nothing suspicious."""
    base = {
        "plate_number": "DL01AB1234",
        "decision": decision,
        "confidence": "HIGH",
        "conflicts": [],
        "provenance": {},
        "source_availability": {"REG": "OK", "INS": "OK", "THEFT": "OK", "CAM": "OK"},
        "insurance_expiry": None,
        "last_seen_time": None,
    }
    base.update(extra)
    return base


# ------------------------------------------------------------------ ordering

def test_stolen_outranks_uninsured_outranks_clear():
    stolen = score(profile("STOLEN — ALERT POLICE")).value
    uninsured = score(profile("UNINSURED — REPORT")).value
    clear = score(profile("CLEAR")).value
    assert stolen > uninsured > clear


def test_every_known_decision_is_ordered_as_the_plan_states():
    ordered = [
        "STOLEN — ALERT POLICE",
        "UNINSURED — REPORT",
        "REGISTRATION INVALID — REPORT",
        "SUSPICIOUS — POSSIBLE CLONED PLATE",
        "UNREGISTERED / SUSPICIOUS",
        "UNKNOWN VEHICLE — NOT REGISTERED",
        "UNDETERMINED",
        "CLEAR",
    ]
    values = [score(profile(d)).value for d in ordered]
    assert values == sorted(values, reverse=True), dict(zip(ordered, values))


def test_scrapped_is_scored_like_stolen():
    assert score(profile("SCRAPPED — ALERT POLICE")).value == \
        score(profile("STOLEN — ALERT POLICE")).value


def test_an_unseen_decision_string_degrades_to_undetermined_rather_than_raising():
    assert score(profile("SOMETHING THE ENGINE LEARNED LATER")).value == \
        score(profile("UNDETERMINED")).value


def test_scoring_is_deterministic():
    p = profile("UNINSURED — REPORT", conflicts=[{"attribute": "vehicle_colour"}])
    assert score(p).value == score(p).value == score(p).value


# ------------------------------------------------------------------ factors

def test_factors_sum_exactly_to_the_value():
    p = profile(
        "UNINSURED — REPORT",
        conflicts=[{"attribute": "a"}, {"attribute": "b"}],
        source_availability={"REG": "OK", "INS": "OK", "THEFT": "DOWN", "CAM": "OK"},
        provenance={"owner_name": {"source": "CAM", "trust": 0.6}},
    )
    result = score(p)
    assert isinstance(result, RiskScore)
    assert all(isinstance(f, RiskFactor) for f in result.factors)
    assert sum(f.points for f in result.factors) == result.value


def test_factors_sum_to_the_value_even_when_the_score_is_clamped():
    p = profile(
        "STOLEN — ALERT POLICE",
        conflicts=[{"a": 1}, {"b": 2}, {"c": 3}, {"d": 4}],
        source_availability={"REG": "OK", "INS": "DOWN", "THEFT": "OK", "CAM": "OK"},
        provenance={"x": {"trust": 0.6}},
        insurance_expiry=(REFERENCE_TODAY + timedelta(days=3)).isoformat(),
    )
    result = score(p)
    assert result.value == 100
    assert sum(f.points for f in result.factors) == 100


def test_every_factor_carries_a_name_and_an_explanation():
    result = score(profile("UNINSURED — REPORT"))
    assert result.factors
    for factor in result.factors:
        assert factor.name.strip()
        assert factor.note.strip()


def test_the_base_factor_names_the_decision_that_produced_it():
    result = score(profile("STOLEN — ALERT POLICE"))
    assert "STOLEN" in result.factors[0].note


# ---------------------------------------------------------------- modifiers

def test_each_conflict_adds_five_points_capped_at_fifteen():
    none = score(profile("CLEAR")).value
    one = score(profile("CLEAR", conflicts=[{"a": 1}])).value
    three = score(profile("CLEAR", conflicts=[{"a": 1}, {"b": 2}, {"c": 3}])).value
    many = score(profile("CLEAR", conflicts=[{"i": i} for i in range(9)])).value
    assert one == none + 5
    assert three == none + 15
    assert many == none + 15  # the cap: a noisy camera cannot dominate the verdict


def test_a_policy_expiring_within_thirty_days_adds_ten():
    soon = (REFERENCE_TODAY + timedelta(days=10)).isoformat()
    later = (REFERENCE_TODAY + timedelta(days=200)).isoformat()
    assert score(profile("CLEAR", insurance_expiry=soon)).value == \
        score(profile("CLEAR", insurance_expiry=later)).value + 10


def test_a_policy_already_expired_is_not_counted_twice_as_expiring_soon():
    past = (REFERENCE_TODAY - timedelta(days=5)).isoformat()
    # The decision already says UNINSURED; the expiry-distance modifier must not pile on.
    assert score(profile("UNINSURED — REPORT", insurance_expiry=past)).value == \
        score(profile("UNINSURED — REPORT")).value


def test_a_stale_camera_sighting_adds_five_only_when_the_camera_was_asked():
    old = (REFERENCE_TODAY - timedelta(days=200)).isoformat()
    fresh = (REFERENCE_TODAY - timedelta(days=2)).isoformat()
    asked = {"REG": "OK", "INS": "OK", "THEFT": "OK", "CAM": "OK"}
    not_asked = {"REG": "OK", "INS": "OK", "THEFT": "OK"}

    stale = score(profile("CLEAR", last_seen_time=old, source_availability=asked)).value
    recent = score(profile("CLEAR", last_seen_time=fresh, source_availability=asked)).value
    assert stale == recent + 5

    # CAM never asked: silence is not staleness, so no modifier and no "not OK" penalty either.
    quiet = score(profile("CLEAR", source_availability=not_asked))
    assert not any("camera" in f.name.lower() for f in quiet.factors)


def test_a_core_source_that_did_not_answer_adds_ten_once():
    one_down = score(profile("CLEAR", source_availability={
        "REG": "OK", "INS": "OK", "THEFT": "DOWN", "CAM": "OK"})).value
    two_down = score(profile("CLEAR", source_availability={
        "REG": "OK", "INS": "ERROR", "THEFT": "DOWN", "CAM": "OK"})).value
    clean = score(profile("CLEAR")).value
    assert one_down == clean + 10
    assert two_down == clean + 10  # uncertainty is a single flag, not a tally


def test_low_average_trust_of_contributing_sources_adds_five():
    camera_only = score(profile("CLEAR", provenance={
        "observed_make": {"source": "CAM", "trust": 0.6},
        "last_seen_location": {"source": "CAM", "trust": 0.6},
    })).value
    official = score(profile("CLEAR", provenance={
        "owner_name": {"source": "REG", "trust": 0.95},
    })).value
    assert camera_only == official + 5


def test_no_provenance_at_all_does_not_trigger_the_trust_modifier():
    # Nothing was contributed, so there is no average trust to be low: claim nothing.
    assert not any("trust" in f.name.lower() for f in score(profile("CLEAR")).factors)


# ------------------------------------------------------------------- levels

@pytest.mark.parametrize("value_decision,expected", [
    ("CLEAR", "LOW"),
    ("UNDETERMINED", "MEDIUM"),
    ("REGISTRATION INVALID — REPORT", "HIGH"),
    ("STOLEN — ALERT POLICE", "CRITICAL"),
])
def test_levels_follow_the_published_thresholds(value_decision, expected):
    assert score(profile(value_decision)).level == expected


def test_level_boundaries_are_exactly_as_documented():
    assert LEVELS == (("LOW", 25), ("MEDIUM", 55), ("HIGH", 80), ("CRITICAL", 101))


def test_the_value_is_always_inside_zero_and_one_hundred():
    for decision in ("CLEAR", "UNDETERMINED", "STOLEN — ALERT POLICE"):
        for conflicts in ([], [{"a": 1}] * 5):
            result = score(profile(decision, conflicts=conflicts))
            assert 0 <= result.value <= 100


def test_an_empty_profile_scores_without_raising():
    result = score({})
    assert 0 <= result.value <= 100
    assert result.level in {name for name, _ in LEVELS}
