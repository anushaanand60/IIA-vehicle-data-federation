"""Cleansing recorded camera data with Ripple-Down Rules (rubric 2; Week 8 deck, slides 8-32).

The workflow is the deck's pipeline (slide 16) applied to camera captures:

    live CAM rows ─► detect anomalies ─► feature extraction (slide 20) ─► RDR inference (slide 27)
                  ─► proposal per field (standardise / correct / quarantine / flag / delete)
                  ─► operator applies through the CAM wrapper's own /admin/sql ─► CLEANSING_LOG

Contextual evidence is the rest of the federation: the registration authority's make, model and
colour (and lexicons mined from it, `mediator.lexicons`), the plate matcher's candidates with their
probabilities, the other sightings of the same plate (impossible travel, duplicates).

Invariants kept from phase 1:
  * every read is live through a wrapper; nothing is cached between calls;
  * the mediator's read path stays read-only: a correction is an UPDATE sent to the *owning
    source's* admin console, by primary key, and it is logged with its before-image so it can be
    undone;
  * no table or column name is written here: tables, columns and the primary key come from the
    registry and from the wrapper's /schema; fields are global attribute names.
"""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from mediator import catalog, lexicons, travel_check
from mediator.contract import Registry, SourceSpec
from mediator.decide import REFERENCE_TODAY
from mediator.executor import render_plate
from mediator.rdr import RDRTree
from mediator.registry_loader import load_registry
from mediator.transforms import get_transform, norm_plate

ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "data" / "rdr_rules.json"
CONFIDENCE_FLOOR = 0.70  # benchmark: exact-read rate <= 32 % below 0.70, >= 42 % above it
PLATE, TIME, CONF, LOC = "plate_number", "last_seen_time", "ocr_confidence", "last_seen_location"
TEXT_FIELDS = {"observed_make": ("make", "vehicle_make"), "observed_model": ("model", "vehicle_model"),
               "observed_colour": ("colour", "vehicle_colour")}
FIELDS = (PLATE, *TEXT_FIELDS, TIME, "record")
TS_FORMATS = ("iso", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M",
              "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M", "epoch")


class CleansingError(RuntimeError):
    """An operator action that could not be carried out; the message says why."""


@dataclass
class Anomaly:
    code: str
    field: str
    observed: Any
    evidence: Dict[str, Any]
    severity: str  # high | medium | low


@dataclass
class FieldFix:
    field: str
    action: str           # keep | set | quarantine | flag | delete
    before: Any
    after: Any
    rule: Optional[str]
    because: str
    path: List[Dict[str, Any]]
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Proposal:
    source_id: str
    record_id: Any
    record: Dict[str, Any]
    anomalies: List[Anomaly]
    fixes: List[FieldFix]

    @property
    def changes(self) -> List[FieldFix]:
        return [f for f in self.fixes if f.action != "keep"]

    def to_dict(self) -> Dict[str, Any]:
        return {"source_id": self.source_id, "record_id": self.record_id,
                "record": {k: v for k, v in self.record.items() if not k.startswith("_")},
                "anomalies": [asdict(a) for a in self.anomalies],
                "fixes": [asdict(f) for f in self.fixes]}


# ------------------------------------------------------------------ live context

def _global(row: Dict[str, Any], spec: SourceSpec, transform: bool) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for m in spec.attribute_map:
        raw = row.get(m.source_attr, row.get(f"{m.source_table}__{m.source_attr}"))
        out[m.global_attr] = get_transform(m.transform)(raw) if (transform and raw is not None) else raw
    return out


