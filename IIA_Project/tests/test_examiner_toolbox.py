"""The examiner toolbox (registrations and camera records, each with an undo) and the Data Cleansing
page's plain words: rule lines, anomaly names, the camera-vs-registration table and the suggested
resolution. The pure functions run without sources; the write tests use the shared `local_cluster`."""
from __future__ import annotations

import sys
from pathlib import Path

_APP = Path(__file__).resolve().parents[1] / "app"
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))

from tabs import cleansing_words as words  # noqa: E402
from tabs import examiner_toolbox as ex  # noqa: E402
from tabs.cleansing_tab import compare_rows  # noqa: E402


def test_noisy_read_swaps_one_lookalike_digit():
    assert ex.noisy_read("DL05CD9876") == "DL05CD987G"
    assert ex.noisy_read("DL09ZZ4321") == "DL09ZZ432I"
    assert ex.noisy_read("DL09ZZ4347") == "DLO9ZZ4347"  # the last look-alike digit, wherever it is
    assert ex.noisy_read("DL47ZZ4347") == "DL47ZZ4347"  # nothing to swap: returned unchanged


def test_every_rule_in_the_tree_has_plain_words_without_slide_references():
    assert set(words.RULE_WORDS) == {f"R{i}" for i in range(13)}
    for rule in words.RULE_WORDS:
        assert "slide" not in words.rule_line(rule).lower()


def test_anomaly_codes_read_as_words():
    assert words.anomaly_words("MAKE_DISAGREES_REGISTRATION") == "make differs from registration"
    assert words.anomaly_words("COLOUR_NOT_IN_LEXICON") == "colour misspelt"
    assert words.anomaly_words("PLATE_NOT_REGISTERED") == "plate not registered"


def test_suggestion_fits_the_open_issue():
    cands = [{"plate": "DL05CD9876", "probability": 0.93}]
    assert words.suggest(["MAKE_DISAGREES_REGISTRATION"], [])[0] == "confirm_clone"
    assert words.suggest(["PLATE_NOT_REGISTERED"], cands)[0] == "accept_candidate"
    assert words.suggest(["PLATE_NOT_REGISTERED"], [])[0] == "delete_record"
    assert words.suggest(["DUPLICATE_CAPTURE"], [])[0] == "delete_record"
    assert words.suggest(["LOW_OCR_CONFIDENCE"], [])[0] == "dismiss"
    assert words.suggest([], cands) is None


def test_compare_rows_shows_the_registration_next_to_the_camera():
    record = {"plate_number": "UP16AB0405", "observed_make": "Honda", "observed_model": "City", "observed_colour": "White"}
    profile = {"plate_number": "UP16AB0405", "vehicle_make": "Mahindra", "vehicle_model": "XUV700", "vehicle_colour": "White"}
    rows = {r["Field"]: r for r in compare_rows(record, profile, None, {}, ["MAKE_DISAGREES_REGISTRATION",
                                                                          "MODEL_DISAGREES_REGISTRATION"])}
    assert rows["Make"]["Registered as"] == "Mahindra" and rows["Make"]["Check"] == "differs"
    assert rows["Colour"]["Check"] == "same"
    unreg = compare_rows({"plate_number": "DL05GHO258"}, {}, "DL05GH0258", {"vehicle_make": "Maruti Suzuki"}, [])
    assert unreg[0]["Registered as"] == "not registered (closest: DL05GH0258)"


def test_registration_writes_reach_the_matcher_and_undo_restores_them(local_cluster):
    from mediator import plate_match
    from scripts.match_plate import live_registry

    change = ex.register("DL09ZZ4321", "Hyundai", "i20", "Red")
    try:
        registry, _ = live_registry()
        cands, _ = plate_match.match(change["read"], confidence=0.8, observed_make="Hyundai", observed_colour="Red",
                                     registry=registry)
        assert cands[0].plate == "DL09ZZ4321" and cands[0].decision == "ACCEPT"
    finally:
        ex.undo(change)
    assert "DL09ZZ4321" not in live_registry()[0]

    before = live_registry()[0]["DL05CD9876"]
    change = ex.delete_registration("DL05CD9876")
    try:
        assert "DL05CD9876" not in live_registry()[0]
    finally:
        ex.undo(change)
    assert live_registry()[0]["DL05CD9876"] == before


def test_camera_modify_undo_puts_the_old_value_back(local_cluster):
    from mediator import cleansing

    rid = str(cleansing.Context.build().records[0]["record_id"])
    change = ex.modify_camera_record(rid, "observed_colour", "Gry")
    assert cleansing.Context.build().record(rid)["observed_colour"] == "Gry"
    ex.undo(change)
    assert cleansing.Context.build().record(rid)["observed_colour"] != "Gry"
