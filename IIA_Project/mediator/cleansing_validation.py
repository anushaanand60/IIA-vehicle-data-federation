"""Does cleansing WORK, not just run? Labelled noise injection and measurement (rubric 2, workstream G).

Method (offline and reproducible; it never writes to a source):

1. Snapshot the registration authority and the camera source -- live through the wrappers'
   read-only /query, or with `local_db=True` straight from this laptop's SQLite copies.
2. Keep the CLEAN captures: those for which `cleansing.detect` raises nothing against the full
   snapshot (registered read, make/model/colour present and agreeing with the registration,
   parseable timestamp, confidence above the floor, no duplicate, no impossible travel).
3. Draw a fixed-seed sample and give each record one labelled noise type (round robin over a
   shuffled sample), or leave it untouched as a control:

       colour_misspelling      Gry, Blu, Whte ...            -> standardise to the clean colour
       make_misspelling        Maruthi, Hyundia, Toyta ...   -> standardise to the clean make
       plate_misread_single    one OCR confusion, drawn with the benchmark's own frequencies
       plate_misread_double    two of them                   -> correct to the clean plate, or quarantine
       invalid_shape           one character of the number dropped
       duplicate               the same capture again, later id -> delete the copy
       timestamp_unparseable   a time no format reads        -> quarantine
       timestamp_future        a time after the present      -> quarantine
       low_confidence          confidence below the floor and no make/colour to corroborate -> quarantine
       low_confidence_corroborated  below the floor, make and colour agree -> keep (by design, R8)
       clone_swap              make/model/colour of another real vehicle -> FLAG, never overwrite
       control                 untouched                     -> nothing

4. Run `cleansing.propose` (detection + RDR) on the noisy set in memory, with a Context built from the
   snapshot, so the plate matcher and the lexicons see exactly what they would see live.
5. Score each record against its label: detection recall, false alarms on the controls (precision),
   correction accuracy (the proposed value equals the original clean value), quarantine rate, and
   wrong corrections -- a value set that is not the original. For plates that number must be 0.
"""
from __future__ import annotations

import copy
import json
import random
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from mediator import catalog, cleansing, lexicons
from mediator.rdr import RDRTree
from mediator.registry_loader import load_registry
from mediator.transforms import norm_plate

ROOT = Path(__file__).resolve().parents[1]
CONFUSIONS = ROOT / "data" / "confusion_costs.json"
OUT_JSON = ROOT / "data" / "cleansing_validation.json"
SEED = 20261017
PLATE, TIME, CONF = cleansing.PLATE, cleansing.TIME, cleansing.CONF

COLOUR_TYPOS = {"Grey": ["Gry", "Gray", "Grye"], "Blue": ["Blu", "Bleu"], "White": ["Whte", "Wite"],
                "Black": ["Blak", "Blck"], "Red": ["Redd", "Rde"], "Silver": ["Silvr", "Silvar"]}
MAKE_TYPOS = {"Maruti Suzuki": ["Maruthi", "Maruti Suzki"], "Hyundai": ["Hyundia", "Hundai"],
              "Tata": ["Tatta", "Taata"], "Honda": ["Hondaa", "Honada"], "Kia": ["Kiaa"],
              "Mahindra": ["Mahendra", "Mahindr"], "Toyota": ["Toyta", "Toyata"]}

# type -> (anomaly codes that count as "detected", field judged, expected action)
EXPECT: Dict[str, Tuple[Tuple[str, ...], Optional[str], Optional[str]]] = {
    "control": ((), None, None),
    "colour_misspelling": (("COLOUR_NOT_IN_LEXICON",), "observed_colour", "set"),
    "make_misspelling": (("MAKE_NOT_IN_LEXICON",), "observed_make", "set"),
    "plate_misread_single": (("PLATE_NOT_REGISTERED",), PLATE, "set"),
    "plate_misread_double": (("PLATE_NOT_REGISTERED",), PLATE, "set"),
    "invalid_shape": (("PLATE_SHAPE_INVALID", "PLATE_NOT_REGISTERED"), PLATE, "set"),
    "duplicate": (("DUPLICATE_CAPTURE",), "record", "delete"),
    "timestamp_unparseable": (("TIMESTAMP_UNPARSEABLE",), TIME, "quarantine"),
    "timestamp_future": (("TIMESTAMP_FUTURE",), TIME, "quarantine"),
    "low_confidence": (("LOW_OCR_CONFIDENCE",), PLATE, "quarantine"),
    "low_confidence_corroborated": ((), None, None),
    "clone_swap": (("MAKE_DISAGREES_REGISTRATION", "COLOUR_DISAGREES_REGISTRATION"), "observed_colour", "flag"),
}
NO_ANOMALY_EXPECTED = ("control", "low_confidence_corroborated")


