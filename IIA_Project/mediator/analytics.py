"""CONTEXT-ADDED (R2: analytics): cross-source contradictions for one vehicle, and a fleet-wide leaderboard.

Each indicator needs rows from two agencies at once, which is exactly what no single agency can see:
  ALIBI_PUC            a PUC test (vehicle physically present) inside the stolen interval (FIR -> recovery or now)
  ALIBI_CAMERA         a camera capture inside the stolen interval (OCR-tolerant plate match)
  ALIBI_CHALLAN        a traffic challan issued inside the stolen interval
  QUICK_CLAIM          theft claim lost <= 15 days after the policy started, or intimated before the FIR
  TRANSFER_BEFORE_LOSS ownership transfer <= 30 days before an FIR or a claim's loss date
  LAPSED_COVER_SEEN    the latest policy has expired but cameras saw the vehicle after expiry
  PERSON_RING          one mobile behind owners / policy holders / accused of >= 3 vehicles
Rows come from a ContextResult when given (no extra traffic), else from the sources via SourceClient.
Dates are read in each agency's own format (mediator/evidence.py).

leaderboard(): the same top-k machinery as free-text search (TA or TPUT), where each source's partial
"suspicion" score is the `decision` conditions context_graph.json declares for its tables (open stolen FIR,
theft claim, accused person, pending challan...); the k winners are then explained by their indicators.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from mediator import entity_topk as E
from mediator import evidence as D
from mediator import topk_sql as S
from mediator.context_types import Indicator, ParsedQuery, RankedVehicle, TopKResult

QUICK_CLAIM_DAYS = 15
TRANSFER_DAYS = 30
RING_MIN_VEHICLES = 3
SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
SOURCES_FOR: Dict[str, List[str]] = {   # which agencies each indicator needs rows from
    "ALIBI_PUC": ["THEFT", "PUC"], "ALIBI_CAMERA": ["THEFT", "CAM"], "ALIBI_CHALLAN": ["THEFT", "CAM"],
    "QUICK_CLAIM": ["INS", "THEFT"], "TRANSFER_BEFORE_LOSS": ["REG", "INS", "THEFT"],
    "LAPSED_COVER_SEEN": ["INS", "CAM"], "PERSON_RING": ["REG", "INS", "THEFT"],
}


def _d(t: Optional[datetime]) -> str:
    return t.strftime("%d %b %Y %H:%M").replace(" 00:00", "") if t else "?"


def _rows(rows: D.Rows, sid: str, table: str) -> List[Dict[str, Any]]:
    return rows.get((sid, table), [])


def _ev(sid: str, table: str, row: Dict[str, Any]) -> Tuple[str, str, Any]:
    return (sid, table, D.pk_of(sid, table, row))


def stolen_intervals(rows: D.Rows, now: datetime) -> List[Tuple[datetime, datetime, Dict[str, Any], str]]:
    """(start, end, FIR row, how the end was set) for each theft FIR."""
    recovered = {r.get("incident_id"): D.when(r.get("recovered_on"), "THEFT", "STOLEN_PROPERTY", "recovered_on")
                 for r in _rows(rows, "THEFT", "STOLEN_PROPERTY")}
    out = []
    for fir in _rows(rows, "THEFT", "CRIME_RECORDS"):
        if str(fir.get("stolen_flag", "")).upper() != "Y":
            continue
        start = D.when(fir.get("reported_date"), "THEFT", "CRIME_RECORDS", "reported_date")
        end = recovered.get(fir.get("incident_id"))
        if start is None:
            continue
        if end is not None:
            out.append((start, end, fir, f"recovered {_d(end)}"))
        elif str(fir.get("recovered_flag", "")).upper() != "Y":
            out.append((start, now, fir, "not recovered"))
    return out


def _alibis(rows: D.Rows, plate: str, intervals: List[Any]) -> List[Indicator]:
    out: List[Indicator] = []
    tests = [(D.when(t.get("test_ts"), "PUC", "CERT_TESTS", "test_ts"), t) for t in _rows(rows, "PUC", "CERT_TESTS")]
    caps = [(D.when(c.get("captured_at"), "CAM", "PLATE_CAPTURES", "captured_at"), c)
            for c in _rows(rows, "CAM", "PLATE_CAPTURES")]
    chals = [(D.when(c.get("issued_at"), "CAM", "CHALLANS", "issued_at"), c) for c in _rows(rows, "CAM", "CHALLANS")]
    for start, end, fir, how in intervals:
        fir_txt = f"FIR {fir.get('fir_no') or fir.get('incident_id')} (reported {_d(start)}, {how})"
        inside = [(t, r) for t, r in tests if t and start < t <= end]
        if inside:
            t, r = inside[0]
            out.append(Indicator("ALIBI_PUC", "high", "PUC test while reported stolen",
                                 f"PUC test {r.get('test_id')} on {_d(t)} falls inside the stolen interval of {fir_txt}; "
                                 "a PUC test needs the vehicle physically present at the centre.",
                                 [_ev("PUC", "CERT_TESTS", x) for _, x in inside] + [_ev("THEFT", "CRIME_RECORDS", fir)]))
        seen = [(t, r) for t, r in caps if t and start < t <= end]
        fined = [(t, r) for t, r in chals if t and start < t <= end]
        if seen:
            conf = max(D.read_confidence(r.get("plate_id"), plate) * float(r.get("ocr_confidence") or 1.0) for _, r in seen)
            times = sorted(t for t, _ in seen)
            out.append(Indicator("ALIBI_CAMERA", "high" if conf >= 0.8 else "medium",   # a doubtful read is a lead, not proof
                                 "Seen on camera while reported stolen",
                                 f"{len(seen)} camera capture(s), first {_d(times[0])}, last {_d(times[-1])}, fall inside the "
                                 f"stolen interval of {fir_txt} (best plate-read confidence {conf:.2f}).",
                                 [_ev("CAM", "PLATE_CAPTURES", r) for _, r in seen] + [_ev("THEFT", "CRIME_RECORDS", fir)]))
        if fined:
            t, r = min(fined, key=lambda x: x[0])
            out.append(Indicator("ALIBI_CHALLAN", "high", "Fined by traffic police while reported stolen",
                                 f"Challan {r.get('challan_id')} ({r.get('violation') or 'violation'}) issued {_d(t)} falls "
                                 f"inside the stolen interval of {fir_txt}.",
                                 [_ev("CAM", "CHALLANS", x) for _, x in fined] + [_ev("THEFT", "CRIME_RECORDS", fir)]))
    return out


def _quick_claims(rows: D.Rows) -> List[Indicator]:
    out: List[Indicator] = []
    policies = {p.get("policy_id"): p for p in _rows(rows, "INS", "POLICY_RECORDS")}
    firs = [D.when(f.get("reported_date"), "THEFT", "CRIME_RECORDS", "reported_date") for f in _rows(rows, "THEFT", "CRIME_RECORDS")]
    first_fir = min((f for f in firs if f), default=None)
    for c in _rows(rows, "INS", "CLAIMS"):
        if str(c.get("loss_type", "")).upper() != "THEFT":
            continue
        loss = D.when(c.get("loss_date"), "INS", "CLAIMS", "loss_date")
        told = D.when(c.get("intimation_date"), "INS", "CLAIMS", "intimation_date")
        pol = policies.get(c.get("policy_id"), {})
        began = D.when(pol.get("policy_start"), "INS", "POLICY_RECORDS", "policy_start")
        reasons = []
        if loss and began and 0 <= (loss - began).days <= QUICK_CLAIM_DAYS:
            reasons.append(f"loss on {_d(loss)} only {(loss - began).days} day(s) after the policy started ({_d(began)})")
        if told and first_fir and told < first_fir:
            reasons.append(f"insurer told on {_d(told)}, before the police FIR of {_d(first_fir)}")
        if reasons:
            out.append(Indicator("QUICK_CLAIM", "high" if len(reasons) == 2 else "medium", "Suspiciously quick theft claim",
                                 f"Theft claim {c.get('claim_id')}: " + "; ".join(reasons) + ".",
                                 [_ev("INS", "CLAIMS", c)] + ([_ev("INS", "POLICY_RECORDS", pol)] if pol else [])))
    return out


def _transfers(rows: D.Rows) -> List[Indicator]:
    losses = [(D.when(f.get("reported_date"), "THEFT", "CRIME_RECORDS", "reported_date"), "FIR", _ev("THEFT", "CRIME_RECORDS", f))
              for f in _rows(rows, "THEFT", "CRIME_RECORDS")]
    losses += [(D.when(c.get("loss_date"), "INS", "CLAIMS", "loss_date"), "claimed loss", _ev("INS", "CLAIMS", c))
               for c in _rows(rows, "INS", "CLAIMS")]
    out: List[Indicator] = []
    for t in _rows(rows, "REG", "OWNERSHIP_HISTORY"):
        moved = D.when(t.get("transfer_date"), "REG", "OWNERSHIP_HISTORY", "transfer_date")
        hits = [(w, kind, ev) for w, kind, ev in losses if moved and w and 0 <= (w - moved).days <= TRANSFER_DAYS]
        if hits:
            w, kind, ev = min(hits, key=lambda h: h[0])
            out.append(Indicator("TRANSFER_BEFORE_LOSS", "medium", "Ownership changed just before the loss",
                                 f"{t.get('transfer_type') or 'Transfer'} on {_d(moved)}, {(w - moved).days} day(s) before the {kind} of {_d(w)}.",
                                 [_ev("REG", "OWNERSHIP_HISTORY", t), ev]))
    return out


def _lapsed_seen(rows: D.Rows, now: datetime) -> List[Indicator]:
    ends = [(D.when(p.get("policy_until"), "INS", "POLICY_RECORDS", "policy_until"), p) for p in _rows(rows, "INS", "POLICY_RECORDS")]
    ends = [(e, p) for e, p in ends if e]
    if not ends:
        return []
    until, pol = max(ends, key=lambda x: x[0])        # latest policy wins, as in phase 1
    if until >= now:
        return []
    after = [c for c in _rows(rows, "CAM", "PLATE_CAPTURES")
             if (t := D.when(c.get("captured_at"), "CAM", "PLATE_CAPTURES", "captured_at")) and t > until + timedelta(days=1)]
    if not after:
        return []
    last = max(D.when(c.get("captured_at"), "CAM", "PLATE_CAPTURES", "captured_at") for c in after)
    return [Indicator("LAPSED_COVER_SEEN", "medium", "Driven after insurance lapsed",
                      f"Latest policy ended {_d(until)}, yet cameras captured the vehicle {len(after)} time(s) since, last on {_d(last)}.",
                      [_ev("INS", "POLICY_RECORDS", pol)] + [_ev("CAM", "PLATE_CAPTURES", c) for c in after])]


def _mobiles(rows: D.Rows) -> Dict[str, List[Tuple[str, str, Any]]]:
    """Mobiles of this vehicle's owner, policy holder and accused, with the row that names each."""
    accused = {r.get("person_id") for r in _rows(rows, "THEFT", "INCIDENT_PERSONS") if str(r.get("role", "")).upper() == "ACCUSED"}
    out: Dict[str, List[Tuple[str, str, Any]]] = {}
    for sid, table in (("REG", "OWNER_CONTACTS"), ("INS", "POLICY_HOLDERS"), ("THEFT", "PERSONS")):
        col = D.tables_meta(sid).get(table, {}).get("person", {}).get("mobile", "mobile")
        for r in _rows(rows, sid, table):
            if r.get(col) and (table != "PERSONS" or r.get("person_id") in accused):
                out.setdefault(str(r[col]), []).append(_ev(sid, table, r))
    return out