class Context:
    """One live snapshot for one operator action: CAM captures, registrations, lexicons, rules."""

    def __init__(self, registry: Registry, cam: SourceSpec, reg: SourceSpec, pk: str,
                 cam_rows: List[Dict[str, Any]], reg_rows: List[Dict[str, Any]], tree: RDRTree) -> None:
        self.registry, self.cam, self.reg, self.pk, self.tree = registry, cam, reg, pk, tree
        self.records = [{"record_id": r[pk], "source_id": cam.source_id, **_global(r, cam, False), "_raw": r}
                        for r in cam_rows]
        self.profiles = {norm_plate(p[PLATE]): p for p in (_global(r, reg, True) for r in reg_rows) if p.get(PLATE)}
        self.lex = lexicons.build([_global(r, reg, False) for r in reg_rows])
        self.tree.lexicons = self.lex.as_rdr()
        self.by_plate: Dict[str, List[Dict[str, Any]]] = {}
        for rec in self.records:
            self.by_plate.setdefault(norm_plate(rec.get(PLATE)) or "", []).append(rec)
        fmts = Counter(parse_ts(r.get(TIME))[1] for r in self.records)
        fmts.pop(None, None)
        self.ts_format = fmts.most_common(1)[0][0] if fmts else "iso"  # the source's own dominant format

    @classmethod
    def build(cls, registry: Optional[Registry] = None, rules_path: Optional[Path] = None) -> "Context":
        registry = registry or load_registry(catalog.META_DB_PATH)
        cam = next(s for s in registry.sources if s.authority == "observational" and "observed_colour" in s.covers)
        reg = next(s for s in registry.sources if s.identity_authority)
        with httpx.Client(trust_env=False) as client:
            pk = lexicons.primary_key(cam, client)
            cam_rows = lexicons.fetch_all(cam, pk, client)
            reg_rows = lexicons.fetch_all(reg, lexicons.primary_key(reg, client), client)
        return cls(registry, cam, reg, pk, cam_rows, reg_rows, RDRTree.load(rules_path or RULES_PATH))

    def record(self, record_id: Any) -> Dict[str, Any]:
        for rec in self.records:
            if str(rec["record_id"]) == str(record_id):
                return rec
        raise CleansingError(f"{self.cam.source_id} has no record {record_id}")

    def profile_for(self, record: Dict[str, Any]) -> Dict[str, Any]:
        return self.profiles.get(norm_plate(record.get(PLATE)) or "", {})


# ------------------------------------------------------------------ feature helpers

def parse_ts(value: Any) -> Tuple[Optional[datetime], Optional[str]]:
    text = str(value or "").strip()
    if not text:
        return None, None
    for fmt in TS_FORMATS:
        try:
            if fmt == "iso":
                return datetime.fromisoformat(text).replace(tzinfo=None), fmt
            if fmt == "epoch":
                if text.isdigit() and 9 <= len(text) <= 10:
                    return datetime.fromtimestamp(int(text), timezone.utc).replace(tzinfo=None), fmt
                continue
            return datetime.strptime(text, fmt), fmt
        except ValueError:
            continue
    return None, None


def render_ts(moment: datetime, fmt: str) -> str:
    return moment.isoformat(timespec="seconds") if fmt in ("iso", "epoch") else moment.strftime(fmt)


def _agrees(observed: Optional[str], registered: Optional[str]) -> Optional[bool]:
    if not observed or not registered:
        return None
    a, b = lexicons.norm_key(observed), lexicons.norm_key(registered)
    return a.startswith(b) or b.startswith(a)


def match_plate(read: str, confidence: Any, make: Any, colour: Any, ctx: Context) -> Dict[str, Any]:
    """The plate matcher's verdict: workstream C's calibrated matcher if present, else plate_resolve."""
    try:
        from mediator import plate_match  # lazy: no hard dependency on a module still being written
        res = plate_match.match(read, confidence=confidence, observed_make=make, observed_colour=colour)
        return _from_plate_match(res)
    except ImportError:
        pass
    from mediator import plate_resolve
    best, ranked, outcome = plate_resolve.resolve(read, make, colour, lambda c: ctx.profiles.get(c, {}))
    decision = {"RESOLVED": "ACCEPT", "AMBIGUOUS": "REVIEW", "NONE": "REJECT"}[outcome]
    return {"decision": decision, "best": best.plate if best else None,
            "probability": best.score if best else None, "method": "plate_resolve",
            "candidates": [{"plate": c.plate, "probability": c.score, "reasons": c.reasons} for c in ranked]}


def _from_plate_match(res: Any) -> Dict[str, Any]:
    get = (lambda k: res.get(k)) if isinstance(res, dict) else (lambda k: getattr(res, k, None))
    cands = []
    for c in get("candidates") or []:
        cg = (lambda k: c.get(k)) if isinstance(c, dict) else (lambda k: getattr(c, k, None))
        cands.append({"plate": cg("plate"), "probability": cg("probability")})
    best = cands[0] if cands else {}
    return {"decision": str(get("decision")).upper(), "best": best.get("plate"),
            "probability": best.get("probability"), "method": "plate_match", "candidates": cands}


