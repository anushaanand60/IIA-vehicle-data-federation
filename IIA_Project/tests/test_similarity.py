"""
Unit tests for mediator/similarity.py.

Every "slide" test below encodes a worked example from the Week 6-8 deck
("Data and Text Matching"); the page is quoted in the comment next to it.
"""
from __future__ import annotations

import difflib

import pytest

from mediator import matcher, similarity
from mediator.plate_resolve import CONFUSABLE
from mediator.similarity import (
    ALL_MEASURES,
    edit_similarity,
    jaccard,
    jaro,
    jaro_winkler,
    levenshtein,
    ocr_confusion_cost,
    ocr_edit_similarity,
    overlap,
    overlap_count,
    qgrams,
    ratcliff_obershelp,
    soundex,
    soundex_similarity,
    tfidf_cosine,
    tfidf_measure,
    weighted_edit_distance,
)

PAIRS = [
    ("DL01AB1234", "DL01AB1234"),
    ("DL05CD9876", "DL05CD9B76"),
    ("DLO1AB1234", "DL01AB1234"),
    ("MH12IJ7788", "HR26EF4455"),
    ("dave", "dav"),
    ("jon", "john"),
]


# ----------------------------------------------------------------- edit distance (p. 16-19)

def test_edit_distance_slide_example_david_smiths():
    # p. 16-17: d("David Smiths", "Davidd Simth") = 4, s = 1 - 4/12 = 0.67
    assert levenshtein("David Smiths", "Davidd Simth") == 4
    assert edit_similarity("David Smiths", "Davidd Simth") == pytest.approx(0.67, abs=0.005)


def test_edit_distance_dp_table_slide_example():
    # p. 19: x = "dva", y = "dave"; the DP table's bottom-right cell is 2
    # (substitute a with e, insert a after d).
    assert levenshtein("dva", "dave") == 2


def test_edit_distance_vishwanathan_exercise():
    # p. 22: "Levenshtein: 4" for Vishwanathan vs Vishvnahtan
    assert levenshtein("Vishwanathan", "Vishvnahtan") == 4


def test_weighted_edit_distance_defaults_to_levenshtein():
    for a, b in PAIRS:
        assert weighted_edit_distance(a, b) == pytest.approx(float(levenshtein(a, b)))


def test_weighted_edit_distance_respects_the_callable():
    # A free substitution turns edit distance into a pure length difference.
    assert weighted_edit_distance("ABCD", "WXYZ", lambda a, b: 0.0) == pytest.approx(0.0)
    assert weighted_edit_distance("ABCD", "WXYZE", lambda a, b: 0.0) == pytest.approx(1.0)


# ----------------------------------------------------------------- OCR weighting (our table)

def test_ocr_confusion_cost_is_symmetric_and_sourced_from_plate_resolve():
    for a, options in CONFUSABLE.items():
        for b in options:
            assert ocr_confusion_cost(a, b) == pytest.approx(0.2)
            assert ocr_confusion_cost(b, a) == pytest.approx(0.2)
    assert ocr_confusion_cost("0", "7") == pytest.approx(1.0)
    assert ocr_confusion_cost("A", "A") == pytest.approx(0.0)


def test_ocr_similarity_beats_plain_edit_on_story_misread_8_for_b():
    # DL05CD9876 photographed as DL05CD9B76: 8 <-> B is a documented confusable.
    a, b = "DL05CD9B76", "DL05CD9876"
    assert ocr_edit_similarity(a, b) > edit_similarity(a, b)


def test_ocr_similarity_beats_plain_edit_on_story_misread_0_for_o():
    a, b = "DLO1AB1234", "DL01AB1234"
    assert ocr_edit_similarity(a, b) > edit_similarity(a, b)


def test_ocr_similarity_does_not_reward_a_non_confusable_substitution():
    a, b = "DL05CD9776", "DL05CD9876"  # 8 -> 7 is not in the table
    assert ocr_edit_similarity(a, b) == pytest.approx(edit_similarity(a, b))


# ----------------------------------------------------------------- Jaro / Jaro-Winkler (p. 20-22)

