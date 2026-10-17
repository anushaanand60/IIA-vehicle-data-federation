"""Test scenarios: the examiner's stress tests, runnable from the GUI or the command line (workstream G).

Each scenario is one thing the phase-2 examiner may try (plan section 5): a noisy plate read, an
unreadable image, a twin plate inserted into the registry, a dirty capture inserted / modified /
deleted, a source switched off, a batch of benchmark reads. A scenario reads everything LIVE through
the federation (registry and captures through the wrappers, as `mediator.cleansing` does), makes
any data change through the owning source's own /admin/sql (`cleansing._admin`, so every write is
in ADMIN_LOG), and undoes that change through the same path before it returns -- also when it
fails half way. `restored` says honestly whether every change it made was undone.

    python -m mediator.scenarios                 # run all, print a table
    python -m mediator.scenarios ocr_b8 batch_stress
"""
from __future__ import annotations

import csv
import difflib
import json
import random
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from mediator import catalog, cleansing, lexicons
from mediator.contract import Registry
from mediator.executor import render_plate
from mediator.registry_loader import load_registry
from mediator.transforms import norm_plate

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "data" / "match_benchmark.csv"
SEED = 20261017


@dataclass
class Step:
    title: str
    detail: str
    ok: Optional[bool]  # None = informational


@dataclass
class ScenarioResult:
    key: str
    title: str
    rubric: str            # "1", "2" or "1+2"
    expected: str
    actual: str
    passed: Optional[bool]  # None = skipped: the scenario cannot run on this deployment (data says why)
    steps: List[Step]
    data: Dict[str, Any]    # tables / numbers for the GUI (json-serialisable)
    elapsed_ms: float
    restored: bool          # True once every data change the scenario made was undone

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


SCENARIOS: Dict[str, Dict[str, str]] = {
    "clean_image": {"title": "Clean plate image", "rubric": "1",
                    "what": "A clean benchmark photo goes through EasyOCR, then the matcher.",
                    "why": "The happy path: an image must end in an ACCEPT of the right vehicle."},
    "ocr_zero_o": {"title": "OCR reads 0 as O", "rubric": "1",
                   "what": "The read DLO1AB1234 is matched against the live registry.",
                   "why": "0/O is 62 % of all OCR substitutions in the benchmark; it must cost almost nothing."},
    "ocr_b8": {"title": "OCR reads 8 as B", "rubric": "1",
               "what": "The read DL05CD9B76 (story capture 916) is matched, with and without camera context.",
               "why": "Shows the camera's make and colour turning a REVIEW into a calibrated ACCEPT."},
    "two_errors_rotated": {"title": "Rotated image, two OCR errors", "rubric": "1",
                           "what": "HR26EF4455__rotate_s2.png reads HR2GEF44551: one substitution, one extra character.",
                           "why": "A single-edit resolver cannot recover this; the combined matcher must."},
    "unreadable_image": {"title": "Unreadable image", "rubric": "1",
                         "what": "Severity-3 occlusion and motion-blur images go through OCR and matching.",
                         "why": "Make it fail: a bad image must give REVIEW or REJECT, never a confident wrong ACCEPT."},
    "unregistered_plate": {"title": "Unregistered plate", "rubric": "1",
                           "what": "DL09QQ9009, a well-formed plate nobody registered, is matched.",
                           "why": "No registered vehicle is close, so the only right answer is REJECT."},
    "ambiguous_twins": {"title": "Twin plates: ambiguous read", "rubric": "1",
                        "what": "A second registration one confusable character from DL01AB1234 (the 0/O twin) is "
                                "inserted through REG's admin console; the read DLD1AB1234 is matched; the twin is deleted.",
                        "why": "When two real vehicles explain a read equally well, a person must decide (REVIEW)."},
    "context_breaks_tie": {"title": "Twin plates: context breaks the tie", "rubric": "1",
                           "what": "Same twins, but the camera also saw a make and colour that only one twin has.",
                           "why": "Contextual evidence from another source resolves what the string alone cannot."},
    "reverse_search": {"title": "Reverse search: registration to captures", "rubric": "1",
                       "what": "Every live camera capture that could be DL05CD9876.",
                       "why": "The 'vice versa' direction: capture 911 (exact) and 916 (8 read as B) must both be found."},
    "examiner_insert_dirty": {"title": "Examiner inserts a dirty capture", "rubric": "2",
                              "what": "A capture with colour 'Blu' is inserted; detected; R1 proposes Blue; applied; "
                                      "re-checked clean; undone; deleted.",
                              "why": "Insert -> detect -> cleanse -> undo, live, with no batch step anywhere."},
    "examiner_modify_conflict": {"title": "Examiner modifies a colour to contradict REG", "rubric": "2",
                                 "what": "A clean capture's colour is changed to a valid colour the registration denies.",
                                 "why": "A valid value that disagrees is clone evidence: flagged, never overwritten."},
    "examiner_delete": {"title": "Examiner deletes a capture", "rubric": "2",
                        "what": "An inserted dirty capture is deleted; the next check no longer lists it.",
                        "why": "No stale cache: a deleted record disappears from the report on the next read."},
    "misread_plate_cleansed": {"title": "Misread plate cleansed", "rubric": "1+2",
                               "what": "A capture whose plate is an OCR misread of a real vehicle is inserted; the "
                                       "proposal corrects it citing the matcher's probability; applied; undone; deleted.",
                               "why": "Rubric 1 and 2 meet: the cleansing rule trusts only the matcher's ACCEPT."},
    "source_down_refusal": {"title": "Insurance source down: refusal", "rubric": "1+2",
                            "what": "The local INS wrapper is stopped; Challan Guard verifies a sighting; INS is restarted.",
                            "why": "A partial federation must refuse (HOLD / UNDETERMINED), never fine on missing evidence."},
    "batch_stress": {"title": "Batch stress test", "rubric": "1",
                     "what": "N random benchmark reads (fixed seed) through the matcher: accuracy, ACCEPT precision, "
                             "REVIEW rate, latency.",
                     "why": "Pass if ACCEPT precision >= 0.98 and p95 latency < 200 ms."},
    "rdr_teach_refusal": {"title": "RDR refuses an over-general rule", "rubric": "2",
                          "what": "Teaching a rule that would change another rule's cornerstone conclusion (temp copy "
                                  "of the rules file).",
                          "why": "Ripple-Down Rules never break a case they were built on; the tree must stay unchanged."},
}


