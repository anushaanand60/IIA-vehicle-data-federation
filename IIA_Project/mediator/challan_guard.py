"""Challan Guard: verify before you fine.

An ANPR camera reads a plate, a database says "no insurance", and a challan goes out. That chain
is wrong often enough that Delhi Traffic Police run an online dispute system for it, and roughly
nine in ten wrongful challans start with a plate misread. This module puts the federation between
the camera and the fine: no challan is raised until identity, clone signal, theft status and
insurance *at the moment of the sighting* have all been confirmed live, and if an authoritative
source cannot be reached the guard refuses to decide rather than guessing. Three properties:

  * **Nothing is decided from stored data.** Every step calls `run_global_query` or the executor
    and reads only global attributes; no source table, column or date format appears here.
  * **Refusal is a verdict.** A dead authoritative source produces HOLD naming it — the same rule
    the executor applies to a dead wrapper — never a fine and never an exception.
  * **A dispute re-asks, it never re-reads.** `dispute()` re-runs the whole verification live; the
    stored evidence bundle only says *what changed*. That is why this federates instead of caching.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from mediator import catalog, plate_resolve, travel_check, watchlist
from mediator.core import run_global_query
from mediator.executor import execute
from mediator.registry_loader import load_registry
from mediator.transforms import get_transform, norm_plate

ISSUE, HOLD, REJECT = "ISSUE", "HOLD", "REJECT"
STATUS_FOR = {ISSUE: "ISSUED", HOLD: "HOLD", REJECT: "REJECTED"}
# MV Act §196 (driving an uninsured vehicle): first offence, then repeat offence.
FINE_FIRST_INR, FINE_REPEAT_INR = 2000, 4000
FAILED = ("DOWN", "TIMEOUT", "ERROR")
IDENTITY_ATTRS = ["vehicle_make", "vehicle_colour", "registration_status"]
SIGHTING_ATTRS = ["last_seen_location", "last_seen_time", "camera_lat", "camera_lon"]
CLONE_REASON = "CLONE SUSPECT (Challan Guard)"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _day(value: Any) -> str:
    """The date part of an ISO timestamp. Comparison is by date, never by clock time."""
    return str(value or "")[:10]


# ------------------------------------------------------------------ sightings (metadata-driven)

def sightings_for(plate: str) -> List[Dict[str, Any]]:
    """Every capture the observational sources hold for this plate, as {location, lat, lon, at}.

    Uses the executor, not `run_global_query`: the integrator collapses a source to its single
    latest row and a travel check needs all of them. Which sources are sighting sources, which
    columns mean location/time/coordinates and how to decode them all come from the registry.
    """
    try:
        registry = load_registry(catalog.META_DB_PATH)
        response = execute(registry, plate, SIGHTING_ATTRS)
    except Exception:  # an unusable registry means "no sighting evidence", never a crash
        return []
    out: List[Dict[str, Any]] = []
    for result in response.results:
        spec = registry.by_id(result.source_id)
        if result.status != "OK" or spec is None or spec.authority != "observational":
            continue
        for row in result.rows:
            values = _to_global(row, spec.attribute_map)
            out.append({"source_id": result.source_id,
                        "location": values.get("last_seen_location"),
                        "lat": _number(values.get("camera_lat")),
                        "lon": _number(values.get("camera_lon")),
                        "at": values.get("last_seen_time")})
    return out


def _to_global(row: Dict[str, Any], mappings: Any) -> Dict[str, Any]:
    """One raw source row -> global attributes, applying each mapping's declared transform."""
    values: Dict[str, Any] = {}
    for m in mappings:
        raw = row.get(m.source_attr)
        if raw is None:  # the SQL builder aliases a duplicated column name as table__column
            raw = row.get(f"{m.source_table}__{m.source_attr}")
        if raw is not None:
            try:
                values[m.global_attr] = get_transform(m.transform)(raw)
            except Exception:
                values[m.global_attr] = raw  # an unusable transform must not lose the value
    return values