def _travel(record: Dict[str, Any], ctx: Context) -> List[Dict[str, Any]]:
    siblings = ctx.by_plate.get(norm_plate(record.get(PLATE)) or "", [])
    sightings = [{"location": s.get(LOC), "lat": s.get("camera_lat"), "lon": s.get("camera_lon"),
                  "at": s.get(TIME)} for s in siblings]
    mine = (record.get(LOC), str(record.get(TIME)))
    return [leg.__dict__ for leg in travel_check.impossible_legs(sightings)
            if (leg.from_loc, leg.from_at) == mine or (leg.to_loc, leg.to_at) == mine]


def _duplicate_of(record: Dict[str, Any], ctx: Context) -> Any:
    key = (record.get(LOC), record.get(TIME))
    for s in ctx.by_plate.get(norm_plate(record.get(PLATE)) or "", []):
        if (s.get(LOC), s.get(TIME)) == key and str(s["record_id"]) < str(record["record_id"]):
            return s["record_id"]
    return None


# ------------------------------------------------------------------ detection

def detect(record: Dict[str, Any], profile: Dict[str, Any], ctx: Context) -> List[Anomaly]:
    """Every anomaly in one camera capture, judged against the live registration and lexicons."""
    out: List[Anomaly] = []
    plate = norm_plate(record.get(PLATE)) or ""
    if not profile:
        out.append(Anomaly("PLATE_NOT_REGISTERED", PLATE, record.get(PLATE),
                           {"registration_authority": ctx.reg.source_id}, "high"))
    pf = ctx.lex.plate_features(plate)
    if not pf["shape_valid"]:
        out.append(Anomaly("PLATE_SHAPE_INVALID", PLATE, record.get(PLATE), pf, "medium"))
    for fld, (attr, reg_attr) in TEXT_FIELDS.items():
        value = record.get(fld)
        if value is None:
            continue  # slide 12, "omitted field": absent is not wrong, and we cannot hallucinate it
        canon, kind = ctx.lex.for_attr(attr).lookup(value)
        if kind != "exact":
            out.append(Anomaly(f"{attr.upper()}_NOT_IN_LEXICON", fld, value,
                               {"canonical": canon, "variant_kind": kind}, "low"))
        if canon and _agrees(canon, profile.get(reg_attr)) is False:
            out.append(Anomaly(f"{attr.upper()}_DISAGREES_REGISTRATION", fld, value,
                               {"registered": profile.get(reg_attr)}, "high"))
    conf = record.get(CONF)
    if conf is not None and float(conf) < CONFIDENCE_FLOOR:
        out.append(Anomaly("LOW_OCR_CONFIDENCE", CONF, conf, {"floor": CONFIDENCE_FLOOR}, "medium"))
    moment, fmt = parse_ts(record.get(TIME))
    if moment is None:
        out.append(Anomaly("TIMESTAMP_UNPARSEABLE", TIME, record.get(TIME), {}, "high"))
    elif moment.date() > REFERENCE_TODAY:
        out.append(Anomaly("TIMESTAMP_FUTURE", TIME, record.get(TIME),
                           {"reference_today": REFERENCE_TODAY.isoformat()}, "high"))
    elif fmt != ctx.ts_format:
        out.append(Anomaly("TIMESTAMP_NONSTANDARD", TIME, record.get(TIME), {"source_format": ctx.ts_format}, "low"))
    if (dup := _duplicate_of(record, ctx)) is not None:
        out.append(Anomaly("DUPLICATE_CAPTURE", "record", record["record_id"], {"duplicate_of": dup}, "low"))
    if legs := _travel(record, ctx):
        out.append(Anomaly("IMPOSSIBLE_TRAVEL", "record", record.get(PLATE), {"legs": legs}, "high"))
    return out


# ------------------------------------------------------------------ features + RDR proposals