# ------------------------------------------------------------------ snapshot

def _local_rows(spec: Any) -> Tuple[str, List[Dict[str, Any]]]:
    from sources.defaults import sqlite_default
    path = sqlite_default(spec.source_id).replace("sqlite:///", "")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        pk = next(r["name"] for r in con.execute(f"PRAGMA table_info({spec.table})") if r["pk"])
        sql = lexicons._page_sql(spec, pk, None).replace(f" LIMIT {lexicons.PAGE_SIZE}", "")
        return pk, [dict(r) for r in con.execute(sql)]
    finally:
        con.close()


def snapshot(local_db: bool = False) -> cleansing.Context:
    """A Context over every capture and registration, read once (read-only either way)."""
    registry = load_registry(catalog.META_DB_PATH)
    cam = next(s for s in registry.sources if s.authority == "observational" and "observed_colour" in s.covers)
    reg = next(s for s in registry.sources if s.identity_authority)
    if local_db:
        pk, cam_rows = _local_rows(cam)
        _, reg_rows = _local_rows(reg)
    else:
        with httpx.Client(trust_env=False) as client:
            pk = lexicons.primary_key(cam, client)
            cam_rows = lexicons.fetch_all(cam, pk, client)
            reg_rows = lexicons.fetch_all(reg, lexicons.primary_key(reg, client), client)
    return cleansing.Context(registry, cam, reg, pk, cam_rows, reg_rows, RDRTree.load(cleansing.RULES_PATH))


def clean_records(ctx: cleansing.Context) -> List[Dict[str, Any]]:
    out = []
    for rec in ctx.records:
        if all(rec.get(f) for f in ("observed_make", "observed_model", "observed_colour")) and \
                not cleansing.detect(rec, ctx.profile_for(rec), ctx):
            out.append(rec)
    return out


# ------------------------------------------------------------------ noise

def _confusions() -> Dict[str, List[Tuple[str, int]]]:
    """char -> [(replacement, count)], symmetric, from the benchmark's measured substitutions."""
    table: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    for p in json.loads(CONFUSIONS.read_text(encoding="utf-8"))["pairs"]:
        table[p["a"]].append((p["b"], p["count"]))
        table[p["b"]].append((p["a"], p["count"]))
    return table


def misread(plate: str, k: int, rng: random.Random, conf: Dict[str, List[Tuple[str, int]]],
            registered: set) -> Optional[str]:
    """`plate` with k OCR confusions at distinct positions, weighted by observed count; never a registered plate."""
    for _ in range(50):
        chars, used = list(plate), set()
        for _ in range(k):
            spots = [(i, b, n) for i, c in enumerate(chars) if i not in used for b, n in conf.get(c, [])]
            if not spots:
                break
            i, b, _n = rng.choices(spots, weights=[s[2] for s in spots])[0]
            chars[i] = b
            used.add(i)
        read = "".join(chars)
        if len(used) == k and read not in registered:
            return read
    return None


def inject(kind: str, rec: Dict[str, Any], rng: random.Random, ctx: cleansing.Context,
           conf: Dict[str, List[Tuple[str, int]]], new_id: int) -> Optional[Dict[str, Any]]:
    """A noisy copy of a clean record (global attribute names), or None if this type cannot apply."""
    noisy = {k: v for k, v in copy.deepcopy(rec).items() if k != "_raw"}
    plate = norm_plate(rec.get(PLATE)) or ""
    if kind == "colour_misspelling":
        typos = COLOUR_TYPOS.get(rec["observed_colour"])
        if not typos:
            return None
        noisy["observed_colour"] = rng.choice(typos)
    elif kind == "make_misspelling":
        typos = MAKE_TYPOS.get(rec["observed_make"])
        if not typos:
            return None
        noisy["observed_make"] = rng.choice(typos)
    elif kind in ("plate_misread_single", "plate_misread_double"):
        read = misread(plate, 1 if kind.endswith("single") else 2, rng, conf, set(ctx.profiles))
        if read is None:
            return None
        noisy[PLATE] = read
    elif kind == "invalid_shape":
        i = rng.randrange(len(plate) - 4, len(plate))  # one digit of the number dropped
        noisy[PLATE] = plate[:i] + plate[i + 1:]
    elif kind == "duplicate":
        noisy["record_id"] = new_id
    elif kind == "timestamp_unparseable":
        noisy[TIME] = rng.choice(["31/02/2026 25:61", "yesterday 9am", "2026-13-45T99:00:00", "??"])
    elif kind == "timestamp_future":
        moment = cleansing.horizon() + timedelta(days=rng.randint(30, 400))
        noisy[TIME] = cleansing.render_ts(moment, ctx.ts_format)
    elif kind == "low_confidence":
        noisy.update({CONF: round(rng.uniform(0.40, 0.65), 3), "observed_make": None, "observed_model": None,
                      "observed_colour": None})
    elif kind == "low_confidence_corroborated":
        noisy[CONF] = round(rng.uniform(0.40, 0.65), 3)
    elif kind == "clone_swap":
        own = ctx.profile_for(rec)
        others = sorted({(p["vehicle_make"], p["vehicle_model"], p["vehicle_colour"]) for p in ctx.profiles.values()
                         if p.get("vehicle_make") and p.get("vehicle_model") and p.get("vehicle_colour")
                         and lexicons.norm_key(p["vehicle_make"]) != lexicons.norm_key(own.get("vehicle_make"))
                         and lexicons.norm_key(p["vehicle_colour"]) != lexicons.norm_key(own.get("vehicle_colour"))})
        if not others:
            return None
        mk, md, cl = rng.choice(others)
        canon = {a: ctx.lex.for_attr(a).lookup(v)[0] or v for a, v in (("make", mk), ("model", md), ("colour", cl))}
        noisy.update(observed_make=canon["make"], observed_model=canon["model"], observed_colour=canon["colour"])
    return noisy