def test_jaro_slide_example_jon_john():
    # p. 21: jaro("jon", "john") = 1/3(3/3 + 3/4 + 3/3) = 0.917
    assert jaro("jon", "john") == pytest.approx(0.917, abs=0.001)


def test_jaro_slide_example_jon_ojhn_with_transpositions():
    # p. 21: jaro("jon", "ojhn") = 0.81 from c = 3 and t = 2 (common sequence "jon" in x
    # against "ojn" in y). The deck rounds 1/3(3/3 + 3/4 + (3 - 1)/3) = 0.8056 to 0.81.
    assert jaro("jon", "ojhn") == pytest.approx(0.8056, abs=0.0005)


def test_jaro_and_winkler_slide_example_vishwanathan():
    # p. 22: Jaro 0.847, Jaro-Winkler 0.9082 with PW = 0.1 and a 4-character common prefix.
    assert jaro("Vishwanathan", "Vishvnahtan") == pytest.approx(0.847, abs=0.001)
    assert jaro_winkler("Vishwanathan", "Vishvnahtan") == pytest.approx(0.9082, abs=0.001)


def test_jaro_winkler_prefix_bonus_is_capped_at_four_characters():
    base = jaro("DL01AB1234", "DL01AB9999")
    jw = jaro_winkler("DL01AB1234", "DL01AB9999")
    assert jw == pytest.approx(base + 4 * 0.1 * (1 - base))


def test_matcher_uses_this_modules_jaro_winkler():
    # One implementation only: matcher.py imports it from here.
    assert matcher.jaro_winkler is similarity.jaro_winkler


# ----------------------------------------------------------------- set based (p. 24-26)

def test_qgrams_slide_example_padding():
    # p. 24-25: Bx for "dave" is {#d, da, av, ve, e#}
    assert qgrams("dave") == ["#D", "DA", "AV", "VE", "E#"]
    assert qgrams("dav") == ["#D", "DA", "AV", "V#"]
    assert qgrams("dave", pad=False) == ["DA", "AV", "VE"]
    assert len(qgrams("dave", pad=False)) == len("dave") - 2 + 1


def test_overlap_slide_example():
    # p. 25: O("dave", "dav") = 3
    assert overlap_count("dave", "dav") == 3
    # normalised for the harness: 3 / min(5, 4)
    assert overlap("dave", "dav") == pytest.approx(3 / 4)


def test_jaccard_slide_example():
    # p. 26: J("dave", "dav") = 3/6
    assert jaccard("dave", "dav") == pytest.approx(0.5)


# ----------------------------------------------------------------- TF/IDF (p. 27-31)

def test_tfidf_slide_worked_example_is_zero():
    # p. 29-30: "Data Mining Techniques" vs "Database Mining Methods" over a two-document
    # collection -> cosine 0.0, because the only shared term ("mining") has idf log(2/2) = 0.
    a, b = "Data Mining Techniques", "Database Mining Methods"
    assert tfidf_cosine(a, b, [a, b], q=None) == pytest.approx(0.0, abs=1e-9)


def test_tfidf_prefers_the_distinguishing_term():
    # p. 27 motivation: x = "Apple Corporation CA", y = "IBM Corporation CA", z = "Apple
    # Corporation". TF/IDF must score (x, z) above (x, y) because "Apple" is distinguishing.
    x = "Apple Corporation CA"
    y = "IBM Corporation CA"
    z = "Apple Corporation"
    corpus = [x, y, z, "Oracle Corporation CA", "Infosys Corporation CA"]
    assert tfidf_cosine(x, z, corpus, q=None) > tfidf_cosine(x, y, corpus, q=None)


def test_tfidf_measure_factory_is_a_two_argument_callable():
    corpus = ["DL01AB1234", "DL05CD9876", "HR26EF4455"]
    m = tfidf_measure(corpus)
    assert m("DL01AB1234", "DL01AB1234") == pytest.approx(1.0)
    assert 0.0 <= m("DL01AB1234", "HR26EF4455") <= 1.0


# ----------------------------------------------------------------- Ratcliff-Obershelp (p. 33-34)

def test_ratcliff_obershelp_slide_example_mathematics():
    # p. 34: MATHEMATICS vs MATEMATICA -> 2*9/(11+10) = 0.857
    assert ratcliff_obershelp("MATHEMATICS", "MATEMATICA") == pytest.approx(0.857, abs=0.001)