def _ring(rows: D.Rows, plate: str, client: Any, use_fetch: bool) -> List[Indicator]:
    mobiles = _mobiles(rows)
    if not mobiles:
        return []
    links = D.plates_for_mobiles(client, list(mobiles)) if use_fetch else D.links_in_rows(rows, mobiles)
    out: List[Indicator] = []
    for m, evs in mobiles.items():
        vehicles = dict(links.get(m, {}))
        vehicles.setdefault(plate, set()).update(e[0] for e in evs)
        if len(vehicles) >= RING_MIN_VEHICLES:
            agencies = sorted(set().union(*vehicles.values()))
            out.append(Indicator("PERSON_RING", "high", "Mobile shared across several vehicles",
                                 f"Mobile ...{m[-4:]} links {len(vehicles)} vehicles ({', '.join(sorted(vehicles))}) "
                                 f"through {', '.join(agencies)} records.", evs))
    return out


def indicators_from_rows(plate: str, rows: D.Rows, now: Optional[datetime] = None,
                         client: Any = None) -> List[Indicator]:
    now = now or datetime.now()
    found = _alibis(rows, plate, stolen_intervals(rows, now)) + _quick_claims(rows) + _transfers(rows) + _lapsed_seen(rows, now)
    found += _ring(rows, plate, client, use_fetch=client is not None)
    return sorted(found, key=lambda i: (SEVERITY_ORDER.get(i.severity, 3), i.code))