def _number(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ the verification steps

def _authoritative_failures(availability: Dict[str, str]) -> List[str]:
    """Sources whose silence blocks a fine. Observational ones (cameras) do not: a fine rests on
    registration, insurance and crime records, and the catalog is what says which is which."""
    cat = catalog.get_source_catalog()
    return sorted(sid for sid, status in (availability or {}).items()
                  if status in FAILED
                  and str(cat.get(sid, {}).get("authority", "")).upper() != "OBSERVATIONAL")


def _step(steps: List[Dict[str, Any]], title: str, detail: str, done: bool = True) -> None:
    steps.append({"title": title, "detail": detail, "done": done})


def _outcome(steps: List[Dict[str, Any]], verdict: str, reason: str, evidence: Dict[str, Any],
             plate_resolved: Optional[str] = None, amount: Optional[int] = None) -> Dict[str, Any]:
    return {"verdict": verdict, "reason": reason, "steps": steps, "evidence": evidence,
            "plate_resolved": plate_resolved, "amount_inr": amount}


def _verification(case: Dict[str, Any], exclude_case_id: Optional[int] = None) -> Dict[str, Any]:
    """The six ordered checks. Pure with respect to the case store: it decides, it does not save."""
    steps: List[Dict[str, Any]] = []
    read = norm_plate(case.get("plate_read")) or str(case.get("plate_read") or "")
    seen_on = _day(case.get("captured_at"))
    profiles: Dict[str, Dict[str, Any]] = {}

    def profile_of(plate: str, attrs: Optional[List[str]] = None) -> Dict[str, Any]:
        key = f"{plate}|{','.join(attrs) if attrs else 'all'}"
        if key not in profiles:
            profiles[key] = (run_global_query(plate, attrs) or {}).get("profile") or {}
        return profiles[key]

    # 1 -- can we even ask? A partial federation may not convict.
    read_profile = profile_of(read)
    availability = read_profile.get("source_availability") or {}
    evidence: Dict[str, Any] = {"captured_at": case.get("captured_at"), "checked_at": _now(),
                                "source_availability": dict(availability)}
    if down := _authoritative_failures(availability):
        detail = f"{', '.join(down)} unreachable — refusing to fine on partial evidence"
        _step(steps, "Sources reachable", detail, done=False)
        return _outcome(steps, HOLD, detail, evidence)
    _step(steps, "Sources reachable", f"all authoritative sources answered ({', '.join(sorted(availability))})")

    # 2 -- which registered vehicle did the camera actually see?
    best, ranked, identity = plate_resolve.resolve(
        read, case.get("observed_make"), case.get("observed_colour"),
        lambda cand: profile_of(cand, IDENTITY_ATTRS))
    evidence["candidates"] = [{"plate": c.plate, "score": c.score, "reasons": c.reasons} for c in ranked]
    if identity == "NONE":
        detail = "no registered vehicle matches this read; route to unknown-vehicle check"
        _step(steps, "Identity", detail, done=False)
        return _outcome(steps, REJECT, detail, evidence)
    if identity == "AMBIGUOUS":
        listed = ", ".join(f"{c.plate} ({c.score})" for c in ranked)
        detail = f"read could be more than one registered vehicle: {listed} — manual review"
        _step(steps, "Identity", detail, done=False)
        return _outcome(steps, HOLD, detail, evidence)
    resolved = best.plate
    _step(steps, "Identity", (f"misread corrected: {read} → {resolved} "
                              f"({'; '.join(best.reasons)})") if resolved != read
          else f"plate read confirmed against the registration authority: {resolved}")

    # 3 -- is this plate in two places at once?
    here = {"location": case.get("location"), "lat": _number(case.get("lat")),
            "lon": _number(case.get("lon")), "at": case.get("captured_at")}
    legs = travel_check.impossible_legs([*sightings_for(resolved), here])
    evidence["impossible_legs"] = [leg.__dict__ for leg in legs]
    if legs:
        leg = legs[0]
        detail = (f"impossible travel: {leg.from_loc} → {leg.to_loc} in {leg.minutes:.0f} min "
                  f"({leg.kmh:.0f} km/h) — cloned plate suspected")
        _step(steps, "Clone signal", detail, done=False)
        if not watchlist.is_watched(resolved):
            watchlist.add(resolved, CLONE_REASON, added_by="challan_guard")
        return _outcome(steps, HOLD, detail, evidence, resolved)
    _step(steps, "Clone signal", "no implausible journey between this and any other sighting")

    # 4 -- was the vehicle already stolen when it was photographed?
    profile = profile_of(resolved)
    evidence["profile"] = {k: v for k, v in profile.items() if not isinstance(v, (dict, list))}
    incident = _day(profile.get("last_incident_date"))
    if str(profile.get("stolen_status") or "").upper() == "STOLEN" and (not incident or incident <= seen_on):
        detail = "vehicle reported stolen before sighting — route to police, do not fine owner"
        _step(steps, "Theft", f"{detail} (reported {incident or 'earlier'}, case {profile.get('case_status')})",
              done=False)
        catalog.log_alert(resolved, "Challan Guard: sighting of a stolen vehicle",
                          case.get("captured_at"), case.get("location"), profile.get("decision"), _now())
        return _outcome(steps, REJECT, detail, evidence, resolved)
    _step(steps, "Theft", f"no open theft report before {seen_on} (status {profile.get('stolen_status')})")

    # 5 -- a void registration is a different offence, not an insurance one.
    reg_status = str(profile.get("registration_status") or "").upper()
    if reg_status and reg_status != "ACTIVE":
        detail = f"registration is {reg_status}, not ACTIVE — route as a registration offence"
        _step(steps, "Registration", detail, done=False)
        return _outcome(steps, REJECT, detail, evidence, resolved)
    _step(steps, "Registration", f"registration {reg_status or 'not on record'}")

    # 6 -- was it insured on the day of the sighting? Not today: on that day.
    expiry = _day(profile.get("insurance_expiry"))
    if expiry and expiry >= seen_on:
        detail = f"insured on {seen_on} (policy valid to {expiry}) — no offence"
        _step(steps, "Insurance at sighting time", detail, done=False)
        return _outcome(steps, REJECT, detail, evidence, resolved)
    detail = (f"uninsured on {seen_on}: policy expired {expiry}" if expiry
              else f"uninsured on {seen_on}: no policy on record")
    _step(steps, "Insurance at sighting time", detail)
    repeat = catalog.challan_prior_issued(resolved, exclude_case_id=exclude_case_id)
    amount = FINE_REPEAT_INR if repeat else FINE_FIRST_INR
    _step(steps, "Amount", f"₹{amount} ({'repeat' if repeat else 'first'} offence, MV Act §196)")
    return _outcome(steps, ISSUE, detail, evidence, resolved, amount)


# ------------------------------------------------------------------ the case API

def new_candidate(plate_read: str, camera_id: str, location: str, lat: Optional[float],
                  lon: Optional[float], captured_at: str, observed_make: Optional[str] = None,
                  observed_colour: Optional[str] = None, ocr_confidence: Optional[float] = None,
                  actor: str = "anpr") -> int:
    case_id = catalog.challan_insert({
        "plate_read": plate_read, "camera_id": camera_id, "location": location, "lat": lat,
        "lon": lon, "captured_at": captured_at, "observed_make": observed_make,
        "observed_colour": observed_colour, "ocr_confidence": ocr_confidence, "status": "CANDIDATE"})
    catalog.challan_event(case_id, "CREATED", actor, {"plate_read": plate_read, "camera_id": camera_id})
    return case_id


get, queue, events = catalog.challan_get, catalog.challan_list, catalog.challan_events


def _load(case_id: int) -> Dict[str, Any]:
    case = catalog.challan_get(case_id)
    if case is None:
        raise ValueError(f"no challan case {case_id}")
    return case


def _persist(case_id: int, result: Dict[str, Any], status: str, actor: str,
             event: str) -> Dict[str, Any]:
    evidence = {**result["evidence"], "steps": result["steps"], "verdict": result["verdict"]}
    catalog.challan_update(case_id, status=status, verdict=result["verdict"],
                           reason=result["reason"], amount_inr=result["amount_inr"],
                           plate_resolved=result["plate_resolved"], evidence=evidence)
    catalog.challan_event(case_id, event, actor,
                          {"verdict": result["verdict"], "reason": result["reason"]})
    return {**_load(case_id), "steps": result["steps"]}


def verify(case_id: int, actor: str = "operator") -> Dict[str, Any]:
    """Run the six checks live and persist the verdict. ISSUE auto-issues; HOLD and REJECT park."""
    case = _load(case_id)
    catalog.challan_event(case_id, "VERIFY_STARTED", actor, {"plate_read": case["plate_read"]})
    result = _verification(case, exclude_case_id=case_id)
    return _persist(case_id, result, STATUS_FOR[result["verdict"]], actor, "VERIFIED")


def _override(case_id: int, status: str, verdict: str, actor: str,
              reason: Optional[str]) -> Dict[str, Any]:
    case = _load(case_id)
    catalog.challan_update(case_id, status=status, verdict=verdict,
                           reason=reason or case.get("reason") or f"{status} by {actor}")
    catalog.challan_event(case_id, status, actor, {"reason": reason, "override": True})
    return _load(case_id)


def issue(case_id: int, actor: str = "operator", reason: Optional[str] = None) -> Dict[str, Any]:
    return _override(case_id, "ISSUED", ISSUE, actor, reason)


def reject(case_id: int, actor: str = "operator", reason: Optional[str] = None) -> Dict[str, Any]:
    return _override(case_id, "REJECTED", REJECT, actor, reason)


def hold(case_id: int, actor: str = "operator", reason: Optional[str] = None) -> Dict[str, Any]:
    return _override(case_id, "HOLD", HOLD, actor, reason)


def summary() -> Dict[str, int]:
    """Queue counts for the GUI. "Prevented" is the number the demo is actually about."""
    cases = catalog.challan_list(limit=1000)
    counts = {s: sum(1 for c in cases if c["status"] == s)
              for s in ("CANDIDATE", "HOLD", "ISSUED", "REJECTED", "DISPUTED", "UPHELD", "CANCELLED")}
    counts["PREVENTED"] = counts["REJECTED"] + counts["CANCELLED"]
    return counts


# Fields a citizen's dispute can legitimately turn on: the facts the verdict was built from.
DISPUTABLE = ("insurance_expiry", "stolen_status", "registration_status")


def _changes(old: Dict[str, Any], new: Dict[str, Any]) -> List[str]:
    return [f"{f} was {old.get(f) or 'none'} now {new.get(f) or 'none'}"
            for f in DISPUTABLE if old.get(f) != new.get(f)]


def dispute(case_id: int, reason: str, actor: str = "citizen") -> Dict[str, Any]:
    """Re-verify an issued challan against live sources. Cancels it if the answer has changed."""
    case = _load(case_id)
    if case["status"] not in ("ISSUED", "UPHELD"):
        raise ValueError(f"case {case_id} is {case['status']}; only an issued challan can be disputed")
    before = (case.get("evidence") or {}).get("profile") or {}
    catalog.challan_update(case_id, status="DISPUTED")
    catalog.challan_event(case_id, "DISPUTED", actor, {"reason": reason})

    result = _verification(case, exclude_case_id=case_id)
    after = result["evidence"].get("profile") or {}
    if result["verdict"] != ISSUE:
        changed = _changes(before, after)
        result["reason"] = (f"record changed since issue: {'; '.join(changed)}" if changed
                            else f"re-verified live: {result['reason']}")
        result["amount_inr"] = None
        return _persist(case_id, result, "CANCELLED", actor, "CANCELLED")
    result["reason"] = f"re-verified live: {result['reason']}"
    return _persist(case_id, result, "UPHELD", actor, "UPHELD")