# ------------------------------------------------------------------ the run record

class _Run:
    """Steps, data and pending restores of one scenario run."""

    def __init__(self, registry: Registry) -> None:
        self.registry = registry
        self.steps: List[Step] = []
        self.data: Dict[str, Any] = {}
        self.pending: List[Tuple[str, Callable[[], str]]] = []
        self.changed = False
        self.restore_failed = False

    def step(self, title: str, detail: str, ok: Optional[bool] = None) -> None:
        self.steps.append(Step(title, detail, ok))

    def on_restore(self, title: str, fn: Callable[[], str]) -> None:
        """Register how to undo a data change just made (run LIFO by restore())."""
        self.changed = True
        self.pending.append((title, fn))

    def restore(self) -> None:
        while self.pending:
            title, fn = self.pending.pop()
            try:
                self.step(f"Restore: {title}", fn(), True)
            except Exception as exc:  # report it; keep undoing the rest
                self.restore_failed = True
                self.step(f"Restore: {title}", f"FAILED: {type(exc).__name__}: {exc}", False)


# ------------------------------------------------------------------ live reads

def _specs(registry: Registry) -> Tuple[Any, Any]:
    cam = next((s for s in registry.sources if s.authority == "observational" and "observed_colour" in s.covers), None)
    reg = next((s for s in registry.sources if s.identity_authority), None)
    if cam is None or reg is None:
        raise lexicons.SourceUnavailable("the registry names no camera source or no identity authority")
    return cam, reg


def _live_registry(registry: Registry) -> Dict[str, Dict[str, Any]]:
    """plate -> {make, model, colour}, read now from the identity authority (as the matcher wants it)."""
    _, reg = _specs(registry)
    with httpx.Client(trust_env=False) as client:
        rows = lexicons.fetch_all(reg, lexicons.primary_key(reg, client), client)
    out = {}
    for row in rows:
        g = cleansing._global(row, reg, True)
        plate = norm_plate(g.get("plate_number"))
        if plate:
            out[plate] = {"make": g.get("vehicle_make"), "model": g.get("vehicle_model"), "colour": g.get("vehicle_colour")}
    return out


def _match(read: str, reg: Dict[str, Dict[str, Any]], **ctx: Any) -> Dict[str, Any]:
    from mediator import plate_match
    t0 = time.perf_counter()
    ranked, stats = plate_match.match(read, registry=reg, **ctx)
    ms = (time.perf_counter() - t0) * 1000
    top = ranked[0] if ranked else None
    return {"read": norm_plate(read) or "", "decision": top.decision if top else "REJECT",
            "best": top.plate if top else None, "probability": round(top.probability, 4) if top else None,
            "elapsed_ms": round(ms, 1), "candidates": [
                {"plate": c.plate, "probability": round(c.probability, 4), "decision": c.decision}
                for c in ranked[:5]]}


def _describe(m: Dict[str, Any]) -> str:
    second = m["candidates"][1] if len(m["candidates"]) > 1 else None
    rival = f"; runner-up {second['plate']} p={second['probability']}" if second else ""
    return f"{m['read']} -> {m['decision']} {m['best']} (p={m['probability']}{rival}, {m['elapsed_ms']} ms)"


def char_diff(read: str, plate: str) -> List[Dict[str, Any]]:
    """Where a read differs from a plate: one row per substitution, insertion or deletion."""
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, read, plate, autojunk=False).get_opcodes():
        if op != "equal":
            out.append({"op": op, "position": i1 + 1, "read": read[i1:i2], "plate": plate[j1:j2]})
    return out


def _benchmark() -> List[Dict[str, str]]:
    with BENCHMARK.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _ocr(run: _Run, image: str) -> Tuple[str, Optional[float], str]:
    """Live EasyOCR read of a benchmark image; the recorded benchmark read when EasyOCR is missing."""
    path = ROOT / image
    try:
        from scripts.match_plate import run_ocr
        out = run_ocr(path.read_bytes())
        return out["raw"], out["confidence"], "live EasyOCR"
    except Exception as exc:  # no EasyOCR on this laptop: say so and use the recorded read
        row = next((r for r in _benchmark() if r["image_path"] == image), None)
        if row is None:
            raise
        run.step("OCR unavailable", f"{type(exc).__name__}: {exc}; using the read recorded in the benchmark CSV")
        conf = float(row["ocr_confidence"]) if row["ocr_confidence"] else None
        return row["ocr_read"], conf, "benchmark CSV (recorded EasyOCR read)"


# ------------------------------------------------------------------ rubric 1: matching

def _image_accepts(run: _Run, image: str, truth: str) -> Tuple[str, str, bool]:
    reg = _live_registry(run.registry)
    run.step("Registry read live", f"{len(reg)} registrations from the identity authority")
    read, conf, how = _ocr(run, image)
    run.step("OCR", f"{image}: read {read!r}, confidence {conf} ({how})")
    m = _match(read, reg, confidence=conf)
    run.data.update(image=image, read=read, confidence=conf, ocr=how, match=m, diff=char_diff(read, truth))
    ok = m["decision"] == "ACCEPT" and m["best"] == truth
    run.step("Match", _describe(m), ok)
    return f"ACCEPT {truth}", f"{m['decision']} {m['best']}", ok


