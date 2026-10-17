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
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import httpx

from mediator import catalog, lexicons, travel_check
from mediator.contract import Registry, SourceSpec
from mediator.decide import REFERENCE_TODAY
from mediator.executor import render_plate
from mediator.rdr import RDRTree, normalise_lexicons
from mediator.registry_loader import load_registry
from mediator.transforms import get_transform, norm_plate

ROOT = Path(__file__).resolve().parents[1]
RULES_PATH = ROOT / "data" / "rdr_rules.json"
CONFIDENCE_FLOOR = 0.70  # benchmark: exact-read rate <= 32 % below 0.70, >= 42 % above it
PLATE, TIME, CONF, LOC = "plate_number", "last_seen_time", "ocr_confidence", "last_seen_location"
TEXT_FIELDS = {"observed_make": ("make", "vehicle_make"), "observed_model": ("model", "vehicle_model"),
               "observed_colour": ("colour", "vehicle_colour")}
FIELDS = (PLATE, *TEXT_FIELDS, TIME, "record")
TS_FORMATS = ("epoch", "iso", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M",
              "%Y/%m/%d %H:%M:%S", "%Y-%m-%d %H:%M")


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
        self.tree.lexicons = normalise_lexicons(self.lex.as_rdr())
        self.by_plate: Dict[str, List[Dict[str, Any]]] = {}
        for rec in self.records:
            self.by_plate.setdefault(norm_plate(rec.get(PLATE)) or "", []).append(rec)
        fmts = Counter(parse_ts(r.get(TIME))[1] for r in self.records)
        fmts.pop(None, None)
        self.ts_format = fmts.most_common(1)[0][0] if fmts else "iso"  # the source's own dominant format
        self.matches: Dict[Tuple[Any, ...], Dict[str, Any]] = {}

    @classmethod
    def build(cls, registry: Optional[Registry] = None, rules_path: Optional[Path] = None) -> "Context":
        registry = registry or load_registry(catalog.META_DB_PATH)
        cam = next((s for s in registry.sources if s.authority == "observational" and "observed_colour" in s.covers), None)
        reg = next((s for s in registry.sources if s.identity_authority), None)
        if cam is None or reg is None:
            raise CleansingError("the registry names no observational camera source or no identity authority")
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


def horizon(now: Optional[datetime] = None) -> datetime:
    """Latest instant a capture may carry. REFERENCE_TODAY is the simulated world's today, but the
    wrapper's own "sight" admin action stamps captures with the real clock, so a capture is only
    impossible once it postdates both (trade-off: a little laxer than REFERENCE_TODAY alone)."""
    return max(datetime.combine(REFERENCE_TODAY, time.max), now or datetime.now())