def indicators_for(plate: str, context: Any = None, client: Any = None, now: Optional[datetime] = None,
                   codes: Optional[List[str]] = None) -> List[Indicator]:
    """Indicators for one vehicle. With a ContextResult, only its rows are used (no extra traffic).
    `codes` limits the check (and the sources asked) to those indicators."""
    plate = "".join(ch for ch in str(plate).upper() if ch.isalnum())
    if context is not None:
        found = indicators_from_rows(plate, D.from_context(context), now)
    else:
        if client is None:
            from mediator.source_client import SourceClient
            client = SourceClient()
        sources = sorted({s for c in codes for s in SOURCES_FOR.get(c, [])}) if codes else None
        ring = codes is None or "PERSON_RING" in codes
        found = indicators_from_rows(plate, D.gather(plate, client, sources), now, client=client if ring else None)
    return [i for i in found if codes is None or i.code in codes]


def suspicion_features() -> Dict[str, List[S.Feature]]:
    """Each source's partial suspicion = the decision conditions declared for its tables in context_graph.json."""
    feats: Dict[str, List[S.Feature]] = {}
    for sid, meta in S.graph()["sources"].items():
        for table, tmeta in meta["tables"].items():
            for d in tmeta.get("decision", []):
                op = "<>" if d["op"] == "!=" else d["op"]
                feats.setdefault(sid, []).append(S.Feature(sid, (table,), f"{table}.{d['col']} {op} {S.lit(d['value'])}",
                                                           float(d["boost"]), f"{table}.{d['col']} {d['op']} {d['value']}"))
    return feats