def s_clean_image(run: _Run) -> Tuple[str, str, bool]:
    return _image_accepts(run, "data/plate_images/HR26EF4455__clean_s0.png", "HR26EF4455")


def s_two_errors_rotated(run: _Run) -> Tuple[str, str, bool]:
    exp, act, ok = _image_accepts(run, "data/plate_images/HR26EF4455__rotate_s2.png", "HR26EF4455")
    run.step("Errors in the read", ", ".join(f"{d['op']} at {d['position']}: {d['read']!r}->{d['plate']!r}"
                                             for d in run.data["diff"]) or "none")
    return exp, act, ok


def _read_accepts(run: _Run, read: str, truth: str, conf: Optional[float] = 0.9) -> Tuple[str, str, bool]:
    reg = _live_registry(run.registry)
    m = _match(read, reg, confidence=conf)
    diff = char_diff(norm_plate(read) or "", truth)
    run.data.update(match=m, diff=diff)
    run.step("Character diff", ", ".join(f"position {d['position']}: read {d['read']!r}, plate {d['plate']!r}"
                                         for d in diff) or "identical")
    ok = m["decision"] == "ACCEPT" and m["best"] == truth
    run.step("Match", _describe(m), ok)
    return f"ACCEPT {truth}", f"{m['decision']} {m['best']}", ok


def s_ocr_zero_o(run: _Run) -> Tuple[str, str, bool]:
    return _read_accepts(run, "DLO1AB1234", "DL01AB1234")


def s_ocr_b8(run: _Run) -> Tuple[str, str, bool]:
    reg = _live_registry(run.registry)
    ctx = cleansing.Context.build(run.registry)
    live = next((r for r in ctx.records if norm_plate(r.get("plate_number")) == "DL05CD9B76"), None)
    truth = reg.get("DL05CD9876", {})
    make = (live or {}).get("observed_make") or truth.get("make")
    colour = (live or {}).get("observed_colour") or truth.get("colour")
    conf = cleansing._number((live or {}).get("ocr_confidence")) or 0.9
    run.step("Camera context", f"capture {live['record_id'] if live else '(none live)'}: make {make!r}, colour {colour!r}, "
                               f"confidence {conf}")
    bare = _match("DL05CD9B76", reg, confidence=conf)
    run.step("String only", _describe(bare) + " -- the string alone is not enough")
    m = _match("DL05CD9B76", reg, confidence=conf, observed_make=make, observed_colour=colour)
    run.data.update(match=m, without_context=bare, diff=char_diff("DL05CD9B76", "DL05CD9876"))
    ok = m["decision"] == "ACCEPT" and m["best"] == "DL05CD9876"
    run.step("With camera make and colour", _describe(m), ok)
    return "ACCEPT DL05CD9876", f"{m['decision']} {m['best']}", ok


def s_unreadable_image(run: _Run) -> Tuple[str, str, bool]:
    reg = _live_registry(run.registry)
    rows, ok = [], True
    for image, truth in (("data/plate_images/DL01AB1234__occlude_s3.png", "DL01AB1234"),
                         ("data/plate_images/HR26EF4455__motion_s3.png", "HR26EF4455")):
        read, conf, how = _ocr(run, image)
        m = _match(read, reg, confidence=conf) if read else {"read": "", "decision": "REJECT", "best": None,
                                                              "probability": None, "elapsed_ms": 0, "candidates": []}
        wrong = m["decision"] == "ACCEPT" and m["best"] != truth
        ok &= not wrong
        rows.append({"image": image, "truth": truth, "read": read, "confidence": conf, "decision": m["decision"],
                     "best": m["best"], "probability": m["probability"], "wrong_accept": wrong})
        run.step(f"OCR + match ({Path(image).name})", f"read {read!r} ({how}); " + _describe(m), not wrong)
    run.data["rows"] = rows
    return "REVIEW or REJECT (no wrong ACCEPT)", ", ".join(r["decision"] for r in rows), ok


def s_unregistered_plate(run: _Run) -> Tuple[str, str, bool]:
    reg = _live_registry(run.registry)
    registered = "DL09QQ9009" in reg
    run.step("Registry check", f"DL09QQ9009 is {'REGISTERED (scenario invalid)' if registered else 'not registered'}",
             not registered)
    m = _match("DL09QQ9009", reg, confidence=0.9)
    run.data["match"] = m
    ok = m["decision"] == "REJECT" and not registered
    run.step("Match", _describe(m), ok)
    return "REJECT", m["decision"], ok


# ------------------------------------------------------------------ twin plates (REG admin writes)

TWIN_OF, TWIN, TWIN_READ = "DL01AB1234", "DLO1AB1234", "DLD1AB1234"


