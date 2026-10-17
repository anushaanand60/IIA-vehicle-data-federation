"""RDR engine tests, built on the Week 8 deck's own worked example (slides 30-32).

The deck labels each token of "H171 Valecha Apartments Saki Naka Mumbai" by walking a chain of
rules (Door No., Building Name, Building Type, Area Name, Area Type, District). With the area
written as one word, "Sakinaka", no rule classifies it (slide 31: "Sakinaka not classified as Area
name or Type. Suggestion: add a rule"), and slide 32 adds "if last 4 characters belong to Area Type".
"""
from __future__ import annotations

import json

import pytest

from mediator.rdr import RDRConflict, RDRTree, Rule, holds

LEXICONS = {"building_types": ["Apartments", "Towers", "Bunglow"],
            "area_types": ["Naka", "Nagar", "Vihar"],
            "districts": ["Mumbai", "Delhi", "Calcutta"]}


def token_cases(address: str):
    tokens = address.split()
    return [{"token": t, "left": tokens[i - 1] if i else None,
             "right": tokens[i + 1] if i + 1 < len(tokens) else None}
            for i, t in enumerate(tokens)]


def deck_tree() -> RDRTree:
    """Slide 30's chain, hung under the slide 26 default rule ``if true then normal``."""
    cases = token_cases("H171 Valecha Apartments Saki Naka Mumbai")
    chain = [
        ("R1", {"all": [{"field": "token", "op": "contains_digit"}, {"field": "left", "op": "is_null"}]},
         "Door No.", cases[0]),
        ("R2", {"field": "right", "op": "in_lexicon", "value": "building_types"}, "Building Name", cases[1]),
        ("R3", {"field": "token", "op": "in_lexicon", "value": "building_types"}, "Building Type", cases[2]),
        ("R4", {"field": "right", "op": "in_lexicon", "value": "area_types"}, "Area Name", cases[3]),
        ("R5", {"field": "token", "op": "in_lexicon", "value": "area_types"}, "Area Type", cases[4]),
        ("R6", {"field": "token", "op": "in_lexicon", "value": "districts"}, "District", cases[5]),
    ]
    rules = [Rule(rid, cond, {"label": label}, because=f"slide 30: {label}", cornerstone=case)
             for rid, cond, label, case in chain]
    for a, b in zip(rules, rules[1:]):
        a.if_false = b
    root = Rule("R0", {"true": True}, {"label": "unclassified"}, because="slide 26 default rule",
                cornerstone={"token": "", "left": None, "right": None}, except_=rules[0])
    return RDRTree(root, LEXICONS)


def labels(tree: RDRTree, address: str):
    return [tree.evaluate(c)[0]["label"] for c in token_cases(address)]


def test_deck_example_slide_30_classifies_every_token():
    assert labels(deck_tree(), "H171 Valecha Apartments Saki Naka Mumbai") == [
        "Door No.", "Building Name", "Building Type", "Area Name", "Area Type", "District"]


def test_slide_31_sakinaka_is_not_classified():
    assert labels(deck_tree(), "H171 Valecha Apartments Sakinaka Mumbai")[3] == "unclassified"


def test_slide_32_new_rule_on_the_false_branch_classifies_sakinaka():
    tree = deck_tree()
    case = token_cases("H171 Valecha Apartments Sakinaka Mumbai")[3]
    new = tree.add_exception(case, "R6", {"field": "token", "op": "suffix_in_lexicon",
                                          "value": "area_types", "n": 4},
                             {"label": "Area Name"}, "slide 32: last 4 characters are an area type",
                             branch="false")
    assert tree.find("R6").if_false is new
    assert labels(tree, "H171 Valecha Apartments Sakinaka Mumbai")[3] == "Area Name"
    # and the original address is untouched
    assert labels(tree, "H171 Valecha Apartments Saki Naka Mumbai")[3] == "Area Name"