def leaderboard_result(k: int = 10, client: Any = None, method: str = "ta", explain: bool = True) -> TopKResult:
    """Fleet-wide most-suspicious vehicles with the access counts (for the TA vs TPUT comparison)."""
    if client is None:
        from mediator.source_client import SourceClient
        client = SourceClient()
    import time
    t0 = time.perf_counter()
    feats = suspicion_features()
    run, depth = E.execute(feats, k, client, method)
    vehicles = E.ranked(run, k, sum(f.weight for fs in feats.values() for f in fs))
    if explain:
        for v in vehicles:
            v.explanation = [i.title for i in indicators_for(v.plate, client=client)] + v.explanation
    res = TopKResult(query=ParsedQuery(text="leaderboard", parser="fleet"), vehicles=vehicles,
                     sorted_accesses=run.sorted_accesses, random_accesses=run.random_accesses,
                     rows_shipped=run.rows_shipped, baseline_rows=E.baseline_rows(feats, client),
                     stopped_at_depth=depth, elapsed_ms=round((time.perf_counter() - t0) * 1000, 1))
    res.round_trips = run.round_trips    # type: ignore[attr-defined]
    res.method = method                  # type: ignore[attr-defined]
    return res


def leaderboard(k: int = 10, client: Any = None, method: str = "ta") -> List[RankedVehicle]:
    return leaderboard_result(k, client, method).vehicles