def _insert_twin(run: _Run) -> Dict[str, Any]:
    """Clone DL01AB1234's own REG row as DLO1AB1234 with another real make/model/colour."""
    _, reg = _specs(run.registry)
    pk, col = lexicons.primary_key(reg), cleansing._column(reg, "plate_number")
    rows = lexicons._post(reg, f"SELECT * FROM {reg.table} WHERE {col} = "
                               f"{lexicons.sql_literal(render_plate(TWIN_OF, reg.key_predicate.render))}", None)["rows"]
    if not rows:
        raise cleansing.CleansingError(f"{TWIN_OF} is not registered; the twin scenario needs it")
    live = _live_registry(run.registry)
    if TWIN in live:
        raise cleansing.CleansingError(f"{TWIN} is already registered; refusing to touch a row this scenario did not make")
    own = live[TWIN_OF]
    # The twin's make/model/colour are another real vehicle's, in REG's own raw spelling (the live
    # registry above went through display transforms): the commonest such vehicle, so it is deterministic.
    mk, md, cl = (cleansing._column(reg, f"vehicle_{a}") for a in ("make", "model", "colour"))
    others = [(r[mk], r[md], r[cl]) for r in lexicons.fetch_all(reg, pk)
              if r.get(mk) and r.get(md) and r.get(cl) and r[mk] != rows[0][mk] and r[cl] != rows[0][cl]]
    make, model, colour = max(sorted(set(others)), key=others.count)
    top = lexicons._post(reg, f"SELECT MAX({pk}) AS m FROM {reg.table}", None)["rows"]
    new_id = int((top[0].get("m") if top else 0) or 0) + 1
    row = {**rows[0], pk: new_id, col: render_plate(TWIN, reg.key_predicate.render),
           cleansing._column(reg, "vehicle_make"): make, cleansing._column(reg, "vehicle_model"): model,
           cleansing._column(reg, "vehicle_colour"): colour}
    sql = (f"INSERT INTO {reg.table} ({', '.join(row)}) VALUES "
           f"({', '.join(lexicons.sql_literal(v) for v in row.values())})")
    if cleansing._admin(reg, sql) != 1:
        raise cleansing.CleansingError(f"{reg.source_id} inserted nothing")

    def undo() -> str:
        gone = f"DELETE FROM {reg.table} WHERE {pk} = {lexicons.sql_literal(new_id)}"
        n = cleansing._admin(reg, gone)
        still = TWIN in _live_registry(run.registry)
        if still:
            raise cleansing.CleansingError(f"{TWIN} is still registered after {gone}")
        return f"{gone} ({n} row); {TWIN} no longer registered"
    run.on_restore(f"delete twin registration {TWIN}", undo)
    twin = {"plate": TWIN, "make": make, "model": model, "colour": colour}
    run.step("Insert twin through REG /admin/sql", sql, True)
    run.data.update(twin=twin, original={"plate": TWIN_OF, **own}, sql_insert=sql)
    return {"original": own, "twin": twin}


def s_ambiguous_twins(run: _Run) -> Tuple[str, str, bool]:
    _insert_twin(run)
    reg = _live_registry(run.registry)
    run.step("Registry re-read live", f"{TWIN_OF} and {TWIN} both registered: {TWIN_OF in reg and TWIN in reg}",
             TWIN_OF in reg and TWIN in reg)
    m = _match(TWIN_READ, reg, confidence=0.8)
    run.data["match"] = m
    ok = m["decision"] == "REVIEW" and m["best"] in (TWIN_OF, TWIN)
    run.step(f"Match the ambiguous read {TWIN_READ}", _describe(m), ok)
    run.restore()
    return "REVIEW (two registered vehicles explain the read)", f"{m['decision']} {m['best']}", ok


def s_context_breaks_tie(run: _Run) -> Tuple[str, str, bool]:
    pair = _insert_twin(run)
    reg = _live_registry(run.registry)
    own, twin = pair["original"], pair["twin"]
    bare = _match(TWIN_READ, reg, confidence=0.8)
    run.step("String only", _describe(bare))
    m = _match(TWIN_READ, reg, confidence=0.8, observed_make=own["make"], observed_colour=own["colour"])
    ok = m["decision"] == "ACCEPT" and m["best"] == TWIN_OF
    run.step(f"Camera saw {own['make']} / {own['colour']}", _describe(m), ok)
    other = _match(TWIN_READ, reg, confidence=0.8, observed_make=twin["make"], observed_colour=twin["colour"])
    run.step(f"Had it seen {twin['make']} / {twin['colour']}", _describe(other))
    run.data.update(without_context=bare, match=m, twin_context=other)
    run.restore()
    return f"ACCEPT {TWIN_OF}", f"{m['decision']} {m['best']}", ok


def s_reverse_search(run: _Run) -> Tuple[str, str, bool]:
    from scripts.match_plate import _capture, reverse_rows
    reg = _live_registry(run.registry)
    ctx = cleansing.Context.build(run.registry)
    captures = [_capture(r["_raw"], ctx.cam, ctx.pk) for r in ctx.records]
    rows = reverse_rows("DL05CD9876", captures, reg)
    table = [{k: r.get(k) for k in ("capture_id", "read", "camera", "time", "observed_make", "observed_colour",
                                    "probability", "decision")} for r in rows]
    run.data["rows"] = table
    accepted = {str(r["capture_id"]) for r in rows if r["decision"] == "ACCEPT"}
    run.step("Captures scanned live", f"{len(captures)} captures; {len(rows)} could be DL05CD9876")
    for r in table[:6]:
        run.step(f"Capture {r['capture_id']}", f"read {r['read']} at {r['camera']}: {r['decision']} p={r['probability']:.3f}")
    ok = {"911", "916"} <= accepted
    run.step("911 and 916 accepted", f"accepted: {', '.join(sorted(accepted)) or 'none'}", ok)
    return "captures 911 and 916 ACCEPT", f"ACCEPT {', '.join(sorted(accepted)) or 'none'}", ok


# ------------------------------------------------------------------ rubric 2: examiner edits on CAM

