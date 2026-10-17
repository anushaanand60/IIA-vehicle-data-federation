"""Tests for mediator/blocking.py.

Two things are being proved here:

1. that the implementation reproduces the course deck's own worked example (Week 8,
   slides 1-7, which recaps Week 6-8 p. 47-65 on the Jeevan/Agra address sets), and
2. that on real data no filter ever loses a true match -- a blocking pass that drops the
   right vehicle cannot be rescued by step-2 scoring.
"""
from __future__ import annotations

import random
import sqlite3
from pathlib import Path
from typing import List

import pytest

from mediator.blocking import (BlockingStats, QGramIndex, candidate_plates,
                               sql_prefilter)
from mediator.plate_resolve import CONFUSABLE
from mediator.transforms import norm_plate

REG_DB = Path(__file__).resolve().parents[1] / "sources" / "reg" / "reg.db"
STRATEGIES = ["index", "size", "prefix", "position"]


# --------------------------------------------------------------- the deck's worked example

def words(s: str, q: int = 2) -> List[str]:
    """Whitespace tokenizer, so the deck's word-token sets can be fed to the index."""
    return s.split()


# Week 8, slide 1. X is the query side, Y the indexed side.
X1 = "Block Jeevan Cinema Old Agra"
X3 = "Block Jeevan Cinema Agra"
X4 = "Building Ravi Vihar New Delhi"
X5 = "Block Jeevan Vihar New Delhi"
X6 = "Mandir Ravi Vihar Delhi"
Y = [X3, X4, X5, X6]


@pytest.fixture(scope="module")
def deck_index() -> QGramIndex:
    return QGramIndex.build(Y, tokenizer=words)


def test_deck_inverted_index(deck_index):
    """Week 8 slide 2: candidate set {3,5}+{3,5}+{3}+{3} = {x3, x5}."""
    assert deck_index.candidates(X1) == {X3, X5}


def test_deck_size_window():
    """Week 8 slide 3: |x|=5, t=0.9  =>  5.5 >= |y| >= 4.5, so only the 5-token sets."""
    lo, hi = QGramIndex.jaccard_size_window(5, 0.9)
    assert round(lo, 2) == 4.5 and round(hi, 2) == 5.56
    assert [y for y in Y if lo <= len(y.split()) <= hi] == [X4, X5]


def test_deck_prefix_filter_unordered(deck_index):
    """Week 8 slide 4: O(x,y) >= 3, prefix x1' of size |x|-(k-1) = 3.

    Slide 4 probes the *full* inverted index with the prefix of x only, so the candidate
    set is {x3, x5}: both contain a token of x1' = the three first tokens of x1.
    """
    # Slide 4 comes before the global order of slide 5, so x1' is simply the first three
    # tokens as the slide lists them: {Block, Jeevan, Cinema}.
    px = set(X1.split()[:5 - (3 - 1)])
    assert px == {"Block", "Jeevan", "Cinema"}
    hit = {y for y in Y if px & set(y.split())}
    assert hit == {X3, X5}


def test_deck_prefix_inverted_index(deck_index):
    """Week 8 slide 6: with the global order and y-side prefixes too, C for x1 is {x3}.

    The deck's order (slide 5) is taken over X u Y; ours is over the indexed collection Y,
    as p. 58 prescribes. Both give x1 = {Old, Agra, Cinema, Block, Jeevan} and the same
    candidate set.
    """
    assert deck_index._query_tokens(X1) == ["Old", "Agra", "Cinema", "Block", "Jeevan"]
    assert deck_index.candidates_prefix_filtered(X1, k=3) == {X3}


def test_deck_position_index(deck_index):
    """Week 8 slide 7 / p. 65: max overlap = prefix overlap + min unseen = 2 + min(2,2) = 4.

    The slide then rejects the pair against O(x,y) > 4, i.e. a required overlap of 5.
    """
    assert deck_index.max_overlap_bound(X1, X3, k=3) == 4
    assert deck_index.candidates_position_filtered(X1, k=3) == {X3}
    assert deck_index.candidates_position_filtered(X1, k=5) == set()


