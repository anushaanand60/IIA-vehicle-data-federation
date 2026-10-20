"""The matcher's context veto (plate_match.CONTEXT_VETO) and the clock-skew tolerance on impossible travel."""
from __future__ import annotations

from mediator import plate_match as pm
from mediator import travel_check

REG = {"DL01AB0118": {"make": "Maruti Suzuki", "model": "Baleno", "colour": "Black"},
       "DL05CD9876": {"make": "Maruti Suzuki", "model": "Swift", "colour": "Silver"},
       "UP16AB0405": {"make": "Mahindra", "model": "XUV700", "colour": "Black"}}


def _top(**context):
    ranked, _ = pm.match("DL01AB0118", confidence=0.9, registry=REG, **context)
    return ranked[0]


def test_one_contradiction_does_not_veto_an_exact_read():
    top = _top(observed_make="Maruti Suzuki", observed_colour="Red")   # cameras misjudge colour
    assert top.plate == "DL01AB0118" and top.decision == "ACCEPT"


def test_two_contradictions_hold_the_accept_at_review_as_a_possible_clone():
    top = _top(observed_make="Honda", observed_colour="Red")
    assert top.plate == "DL01AB0118" and top.decision == "REVIEW"
    assert any("possible clone" in r for r in top.reasons)


GURGAON = {"location": "Gurgaon", "lat": 28.4595, "lon": 77.0266}
NOIDA = {"location": "Noida", "lat": 28.5355, "lon": 77.3910}
AGRA = {"location": "Agra", "lat": 27.1767, "lon": 78.0081}


def test_clock_tolerance_forgives_a_short_trip_that_skewed_clocks_made_impossible():
    s = [{**GURGAON, "at": "2026-09-03T14:15:00"}, {**NOIDA, "at": "2026-09-03T14:20:00"}]  # 37 km in 5 min
    assert travel_check.impossible_legs(s)                                   # phase-1 default: flagged
    assert not travel_check.impossible_legs(s, clock_tolerance_min=10)       # 37 km in 15 min is legal


def test_clock_tolerance_still_catches_a_real_clone_and_same_instant_sightings():
    s = [{**GURGAON, "at": "2026-09-03T14:15:00"}, {**AGRA, "at": "2026-09-03T14:45:00"}]
    assert travel_check.impossible_legs(s, clock_tolerance_min=10)
    same = [{**GURGAON, "at": "2026-09-03T14:15:00"}, {**AGRA, "at": "2026-09-03T14:15:00"}]
    assert not travel_check.impossible_legs(same)                            # phase 1 skipped it
    assert travel_check.impossible_legs(same, clock_tolerance_min=10)
