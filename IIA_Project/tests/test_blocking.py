"""Tests for mediator/blocking.py -- the deck's worked example and recall safety."""
from __future__ import annotations

import random
import sqlite3
from pathlib import Path
from typing import List

import pytest

from mediator.blocking import QGramIndex, candidate_plates, sql_prefilter
from mediator.plate_resolve import CONFUSABLE
from mediator.transforms import norm_plate

REG_DB = Path(__file__).resolve().parents[1] / "sources" / "reg" / "reg.db"


def words(s: str, q: int = 2) -> List[str]:
    return s.split()


# Week 8 deck, slides 1-7: the Jeevan/Agra recap of the Week 6-8 filters.
Y = {
    "x3": "Block Jeevan Cinema Agra",
    "x4": "Building Ravi Vihar New Delhi",
    "x5": "Block Jeevan Vihar New Delhi",
    "x6": "Mandir Ravi Vihar Delhi",
}
X1 = "Block Jeevan Cinema Old Agra"


@pytest.fixture()
def deck_index() -> QGramIndex:
    return QGramIndex.build(list(Y.values()), tokenizer=words)


def test_deck_inverted_index(deck_index):
    # Week 8 slide 2: candidate set {3, 5}
    got = deck_index.candidates(X1)
    assert got == {Y["x3"], Y["x5"]}


def test_deck_prefix_filter(deck_index):
    # Week 8 slide 6: with the global order, C for x1 is {x3}
    got = deck_index.candidates_prefix_filtered(X1, k=3)
    assert got == {Y["x3"]}


def test_deck_position_bound(deck_index):
    # Week 8 slide 7: 2 + min(2, 2) = 4
    assert deck_index.max_overlap_bound(X1, Y["x3"], k=3) == 4
    assert deck_index.candidates_position_filtered(X1, k=3) == {Y["x3"]}
    assert deck_index.candidates_position_filtered(X1, k=5) == set()


def reg_plates() -> List[str]:
    with sqlite3.connect(f"file:{REG_DB}?mode=ro", uri=True) as con:
        return [r[0] for r in con.execute("SELECT registration_no FROM VEHICLE_REGISTRATION")]


def corruptions(plate: str) -> List[str]:
    out = []
    for i, ch in enumerate(plate):
        for alt in CONFUSABLE.get(ch, ""):
            out.append(plate[:i] + alt + plate[i + 1:])
    return out


@pytest.mark.parametrize("strategy", ["index", "size", "prefix", "position"])
def test_recall_on_ocr_corruptions(strategy):
    plates = [norm_plate(p) for p in reg_plates()]
    rng = random.Random(7)
    sample = rng.sample(plates, min(200, len(plates)))
    for truth in sample:
        for read in corruptions(truth):
            cands, _ = candidate_plates(read, plates, strategy=strategy)
            assert truth in cands, f"{strategy} lost {truth} for read {read}"


def test_pruning_works():
    plates = [norm_plate(p) for p in reg_plates()]
    read = plates[0][:-1] + ("0" if plates[0][-1] != "0" else "O")
    full, stats_none = candidate_plates(read, plates, strategy="none")
    assert len(full) == len(set(plates))
    prev = len(plates)
    for strategy in ["index", "size", "prefix", "position"]:
        cands, stats = candidate_plates(read, plates, strategy=strategy)
        assert stats.candidates_after < stats.candidates_before
        assert len(cands) <= prev
        prev = len(cands)


def test_determinism():
    plates = [norm_plate(p) for p in reg_plates()]
    a, _ = candidate_plates("DL01AB1234", plates)
    b, _ = candidate_plates("DL01AB1234", plates)
    assert a == b == sorted(a)


def test_empty_and_no_candidates():
    idx = QGramIndex.build([])
    assert idx.candidates("DL01AB1234") == set()
    cands, stats = candidate_plates("DL01AB1234", [], strategy="position")
    assert cands == []
    plates = ["DL01AB1234", "DL02AB1234"]
    cands, _ = candidate_plates("ZZ99ZZ9999", plates, strategy="index")
    assert cands == []


def test_query_longer_than_anything_indexed():
    plates = ["AB12", "AB13"]
    cands, stats = candidate_plates("AB1234567890", plates, strategy="position")
    assert isinstance(cands, list)


def test_sql_prefilter_shape():
    sql = sql_prefilter("dl-01-ab-1234", "VEHICLE_REGISTRATION.registration_no", "sqlite")
    assert "UPPER(REPLACE(REPLACE(" in sql
    assert "LIKE 'DL%'" in sql
    assert "BETWEEN 9 AND 11" in sql
    assert "CHAR_LENGTH" in sql_prefilter("DL01AB1234", "c", "mysql")
