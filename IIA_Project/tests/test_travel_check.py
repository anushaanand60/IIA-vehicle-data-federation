"""Impossible-travel clone detection (Challan Guard step 3).

The same plate photographed in two places that no vehicle could have driven between is the
classic cloned-plate signal (GB2448780A; Met Police ANPR). Pure geometry, no source access.
"""
from __future__ import annotations

import pytest

from mediator import travel_check

GURGAON = {"location": "Sector 29 Crossing", "lat": 28.4601, "lon": 77.0648}
AGRA = {"location": "Yamuna Expressway Toll, Agra", "lat": 27.1767, "lon": 78.0081}


def at(place: dict, when: str) -> dict:
    return {**place, "at": when}


# ------------------------------------------------------------------ distance

def test_haversine_matches_the_known_gurgaon_agra_distance():
    km = travel_check.haversine_km(28.4601, 77.0648, 27.1767, 78.0081)
    assert 165 <= km <= 180


def test_the_same_point_is_zero_km():
    assert travel_check.haversine_km(28.46, 77.06, 28.46, 77.06) == pytest.approx(0.0, abs=1e-6)


# ------------------------------------------------------------------ legs

def test_thirty_minutes_between_gurgaon_and_agra_is_impossible():
    legs = travel_check.legs([at(GURGAON, "2026-09-04T11:00:00"), at(AGRA, "2026-09-04T11:30:00")])
    assert len(legs) == 1
    leg = legs[0]
    assert leg.impossible is True
    assert leg.kmh > 300 and leg.minutes == pytest.approx(30.0)
    assert leg.from_loc == GURGAON["location"] and leg.to_loc == AGRA["location"]


def test_three_hours_for_the_same_journey_is_plausible():
    legs = travel_check.legs([at(GURGAON, "2026-09-04T11:00:00"), at(AGRA, "2026-09-04T14:00:00")])
    assert legs[0].impossible is False
    assert travel_check.impossible_legs([at(GURGAON, "2026-09-04T11:00:00"),
                                         at(AGRA, "2026-09-04T14:00:00")]) == []


def test_sightings_are_sorted_before_they_are_paired():
    out_of_order = [at(AGRA, "2026-09-04T11:30:00"), at(GURGAON, "2026-09-04T11:00:00")]
    assert travel_check.legs(out_of_order)[0].from_loc == GURGAON["location"]


def test_a_sighting_without_coordinates_is_skipped_not_guessed():
    no_coords = {"location": "Unmapped camera", "lat": None, "lon": None,
                 "at": "2026-09-04T11:15:00"}
    legs = travel_check.legs([at(GURGAON, "2026-09-04T11:00:00"), no_coords,
                              at(AGRA, "2026-09-04T11:30:00")])
    assert len(legs) == 1 and legs[0].to_loc == AGRA["location"]


def test_two_readings_at_the_same_instant_yield_no_speed():
    assert travel_check.legs([at(GURGAON, "2026-09-04T11:00:00"),
                              at(AGRA, "2026-09-04T11:00:00")]) == []


def test_an_unparseable_timestamp_is_dropped_rather_than_raising():
    assert travel_check.legs([at(GURGAON, "not a date"), at(AGRA, "2026-09-04T11:30:00")]) == []


def test_a_single_sighting_has_no_legs():
    assert travel_check.legs([at(GURGAON, "2026-09-04T11:00:00")]) == []


def test_the_plausible_speed_is_tunable():
    fast = [at(GURGAON, "2026-09-04T11:00:00"), at(AGRA, "2026-09-04T14:00:00")]
    assert travel_check.impossible_legs(fast, plausible_kmh=40.0)
