"""Workstream E: cleansing camera captures with lexicons and Ripple-Down Rules.

Two layers. The offline tests build a `Context` from hand-made rows (expressed through the registry's
own attribute map, so no source column name is written here) and the shipped seed rules; they run in
milliseconds. The live tests use the session `local_cluster` fixture and go through the real CAM
wrapper: apply + undo round trip, and the examiner's insert / modify / delete loop.
"""
from __future__ import annotations

import copy
import json
import shutil
import sys

import httpx
import pytest

import mediator
from mediator import catalog, cleansing, lexicons
from mediator.rdr import RDRConflict, RDRTree
from mediator.registry_loader import load_registry

PK = "rid"  # offline rows only: the Context is told which key column the fake rows carry


def _row(spec, pk_value, **values):
    """A raw source row for `spec`, keyed by the registry's own source column names."""
    row = {PK: pk_value}
    for m in spec.attribute_map:
        if m.global_attr in values:
            row[m.source_attr] = values[m.global_attr]
    return row


@pytest.fixture(scope="module")
def specs():
    registry = load_registry(catalog.META_DB_PATH)
    cam = next(s for s in registry.sources if s.authority == "observational" and "observed_colour" in s.covers)
    reg = next(s for s in registry.sources if s.identity_authority)
    return registry, cam, reg


REGISTRATIONS = [  # plate, make, model, colour -- repeated so every word clears MIN_SUPPORT
    *[(f"DL01AB{i:04d}", "Tata", "Nexon", "Blue") for i in range(1, 6)],
    *[(f"DL05CD{i:04d}", "Maruti Suzuki", "Swift", "Silver") for i in range(1, 6)],
    *[(f"HR26EF{i:04d}", "Hyundai", "Creta", "Grey") for i in range(1, 6)],
    *[(f"UP16GH{i:04d}", "Hyundai", "i20", "White") for i in range(1, 6)],
    ("DL05CD9876", "Maruti Suzuki", "Swift", "Silver"),
    ("MH12IJ0001", "Hyundia", "Creta", "Red"), ("MH12IJ0002", "Hyundia", "Creta", "Red"),
    ("MH12IJ0003", "Kia", "Seltos", "Red"), ("MH12IJ0004", "Kia", "Seltos", "Black"),
    ("MH12IJ0005", "Kia", "Seltos", "Black"), ("MH12IJ0006", "Kia", "Seltos", "Black"),
]
BASE = {"last_seen_location": "Ring Road Junction", "camera_lat": 28.6139, "camera_lon": 77.209,
        "ocr_confidence": 0.93}


def make_ctx(specs, captures, rules=cleansing.RULES_PATH):
    registry, cam, reg = specs
    reg_rows = [_row(reg, i, plate_number=p, vehicle_make=mk, vehicle_model=md, vehicle_colour=c,
                     registration_status="ACT") for i, (p, mk, md, c) in enumerate(REGISTRATIONS, 1)]
    cam_rows = [_row(cam, rid, **{**BASE, **values}) for rid, values in captures]
    return cleansing.Context(registry, cam, reg, PK, cam_rows, reg_rows, RDRTree.load(rules))


def capture(plate, make, model, colour, at="2026-09-01T10:00:00", **extra):
    return {"plate_number": plate, "observed_make": make, "observed_model": model,
            "observed_colour": colour, "last_seen_time": at, **extra}


def fix(prop, field):
    return next(f for f in prop.fixes if f.field == field)


def codes(prop):
    return {a.code for a in prop.anomalies}


# ------------------------------------------------------------------ lexicons (slides 19-23)