def _number(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def render_ts(moment: datetime, fmt: str) -> str:
    return moment.isoformat(timespec="seconds") if fmt in ("iso", "epoch") else moment.strftime(fmt)


def _agrees(observed: Optional[str], registered: Optional[str]) -> Optional[bool]:
    if not observed or not registered:
        return None
    a, b = lexicons.norm_key(observed), lexicons.norm_key(registered)
    return a.startswith(b) or b.startswith(a)


def match_plate(read: str, confidence: Any, make: Any, colour: Any, ctx: Context,
                model: Any = None) -> Dict[str, Any]:
    """The plate matcher's verdict: workstream C's calibrated matcher if present, else plate_resolve.

    The matcher is handed the registrations from this live snapshot, so it never opens a source
    database itself (on the four-laptop deployment REG's file is not even on this machine).
    Memoised per Context: a scan meets the same misread several times; the snapshot is one moment.
    """
    key = (read, confidence, make, colour, model)
    if key not in ctx.matches:
        ctx.matches[key] = _match_plate(read, confidence, make, colour, model, ctx)
    return ctx.matches[key]


def _match_plate(read: str, confidence: Any, make: Any, colour: Any, model: Any, ctx: Context) -> Dict[str, Any]:
    note = None
    try:
        from mediator import plate_match  # lazy: no hard dependency on workstream C's module
    except ImportError:
        plate_match = None
    if plate_match is not None:
        registry = {p: {"make": v.get("vehicle_make"), "model": v.get("vehicle_model"),
                        "colour": v.get("vehicle_colour")} for p, v in ctx.profiles.items()}
        try:
            res = plate_match.match(read, confidence=confidence, observed_make=make, observed_colour=colour,
                                    observed_model=model, registry=registry)
            return _from_plate_match(res)
        except Exception as exc:  # a broken matcher degrades to the rule-based one, never a crash
            note = f"plate_match failed ({type(exc).__name__}: {exc}); fell back to plate_resolve"
    from mediator import plate_resolve
    best, ranked, outcome = plate_resolve.resolve(read, make, colour, lambda c: ctx.profiles.get(c, {}))
    decision = {"RESOLVED": "ACCEPT", "AMBIGUOUS": "REVIEW", "NONE": "REJECT"}[outcome]
    return {"decision": decision, "best": best.plate if best else None, "note": note,
            "probability": best.score if best else None, "method": "plate_resolve (uncalibrated score)",
            "candidates": [{"plate": c.plate, "probability": c.score, "reasons": c.reasons} for c in ranked]}


def _from_plate_match(res: Any) -> Dict[str, Any]:
    """plate_match.match returns (ranked candidates, blocking stats); the top one carries the decision."""
    ranked = res[0] if isinstance(res, tuple) else res
    cands = [{"plate": c.plate, "probability": round(float(c.probability), 4), "decision": c.decision,
              "reasons": list(c.reasons)[-2:]} for c in ranked or []]
    best = cands[0] if cands else {}
    decision = str(best.get("decision") or "REJECT").upper()
    if decision not in ("ACCEPT", "REVIEW", "REJECT"):
        decision = "REVIEW"  # an answer we cannot read is a question for a person, never an accept
    return {"decision": decision, "best": best.get("plate"), "probability": best.get("probability"),
            "method": "plate_match", "candidates": cands, "note": None}


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
        if (s.get(LOC), s.get(TIME)) == key and _order(s["record_id"]) < _order(record["record_id"]):
            return s["record_id"]
    return None


def _order(record_id: Any) -> Tuple[int, Any]:
    """Numeric ids compare as numbers ("10" after "9"), anything else as text."""
    n = _number(record_id)
    return (0, n) if n is not None else (1, str(record_id))


def _corroborated(record: Dict[str, Any], profile: Dict[str, Any]) -> bool:
    return bool(_agrees(record.get("observed_make"), profile.get("vehicle_make"))
                or _agrees(record.get("observed_colour"), profile.get("vehicle_colour")))


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
    conf = _number(record.get(CONF))
    # Low confidence is the camera doubting its own read; a read the registration corroborates
    # (make or colour agree) has had that doubt answered, so only an uncorroborated one is flagged.
    if conf is not None and conf < CONFIDENCE_FLOOR and not _corroborated(record, profile):
        out.append(Anomaly("LOW_OCR_CONFIDENCE", CONF, record.get(CONF), {"floor": CONFIDENCE_FLOOR}, "medium"))
    moment, fmt = parse_ts(record.get(TIME))
    if moment is None:
        out.append(Anomaly("TIMESTAMP_UNPARSEABLE", TIME, record.get(TIME), {}, "high"))
    elif moment > horizon():
        out.append(Anomaly("TIMESTAMP_FUTURE", TIME, record.get(TIME),
                           {"horizon": horizon().isoformat(timespec="seconds")}, "high"))
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
        conf = _number(record.get(CONF))
        case.update(ctx.lex.plate_features(plate), plate=plate, confidence=conf,
                    below_floor=conf is not None and conf < CONFIDENCE_FLOOR,
                    corroborated=_corroborated(record, profile))
        if not profile:
            m = match_plate(plate, conf, record.get("observed_make"), record.get("observed_colour"), ctx,
                            record.get("observed_model"))
            best = ctx.profiles.get(m["best"] or "", {})
            case.update(match_decision=m["decision"], match_best=m["best"], match_probability=m["probability"],
                        match_method=m["method"], match_candidates=m["candidates"][:5], match_note=m.get("note"),
                        match_best_make_agrees=_agrees(record.get("observed_make"), best.get("vehicle_make")))
    elif fld == TIME:
        moment, fmt = parse_ts(record.get(TIME))
        case.update(ts_parsed=moment is not None, ts_ok=moment is not None and fmt == ctx.ts_format,
                    ts_future=bool(moment and moment > horizon()),
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
                                         "match_candidates", "registered_value", "canonical", "lexicon_kind")
                    if k in case}
        # Two invariants no taught rule can override: a plate is only rewritten on the matcher's
        # ACCEPT (REVIEW means a person decides), and a "set" with nothing to set is not a fix.
        if action == "set" and (after is None or (fld == PLATE and case.get("match_decision") != "ACCEPT")):
            action, because = "quarantine", f"{because} [downgraded: no accepted value to write]"
        if action == "set" and fld == PLATE:
            because = (f"{because} -- matcher {case['match_method']} accepts {case['match_best']} "
                       f"with probability {case['match_probability']}")
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
    try:
        ctx = ctx or Context.build()
    except lexicons.SourceUnavailable as exc:  # refuse rather than crash: "undetermined" is an answer
        return {"status": "UNAVAILABLE", "error": str(exc)}
    before, after, actions, disposition = Counter(), Counter(), Counter(), Counter()
    resolved = active_resolutions(ctx.cam.source_id)
    items = []
    for rec in ctx.records[:limit]:
        prop = propose(rec, ctx)
        before.update(a.code for a in prop.anomalies)
        actions.update(f.action for f in prop.changes)
        remaining = _remaining(prop, ctx)
        after.update(a.code for a in remaining)
        resolution = resolved.get(str(rec["record_id"]))
        dispositions = [_disposition(a, prop, resolution) for a in remaining]
        disposition.update(dispositions)
        if prop.anomalies or prop.changes:
            items.append({**prop.to_dict(), "remaining": [a.code for a in remaining],
                          "dispositions": dispositions, "resolution": resolution})
    open_after = sum(n for d, n in disposition.items() if d != "resolved")
    return {"status": "OK", "source_id": ctx.cam.source_id, "records_scanned": len(ctx.records[:limit]),
            "records_with_anomalies": sum(1 for i in items if i["anomalies"]),
            "anomalies_before": dict(before), "proposals": dict(actions),
            "remaining_after": dict(after), "remaining_by_disposition": dict(disposition),
            "remaining_after_resolution": open_after, "items": items}