def _quiet_vehicle(ctx: cleansing.Context, colour: Optional[str] = None) -> Tuple[str, Dict[str, Any]]:
    """A registered vehicle with no captures yet, so an inserted capture meets no duplicate or
    impossible-travel sibling and the check under test is the only one that fires. Its make, model and
    colour are returned in the lexicon's own spelling: the registry's profile values went through the
    display transforms (XUV700 -> Xuv700), and a capture written with those would itself be dirty."""
    for plate in sorted(ctx.profiles):
        p = ctx.profiles[plate]
        if ctx.by_plate.get(plate):
            continue
        canon = {}
        for attr in ("make", "model", "colour"):
            value = p.get(f"vehicle_{attr}")
            canon[attr] = ctx.lex.for_attr(attr).lookup(value)[0] if value else None
        if not all(canon.values()) or (colour and canon["colour"] != colour):
            continue
        if all(ctx.lex.for_attr(a).lookup(v)[1] == "exact" for a, v in canon.items()):
            return plate, {"vehicle_make": canon["make"], "vehicle_model": canon["model"],
                           "vehicle_colour": canon["colour"]}
    raise cleansing.CleansingError(f"no registered {colour or ''} vehicle without captures to test with")


def _insert_capture(run: _Run, ctx: cleansing.Context, values: Dict[str, Any]) -> int:
    """Insert one capture through CAM's /admin/sql: an existing row's camera, the given global values."""
    cam, pk = ctx.cam, ctx.pk
    template = cleansing._fetch_row(cam, pk, ctx.records[0]["record_id"])  # the table's own columns only
    top = lexicons._post(cam, f"SELECT MAX({pk}) AS m FROM {cam.table}", None)["rows"]
    new_id = int((top[0].get("m") if top else 0) or 0) + 1
    moment, _ = cleansing.parse_ts(ctx.records[0].get(cleansing.TIME))
    values = {cleansing.TIME: cleansing.render_ts(moment + timedelta(minutes=45), ctx.ts_format), **values}
    row = {**template, pk: new_id}
    for g, v in values.items():
        row[cleansing._column(cam, g)] = render_plate(v, cam.key_predicate.render) if g == cleansing.PLATE else v
    sql = f"INSERT INTO {cam.table} ({', '.join(row)}) VALUES ({', '.join(lexicons.sql_literal(v) for v in row.values())})"
    if cleansing._admin(cam, sql) != 1:
        raise cleansing.CleansingError(f"{cam.source_id} inserted nothing")

    def undo() -> str:
        gone = f"DELETE FROM {cam.table} WHERE {pk} = {new_id}"
        n = cleansing._admin(cam, gone)
        if any(str(r["record_id"]) == str(new_id) for r in cleansing.Context.build(run.registry).records):
            raise cleansing.CleansingError(f"capture {new_id} still present after {gone}")
        return f"{gone} ({n} row); capture {new_id} gone from a fresh live read"
    run.on_restore(f"delete inserted capture {new_id}", undo)
    run.step("Insert capture through CAM /admin/sql", sql, True)
    run.data.setdefault("sql", []).append(sql)
    return new_id


def _modify(ctx: cleansing.Context, record_id: int, fld: str, value: Any) -> str:
    sql = (f"UPDATE {ctx.cam.table} SET {cleansing._column(ctx.cam, fld)} = {lexicons.sql_literal(value)} "
           f"WHERE {ctx.pk} = {record_id}")
    if cleansing._admin(ctx.cam, sql) != 1:
        raise cleansing.CleansingError(f"capture {record_id} not modified")
    return sql


def _check(run: _Run, record_id: int, label: str) -> Tuple[cleansing.Context, cleansing.Proposal]:
    """Re-read CAM and REG live and run detection + RDR on the one record (scan() would take ~25 s)."""
    ctx = cleansing.Context.build(run.registry)
    prop = cleansing.propose(ctx.record(record_id), ctx)
    codes = [a.code for a in prop.anomalies]
    fixes = [f"{f.field}: {f.action} {f.before!r}->{f.after!r} ({f.rule})" for f in prop.changes]
    run.step(label, f"anomalies {codes or 'none'}; proposals {fixes or 'none'}")
    run.data.setdefault("checks", []).append({"label": label, "anomalies": codes, "proposals": fixes})
    return ctx, prop


def _fix(prop: cleansing.Proposal, fld: str) -> cleansing.FieldFix:
    return next(f for f in prop.fixes if f.field == fld)


def s_examiner_insert_dirty(run: _Run) -> Tuple[str, str, bool]:
    ctx = cleansing.Context.build(run.registry)
    plate, p = _quiet_vehicle(ctx, "Blue")
    rid = _insert_capture(run, ctx, {cleansing.PLATE: plate, "observed_make": p["vehicle_make"],
                                     "observed_model": p["vehicle_model"], "observed_colour": "Blu",
                                     cleansing.CONF: 0.93})
    ctx, prop = _check(run, rid, f"Check capture {rid} (registered {plate}, {p['vehicle_colour']})")
    colour = _fix(prop, "observed_colour")
    detected = "COLOUR_NOT_IN_LEXICON" in [a.code for a in prop.anomalies]
    proposed = (colour.action, colour.after, colour.rule) == ("set", "Blue", "R1")
    run.step("Detected and R1 proposes Blue", f"{colour.action} -> {colour.after!r} by {colour.rule}: {colour.because}",
             detected and proposed)
    single = cleansing.Proposal(prop.source_id, prop.record_id, prop.record, prop.anomalies, [colour])
    done = cleansing.apply(single, actor="scenario", ctx=ctx)
    run.step("Apply", done[0]["sql"], True)
    _, after = _check(run, rid, "Re-check after apply")
    clean = not after.anomalies
    run.step("Re-check is clean", "no anomaly left" if clean else str([a.code for a in after.anomalies]), clean)
    undone = cleansing.undo(done[0]["log_id"], actor="scenario")
    _, back = _check(run, rid, "Re-check after undo")
    reverted = _fix(back, "observed_colour").before == "Blu"
    run.step("Undo restored 'Blu'", undone["sql"], reverted)
    run.restore()
    ok = detected and proposed and clean and reverted
    return "detected, R1 -> Blue, clean after apply, 'Blu' after undo", \
        f"detected={detected}, proposal={colour.action} {colour.after!r}, clean={clean}, undone={reverted}", ok