def test_lexicon_mining_from_registrations():
    rows = [{"plate_number": p, "vehicle_make": mk, "vehicle_model": md, "vehicle_colour": c}
            for p, mk, md, c in REGISTRATIONS]
    lx = lexicons.build(rows)
    assert lx.make.lookup("Hyundia") == ("Hyundai", "mined")          # rare spelling beside a frequent one
    assert lx.make.lookup("MARUTI-SUZUKI") == ("Maruti Suzuki", "surface")
    assert lx.make.lookup("Maruti") == ("Maruti Suzuki", "prefix")
    assert lx.colour.lookup("Gry") == ("Grey", "abbreviation")
    assert lx.colour.lookup("Silvr") == ("Silver", "abbreviation")
    assert lx.colour.lookup("Gray") == ("Grey", "edit")
    assert lx.model.lookup("i10") == (None, None)                     # slide 12: overloaded number
    assert lx.model_make["creta"] == "Hyundai"
    assert lx.plate_features("DL01AB1234")["pattern"] == "S^+^"       # slide 21 notation
    assert not lx.plate_features("DLO1AB1234")["shape_valid"]
    assert not lx.plate_features("ZZ01AB1234")["shape_valid"]         # real shape, unknown state code


def test_mining_is_deterministic():
    rows = [{"plate_number": p, "vehicle_make": mk, "vehicle_model": md, "vehicle_colour": c}
            for p, mk, md, c in REGISTRATIONS]
    a, b = lexicons.build(rows), lexicons.build(list(reversed(rows)))
    assert (a.make.canonical, a.make.variants, a.shapes) == (b.make.canonical, b.make.variants, b.shapes)


# ------------------------------------------------------------------ the seed tree

def test_seed_tree_is_consistent_with_its_cornerstones():
    tree = RDRTree.load(cleansing.RULES_PATH)
    rules = list(tree.rules())
    assert 5 <= len(rules) <= 12
    for rule in rules:
        assert rule.cornerstone and rule.because, rule.id
        assert tree.evaluate(rule.cornerstone)[0]["rule"] == rule.id, rule.id


# ------------------------------------------------------------------ standardisation

def test_colour_and_make_variants_are_standardised(specs):
    ctx = make_ctx(specs, [(1, capture("HR26EF0001", "HYUNDAI", "Creta", "Gry")),
                           (2, capture("DL05CD0001", "Maruti", "Swift", "silvr"))])
    p1, p2 = (cleansing.propose(ctx.record(i), ctx) for i in (1, 2))
    assert (fix(p1, "observed_colour").action, fix(p1, "observed_colour").after) == ("set", "Grey")
    assert fix(p1, "observed_colour").rule == "R1"
    assert fix(p1, "observed_make").after == "Hyundai"
    assert (fix(p2, "observed_make").after, fix(p2, "observed_colour").after) == ("Maruti Suzuki", "Silver")
    assert "COLOUR_NOT_IN_LEXICON" in codes(p1)
    assert cleansing._after(p1) and not cleansing.detect(cleansing._after(p1), ctx.profile_for(p1.record), ctx)


def test_edit_guess_against_the_registration_is_quarantined_not_corrected(specs):
    # "Gray" is one edit from Grey, but this car is registered Silver: R2, the exception under R1
    ctx = make_ctx(specs, [(1, capture("DL05CD0001", "Maruti Suzuki", "Swift", "Gray"))])
    f = fix(cleansing.propose(ctx.record(1), ctx), "observed_colour")
    assert (f.action, f.rule) == ("quarantine", "R2")
    assert [p["rule"] for p in f.path][:3] == ["R0", "R1", "R2"]


def test_unknown_token_is_enriched_from_the_registration(specs):
    ctx = make_ctx(specs, [(1, capture("DL05CD0001", "Maruthi", "Swift", "Silver"))])
    f = fix(cleansing.propose(ctx.record(1), ctx), "observed_make")
    assert (f.action, f.after, f.rule) == ("set", "Maruti Suzuki", "R3")


def test_valid_value_disagreeing_with_registration_is_flagged_never_overwritten(specs):
    ctx = make_ctx(specs, [(1, capture("UP16GH0001", "Kia", "Seltos", "Blue"))])
    prop = cleansing.propose(ctx.record(1), ctx)
    assert {"MAKE_DISAGREES_REGISTRATION", "COLOUR_DISAGREES_REGISTRATION"} <= codes(prop)
    assert fix(prop, "observed_make").action == "flag" and fix(prop, "observed_make").after == "Kia"


# ------------------------------------------------------------------ plates and the matcher