# ------------------------------------------------------------------ scoring

def _same(a: Any, b: Any, fld: str) -> bool:
    if fld == PLATE:
        return (norm_plate(a) or "") == (norm_plate(b) or "")
    return lexicons.norm_key(a) == lexicons.norm_key(b)


def score(kind: str, original: Dict[str, Any], prop: cleansing.Proposal) -> Dict[str, Any]:
    codes, fld, action = EXPECT[kind]
    found = [a.code for a in prop.anomalies]
    fixes = {f.field: f for f in prop.fixes}
    detected = bool(found) if kind in NO_ANOMALY_EXPECTED else any(c in found for c in codes)
    fix = fixes.get(fld) if fld else None
    if kind in NO_ANOMALY_EXPECTED:
        correct = not found and not prop.changes
    elif action == "set":
        correct = fix is not None and fix.action == "set" and _same(fix.after, original.get(fld), fld)
    else:
        correct = fix is not None and fix.action == action
    if kind == "clone_swap":  # either disagreeing field may carry the flag; neither may be overwritten
        correct = any(fixes[f].action == "flag" for f in ("observed_make", "observed_colour")) and \
            not any(fixes[f].action == "set" for f in ("observed_make", "observed_colour"))
    wrong = [f.field for f in prop.fixes if f.action == "set" and f.field in original
             and not _same(f.after, original.get(f.field), f.field)]
    return {"detected": detected, "correct": correct, "codes": found,
            "quarantined": bool(fix and fix.action == "quarantine"),
            "flagged": any(f.action == "flag" for f in prop.fixes), "wrong": wrong, "fix": fix}


def _rate(num: int, den: int) -> Optional[float]:
    return round(num / den, 3) if den else None


