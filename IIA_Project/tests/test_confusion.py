"""Learned OCR confusion costs: train-only estimation, symmetry, and the 0/O-vs-0/7 ordering."""
from __future__ import annotations

import json

import pytest

from mediator import confusion
from mediator.similarity import ocr_confusion_cost


def _row(true, read, degradation="blur"):
    return {"true_plate": true, "ocr_read": read, "degradation": degradation}


@pytest.fixture
def costs_file(tmp_path, monkeypatch):
    """Estimate from a tiny synthetic benchmark and point the module at it, never at data/."""
    rows = [_row("DL01AB1234", "DLO1AB1234")] * 30 + [_row("HR26EF4455", "HR26EF44S5")] * 3
    costs, counts = confusion.estimate_costs(rows, ["DL01AB1234", "HR26EF4455"])
    path = tmp_path / "costs.json"
    confusion.save_costs(costs, counts, 7, ["DL01AB1234", "HR26EF4455"], path=path)
    monkeypatch.setattr(confusion, "COSTS_PATH", path)
    confusion.reload_costs()
    yield path
    confusion.reload_costs()


def test_zero_o_cheaper_than_zero_seven(costs_file):
    assert confusion.learned_confusion_cost("0", "O") < confusion.learned_confusion_cost("0", "7")
    assert confusion.learned_confusion_cost("0", "O") >= confusion.FLOOR


def test_costs_symmetric(costs_file):
    for a, b in [("0", "O"), ("5", "S"), ("0", "7"), ("H", "4")]:
        assert confusion.learned_confusion_cost(a, b) == confusion.learned_confusion_cost(b, a)


def test_identity_is_free_and_unseen_pairs_use_hand_table(costs_file):
    assert confusion.learned_confusion_cost("A", "A") == 0.0
    assert confusion.learned_confusion_cost("2", "Z") == ocr_confusion_cost("2", "Z")
    assert confusion.learned_confusion_cost("Q", "7") == 1.0


def test_confusion_only_in_test_rows_does_not_lower_its_cost():
    split = {"DL01AB1234": "train", "HR26EF4455": "test"}
    rows = [_row("DL01AB1234", "DLO1AB1234")] * 5 + [_row("HR26EF4455", "HR26EF44S5")] * 50
    train = [p for p, s in split.items() if s == "train"]
    costs, counts = confusion.estimate_costs(rows, train)
    assert ("5", "S") not in costs and ("5", "S") not in counts
    assert ("0", "O") in costs


def test_story_misreads_are_not_noise_samples():
    rows = [_row("DL05CD9876", "DL05CD9B76", degradation="observed")]
    costs, _ = confusion.estimate_costs(rows, ["DL05CD9876"])
    assert costs == {}


def test_unequal_lengths_contribute_nothing():
    assert not confusion.observed_substitutions([("DL01AB1234", "DL01AB123")])


def test_split_is_deterministic_and_disjoint():
    plates = [f"DL01AB{i:04d}" for i in range(100)]
    a = confusion.split_by_plate(plates, seed=3)
    b = confusion.split_by_plate(list(reversed(plates)), seed=3)
    assert a == b
    sizes = {s: sum(1 for v in a.values() if v == s) for s in ("train", "val", "test")}
    assert sizes == {"train": 60, "val": 20, "test": 20}
    assert confusion.split_by_plate(plates, seed=4) != a


def test_saved_json_records_seed_and_counts(costs_file):
    data = json.loads(costs_file.read_text(encoding="utf-8"))
    assert data["seed"] == 7 and data["n_train_plates"] == 2
    zero_o = next(p for p in data["pairs"] if (p["a"], p["b"]) == ("0", "O"))
    assert zero_o["count"] == 30


def test_learned_similarity_prefers_confusion_over_arbitrary_edit(costs_file):
    s_conf = confusion.learned_ocr_similarity("DLO1AB1234", "DL01AB1234")
    s_arb = confusion.learned_ocr_similarity("DL71AB1234", "DL01AB1234")
    assert 0.0 <= s_arb < s_conf < 1.0
    assert confusion.learned_ocr_similarity("", "") == 1.0
    assert confusion.learned_ocr_similarity("A", "") == 0.0