def test_evaluate_returns_the_full_path_in_execution_order():
    conclusion, path = deck_tree().evaluate(token_cases("H171 Valecha Apartments Saki Naka Mumbai")[2])
    assert conclusion["label"] == "Building Type" and conclusion["rule"] == "R3"
    assert [(p["rule"], p["fired"]) for p in path] == [("R0", True), ("R1", False), ("R2", False),
                                                      ("R3", True)]


def test_exception_is_added_under_the_rule_that_fired_wrongly():
    tree = deck_tree()
    # "Saki" in "Saki Naka" is an area name, but "Andheri" before "Naka" here is a landmark
    case = {"token": "Andheri", "left": "Near", "right": "Naka"}
    assert tree.evaluate(case)[0]["rule"] == "R4"
    new = tree.add_exception(case, "R4", {"field": "left", "op": "eq", "value": "Near"},
                             {"label": "Landmark"}, "a token after 'Near' is a landmark (slide 23)")
    assert tree.find("R4").except_ is new
    assert tree.evaluate(case)[0] == {"label": "Landmark", "rule": new.id}
    assert tree.evaluate(token_cases("H171 Valecha Apartments Saki Naka Mumbai")[3])[0]["label"] == "Area Name"


def test_cornerstone_protection_refuses_an_over_general_rule():
    tree = deck_tree()
    case = {"token": "Andheri", "left": "Near", "right": "Naka"}
    before = json.dumps(tree.to_dict(), sort_keys=True)
    with pytest.raises(RDRConflict, match="R4"):
        # "right token is an area type" also catches R4's own cornerstone, "Saki"
        tree.add_exception(case, "R4", {"field": "right", "op": "in_lexicon", "value": "area_types"},
                           {"label": "Landmark"}, "over-general")
    assert json.dumps(tree.to_dict(), sort_keys=True) == before  # refused means unchanged


def test_condition_must_hold_for_the_case_it_fixes():
    tree = deck_tree()
    with pytest.raises(RDRConflict, match="not satisfied"):
        tree.add_exception({"token": "Andheri", "left": "Near", "right": "Naka"}, "R4",
                           {"field": "left", "op": "eq", "value": "Opposite"}, {"label": "X"}, "")


def test_except_requires_the_named_rule_to_have_fired():
    tree = deck_tree()
    case = {"token": "Andheri", "left": "Near", "right": "Naka"}
    with pytest.raises(RDRConflict, match="did not fire"):
        tree.add_exception(case, "R2", {"field": "left", "op": "eq", "value": "Near"}, {"label": "X"}, "")


def test_a_rule_shadowed_by_an_earlier_sibling_is_refused():
    tree = deck_tree()
    case = {"token": "Andheri", "left": "Near", "right": "Naka"}
    tree.add_exception(case, "R4", {"field": "left", "op": "eq", "value": "Near"}, {"label": "Landmark"}, "")
    with pytest.raises(RDRConflict, match="would never fire"):
        tree.add_exception(case, "R4", {"field": "token", "op": "eq", "value": "Andheri"},
                           {"label": "Area Name"}, "shadowed by the Near rule")


def test_round_trip_through_json(tmp_path):
    tree = deck_tree()
    path = tmp_path / "rules.json"
    tree.save(path)
    again = RDRTree.load(path, LEXICONS)
    assert again.to_dict() == tree.to_dict()
    assert labels(again, "H171 Valecha Apartments Saki Naka Mumbai")[0] == "Door No."


def test_condition_language():
    case = {"a": 1, "b": "x", "c": None, "d": True}
    assert holds({"all": [{"field": "a", "op": "ge", "value": 1}, {"field": "b", "op": "in", "value": ["x"]}]}, case)
    assert holds({"any": [{"field": "c", "op": "eq", "value": 1}, {"field": "d", "op": "is_true"}]}, case)
    assert holds({"not": {"field": "c", "op": "not_null"}}, case)
    assert not holds({"field": "c", "op": "ne", "value": 1}, case)  # a missing value never compares
    assert holds({"field": "b", "op": "eq", "value": "$b"}, case)
    with pytest.raises(ValueError):
        holds({"field": "a", "op": "approximately"}, case)
    with pytest.raises(ValueError):
        holds({"op": "eq"}, case)