def features(record: Dict[str, Any], fld: str, profile: Dict[str, Any], ctx: Context) -> Dict[str, Any]:
    """The case the RDR tree sees for one field (slide 20: surface, dictionary, contextual signals)."""
    case: Dict[str, Any] = {"field": fld, "value": record.get(fld) if fld != "record" else None,
                            "record_id": record["record_id"], "registered": bool(profile)}
    if fld in TEXT_FIELDS:
        attr, reg_attr = TEXT_FIELDS[fld]
        canon, kind = ctx.lex.for_attr(attr).lookup(record.get(fld))
        case.update(canonical=canon, lexicon_kind=kind, in_lexicon=kind == "exact",
                    registered_value=profile.get(reg_attr), agrees_registration=_agrees(canon, profile.get(reg_attr)))
    elif fld == PLATE:
        plate = norm_plate(record.get(PLATE)) or ""
        conf = record.get(CONF)
        case.update(ctx.lex.plate_features(plate), plate=plate, confidence=conf,
                    below_floor=conf is not None and float(conf) < CONFIDENCE_FLOOR,
                    corroborated=bool(_agrees(record.get("observed_make"), profile.get("vehicle_make"))
                                      or _agrees(record.get("observed_colour"), profile.get("vehicle_colour"))))
        if not profile:
            m = match_plate(plate, conf, record.get("observed_make"), record.get("observed_colour"), ctx)
            best = ctx.profiles.get(m["best"] or "", {})
            case.update(match_decision=m["decision"], match_best=m["best"], match_probability=m["probability"],
                        match_method=m["method"], match_candidates=m["candidates"][:5],
                        match_best_make_agrees=_agrees(record.get("observed_make"), best.get("vehicle_make")))
    elif fld == TIME:
        moment, fmt = parse_ts(record.get(TIME))
        case.update(ts_parsed=moment is not None, ts_ok=moment is not None and fmt == ctx.ts_format,
                    ts_future=bool(moment and moment.date() > REFERENCE_TODAY),
                    ts_normalised=render_ts(moment, ctx.ts_format) if moment else None)
    else:
        legs = _travel(record, ctx)
        case.update(duplicate_of=_duplicate_of(record, ctx), travel_conflict=bool(legs))
    return case


def propose(record: Dict[str, Any], ctx: Optional[Context] = None) -> Proposal:
    """Run the RDR tree once per field (slide 30 labels each token the same way)."""
    ctx = ctx or Context.build()
    profile = ctx.profile_for(record)
    fixes = []
    for fld in FIELDS:
        case = features(record, fld, profile, ctx)
        conclusion, path = ctx.tree.evaluate(case)
        conclusion = conclusion or {"action": "keep"}
        action = conclusion.get("action", "keep")
        after = conclusion.get("value")
        after = case.get(after[1:]) if isinstance(after, str) and after.startswith("$") else after
        rule = conclusion.get("rule")
        because = next((p["because"] for p in path if p["rule"] == rule), "")
        evidence = {k: case[k] for k in ("match_best", "match_probability", "match_method", "match_decision",
                                         "registered_value", "canonical", "lexicon_kind") if k in case}
        fixes.append(FieldFix(fld, action, case["value"], after if action == "set" else case["value"],
                              rule, because, path, evidence))
    return Proposal(ctx.cam.source_id, record["record_id"], record, detect(record, profile, ctx), fixes)


def _after(proposal: Proposal) -> Optional[Dict[str, Any]]:
    if any(f.action == "delete" for f in proposal.fixes):
        return None
    rec = dict(proposal.record)
    for f in proposal.fixes:
        if f.action == "set":
            rec[f.field] = f.after
    return rec


def scan(limit: Optional[int] = None, ctx: Optional[Context] = None) -> Dict[str, Any]:
    """Live scan: anomalies before, proposals, and what would remain if every proposal were applied."""
    ctx = ctx or Context.build()
    before, after, actions = Counter(), Counter(), Counter()
    items = []
    for rec in ctx.records[:limit]:
        prop = propose(rec, ctx)
        before.update(a.code for a in prop.anomalies)
        actions.update(f.action for f in prop.changes)
        fixed = _after(prop)
        remaining = detect(fixed, ctx.profile_for(fixed), ctx) if fixed else []
        after.update(a.code for a in remaining)
        if prop.anomalies or prop.changes:
            items.append({**prop.to_dict(), "remaining": [a.code for a in remaining]})
    return {"source_id": ctx.cam.source_id, "records_scanned": len(ctx.records[:limit]),
            "records_with_anomalies": sum(1 for i in items if i["anomalies"]),
            "anomalies_before": dict(before), "proposals": dict(actions),
            "remaining_after": dict(after), "items": items}


