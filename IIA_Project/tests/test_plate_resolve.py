"""Confusable-character plate identity resolution (Challan Guard step 2).

The lookup is injected, so these tests never touch a wrapper: they pin the *policy* (which
candidates are generated, how they are scored, when the guard is allowed to rewrite a plate),
which is exactly the part a citizen would challenge in a dispute.
"""
from __future__ import annotations

from mediator import plate_resolve

# REG profiles, keyed by canonical plate. Anything absent is "not registered".
WORLD = {
    "DL05CD9876": {"vehicle_make": "Maruti Suzuki", "vehicle_colour": "Silver",
                   "registration_status": "ACTIVE"},
    "DL05CD9B76": {},  # the literal OCR read exists nowhere
}


def lookup(plate: str) -> dict:
    return dict(WORLD.get(plate) or {})


# ------------------------------------------------------------------ candidate generation

def test_the_read_itself_is_always_the_first_candidate():
    assert plate_resolve.candidates("DL05CD9B76")[0] == "DL05CD9B76"


def test_a_one_character_confusion_is_generated():
    assert "DL05CD9876" in plate_resolve.candidates("DL05CD9B76")


def test_candidates_are_canonical_unique_and_capped():
    cands = plate_resolve.candidates("dl-05-cd-9b76", limit=4)
    assert len(cands) == len(set(cands)) <= 4
    assert all(c.isalnum() and c.isupper() for c in cands)
    assert cands[0] == "DL05CD9B76"


def test_only_one_substitution_is_ever_applied():
    # DL05CD9B76 -> DL05CD9876 needs B->8; a second edit (0->O) must not appear as well.
    assert "DLO5CD9876" not in plate_resolve.candidates("DL05CD9B76", max_edits=1)


def test_an_empty_read_produces_nothing():
    assert plate_resolve.candidates("  ") == []


# ------------------------------------------------------------------ scoring

def test_score_requires_registration():
    cand = plate_resolve.score_candidate("KA05MN9999", "KA05MN9999", {}, "Kia", "Blue")
    assert cand.registered is False and cand.score == 0.0


def test_make_and_colour_agreement_add_up():
    cand = plate_resolve.score_candidate("DL05CD9876", "DL05CD9B76", WORLD["DL05CD9876"],
                                         "Maruti Suzuki", "Silver")
    assert cand.score == 0.8  # 0.5 make + 0.3 colour, no exact-read bonus
    assert any("make" in r for r in cand.reasons)


def test_the_exact_read_earns_its_own_bonus():
    cand = plate_resolve.score_candidate("DL05CD9876", "DL05CD9876", WORLD["DL05CD9876"], None, None)
    assert cand.score == 0.2


# ------------------------------------------------------------------ resolve()

def test_a_misread_resolves_when_the_vehicle_description_agrees():
    best, ranked, outcome = plate_resolve.resolve("DL05CD9B76", "Maruti Suzuki", "Silver", lookup)
    assert outcome == "RESOLVED"
    assert best is not None and best.plate == "DL05CD9876"
    assert ranked[0].plate == "DL05CD9876"


def test_two_equally_plausible_registered_candidates_are_ambiguous():
    # Both are one confusable edit from the read: B->8 and 5->S.
    twins = {"DL05CD9876": WORLD["DL05CD9876"],
             "DL0SCD9B76": {"vehicle_make": "Maruti Suzuki", "vehicle_colour": "Silver"}}
    best, ranked, outcome = plate_resolve.resolve(
        "DL05CD9B76", "Maruti Suzuki", "Silver", lambda p: dict(twins.get(p) or {}))
    assert outcome == "AMBIGUOUS" and len(ranked) == 2


def test_nothing_registered_means_no_identity():
    best, ranked, outcome = plate_resolve.resolve("KA05MN9999", "Kia", "Blue", lambda p: {})
    assert (best, ranked, outcome) == (None, [], "NONE")


def test_a_registered_read_always_resolves_to_itself_even_when_the_vehicle_looks_wrong():
    # UP16GH1122: REG says Hyundai/Red, the camera saw Kia/Blue. That is a *clone* signal, not an
    # identity failure -- the guard must still know which registration it is talking about.
    world = {"UP16GH1122": {"vehicle_make": "Hyundai", "vehicle_colour": "Red",
                            "registration_status": "ACTIVE"}}
    best, _, outcome = plate_resolve.resolve("UP16GH1122", "Kia", "Blue",
                                             lambda p: dict(world.get(p) or {}))
    assert outcome == "RESOLVED" and best.plate == "UP16GH1122"


def test_a_misread_with_no_vehicle_description_is_not_rewritten():
    best, _, outcome = plate_resolve.resolve("DL05CD9B76", None, None, lookup)
    assert outcome == "AMBIGUOUS" and best.plate == "DL05CD9876"


def test_lookup_failures_degrade_to_not_registered():
    def broken(_plate: str) -> dict:
        raise RuntimeError("INS wrapper exploded")

    assert plate_resolve.resolve("DL05CD9B76", "Maruti Suzuki", "Silver", broken)[2] == "NONE"