def test_misread_plate_proposal_cites_the_matcher(specs, monkeypatch):
    seen = {}

    def fake(read, confidence, make, colour, model, ctx):
        seen.update(read=read, confidence=confidence, make=make)
        return {"decision": "ACCEPT", "best": "DL05CD9876", "probability": 0.9983, "method": "plate_match",
                "candidates": [{"plate": "DL05CD9876", "probability": 0.9983}], "note": None}

    monkeypatch.setattr(cleansing, "_match_plate", fake)
    ctx = make_ctx(specs, [(1, capture("DL05CD9B76", "Maruti Suzuki", "Swift", "Silver", ocr_confidence=0.71))])
    prop = cleansing.propose(ctx.record(1), ctx)
    f = fix(prop, "plate_number")
    assert (f.action, f.after, f.rule) == ("set", "DL05CD9876", "R5")
    assert "DL05CD9876" in f.because and "0.9983" in f.because
    assert f.evidence["match_probability"] == 0.9983 and f.evidence["match_method"] == "plate_match"
    assert seen == {"read": "DL05CD9B76", "confidence": 0.71, "make": "Maruti Suzuki"}
    assert {"PLATE_NOT_REGISTERED", "PLATE_SHAPE_INVALID"} <= codes(prop)
    assert cleansing.detect(cleansing._after(prop), ctx.profiles["DL05CD9876"], ctx) == []


def test_review_from_the_matcher_means_quarantine_never_a_guess(specs, monkeypatch):
    monkeypatch.setattr(cleansing, "_match_plate", lambda *a: {
        "decision": "REVIEW", "best": "DL05CD9876", "probability": 0.62, "method": "plate_match",
        "candidates": [{"plate": "DL05CD9876", "probability": 0.62}, {"plate": "DL05CD0001", "probability": 0.58}],
        "note": None})
    ctx = make_ctx(specs, [(1, capture("DL05CD9B76", "Maruti Suzuki", "Swift", "Silver"))])
    f = fix(cleansing.propose(ctx.record(1), ctx), "plate_number")
    assert (f.action, f.after, f.rule) == ("quarantine", "DL05CD9B76", "R6")


def test_a_taught_rule_cannot_rewrite_a_plate_on_review(specs, monkeypatch, tmp_path):
    monkeypatch.setattr(cleansing, "_match_plate", lambda *a: {
        "decision": "REVIEW", "best": "DL05CD9876", "probability": 0.62, "method": "plate_match",
        "candidates": [], "note": None})
    rules = tmp_path / "rules.json"
    shutil.copy(cleansing.RULES_PATH, rules)
    ctx = make_ctx(specs, [(1, capture("DL05CD9B76", "Maruti Suzuki", "Swift", "Silver"))], rules)
    cleansing.teach(1, "plate_number", "R6", {"field": "plate", "op": "eq", "value": "DL05CD9B76"},
                    {"action": "set", "value": "$match_best"}, "operator insists", ctx=ctx, rules_path=rules)
    f = fix(cleansing.propose(ctx.record(1), ctx), "plate_number")
    assert f.action == "quarantine" and "downgraded" in f.because


def test_real_matcher_corrects_the_story_misread(specs):
    ctx = make_ctx(specs, [(1, capture("DL05CD9B76", "Maruti Suzuki", "Swift", "Silver", ocr_confidence=0.71))])
    f = fix(cleansing.propose(ctx.record(1), ctx), "plate_number")
    assert f.after == "DL05CD9876" and f.action == "set"
    assert f.evidence["match_best"] == "DL05CD9876" and f.evidence["match_probability"] is not None


def test_low_confidence_corroborated_read_is_kept(specs):
    ctx = make_ctx(specs, [(1, capture("HR26EF0001", "Hyundai", "Creta", "Grey", ocr_confidence=0.6)),
                           (2, capture("HR26EF0002", "Kia", "Seltos", "Red", ocr_confidence=0.6))])
    p1, p2 = (cleansing.propose(ctx.record(i), ctx) for i in (1, 2))
    assert "LOW_OCR_CONFIDENCE" not in codes(p1) and fix(p1, "plate_number").rule == "R8"
    assert "LOW_OCR_CONFIDENCE" in codes(p2) and fix(p2, "plate_number").action == "quarantine"