def validate(n: int = 300, seed: int = SEED, local_db: bool = False) -> Dict[str, Any]:
    """Inject labelled noise into n clean captures, run detection + RDR, measure per noise type."""
    full = snapshot(local_db)
    clean = clean_records(full)
    rng = random.Random(seed)
    sample = rng.sample(clean, min(n, len(clean)))
    kinds = list(EXPECT)
    conf = _confusions()
    next_id = max(int(cleansing._number(r["record_id"]) or 0) for r in full.records) + 1
    labelled: List[Tuple[str, Dict[str, Any], Dict[str, Any]]] = []
    rows: List[Dict[str, Any]] = []
    for i, rec in enumerate(sample):
        kind = kinds[i % len(kinds)]
        noisy = rec if kind == "control" else inject(kind, rec, rng, full, conf, next_id)
        if noisy is None:  # e.g. a colour with no typo list: keep it as a control instead of skipping it
            kind, noisy = "control", rec
        if kind == "duplicate":
            rows.append({k: v for k, v in rec.items() if k != "_raw"})
            next_id += 1
        rows.append({k: v for k, v in noisy.items() if k != "_raw"})
        labelled.append((kind, rec, noisy))
    ctx = _context(full, rows)
    per_type: Dict[str, Counter] = defaultdict(Counter)
    examples: List[Dict[str, Any]] = []
    wrong: List[Dict[str, Any]] = []
    for kind, original, noisy in labelled:
        prop = cleansing.propose(ctx.record(noisy["record_id"]), ctx)
        s = score(kind, original, prop)
        c = per_type[kind]
        c["n"] += 1
        c["detected"] += s["detected"]
        c["correct"] += s["correct"]
        c["quarantined"] += s["quarantined"]
        c["flagged"] += s["flagged"]
        c["wrong_corrections"] += len(s["wrong"])
        c["plate_wrong_corrections"] += PLATE in s["wrong"]
        for fld in s["wrong"]:  # every wrong correction is listed, not just counted: each one is a finding
            fix = next(f for f in prop.fixes if f.field == fld)
            wrong.append({"type": kind, "field": fld, "original": original.get(fld), "noisy": noisy.get(fld),
                          "set_to": fix.after, "rule": fix.rule, "because": fix.because,
                          "registered_profile_of_set_value": {k: v for k, v in ctx.profiles.get(
                              norm_plate(fix.after) or "", {}).items() if k in ("vehicle_make", "vehicle_model",
                                                                               "vehicle_colour")} if fld == PLATE else None,
                          "observed": {f: noisy.get(f) for f in ("observed_make", "observed_model", "observed_colour")}})
        if sum(1 for e in examples if e["type"] == kind) < 2:
            fld = EXPECT[kind][1] or "observed_colour"
            fld = PLATE if fld == "record" else fld  # a duplicate is shown by its plate, not an empty "record"
            fix = s["fix"]
            examples.append({"type": kind, "field": fld, "original": original.get(fld), "noisy": noisy.get(fld),
                             "detected": s["codes"],
                             "proposed": f"{fix.action} {fix.after!r}" if fix else "keep",
                             "rule_path": [p["rule"] for p in (fix.path if fix else []) if p["fired"]],
                             "correct": s["correct"]})
    rep = _report(per_type, examples, seed, n, len(clean), local_db)
    rep["wrong_corrections_detail"] = wrong
    return rep


def _context(full: cleansing.Context, rows: List[Dict[str, Any]]) -> cleansing.Context:
    """A Context over the noisy rows; registrations, lexicons and rules exactly as in the snapshot."""
    ctx = copy.copy(full)
    ctx.records = [{**r, "_raw": {}} for r in rows]
    ctx.by_plate = {}
    for rec in ctx.records:
        ctx.by_plate.setdefault(norm_plate(rec.get(PLATE)) or "", []).append(rec)
    ctx.matches = {}
    return ctx


def _report(per_type: Dict[str, Counter], examples: List[Dict[str, Any]], seed: int, n: int, clean: int,
            local_db: bool) -> Dict[str, Any]:
    table = {}
    for kind in EXPECT:
        c = per_type.get(kind, Counter())
        if not c["n"]:
            continue
        none_expected = kind in NO_ANOMALY_EXPECTED
        table[kind] = {"n": c["n"], "expected": "no anomaly, keep" if none_expected else
                       f"{' or '.join(EXPECT[kind][0])} -> {EXPECT[kind][2]}",
                       "detection_recall": None if none_expected else _rate(c["detected"], c["n"]),
                       "false_alarm_rate": _rate(c["detected"], c["n"]) if none_expected else None,
                       "correction_accuracy": _rate(c["correct"], c["n"]),
                       "quarantine_rate": _rate(c["quarantined"], c["n"]),
                       "flag_rate": _rate(c["flagged"], c["n"]),
                       "wrong_corrections": c["wrong_corrections"],
                       "plate_wrong_corrections": c["plate_wrong_corrections"]}
    noisy = [k for k in table if k not in NO_ANOMALY_EXPECTED]
    tp = sum(per_type[k]["detected"] for k in noisy)
    fp = per_type["control"]["detected"]
    overall = {"records": sum(t["n"] for t in table.values()), "noisy": sum(table[k]["n"] for k in noisy),
               "controls": table.get("control", {}).get("n", 0), "clean_pool": clean,
               "detection_recall": _rate(tp, sum(table[k]["n"] for k in noisy)),
               "detection_precision": _rate(tp, tp + fp),
               "control_false_alarm_rate": table.get("control", {}).get("false_alarm_rate"),
               "correction_accuracy": _rate(sum(per_type[k]["correct"] for k in noisy), sum(table[k]["n"] for k in noisy)),
               "wrong_corrections": sum(t["wrong_corrections"] for t in table.values()),
               "plate_wrong_corrections": sum(t["plate_wrong_corrections"] for t in table.values())}
    return {"overall": overall, "per_type": table, "examples": examples, "seed": seed, "n_requested": n,
            "source": "local SQLite copies" if local_db else "live wrappers (read-only)",
            "generated_at": datetime.now().isoformat(timespec="seconds")}
