"""PHASE3-ADDED (C3: matching re-evaluated): the open-world check and how plate_match uses it."""
from __future__ import annotations

import pytest

from mediator import open_world as ow
from mediator import plate_match as pm


@pytest.mark.parametrize("plate, valid", [
    ("DL01AB1234", True), ("KL48S5665", True), ("22BH1234AA", True),
    ("0L01AB1234", False), ("XX01AB1234", False), ("DL01AB12", False)])
def test_plate_validity_uses_the_official_state_codes(plate, valid):
    assert ow.is_valid(plate) is valid


def test_a_cleaner_read_has_a_cleaner_channel():
    assert ow.channel(0.95).eps < ow.channel(0.7).eps < ow.channel(0.2).eps


def test_the_sum_over_all_plates_contains_each_plate():
    assert ow.all_valid_mass("DL01AB1234") >= ow.likelihood("DL01AB1234", "DL01AB1234")


def test_an_exact_read_of_a_held_plate_is_trusted():
    assert ow.posterior("DL01AB1234", ["DL01AB1234"], confidence=0.95).probabilities["DL01AB1234"] > 0.9


def test_a_plate_one_digit_from_a_held_one_is_probably_another_car():
    res = ow.posterior("DL01AB1235", ["DL01AB1234"], confidence=0.95)
    assert res.probabilities["DL01AB1234"] < 0.05 and res.unknown > 0.9


def test_an_unreadable_state_code_could_be_any_state():
    assert ow.posterior("XX01AB1234", ["DL01AB1234"], confidence=0.95).probabilities["DL01AB1234"] < 0.5


def test_a_truncated_read_is_not_one_plate():
    assert ow.posterior("DL01AB12", ["DL01AB1234"], confidence=0.95).probabilities["DL01AB1234"] < 0.1


def test_a_weak_source_prior_lowers_the_probability():
    strong = ow.posterior("DL05CD9B76", ["DL05CD9876"]).probabilities["DL05CD9876"]
    weak = ow.posterior("DL05CD9B76", ["DL05CD9876"], priors={"DL05CD9876": 0.3}).probabilities["DL05CD9876"]
    assert weak < strong


def test_corrections_name_the_state_code():
    (fix,) = ow.corrections("0L01AB1234", "DL01AB1234")
    assert (fix.kind, fix.read, fix.plate, fix.position, fix.in_state_code) == ("swap", "0", "D", 1, True)
    assert "state code" in fix.words()
    assert ow.corrections("DL01AB1234", "DL01AB1234") == []


# ------------------------------------------------------------------ inside plate_match

REG = {"DL01AB1234": {"make": "Hyundai", "model": "Creta", "colour": "White"},
       "DL01AB0123": {"make": "Tata", "model": "Nexon", "colour": "Red"},
       "DL05CD9876": {"make": "Honda", "model": "City", "colour": "Grey"}}


def test_the_examiners_read_is_no_longer_certain_or_accepted():
    top = pm.match("0L01AB1234", registry=REG)[0][0]
    assert top.plate == "DL01AB1234"
    assert top.probability < 1.0  # PHASE3-REPLACED (C3 follow-up): was < 0.9; the decision is the point
    assert top.decision == "REVIEW"
    assert any("state code" in r for r in top.reasons)
    assert top.evidence["model_p"] > top.probability  # the learned model alone was surer


def test_before_the_check_the_examiners_read_was_accepted():
    top = pm.match("0L01AB1234", registry=REG, open_world_check=False)[0][0]
    assert top.decision == "ACCEPT" and "p_unknown" not in top.evidence


def test_an_exact_read_is_still_accepted():
    top = pm.match("DL01AB1234", confidence=0.95, registry=REG)[0][0]
    assert top.plate == "DL01AB1234" and top.decision == "ACCEPT" and top.probability < 1.0


def test_an_unknown_neighbour_is_not_accepted_as_the_registered_car():
    top = pm.match("DL01AB1235", confidence=0.95, registry=REG)[0][0]
    assert top.decision != "ACCEPT"


def test_other_databases_and_the_photo_can_confirm_a_state_code_correction():
    reg = dict(REG)
    reg["DL01AB1234"] = {**REG["DL01AB1234"], "sources": ["REG", "INS", "PUC"], "registered": True, "prior": 1.0}
    top = pm.match("0L01AB1234", observed_make="Hyundai", observed_colour="White", registry=reg)[0][0]
    assert top.decision == "ACCEPT"
    assert any("corroborated" in r for r in top.reasons)


def test_one_source_is_not_corroboration():
    top = pm.match("0L01AB1234", observed_make="Hyundai", observed_colour="White", registry=REG)[0][0]
    assert top.decision == "REVIEW"


# ------------------------------------------------------------------ PHASE3-ADDED (C3 follow-up: learned extra / missing characters)

def test_the_plate_border_read_as_a_trailing_1_is_accepted():
    top = pm.match("HR26AB05501", confidence=0.789)[0][0]  # the registry on this machine, as the page uses
    assert top.plate == "HR26AB0550" and top.decision == "ACCEPT"
    assert top.evidence["corrections"] == ["extra 1 at the end"]


def test_an_extra_character_before_the_plate_is_no_state_code_correction():
    (fix,) = ow.corrections("IDL01AB1234", "DL01AB1234")
    assert fix.at_start and not fix.in_state_code and fix.words() == "extra I at the start"


def test_a_trailing_1_is_likelier_than_any_other_extra_character():
    ch = ow.channel(0.8)
    assert ch.ins("1", "end") > 10 * ch.ins("Q", "end") and ch.ins("1", "end") > ch.ins("1", "middle")


def test_unknown_plates_take_the_shapes_of_the_held_ones():
    w = ow.shape_weights(["DL01AB1234", "HR26EF4455", "UP16GH1122"])
    assert w["civilian2"] > w["civilian3"] == w["civilian1"] > 0
    assert ow.shape_weights([]) == dict.fromkeys(ow.SHAPES, 1.0)
