"""Watchlist and alert log (Task 2.3).

Two enforcement ideas, both taken from systems that actually run:

  * **Watchlist** — an operator marks a plate with a stated reason, and every later query on it
    files a timestamped alert. This is the marker the UK MIB sets under Operation Tutelage, where
    ANPR sightings that keep disagreeing with the insurance database flag a vehicle to patrols
    (docs/FIELD_RESEARCH.md fact 7), and the flag the IIB raises behind India's ANPR-based
    uninsured-vehicle challans (facts 1, 3).
  * **Hotlist** — a stolen or scrapped verdict raises the same alert without anyone having marked
    the plate. Nobody should have to remember to watch a stolen car.

What is stored is *policy and audit*, never source data: a plate, a reason, a verdict and two
timestamps. Caching a source row here would contradict the federation thesis; a marker does not.
Storage lives in `mediator/catalog.py` (meta.db); this module owns the policy.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from mediator import catalog
from mediator.transforms import norm_plate

# A verdict at or above this severity alerts on its own. Prefix matching on the decision string,
# because `mediator/decide.py` owns its wording and adds verdicts over time.
HOTLIST_PREFIXES = ("STOLEN", "SCRAPPED", "SHREDDED")
HOTLIST_REASON = "Hotlist hit"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _key(plate: Any) -> str:
    """Every plate is canonicalised the same way the mediator canonicalises a query."""
    return norm_plate(plate) or ""


def add(plate: str, reason: str, added_by: str = "operator") -> Dict[str, Any]:
    """Mark a plate. Re-marking the same plate updates the reason instead of duplicating it."""
    key = _key(plate)
    catalog.watchlist_upsert(key, reason or "", added_by or "operator", _now())
    return catalog.watchlist_get(key) or {}


def remove(plate: str) -> None:
    """Unmark a plate. Removing one that was never watched is deliberately harmless."""
    catalog.watchlist_delete(_key(plate))


def listing() -> List[Dict[str, Any]]:
    """Every watched plate, most recently added first."""
    return catalog.watchlist_all()


def is_watched(plate: str) -> bool:
    return catalog.watchlist_get(_key(plate)) is not None


def alerts(limit: int = 50) -> List[Dict[str, Any]]:
    """The alert log, newest first."""
    return catalog.get_alerts(limit)


def _raise(plate: str, reason: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    """File one alert, carrying whatever sighting evidence this query happened to return."""
    seen_at = profile.get("last_seen_time")
    location = profile.get("last_seen_location")
    decision = profile.get("decision")
    ts = _now()
    alert_id = catalog.log_alert(plate, reason, seen_at, location, decision, ts)
    return {
        "alert_id": alert_id,
        "plate": plate,
        "reason": reason,
        "seen_at": seen_at,
        "location": location,
        "decision": decision,
        "ts": ts,
    }


def check(profile: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Alerts raised by this query, already appended to ALERT_LOG.

    A watched plate and a hotlist verdict are two different findings, so a watched stolen vehicle
    raises both: the operator's own reason, and the standing rule that caught it anyway.
    """
    profile = profile or {}
    plate = _key(profile.get("plate_number"))
    if not plate:
        return []

    raised: List[Dict[str, Any]] = []
    entry: Optional[Dict[str, Any]] = catalog.watchlist_get(plate)
    if entry:
        raised.append(_raise(plate, entry.get("reason") or "On watchlist", profile))

    decision = str(profile.get("decision") or "").upper().strip()
    if any(decision.startswith(prefix) for prefix in HOTLIST_PREFIXES):
        raised.append(_raise(plate, f"{HOTLIST_REASON}: {profile.get('decision')}", profile))

    return raised
