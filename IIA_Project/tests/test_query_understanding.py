"""CONTEXT-ADDED (R2): free text -> typed predicates. Rules only, plus one test with a mocked LLM."""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional

import pytest

from mediator import llm
from mediator import query_understanding as Q

TODAY = date(2026, 11, 11)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    Q._cache.clear()

    def no_llm(*_: Any, **__: Any) -> None:
        raise AssertionError("the LLM must not be called in this test")
    monkeypatch.setattr(llm, "generate_json", no_llm)


def preds(text: str) -> Dict[str, Any]:
    q = Q.parse(text, use_llm=False, today=TODAY)
    return {p.attr: p.value for p in q.predicates}


@pytest.mark.parametrize("text", ["DL05CD9876", "dl-05 cd 9876", "DL 05 CD 9876"])
def test_plates_are_canonicalised(text: str) -> None:
    q = Q.parse(text, use_llm=False, today=TODAY)
    assert q.plates == ["DL05CD9876"] and q.predicates == [] and q.parser == "rules"


def test_stolen_white_creta_gurgaon() -> None:
    assert preds("list all stolen white Creta Gurgaon") == {
        "stolen_status": True, "vehicle_colour": "White", "vehicle_model": "Creta", "vehicle_make": "Hyundai",
        "location": "Gurugram"}


def test_expired_hyundai_seen_in_noida_last_month() -> None:
    q = Q.parse("expired insurance Hyundai seen in Noida last month", use_llm=False, today=TODAY)
    assert {p.attr: p.value for p in q.predicates} == {
        "insurance_status": "EXPIRED", "vehicle_make": "Hyundai", "last_seen_location": "Noida"}
    assert q.time_window == ("2026-10-01", "2026-10-31")
    assert q.keywords == []


def test_owner_name() -> None:
    q = Q.parse("vehicles owned by Rahul Sharma", use_llm=False, today=TODAY)
    assert [(p.attr, p.op, p.value) for p in q.predicates] == [("owner_name", "~", "Rahul Sharma")]


def test_relational_indicator_phrase() -> None:
    assert preds("stolen cars tested for PUC after theft") == {"stolen_status": True, "indicator": "ALIBI_PUC"}
    assert preds("cars seen in Noida after theft") == {"indicator": "ALIBI_CAMERA", "stolen_status": True,
                                                        "last_seen_location": "Noida"}


def test_every_predicate_has_a_note_and_fuzzy_words_lower_confidence() -> None:
    q = Q.parse("red cretta spotted at cyber city in July 2026", use_llm=False, today=TODAY)
    by = {p.attr: p for p in q.predicates}
    assert by["vehicle_model"].value == "Creta" and by["vehicle_model"].confidence < 1.0
    assert by["last_seen_location"].value == "Gurugram"
    assert q.time_window == ("2026-07-01", "2026-07-31")
    assert len(q.notes) >= len(q.predicates)


def test_ambiguous_model_word_needs_its_make() -> None:
    assert "vehicle_model" not in preds("stolen cars in the city")
    assert preds("honda city suspended")["vehicle_model"] == "City"


def test_llm_output_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: Dict[str, Optional[str]] = {"prompt": None}

    def fake(prompt: str, **_: Any) -> Dict[str, Any]:
        seen["prompt"] = prompt
        return {"predicates": [
            {"word": "rouge", "attr": "vehicle_colour", "op": "=", "value": "red", "confidence": 0.95},  # synonym: half weight
            {"word": "gurgoan", "attr": "location", "op": "~", "value": "Gurugram", "confidence": 0.9},  # misspelt alias: kept
            {"word": "hatchback", "attr": "vehicle_model", "op": "=", "value": "Swift"},   # a name never inferred
            {"word": "rouge", "attr": "engine_cc", "op": ">=", "value": 1500},            # not in the registry
            {"word": "rouge", "attr": "vehicle_make", "op": "=", "value": "Ferrari"},     # value not allowed
            {"word": "nowhere", "attr": "vehicle_colour", "op": "=", "value": "Blue"},    # cites a word not left over
            {"attr": "owner_name", "op": "~", "value": "Mukesh Ambani"},                 # not in the text
            {"word": "rouge", "attr": "stolen_status", "op": "=", "value": True},         # rules already set it
            "DROP TABLE"]}
    monkeypatch.setattr(llm, "generate_json", fake)
    q = Q.parse("stolen rouge hatchback in gurgoan", use_llm=True, today=TODAY)
    assert q.parser == "rules+llm"
    assert "rouge" in (seen["prompt"] or "")
    kept = {p.attr: p for p in q.predicates}
    assert set(kept) == {"stolen_status", "vehicle_colour", "location"}
    assert kept["vehicle_colour"].value == "Red" and kept["vehicle_colour"].confidence == Q.SYNONYM_CAP
    assert kept["location"].value == "Gurugram" and kept["location"].confidence == Q.LLM_CONFIDENCE_CAP
    dropped = " ".join(n for n in q.notes if "dropped" in n)
    for reason in ("names are never inferred", "engine_cc", "Ferrari", "not a word the rules left", "hallucination",
                   "rules win", "not an object"):
        assert reason in dropped


def test_llm_self_contradiction_is_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: {"predicates": [
        {"word": "rouge", "attr": "vehicle_colour", "value": "Red"}, {"word": "rouge", "attr": "vehicle_colour", "value": "Maroon"}]})
    q = Q.parse("rouge cars", use_llm=True, today=TODAY)
    assert q.predicates == [] and any("conflicting" in n for n in q.notes)


def test_llm_off_falls_back_to_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "generate_json", lambda *a, **k: None)
    q = Q.parse("stolen rouge hatchback", use_llm=True, today=TODAY)
    assert q.parser == "rules" and "rouge" in q.keywords
    assert any("unavailable" in n for n in q.notes)


def test_llm_not_called_when_rules_explain_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    q = Q.parse("list all stolen white Creta Gurgaon", use_llm=True, today=TODAY)   # the autouse fixture would raise
    assert q.parser == "rules"


def test_parse_is_cached_per_query_string() -> None:
    a = Q.parse("stolen white creta", use_llm=False, today=TODAY)
    assert Q.parse("stolen white creta", use_llm=False, today=TODAY) is a


def test_vocabulary_grows_from_live_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    from mediator import query_vocab as V
    from tests.phase3_fakes import FakeClient
    monkeypatch.setattr(V, "MODELS", dict(V.MODELS))
    monkeypatch.setattr(V, "PLACES", dict(V.PLACES))
    client = FakeClient({"REG": {"VEHICLE_REGISTRATION": [{"registration_id": 1, "registration_no": "HR26AB1001",
                                                           "make": "Tata", "model": "Altroz"}]},
                         "CAM": {"ZONES": [{"zone_id": 1, "zone_name": "North", "city": "Sonipat"}]}})
    added = V.refresh_vocabulary(client)
    assert added["models"] == 1 and added["places"] >= 1
    assert preds("stolen altroz in sonipat") == {"stolen_status": True, "vehicle_model": "Altroz",
                                                  "vehicle_make": "Tata", "location": "Sonipat"}