def _remaining(prop: Proposal, ctx: Context) -> List[Anomaly]:
    """The anomalies a record would still carry once every proposal for it were applied."""
    fixed = _after(prop)
    return detect(fixed, ctx.profile_for(fixed), ctx) if fixed else []


def _disposition(anomaly: Anomaly, proposal: Proposal, resolution: Optional[Dict[str, Any]] = None) -> str:
    """What happens to an anomaly a proposal does not remove: flagged as evidence (a clone signal
    must survive cleansing), quarantined for a person, or unhandled -- the number to drive to 0.
    An operator's recorded resolution (confirm_clone / dismiss) turns it into `resolved`."""
    if resolution and anomaly.code in resolution.get("anomalies", []):
        return "resolved"
    fld = PLATE if anomaly.field == CONF else anomaly.field
    action = next((f.action for f in proposal.fixes if f.field == fld), "keep")
    return {"flag": "flagged", "quarantine": "quarantined"}.get(action, "unhandled")


# ------------------------------------------------------------------ apply / undo through /admin/sql

@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    """meta.db, resolved at call time (tests redirect catalog.META_DB_PATH); committed and closed."""
    conn = sqlite3.connect(catalog.META_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS CLEANSING_LOG (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT, source_id TEXT, record_id TEXT, field TEXT,
            action TEXT, before_value TEXT, after_value TEXT, rule_id TEXT, rule_path TEXT,
            evidence TEXT, actor TEXT, applied_at TEXT, sql_sent TEXT, undone_at TEXT, undone_by TEXT)""")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(CLEANSING_LOG)")}
        for col in ("decision", "note"):  # additive migration for the resolution step
            if col not in cols:
                conn.execute(f"ALTER TABLE CLEANSING_LOG ADD COLUMN {col} TEXT")
        yield conn
        conn.commit()
    finally:
        conn.close()


def _admin(spec: SourceSpec, sql: str) -> int:
    with httpx.Client(trust_env=False) as client:
        r = client.post(spec.base_url.rstrip("/") + "/admin/sql", json={"sql": sql}, timeout=spec.timeout_ms / 1000)
    if r.status_code != 200:
        raise CleansingError(f"{spec.source_id} refused the write: HTTP {r.status_code} {r.text[:200]}")
    return int(r.json().get("rows_affected", 0))


def _fetch_row(spec: SourceSpec, pk: str, record_id: Any) -> Dict[str, Any]:
    rows = lexicons._post(spec, f"SELECT * FROM {spec.table} WHERE {pk} = {lexicons.sql_literal(record_id)}",
                          None).get("rows", [])
    if not rows:
        raise CleansingError(f"{spec.source_id} has no record {record_id}")
    return rows[0]


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
    changes = proposal.changes
    if any(f.action == "delete" for f in changes):  # deleting the row makes every other fix moot
        changes = [f for f in changes if f.action == "delete"][:1]
    for fix in changes:
        before, after, sql = fix.before, fix.after, None
        if fix.action == "set":
            col = _column(spec, fix.field)
            # log what the source now holds (its own plate spelling), so undo can match it exactly
            after = render_plate(after, spec.key_predicate.render) if fix.field == PLATE else after
            sql = f"UPDATE {spec.table} SET {col} = {lexicons.sql_literal(after)} WHERE {where} AND {_match(col, before)}"
            if _admin(spec, sql) != 1:
                raise CleansingError(f"record {proposal.record_id} changed since the proposal; re-scan first")
        elif fix.action == "delete":
            before = _fetch_row(spec, pk, proposal.record_id)  # the full row, unmapped columns included
            sql = f"DELETE FROM {spec.table} WHERE {where}"
            if _admin(spec, sql) != 1:
                raise CleansingError(f"record {proposal.record_id} no longer exists; re-scan first")
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
    if row["undone_at"]:
        raise CleansingError(f"log entry {log_id} was already undone at {row['undone_at']}")
    registry = ctx.registry if ctx else load_registry(catalog.META_DB_PATH)
    spec = registry.by_id(row["source_id"])
    pk = ctx.pk if ctx else lexicons.primary_key(spec)
    before, after, sql = json.loads(row["before_value"]), json.loads(row["after_value"]), None
    where = f"{pk} = {lexicons.sql_literal(row['record_id'])}"
    kind = row["action"]
    if kind == "resolve":
        kind = {"accept_candidate": "set", "delete_record": "delete"}.get(row["decision"], "resolve")
        if row["decision"] == "confirm_clone":
            _undo_watch(json.loads(row["evidence"] or "{}"))
    if kind == "set":
        col = _column(spec, row["field"])
        sql = f"UPDATE {spec.table} SET {col} = {lexicons.sql_literal(before)} WHERE {where} AND {_match(col, after)}"
        if _admin(spec, sql) != 1:  # someone edited it after us: never overwrite their change blindly
            raise CleansingError(f"record {row['record_id']} no longer holds {after!r}; not undone")
    elif kind == "delete":
        cols = ", ".join(before)
        vals = ", ".join(lexicons.sql_literal(v) for v in before.values())
        sql = f"INSERT INTO {spec.table} ({cols}) VALUES ({vals})"
        _admin(spec, sql)
    with _db() as conn:
        conn.execute("UPDATE CLEANSING_LOG SET undone_at = ?, undone_by = ? WHERE log_id = ?",
                     (datetime.now(timezone.utc).isoformat(), actor, log_id))
    return {"log_id": log_id, "action": row["action"], "sql": sql}


RESOLUTIONS = ("confirm_clone", "accept_candidate", "delete_record", "dismiss")


def resolve(capture_id: Any, decision: str, note: str = "", actor: str = "operator",
            candidate: Optional[str] = None, ctx: Optional[Context] = None) -> Dict[str, Any]:
    """Close what the proposals leave open with a recorded human decision (the resolution step).

    confirm_clone    keep the camera's observation, record it as verified clone evidence and put the
                     plate on the watchlist (mediator.watchlist) -- no source write
    accept_candidate rewrite the plate to a candidate the matcher listed for this read
    delete_record    delete the capture through the source's /admin/sql (full row logged for undo)
    dismiss          the operator judges the anomaly harmless; a note saying why is mandatory
    Every resolution is a CLEANSING_LOG row, so scan() reports it as `resolved` and undo() reverts it.
    """
    if decision not in RESOLUTIONS:
        raise CleansingError(f"unknown resolution {decision!r}; expected one of {', '.join(RESOLUTIONS)}")
    if decision == "dismiss" and not (note or "").strip():
        raise CleansingError("dismiss needs a note saying why the anomaly is acceptable")
    ctx = ctx or Context.build()
    spec, pk = ctx.cam, ctx.pk
    record = ctx.record(capture_id)
    prop = propose(record, ctx)
    open_codes = [a.code for a in _remaining(prop, ctx)]
    if not open_codes:
        raise CleansingError(f"record {capture_id} has nothing left to resolve")
    where = f"{pk} = {lexicons.sql_literal(record['record_id'])}"
    fld, before, after, sql = "record", None, decision, None
    evidence: Dict[str, Any] = {"anomalies": open_codes}
    if decision == "confirm_clone":
        from mediator import watchlist
        plate = norm_plate(record.get(PLATE)) or ""
        previous = catalog.watchlist_get(plate)
        watchlist.add(plate, f"verified clone evidence, {spec.source_id} capture {capture_id}: {note}".strip(),
                      actor)
        evidence.update(plate=plate, watch_previous=previous)
    elif decision == "accept_candidate":
        m = match_plate(norm_plate(record.get(PLATE)) or "", _number(record.get(CONF)), record.get("observed_make"),
                        record.get("observed_colour"), ctx, record.get("observed_model"))
        listed = [c["plate"] for c in m["candidates"]]
        if norm_plate(candidate) not in listed:
            raise CleansingError(f"{candidate} is not among the matcher's candidates for this read ({', '.join(listed)})")
        fld, col = PLATE, _column(spec, PLATE)
        before = record["_raw"].get(col, record.get(PLATE))
        after = render_plate(norm_plate(candidate), spec.key_predicate.render)
        sql = f"UPDATE {spec.table} SET {col} = {lexicons.sql_literal(after)} WHERE {where} AND {_match(col, before)}"
        if _admin(spec, sql) != 1:
            raise CleansingError(f"record {capture_id} changed since the scan; re-scan first")
        evidence.update(candidates=m["candidates"][:5])
    elif decision == "delete_record":
        before = _fetch_row(spec, pk, record["record_id"])
        sql = f"DELETE FROM {spec.table} WHERE {where}"
        if _admin(spec, sql) != 1:
            raise CleansingError(f"record {capture_id} no longer exists; re-scan first")
    with _db() as conn:
        cur = conn.execute(
            "INSERT INTO CLEANSING_LOG (source_id, record_id, field, action, before_value, after_value, rule_id, "
            "rule_path, evidence, actor, applied_at, sql_sent, decision, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (spec.source_id, str(record["record_id"]), fld, "resolve", json.dumps(before, default=str),
             json.dumps(after, default=str), None, "[]", json.dumps(evidence, default=str), actor,
             datetime.now(timezone.utc).isoformat(), sql, decision, note))
    return {"log_id": cur.lastrowid, "decision": decision, "record_id": record["record_id"], "sql": sql,
            "resolved": open_codes}


def active_resolutions(source_id: str) -> Dict[str, Dict[str, Any]]:
    """record_id -> the latest resolution still in force that leaves the record in place."""
    with _db() as conn:
        rows = conn.execute(
            "SELECT * FROM CLEANSING_LOG WHERE action = 'resolve' AND undone_at IS NULL AND source_id = ? "
            "AND decision IN ('confirm_clone', 'dismiss') ORDER BY log_id", (source_id,)).fetchall()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        ev = json.loads(r["evidence"] or "{}")
        out[str(r["record_id"])] = {"log_id": r["log_id"], "decision": r["decision"], "note": r["note"],
                                    "actor": r["actor"], "at": r["applied_at"], "anomalies": ev.get("anomalies", [])}
    return out


def _undo_watch(evidence: Dict[str, Any]) -> None:
    """Put the watchlist back the way confirm_clone found it."""
    plate, previous = evidence.get("plate"), evidence.get("watch_previous")
    if not plate:
        return
    if previous:
        catalog.watchlist_upsert(plate, previous.get("reason") or "", previous.get("added_by") or "operator",
                                 previous.get("added_at") or "")
    else:
        catalog.watchlist_delete(plate)


def log_entries(limit: int = 50) -> List[Dict[str, Any]]:
    with _db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM CLEANSING_LOG ORDER BY log_id DESC LIMIT ?", (limit,))]


def teach(record_id: Any, fld: str, wrong_rule_id: str, condition: Dict[str, Any], conclusion: Dict[str, Any],
          because: str, branch: str = "except", actor: str = "expert", ctx: Optional[Context] = None,
          rules_path: Optional[Path] = None) -> Dict[str, Any]:
    """Knowledge acquisition on a live record (slides 27-32): the record's case becomes the new
    rule's cornerstone; the tree refuses the rule if it would change another cornerstone."""
    path = Path(rules_path or RULES_PATH)
    ctx = ctx or Context.build(rules_path=path)
    record = ctx.record(record_id)
    case = features(record, fld, ctx.profile_for(record), ctx)
    case["_provenance"] = f"{ctx.cam.source_id} record {record_id}, taught by {actor}"
    rule = ctx.tree.add_exception(case, wrong_rule_id, condition, conclusion, because, branch, created_by=actor)
    ctx.tree.save(path)
    return {"rule": rule.id, "under": wrong_rule_id, "branch": branch, "conclusion": ctx.tree.evaluate(case)[0]}
