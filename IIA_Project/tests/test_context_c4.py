"""PHASE3-ADDED (C4): the observed car moves the open-world probability, and cannot rescue an implausible read.

C3 multiplied the stacked model's p (saturated near 1) by p(held), so make, model and colour no longer moved
the probability shown. C4 turns each observed attribute into a likelihood ratio on the odds of p(held), and
keeps an implausible read (p_unknown >= 0.5) from being lifted to an automatic ACCEPT by the car's look."""
from __future__ import annotations

import pytest

from mediator import plate_match as pm

AGREE = dict(observed_make="Maruti Suzuki", observed_model="Swift", observed_colour="Silver")
DIFFER = dict(observed_make="Honda", observed_colour="Red")


@pytest.fixture(scope="module")
def reg():
    return pm.load_registry()


def _top(read, registry, **context):
    return pm.match(read, confidence=0.8, registry=registry, **context)[0][0]


def test_context_moves_the_probability_both_ways(reg):
    none, agree, differ = (_top("DL05CD9B76", reg, **kw) for kw in ({}, AGREE, DIFFER))
    assert agree.probability > none.probability > differ.probability
    assert agree.decision == "ACCEPT" and differ.decision == "REVIEW"


def test_the_examiners_state_code_case_stays_review_without_corroboration(reg):
    assert _top("0L01AB1234", reg).decision == "REVIEW"
    assert _top("0L05CD9876", reg, **AGREE).decision == "REVIEW"   # REG alone holds it: not corroborated


def test_an_implausible_read_is_not_lifted_by_the_car(reg):
    multi = {p: {**v, "sources": ["REG", "INS", "PUC"], "prior": 1.0} for p, v in reg.items()}
    top = _top("XX05CD9876", multi, **AGREE)
    assert top.decision == "REVIEW" and top.probability < 0.5


def test_unknown_context_changes_nothing():
    c = pm.Candidate("DL05CD9876", {}, {"make_agree": 0.0, "colour_agree": 0.0}, 0.9, "")
    assert pm.context_ratio(c, {"make": "Maruti Suzuki"}, {"make": {"maruti suzuki": 0.2}})[0] == 1.0