# ------------------------------------------------------------------ time and duplicates

def test_timestamps(specs):
    ctx = make_ctx(specs, [(1, capture("HR26EF0001", "Hyundai", "Creta", "Grey")),
                           (2, capture("HR26EF0002", "Hyundai", "Creta", "Grey", at="01/09/2026 10:30")),
                           (3, capture("HR26EF0003", "Hyundai", "Creta", "Grey", at="2026-13-45 99:99")),
                           (4, capture("HR26EF0004", "Hyundai", "Creta", "Grey", at="2099-01-01T00:00:00"))])
    props = {i: cleansing.propose(ctx.record(i), ctx) for i in (2, 3, 4)}
    assert fix(props[2], "last_seen_time").after == "2026-09-01T10:30:00"   # the source's own format
    assert "TIMESTAMP_UNPARSEABLE" in codes(props[3]) and fix(props[3], "last_seen_time").action == "quarantine"
    assert "TIMESTAMP_FUTURE" in codes(props[4]) and fix(props[4], "last_seen_time").action == "quarantine"


def test_duplicate_capture_is_deleted_keeping_the_first(specs):
    same = capture("HR26EF0001", "Hyundai", "Creta", "Grey")
    ctx = make_ctx(specs, [(9, same), (10, dict(same))])  # 10 sorts after 9 numerically, not as text
    assert fix(cleansing.propose(ctx.record(9), ctx), "record").action == "keep"
    prop = cleansing.propose(ctx.record(10), ctx)
    assert "DUPLICATE_CAPTURE" in codes(prop) and fix(prop, "record").action == "delete"


# ------------------------------------------------------------------ knowledge acquisition on records

def test_teach_clears_the_case_and_similar_ones_and_protects_cornerstones(specs, tmp_path):
    rules = tmp_path / "rules.json"
    shutil.copy(cleansing.RULES_PATH, rules)
    ctx = make_ctx(specs, [(1, capture("DL05CD0001", "Maruti Suzuki", "Swift", "Golden")),
                           (2, capture("DL05CD0002", "Maruti Suzuki", "Swift", "Golden"))], rules)
    assert fix(cleansing.propose(ctx.record(1), ctx), "observed_colour").after == "Silver"  # R3 enrichment
    with pytest.raises(RDRConflict, match="cornerstone"):
        cleansing.teach(1, "observed_colour", "R3", {"field": "registered", "op": "is_true"},
                        {"action": "flag", "flag": "REPAINT_SUSPECT"}, "over-general", ctx=ctx, rules_path=rules)
    out = cleansing.teach(1, "observed_colour", "R3", {"field": "value", "op": "eq", "value": "Golden"},
                          {"action": "flag", "flag": "REPAINT_SUSPECT"}, "a real colour REG does not list",
                          ctx=ctx, rules_path=rules)
    assert out["under"] == "R3" and RDRTree.load(rules).find("R3").except_.id == out["rule"]
    for rid in (1, 2):
        assert fix(cleansing.propose(ctx.record(rid), ctx), "observed_colour").rule == out["rule"]


# ------------------------------------------------------------------ live: through the CAM wrapper

def _live(ctx):
    return ctx.cam, ctx.pk


def _admin(spec, sql):
    r = httpx.post(spec.base_url + "/admin/sql", json={"sql": sql}, timeout=5, trust_env=False)
    assert r.status_code == 200, r.text
    return r.json()["rows_affected"]


def _stored(spec, pk, rid, field):
    col = cleansing._column(spec, field)
    rows = lexicons._post(spec, f"SELECT {col} FROM {spec.table} WHERE {pk} = {rid}", None)["rows"]
    return rows[0][col] if rows else None