# ------------------------------------------------------------------ apply / undo through /admin/sql

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(catalog.META_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE IF NOT EXISTS CLEANSING_LOG (
        log_id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT, record_id TEXT, field TEXT,
        action TEXT, before_value TEXT, after_value TEXT, rule_id TEXT, rule_path TEXT,
        evidence TEXT, actor TEXT, applied_at TEXT, sql_sent TEXT, undone_at TEXT, undone_by TEXT)""")
    return conn


def _admin(spec: SourceSpec, sql: str) -> int:
    with httpx.Client(trust_env=False) as client:
        r = client.post(spec.base_url.rstrip("/") + "/admin/sql", json={"sql": sql}, timeout=spec.timeout_ms / 1000)
    if r.status_code != 200:
        raise CleansingError(f"{spec.source_id} refused the write: HTTP {r.status_code} {r.text[:200]}")
    return int(r.json().get("rows_affected", 0))


def _column(spec: SourceSpec, fld: str) -> str:
    for m in spec.attribute_map:
        if m.global_attr == fld and m.source_table.lower() == spec.table.lower():
            return m.source_attr
    raise CleansingError(f"{fld} is not a column of {spec.source_id}'s {spec.table}; it cannot be corrected here")


def _match(col: str, value: Any) -> str:
    return f"{col} IS NULL" if value is None else f"{col} = {lexicons.sql_literal(value)}"


def apply(proposal: Proposal, actor: str = "operator", ctx: Optional[Context] = None) -> List[Dict[str, Any]]:
    """Write every non-keep fix of a proposal through the owning source's /admin/sql, and log it."""
    ctx = ctx or Context.build()
    spec, pk, out = ctx.cam, ctx.pk, []
    where = f"{pk} = {lexicons.sql_literal(proposal.record_id)}"
    for fix in proposal.changes:
        before, after, sql = fix.before, fix.after, None
        if fix.action == "set":
            col = _column(spec, fix.field)
            stored = render_plate(after, spec.key_predicate.render) if fix.field == PLATE else after
            sql = f"UPDATE {spec.table} SET {col} = {lexicons.sql_literal(stored)} WHERE {where} AND {_match(col, before)}"
            if _admin(spec, sql) != 1:
                raise CleansingError(f"record {proposal.record_id} changed since the proposal; re-scan first")
        elif fix.action == "delete":
            before = proposal.record.get("_raw")
            sql = f"DELETE FROM {spec.table} WHERE {where}"
            _admin(spec, sql)
        with _db() as conn:
            cur = conn.execute(
                "INSERT INTO CLEANSING_LOG (source_id, record_id, field, action, before_value, after_value, "
                "rule_id, rule_path, evidence, actor, applied_at, sql_sent) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (spec.source_id, str(proposal.record_id), fix.field, fix.action, json.dumps(before, default=str),
                 json.dumps(after, default=str), fix.rule, json.dumps(fix.path), json.dumps(fix.evidence, default=str),
                 actor, datetime.now(timezone.utc).isoformat(), sql))
            out.append({"log_id": cur.lastrowid, "field": fix.field, "action": fix.action, "sql": sql})
    return out


def undo(log_id: int, actor: str = "operator", ctx: Optional[Context] = None) -> Dict[str, Any]:
    """Restore the before-image of one logged correction through the same admin path."""
    with _db() as conn:
        row = conn.execute("SELECT * FROM CLEANSING_LOG WHERE log_id = ?", (log_id,)).fetchone()
    if row is None:
        raise CleansingError(f"no cleansing log entry {log_id}")
    registry = ctx.registry if ctx else load_registry(catalog.META_DB_PATH)
    spec = registry.by_id(row["source_id"])
    pk = ctx.pk if ctx else lexicons.primary_key(spec)
    before, after, sql = json.loads(row["before_value"]), json.loads(row["after_value"]), None
    where = f"{pk} = {lexicons.sql_literal(row['record_id'])}"
    if row["action"] == "set":
        col = _column(spec, row["field"])
        sql = f"UPDATE {spec.table} SET {col} = {lexicons.sql_literal(before)} WHERE {where}"
        _admin(spec, sql)
    elif row["action"] == "delete":
        cols = ", ".join(before)
        vals = ", ".join(lexicons.sql_literal(v) for v in before.values())
        sql = f"INSERT INTO {spec.table} ({cols}) VALUES ({vals})"
        _admin(spec, sql)
    with _db() as conn:
        conn.execute("UPDATE CLEANSING_LOG SET undone_at = ?, undone_by = ? WHERE log_id = ?",
                     (datetime.now(timezone.utc).isoformat(), actor, log_id))
    return {"log_id": log_id, "action": row["action"], "sql": sql}


def log_entries(limit: int = 50) -> List[Dict[str, Any]]:
    with _db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM CLEANSING_LOG ORDER BY log_id DESC LIMIT ?", (limit,))]