def test_global_order_is_increasing_document_frequency(deck_index):
    """p. 57: rare tokens first. Over Y: Agra/Building/Cinema/Mandir 1, ... Delhi/Vihar 3."""
    order = [t for t in sorted(deck_index.df, key=deck_index._order_key)]
    freqs = [deck_index.df[t] for t in order]
    assert freqs == sorted(freqs)
    assert order[-2:] == ["Delhi", "Vihar"]


# ------------------------------------------------------------------- real-data recall

@pytest.fixture(scope="module")
def plates() -> List[str]:
    con = sqlite3.connect(f"file:{REG_DB}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT registration_no FROM VEHICLE_REGISTRATION").fetchall()
    finally:
        con.close()
    return sorted({norm_plate(r[0]) for r in rows})


@pytest.fixture(scope="module")
def plate_index(plates) -> QGramIndex:
    return QGramIndex.build(plates)


def corruptions(plate: str) -> List[str]:
    """Every single-character OCR confusion of `plate`, from plate_resolve.CONFUSABLE."""
    out = []
    for i, ch in enumerate(plate):
        for alt in CONFUSABLE.get(ch, ""):
            out.append(plate[:i] + alt + plate[i + 1:])
    return out


@pytest.fixture(scope="module")
def sample(plates) -> List[str]:
    return random.Random(7).sample(plates, min(200, len(plates)))


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_recall_on_single_character_ocr_corruptions(strategy, plates, plate_index, sample):
    """No filter may lose the true plate for a one-confusion read.

    Each bound is used with max_edits=1, which is exactly the noise this test injects, so
    recall must be 1.0 -- not "high". A failure here is a bug in the bound, not a tuning
    knob.
    """
    for truth in sample:
        for read in corruptions(truth):
            cands, _ = candidate_plates(read, plates, strategy=strategy,
                                        index=plate_index)
            assert truth in cands, f"{strategy} lost {truth} for read {read!r}"


def test_exact_read_is_always_retrieved(plates, plate_index):
    for truth in plates[:50]:
        for strategy in STRATEGIES:
            cands, _ = candidate_plates(truth, plates, strategy=strategy,
                                        index=plate_index)
            assert truth in cands


def test_pruning_is_monotone_and_real(plates, plate_index):
    """`none` returns everything; each added filter cuts the candidate set further."""
    read = "DLO1AB1234"          # one confusion (0 -> O) on the story plate DL01AB1234
    full, stats_none = candidate_plates(read, plates, strategy="none")
    assert full == sorted(set(plates))
    assert stats_none.candidates_after == stats_none.candidates_before == len(set(plates))
    assert stats_none.filters_applied == ["full_scan"]

    prev = len(plates) + 1
    sizes = {}
    for strategy in STRATEGIES:
        cands, stats = candidate_plates(read, plates, strategy=strategy,
                                        index=plate_index)
        assert isinstance(stats, BlockingStats)
        assert stats.candidates_before == len(plates)
        assert stats.candidates_after == len(cands) < stats.candidates_before
        assert stats.index_lookups > 0
        assert len(cands) <= prev
        prev = len(cands)
        sizes[strategy] = len(cands)
    # The filters must actually earn their place, not just be present.
    assert sizes["position"] < sizes["index"]
    assert plate_index.last_stats.filters_applied[-1] == "position"
    assert "DL01AB1234" in candidate_plates(read, plates, strategy="position",
                                            index=plate_index)[0]


def test_two_edit_read_needs_the_budget_it_was_given(plates, plate_index):
    """The bounds are only safe for the noise budget they are told about.

    `DLO1AB1Z34` is the CAM story misread of `DL01AB1234`: *two* confusions (0->O, 2->Z).
    At the default max_edits=1 the p. 45 overlap bound legitimately excludes it -- that is
    the documented bound, not a bug. Raising the budget to 2 brings it back, at the cost of
    a larger candidate set. This is the knob the viva asks about.
    """
    tight, _ = candidate_plates("DLO1AB1Z34", plates, strategy="position",
                                index=plate_index)
    assert "DL01AB1234" not in tight

    loose, stats = candidate_plates("DLO1AB1Z34", plates, strategy="position",
                                    max_edits=2, index=plate_index)
    assert "DL01AB1234" in loose
    assert stats.candidates_after < stats.candidates_before