def test_apply_and_undo_round_trip_through_the_wrapper(local_cluster):
    ctx = cleansing.Context.build()
    spec, pk = _live(ctx)
    rid = next(r["record_id"] for r in ctx.records if r["plate_number"] == "DL05CD9B76")
    prop = cleansing.propose(ctx.record(rid), ctx)
    assert fix(prop, "plate_number").after == "DL05CD9876"
    try:
        done = cleansing.apply(prop, actor="pytest", ctx=ctx)
        assert _stored(spec, pk, rid, "plate_number") == "DL05CD9876"
        log = next(e for e in cleansing.log_entries() if e["log_id"] == done[0]["log_id"])
        assert (log["actor"], log["rule_id"], json.loads(log["before_value"])) == ("pytest", "R5", "DL05CD9B76")
        with pytest.raises(cleansing.CleansingError, match="changed since"):
            cleansing.apply(prop, actor="pytest", ctx=ctx)  # stale: the row no longer holds the before value
        cleansing.undo(done[0]["log_id"], actor="pytest")
        assert _stored(spec, pk, rid, "plate_number") == "DL05CD9B76"
        with pytest.raises(cleansing.CleansingError, match="already undone"):
            cleansing.undo(done[0]["log_id"], actor="pytest")
    finally:
        col = cleansing._column(spec, "plate_number")
        _admin(spec, f"UPDATE {spec.table} SET {col} = 'DL05CD9B76' WHERE {pk} = {rid}")


def _scan_item(record_id):
    report = cleansing.scan()
    assert report["status"] == "OK"
    return next((i for i in report["items"] if str(i["record_id"]) == str(record_id)), None)


def test_examiner_insert_modify_delete_loop(local_cluster, monkeypatch):
    # This loop is about detection staying live, not about the matcher: route plate matching to the
    # rule-based fallback so three full scans take seconds instead of the learned matcher's ~25 s each.
    monkeypatch.setitem(sys.modules, "mediator.plate_match", None)
    monkeypatch.delattr(mediator, "plate_match", raising=False)  # else `from mediator import` finds it
    ctx = cleansing.Context.build()
    spec, pk = _live(ctx)
    r = httpx.post(spec.base_url + "/admin/mutate", trust_env=False, timeout=5, json={
        "action": "sight", "plate": "DL01AB0377",
        "params": {"make": "Tata", "model": "Nexon", "colour": "Blu", "ocr_confidence": 0.93}})
    assert r.status_code == 200, r.text
    rid = int(r.json()["detail"].split()[1].rstrip(":"))
    try:
        item = _scan_item(rid)  # insert: the dirty colour shows up, with the rule that fixes it
        assert item and "COLOUR_NOT_IN_LEXICON" in [a["code"] for a in item["anomalies"]]
        colour = next(f for f in item["fixes"] if f["field"] == "observed_colour")
        assert (colour["action"], colour["after"], colour["rule"]) == ("set", "Blue", "R1")
        assert "TIMESTAMP_FUTURE" not in [a["code"] for a in item["anomalies"]]  # "seen just now" is not the future

        col = cleansing._column(spec, "observed_colour")
        _admin(spec, f"UPDATE {spec.table} SET {col} = 'Red' WHERE {pk} = {rid}")
        item = _scan_item(rid)  # modify: now a valid colour that contradicts the registration
        assert "COLOUR_DISAGREES_REGISTRATION" in [a["code"] for a in item["anomalies"]]
        assert next(f for f in item["fixes"] if f["field"] == "observed_colour")["action"] == "flag"
    finally:
        _admin(spec, f"DELETE FROM {spec.table} WHERE {pk} = {rid}")
    assert _scan_item(rid) is None  # delete: gone from the report, no stale cache anywhere


def test_cli_teach_is_non_interactive_and_names_missing_flags(capsys):
    from scripts import cleanse
    with pytest.raises(SystemExit) as exc:
        cleanse.main(["--teach", "--capture", "5"])
    assert exc.value.code == 2 and "--field" in capsys.readouterr().err


def test_cli_prints_rule_tree(capsys):
    from scripts import cleanse
    assert cleanse.main(["--tree"]) == 0
    out = capsys.readouterr().out
    assert "R0:" in out and "R11:" in out