def test_ratcliff_obershelp_slide_example_mohania():
    # p. 34 second example, no score given in the deck; agreement with the reference
    # implementation is the check.
    s1, s2 = "MKUESH MOHANIA", "MUKHESH MOHANIYA"
    assert ratcliff_obershelp(s1, s2) == pytest.approx(
        difflib.SequenceMatcher(None, s1, s2).ratio(), abs=1e-9
    )


def test_ratcliff_obershelp_agrees_with_difflib_on_plates():
    for a, b in PAIRS:
        assert ratcliff_obershelp(a, b) == pytest.approx(
            difflib.SequenceMatcher(None, a.upper(), b.upper()).ratio(), abs=1e-9
        )


# ----------------------------------------------------------------- Soundex (p. 36-37)

def test_soundex_slide_example_banarjee():
    # p. 37: Banarjee -> B562, Benerji -> B562
    assert soundex("Banarjee") == "B562"
    assert soundex("Benerji") == "B562"
    assert soundex_similarity("Banarjee", "Benerji") == 1.0


def test_soundex_padding_and_w_h_removal():
    assert soundex("Lee") == "L000"          # not enough digits -> padded with 0
    assert soundex("Ashcraft") == "A261"     # W/H removed before collapsing (p. 37 step 2)


def test_soundex_is_useless_on_plates_as_predicted():
    # The deck's measure is for surnames (p. 36); plates are not phonetic and Soundex throws
    # the digits away entirely. Both failure directions, documented rather than fixed:
    # a false positive -- two different vehicles share the code D410 ...
    assert soundex("DL01AB1234") == soundex("DL01AB9999") == "D410"
    assert soundex_similarity("DL01AB1234", "DL01AB9999") == 1.0
    # ... and a false negative -- the 8/B misread is invisible to it, but a different series
    # letter makes it disagree even though the two plates differ by one character.
    assert soundex_similarity("DL05CD9B76", "DL05CD9876") == 1.0
    assert soundex_similarity("DL01AB1234", "DL01AC1234") == 0.0


# ----------------------------------------------------------------- properties

@pytest.mark.parametrize("name,fn", sorted(ALL_MEASURES.items()))
def test_identity_is_one(name, fn):
    for s in ("DL01AB1234", "a", "Mukesh Mohania"):
        assert fn(s, s) == pytest.approx(1.0)


@pytest.mark.parametrize("name,fn", sorted(ALL_MEASURES.items()))
def test_symmetry(name, fn):
    for a, b in PAIRS:
        assert fn(a, b) == pytest.approx(fn(b, a))


@pytest.mark.parametrize("name,fn", sorted(ALL_MEASURES.items()))
def test_range_is_unit_interval(name, fn):
    for a, b in PAIRS + [("", ""), ("", "X"), ("X", "")]:
        assert 0.0 <= fn(a, b) <= 1.0


@pytest.mark.parametrize("name,fn", sorted(ALL_MEASURES.items()))
def test_empty_string_rules(name, fn):
    assert fn("", "") == pytest.approx(1.0)
    assert fn("", "DL01AB1234") == pytest.approx(0.0)
    assert fn("DL01AB1234", "") == pytest.approx(0.0)


@pytest.mark.parametrize("name,fn", sorted(ALL_MEASURES.items()))
def test_case_folding(name, fn):
    assert fn("dl01ab1234", "DL01AB1234") == pytest.approx(1.0)


def test_tfidf_identity_holds_on_a_degenerate_corpus():
    # With a one-document collection every idf is log(1/1) = 0, so both vectors are zero;
    # identity must still be 1.0 for the harness to compare measures fairly.
    assert tfidf_cosine("DL01AB1234", "DL01AB1234", ["DL01AB1234"]) == pytest.approx(1.0)


def test_tfidf_obeys_the_same_empty_rules():
    corpus = ["DL01AB1234", "DL05CD9876"]
    assert tfidf_cosine("", "", corpus) == pytest.approx(1.0)
    assert tfidf_cosine("", "DL01AB1234", corpus) == pytest.approx(0.0)