def s_examiner_modify_conflict(run: _Run) -> Tuple[str, str, bool]:
    ctx = cleansing.Context.build(run.registry)
    plate, p = _quiet_vehicle(ctx)
    lex = ctx.lex.for_attr("colour")
    other = sorted({lex.lookup(v["vehicle_colour"])[0] or v["vehicle_colour"] for v in ctx.profiles.values()
                    if v.get("vehicle_colour")} - {p["vehicle_colour"]})[0]  # a valid colour REG denies
    rid = _insert_capture(run, ctx, {cleansing.PLATE: plate, "observed_make": p["vehicle_make"],
                                     "observed_model": p["vehicle_model"], "observed_colour": p["vehicle_colour"],
                                     cleansing.CONF: 0.93})
    _, first = _check(run, rid, f"Check clean capture {rid} ({plate}, {p['vehicle_colour']})")
    run.step("Starts clean", "no anomaly" if not first.anomalies else str([a.code for a in first.anomalies]),
             not first.anomalies)
    run.step("Examiner modifies the colour", _modify(ctx, rid, "observed_colour", other), True)
    _, prop = _check(run, rid, f"Check after colour -> {other}")
    colour = _fix(prop, "observed_colour")
    flagged = "COLOUR_DISAGREES_REGISTRATION" in [a.code for a in prop.anomalies] and colour.action == "flag"
    run.step("Flagged as clone evidence, not overwritten", f"{colour.action} by {colour.rule}: {colour.because}", flagged)
    run.step("Restore the colour", _modify(ctx, rid, "observed_colour", p["vehicle_colour"]), True)
    _, back = _check(run, rid, "Check after restoring the colour")
    run.restore()
    ok = flagged and not first.anomalies and not back.anomalies
    return "COLOUR_DISAGREES_REGISTRATION, action flag (R4)", f"{colour.action} by {colour.rule}", ok


def s_examiner_delete(run: _Run) -> Tuple[str, str, bool]:
    ctx = cleansing.Context.build(run.registry)
    plate, p = _quiet_vehicle(ctx, "Blue")
    rid = _insert_capture(run, ctx, {cleansing.PLATE: plate, "observed_make": p["vehicle_make"],
                                     "observed_model": p["vehicle_model"], "observed_colour": "Blu",
                                     cleansing.CONF: 0.93})
    _, prop = _check(run, rid, f"Check capture {rid}")
    seen = bool(prop.anomalies)
    run.step("Visible with its anomaly", str([a.code for a in prop.anomalies]), seen)
    run.restore()  # the delete *is* the examiner's action here, sent through the same admin path
    present = any(str(r["record_id"]) == str(rid) for r in cleansing.Context.build(run.registry).records)
    run.step("Gone from the next live read", f"capture {rid} {'STILL PRESENT' if present else 'absent'}", not present)
    return "capture absent after delete", "absent" if not present else "still present", seen and not present


