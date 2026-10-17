"""plate_match: retrieve-rerank-calibrate-decide. Fast, offline, no wrappers, no ports."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from mediator import confusion
from mediator import plate_match as pm

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def reg():
    return pm.load_registry()


@pytest.fixture
def no_model(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "MODEL_PATH", tmp_path / "missing.joblib")
    pm.reload_bundle()
    yield
    pm.reload_bundle()


def test_feature_vector_has_every_named_feature(reg):
    plates = sorted(reg)
    v = pm.features("DLO1AB1234", "DL01AB1234", confidence=0.9, observed_make="Hyundai",
                    observed_colour=None, registry_row=reg["DL01AB1234"], corpus=plates)
    assert list(v) == pm.FEATURE_NAMES
    assert v["colour_agree"] == 0.0 and v["read_is_registered"] == 0.0
    assert v["state_agree"] == 1.0 and v["length_diff"] == 0.0


@pytest.mark.parametrize("read,truth", [("DL05CD9B76", "DL05CD9876"),
                                        ("DLO1AB1234", "DL01AB1234")])
def test_story_misreads_rank_their_vehicle_first(reg, read, truth):
    ranked, stats = pm.match(read, registry=reg)
    assert ranked[0].plate == truth
    assert stats.candidates_before == len(reg)
    assert ranked[0].decision in ("ACCEPT", "REVIEW")


@pytest.mark.parametrize("model", ["rule", "weighted", "jaro_winkler", "logreg", "gbt"])
def test_every_combiner_runs(reg, model):
    ranked, _ = pm.match("DL05CD9B76", registry=reg, model=model)
    assert ranked and ranked[0].plate == "DL05CD9876"


def test_reverse_match_finds_the_misreads(reg):
    captures = ["DL05CD9B76", "DLO1AB1234", "HR26EF4455",
                {"read": "DL01AB1234", "ocr_confidence": 0.99}]
    got = {c.plate for c in pm.reverse_match("DL05CD9876", captures, registry=reg)}
    assert "DL05CD9B76" in got and "HR26EF4455" not in got
    got = {c.plate for c in pm.reverse_match("DL01AB1234", captures, registry=reg)}
    assert {"DLO1AB1234", "DL01AB1234"} <= got


def test_tie_between_two_registered_plates_is_review(reg):
    # Two registered plates one deletion away from the read, identical vehicles: both are very
    # likely, neither can be chosen, so a human must look -- never an automatic ACCEPT.
    row = {"make": "Hyundai", "model": "Creta", "colour": "White"}
    tied = dict(reg, KA03MN5671=row, KA03MN5672=row)
    ranked, _ = pm.match("KA03MN567", registry=tied, observed_make="Hyundai",
                         observed_colour="White", confidence=0.9)
    assert {ranked[0].plate, ranked[1].plate} == {"KA03MN5671", "KA03MN5672"}
    assert ranked[0].probability == pytest.approx(ranked[1].probability, abs=0.01)
    assert ranked[0].decision == "REVIEW"
    assert all(c.decision != "ACCEPT" for c in ranked)


def test_graceful_fallback_without_model_file(reg, no_model):
    ranked, _ = pm.match("DL05CD9B76", registry=reg, model="logreg")
    assert ranked[0].plate == "DL05CD9876"
    assert any("fell back to the hand-weighted combiner" in r for r in ranked[0].reasons)
    assert pm.logreg_coefficients() == {}


def test_nothing_survives_blocking_widens_then_full_scans(reg):
    ranked, stats = pm.match("ZZZZ", registry=reg, model="weighted")
    assert stats.candidates_after == len(reg)
    assert any("full scan" in r for r in ranked[0].reasons)
    assert ranked[0].decision != "ACCEPT"


def test_empty_read_returns_nothing(reg):
    ranked, _ = pm.match("", registry=reg)
    assert ranked == []


def test_registry_never_written(reg):
    before = (ROOT / "sources" / "reg" / "reg.db").stat().st_mtime
    pm.match("DL05CD9B76", registry=None, model="weighted")
    assert (ROOT / "sources" / "reg" / "reg.db").stat().st_mtime == before


def test_benchmark_split_is_by_plate_and_deterministic():
    with (ROOT / "data" / "match_benchmark.csv").open(newline="", encoding="utf-8") as f:
        plates = [r["true_plate"] for r in csv.DictReader(f) if r["true_plate"]]
    a = confusion.split_by_plate(plates, seed=20261017)
    assert a == confusion.split_by_plate(sorted(plates, reverse=True), seed=20261017)
    groups = {s: {p for p, v in a.items() if v == s} for s in ("train", "val", "test")}
    assert not (groups["train"] & groups["test"]) and not (groups["val"] & groups["test"])


def test_saved_bundle_is_consistent_when_present():
    bundle, err = pm.load_bundle()
    if bundle is None:
        pytest.skip(f"no model bundle: {err}")
    assert bundle["features"] == pm.FEATURE_NAMES
    for model in ("logreg", "gbt", "weighted"):
        th = bundle["thresholds"][model]
        assert 0.0 <= th["reject"] <= th["accept"]
    assert set(pm.logreg_coefficients()) == set(pm.FEATURE_NAMES)


def test_cached_tfidf_equals_the_reference_measure(reg):
    from mediator.similarity import tfidf_cosine
    plates = sorted(reg)
    cached = pm.CachedTfidf(plates)
    pairs = [("DLO1AB1234", p) for p in plates[:40]] + [
        ("", ""), ("", "A"), ("X", "Y"), ("DL", "DL01AB1234"), ("dl01ab1234", "DL01AB1234"),
        ("QQ", "QQQ"), (plates[3], plates[4])]
    for a, b in pairs:
        assert cached(a, b) == pytest.approx(tfidf_cosine(a, b, plates), abs=1e-12)


def test_registry_change_is_seen_immediately():
    small = {"DL01AB1234": {}, "HR26EF4455": {}}
    ranked, _ = pm.match("DL05CD9B76", registry=small, model="weighted")
    assert "DL05CD9876" not in {c.plate for c in ranked}
    small["DL05CD9876"] = {}
    ranked, _ = pm.match("DL05CD9B76", registry=small, model="weighted")
    assert ranked[0].plate == "DL05CD9876"
    del small["DL05CD9876"]
    ranked, _ = pm.match("DL05CD9B76", registry=small, model="weighted")
    assert "DL05CD9876" not in {c.plate for c in ranked}


def test_no_plausible_candidate_is_reject_and_review_means_plausible(reg):
    bundle, err = pm.load_bundle()
    if bundle is None:
        pytest.skip(f"no model bundle: {err}")
    floor = pm.thresholds_for("logreg", bundle)["reject"]
    assert floor >= 0.05
    for read in ("ZZZZ", "746X36356", "QW9"):
        ranked, _ = pm.match(read, registry=reg)
        assert ranked[0].decision == "REJECT", (read, ranked[0].probability)
    ranked, _ = pm.match("DL05CD9B76", registry=reg)
    assert all(c.probability >= floor for c in ranked if c.decision == "REVIEW")


def test_warm_match_is_fast(reg):
    import time
    reads = ["DL05CD9B76", "DLO1AB1234", "DL01AB000I", "HR26EF4455", "ZZZZ"]
    for r in reads:
        pm.match(r, registry=reg)
    t = time.process_time()
    for r in reads * 2:
        pm.match(r, registry=reg)
    assert (time.process_time() - t) / (2 * len(reads)) < 0.05