def test_determinism(plates, plate_index):
    a, sa = candidate_plates("DL01AB1234", plates, index=plate_index)
    b, sb = candidate_plates("DL01AB1234", plates, index=plate_index)
    assert a == b == sorted(a)
    assert sa.candidates_after == sb.candidates_after
    assert sa.filters_applied == sb.filters_applied


# ------------------------------------------------------------------------- edge cases

def test_empty_index():
    idx = QGramIndex.build([])
    assert idx.candidates("DL01AB1234") == set()
    assert idx.recommended_k("DL01AB1234") >= 1
    for strategy in ["none"] + STRATEGIES:
        cands, stats = candidate_plates("DL01AB1234", [], strategy=strategy)
        assert cands == [] and stats.candidates_after == 0


def test_empty_query():
    plates = ["DL01AB1234", "DL02AB1234"]
    for strategy in STRATEGIES:
        cands, _ = candidate_plates("", plates, strategy=strategy)
        assert cands == []


def test_query_with_no_candidates():
    plates = ["DL01AB1234", "DL02AB1234"]
    for strategy in STRATEGIES:
        cands, _ = candidate_plates("XY99QQ7766", plates, strategy=strategy)
        assert cands == []


def test_query_longer_than_anything_indexed():
    """A long query must not crash, and must not match short strings it cannot be."""
    plates = ["AB12", "AB13"]
    for strategy in STRATEGIES:
        cands, stats = candidate_plates("AB1234567890123456", plates, strategy=strategy)
        assert isinstance(cands, list)
        assert stats.candidates_after <= 2
    # the size filter knows a 4-character plate cannot be 18 characters
    assert candidate_plates("AB1234567890123456", plates, strategy="size")[0] == []


def test_unknown_strategy_rejected():
    with pytest.raises(ValueError):
        candidate_plates("DL01AB1234", ["DL01AB1234"], strategy="magic")


def test_invalid_k_rejected():
    idx = QGramIndex.build(["DL01AB1234"])
    with pytest.raises(ValueError):
        idx.candidates_prefix_filtered("DL01AB1234", k=0)
    with pytest.raises(ValueError):
        idx.candidates_position_filtered("DL01AB1234", k=0)


# ------------------------------------------------------------------- the SQL pushdown

def test_sql_prefilter_matches_decomposer_normalisation():
    col = "VEHICLE_REGISTRATION.registration_no"
    sql = sql_prefilter("dl-01-ab-1234", col, "sqlite")
    assert f"UPPER(REPLACE(REPLACE({col}, '-', ''), ' ', ''))" in sql
    assert "LIKE 'DL%'" in sql
    assert "BETWEEN 9 AND 11" in sql
    assert ";" not in sql


def test_sql_prefilter_dialects():
    assert "CHAR_LENGTH" in sql_prefilter("DL01AB1234", "c", "MySQL")
    assert "LENGTH(" in sql_prefilter("DL01AB1234", "c", "postgres")
    with pytest.raises(ValueError):
        sql_prefilter("DL01AB1234", "c", "oracle")


def test_sql_prefilter_rejects_unsafe_column_and_quotes():
    with pytest.raises(ValueError):
        sql_prefilter("DL01AB1234", "c'; DROP TABLE x; --", "sqlite")
    # the plate itself is normalised to A-Z0-9, so nothing from the read reaches the SQL
    # as syntax: the only literal is the two-character head.
    sql = sql_prefilter("DL'01--AB", "c", "sqlite")
    assert "LIKE 'DL%'" in sql
    assert "DROP" not in sql.upper() and "--" not in sql


def test_sql_prefilter_degenerate_read():
    assert sql_prefilter("", "c", "sqlite") == "1=1"
    assert sql_prefilter("D", "c", "sqlite") == "1=1"


def test_sql_prefilter_executes_nothing_but_is_valid_sql(plates):
    """Run the built predicate against reg.db read-only: it must select few rows, all
    of them length-compatible, and must include the true plate."""
    pred = sql_prefilter("DLO1AB1Z34", "registration_no", "sqlite")
    con = sqlite3.connect(f"file:{REG_DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            f"SELECT registration_no FROM VEHICLE_REGISTRATION WHERE {pred}").fetchall()
    finally:
        con.close()
    got = {norm_plate(r[0]) for r in rows}
    assert "DL01AB1234" in got
    assert 0 < len(got) < len(plates)