def _misread(plate: str, reg: Dict[str, Dict[str, Any]], p: Dict[str, Any]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """The first one-character OCR confusion of `plate` the matcher ACCEPTs back to it (with context)."""
    for a, b in (("8", "B"), ("0", "O"), ("5", "S"), ("1", "I"), ("2", "Z"), ("6", "G")):
        for i in range(len(plate) - 1, 1, -1):
            if plate[i] == a:
                read = plate[:i] + b + plate[i + 1:]
                if read in reg:
                    continue
                m = _match(read, reg, confidence=0.9, observed_make=p["vehicle_make"],
                           observed_colour=p["vehicle_colour"])
                if m["decision"] == "ACCEPT" and m["best"] == plate:
                    return read, m
    return None


def s_misread_plate_cleansed(run: _Run) -> Tuple[str, str, bool]:
    ctx = cleansing.Context.build(run.registry)
    reg = {k: {"make": v.get("vehicle_make"), "model": v.get("vehicle_model"), "colour": v.get("vehicle_colour")}
           for k, v in ctx.profiles.items()}
    found = None
    for plate in sorted(ctx.profiles):
        p = ctx.profiles[plate]
        if ctx.by_plate.get(plate) or not all(p.get(k) for k in ("vehicle_make", "vehicle_colour")):
            continue
        found = _misread(plate, reg, p)
        if found:
            break
    if not found:
        raise cleansing.CleansingError("no registered vehicle has a confusable misread the matcher accepts")
    read, m = found
    run.step("Misread chosen", f"{plate} read as {read}; matcher with context: {_describe(m)}")
    rid = _insert_capture(run, ctx, {cleansing.PLATE: read, "observed_make": p["vehicle_make"],
                                     "observed_model": p.get("vehicle_model"), "observed_colour": p["vehicle_colour"],
                                     cleansing.CONF: 0.9})
    ctx, prop = _check(run, rid, f"Check capture {rid}")
    fix = _fix(prop, cleansing.PLATE)
    proposed = fix.action == "set" and fix.after == plate and "probability" in fix.because
    run.step("Proposal corrects the plate citing the matcher", f"{fix.action} {fix.before!r}->{fix.after!r} by "
                                                               f"{fix.rule}: {fix.because}", proposed)
    run.data.update(misread=read, plate=plate, match=m, because=fix.because)
    if not proposed:
        run.restore()
        return f"set {read} -> {plate}", f"{fix.action} {fix.after!r}", False
    single = cleansing.Proposal(prop.source_id, prop.record_id, prop.record, prop.anomalies, [fix])
    done = cleansing.apply(single, actor="scenario", ctx=ctx)
    col = cleansing._column(ctx.cam, cleansing.PLATE)
    stored = cleansing._fetch_row(ctx.cam, ctx.pk, rid)[col]
    applied = norm_plate(stored) == plate
    run.step("Apply", f"{done[0]['sql']} -> stored {stored!r}", applied)
    undone = cleansing.undo(done[0]["log_id"], actor="scenario")
    stored = cleansing._fetch_row(ctx.cam, ctx.pk, rid)[col]
    reverted = norm_plate(stored) == read
    run.step("Undo", f"{undone['sql']} -> stored {stored!r}", reverted)
    run.restore()
    return f"set {read} -> {plate}, applied, undone", f"{fix.action} {fix.after}, applied={applied}, undone={reverted}", \
        proposed and applied and reverted


# ------------------------------------------------------------------ failure: a source goes down

def s_source_down_refusal(run: _Run) -> Tuple[str, str, Optional[bool]]:
    from mediator import challan_guard
    from mediator.core import run_global_query
    from sources import server_manager
    meta = catalog.get_source_catalog().get("INS") or {}
    target = server_manager.local_target(meta.get("base_url", ""))
    cluster = server_manager.get_cluster()
    if target is None or "INS" not in cluster.servers:
        why = ("INS runs on another laptop" if target is None else
               "INS on this machine was not started by this process, and a process we did not start is never killed")
        run.step("Skipped", why, None)
        run.data["skipped"] = why
        return "HOLD with INS down", f"skipped: {why}", None
    host, port = target
    case = {"plate_read": "DL01AB1234", "captured_at": "2026-09-04T09:00:00", "location": "scenario",
            "lat": None, "lon": None, "observed_make": "Hyundai", "observed_colour": "White"}

    def bring_back() -> str:
        cluster.resume("INS")
        state = server_manager.ensure_local_sources({"INS": meta}, cluster=cluster)["INS"]
        r = httpx.get(f"http://{host}:{port}/health", timeout=3, trust_env=False)
        if r.status_code != 200:
            raise cleansing.CleansingError(f"INS {state} but /health answered {r.status_code}")
        return f"INS {state}; /health 200"
    cluster.suspend("INS")
    run.on_restore("restart INS", bring_back)
    deadline = time.monotonic() + 5
    while server_manager.is_port_in_use(port, host) and time.monotonic() < deadline:
        time.sleep(0.05)
    down = not server_manager.is_port_in_use(port, host)
    run.step("INS stopped", f"port {port} {'closed' if down else 'STILL OPEN'}", down)
    result = challan_guard._verification(case)  # decides without saving a case
    verdict, reason = result["verdict"], result["reason"]
    held = verdict == challan_guard.HOLD
    run.step("Challan Guard verifies DL01AB1234", f"{verdict}: {reason}", held)
    profile = run_global_query("DL01AB1234")["profile"]
    undetermined = profile.get("decision") == "UNDETERMINED"
    run.step("Federated profile decision", f"{profile.get('decision')} ({profile.get('source_availability')})",
             undetermined)
    run.data.update(verdict=verdict, reason=reason, profile_decision=profile.get("decision"),
                    availability=profile.get("source_availability"))
    run.restore()
    after = challan_guard._verification(case)
    run.step("With INS back", f"{after['verdict']}: {after['reason']}")
    return "HOLD (and UNDETERMINED profile)", f"{verdict} / {profile.get('decision')}", held and undetermined


# ------------------------------------------------------------------ batch and RDR

def s_batch_stress(run: _Run, n: int = 100) -> Tuple[str, str, bool]:
    reg = _live_registry(run.registry)
    rows = random.Random(SEED).sample(_benchmark(), min(n, len(_benchmark())))
    _match("DL01AB1234", reg)  # warm-up: model bundle and q-gram index load once, not inside the timings
    out, lat = [], []
    for r in rows:
        conf = float(r["ocr_confidence"]) if r["ocr_confidence"] else None
        m = _match(r["ocr_read"], reg, confidence=conf)
        lat.append(m["elapsed_ms"])
        out.append({"image": Path(r["image_path"]).name, "truth": r["true_plate"], "read": r["ocr_read"],
                    "decision": m["decision"], "best": m["best"], "probability": m["probability"],
                    "correct": m["best"] == r["true_plate"], "elapsed_ms": m["elapsed_ms"]})
    registered = [o for o in out if o["truth"] in reg]
    accepts = [o for o in out if o["decision"] == "ACCEPT"]
    precision = sum(o["correct"] for o in accepts) / len(accepts) if accepts else 1.0
    lat_sorted = sorted(lat)
    p95 = lat_sorted[max(0, int(round(0.95 * len(lat_sorted))) - 1)]
    summary = {"n": len(out), "registered": len(registered),
               "accuracy": round(sum(o["correct"] for o in registered) / max(1, len(registered)), 3),
               "accept_precision": round(precision, 3), "accepts": len(accepts),
               "review_rate": round(sum(o["decision"] == "REVIEW" for o in out) / len(out), 3),
               "reject_rate": round(sum(o["decision"] == "REJECT" for o in out) / len(out), 3),
               "p50_ms": round(statistics.median(lat), 1), "p95_ms": round(p95, 1), "max_ms": round(max(lat), 1),
               "seed": SEED}
    run.data.update(summary=summary, rows=out, latencies_ms=lat)
    run.step("Reads", f"{len(out)} benchmark reads (seed {SEED}), {len(registered)} of registered vehicles")
    run.step("Accuracy (top-1, registered)", str(summary["accuracy"]))
    run.step("ACCEPT precision", f"{summary['accept_precision']} over {len(accepts)} ACCEPTs", precision >= 0.98)
    run.step("REVIEW / REJECT rate", f"{summary['review_rate']} / {summary['reject_rate']}")
    run.step("Latency", f"p50 {summary['p50_ms']} ms, p95 {summary['p95_ms']} ms, max {summary['max_ms']} ms",
             p95 < 200)
    return "ACCEPT precision >= 0.98 and p95 < 200 ms", \
        f"precision {summary['accept_precision']}, p95 {summary['p95_ms']} ms", precision >= 0.98 and p95 < 200


def s_rdr_teach_refusal(run: _Run) -> Tuple[str, str, bool]:
    import copy
    import hashlib
    import shutil
    import tempfile
    from mediator.rdr import RDRConflict, RDRTree
    real_hash = hashlib.sha256(cleansing.RULES_PATH.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "rdr_rules.json"
        shutil.copy(cleansing.RULES_PATH, path)
        tree = RDRTree.load(path)
        before = json.dumps(tree.to_dict(), sort_keys=True)
        r3 = tree.find("R3")
        case = {**copy.deepcopy(r3.cornerstone), "value": "Maroothi", "record_id": "scenario"}
        run.step("New case", f"observed_make {case['value']!r}; it reaches R3 (enrich from registration) "
                             f"like R3's own cornerstone {r3.cornerstone.get('value')!r}")
        try:
            tree.add_exception(case, "R3", {"field": "registered", "op": "is_true"},
                               {"action": "flag", "flag": "REPAINT_SUSPECT"}, "over-general rule (scenario)")
            refused, message = False, "ACCEPTED (the tree should have refused)"
        except RDRConflict as exc:
            refused, message = True, str(exc)
        run.step("Teach 'registered => flag' under R3", message, refused)
        unchanged = json.dumps(tree.to_dict(), sort_keys=True) == before and \
            json.dumps(RDRTree.load(path).to_dict(), sort_keys=True) == before
        run.step("Tree unchanged (memory and temp file)", "identical" if unchanged else "CHANGED", unchanged)
        narrow = tree.add_exception(case, "R3", {"field": "value", "op": "eq", "value": "Maroothi"},
                                    {"action": "set", "value": "$registered_value"}, "narrow rule (scenario)")
        run.step("A narrow rule on the same case is accepted (temp copy only)", f"{narrow.id} under R3")
    untouched = hashlib.sha256(cleansing.RULES_PATH.read_bytes()).hexdigest() == real_hash
    run.step("Real data/rdr_rules.json untouched", "sha256 unchanged" if untouched else "CHANGED", untouched)
    run.data.update(refusal=message, rules_file_unchanged=untouched)
    return "refused; tree unchanged", ("refused" if refused else "accepted") + \
        ("; unchanged" if unchanged else "; changed"), refused and unchanged and untouched


# ------------------------------------------------------------------ runner

_FUNCS: Dict[str, Callable[..., Any]] = {
    "clean_image": s_clean_image, "ocr_zero_o": s_ocr_zero_o, "ocr_b8": s_ocr_b8,
    "two_errors_rotated": s_two_errors_rotated, "unreadable_image": s_unreadable_image,
    "unregistered_plate": s_unregistered_plate, "ambiguous_twins": s_ambiguous_twins,
    "context_breaks_tie": s_context_breaks_tie, "reverse_search": s_reverse_search,
    "examiner_insert_dirty": s_examiner_insert_dirty, "examiner_modify_conflict": s_examiner_modify_conflict,
    "examiner_delete": s_examiner_delete, "misread_plate_cleansed": s_misread_plate_cleansed,
    "source_down_refusal": s_source_down_refusal, "batch_stress": s_batch_stress,
    "rdr_teach_refusal": s_rdr_teach_refusal,
}


def run_scenario(key: str, *, registry: Optional[Registry] = None, batch_n: int = 100) -> ScenarioResult:
    """Run one scenario. Never raises: a failure is a result with the exception in its last step."""
    meta = SCENARIOS[key]
    t0 = time.perf_counter()
    run: Optional[_Run] = None
    expected, actual, passed = meta["why"], "", False
    try:
        run = _Run(registry or load_registry(catalog.META_DB_PATH))
        kwargs = {"n": batch_n} if key == "batch_stress" else {}
        expected, actual, passed = _FUNCS[key](run, **kwargs)
    except Exception as exc:  # a scenario that breaks is a failed scenario, never a crashed page
        run = run or _Run(None)  # type: ignore[arg-type]
        actual = f"error: {type(exc).__name__}: {exc}"
        run.step("Error", actual, False)
        passed = False
    finally:
        if run is not None and run.pending:
            run.restore()  # failed half way: undo what was changed before reporting
    restored = bool(run.changed and not run.pending and not run.restore_failed)
    return ScenarioResult(key, meta["title"], meta["rubric"], expected, actual, passed, run.steps,
                          json.loads(json.dumps(run.data, default=str)),
                          round((time.perf_counter() - t0) * 1000, 1), restored)


def run_all(keys: Optional[List[str]] = None) -> List[ScenarioResult]:
    """Run scenarios one after another (they share the sources, so never in parallel)."""
    return [run_scenario(k) for k in (keys or list(SCENARIOS))]


def main(argv: Optional[List[str]] = None) -> int:
    results = run_all(list(argv) if argv else None)
    for r in results:
        verdict = "SKIP" if r.passed is None else ("PASS" if r.passed else "FAIL")
        print(f"{verdict:4}  {r.key:26} {r.elapsed_ms:9.0f} ms  restored={r.restored!s:5}  {r.actual}")
    return 0 if all(r.passed is not False for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
