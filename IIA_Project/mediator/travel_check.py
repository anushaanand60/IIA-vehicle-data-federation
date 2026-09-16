"""Impossible-travel (cloned-plate) detection for Challan Guard.

If the same plate is photographed in two places and the implied speed between them is beyond any
vehicle, the plate exists twice: one of the two is a clone. This is the check behind UK patent
GB2448780A and behind Met Police ANPR clone flagging, and it is the reason a Hyderabad e-challan
racket ("one number, two scooters") was caught in 2025 (docs/FIELD_RESEARCH.md).

Trade-offs worth defending:

  * **Great-circle distance, not road distance.** Haversine understates real driving distance, so
    the implied speed is a *lower bound*. Every leg this module calls impossible really is
    impossible; it will simply miss some borderline clones. Erring towards silence is the right
    bias when the output can cost a citizen a fine.
  * **One global speed threshold.** 160 km/h is above every legal Indian limit with margin, is one
    number to justify on stage, and is a parameter rather than a constant in the call sites.
  * **A sighting we cannot place or time is dropped, never guessed.** A camera without
    coordinates, or a timestamp we cannot parse, produces no leg at all.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

PLAUSIBLE_KMH = 160.0
EARTH_RADIUS_KM = 6371.0088


@dataclass
class Leg:
    from_loc: str
    to_loc: str
    from_at: str
    to_at: str
    km: float
    minutes: float
    kmh: float
    impossible: bool


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
    d_phi = phi2 - phi1
    d_lambda = math.radians(float(lon2) - float(lon1))
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _moment(value: Any) -> Optional[datetime]:
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)  # every source timestamp here is local wall clock


def _placed(sighting: Dict[str, Any]) -> bool:
    return sighting.get("lat") is not None and sighting.get("lon") is not None


def legs(sightings: List[Dict[str, Any]], plausible_kmh: float = PLAUSIBLE_KMH) -> List[Leg]:
    """Consecutive pairs of locatable, timestamped sightings, oldest first."""
    usable = [(m, s) for s in (sightings or [])
              if _placed(s) and (m := _moment(s.get("at"))) is not None]
    usable.sort(key=lambda pair: pair[0])
    out: List[Leg] = []
    for (t0, a), (t1, b) in zip(usable, usable[1:]):
        minutes = (t1 - t0).total_seconds() / 60.0
        if minutes <= 0:  # same instant: no elapsed time, so no speed can be implied
            continue
        km = haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
        kmh = km / (minutes / 60.0)
        out.append(Leg(from_loc=str(a.get("location") or "unknown"),
                       to_loc=str(b.get("location") or "unknown"),
                       from_at=str(a.get("at")), to_at=str(b.get("at")),
                       km=round(km, 2), minutes=round(minutes, 1), kmh=round(kmh, 1),
                       impossible=kmh > plausible_kmh))
    return out


def impossible_legs(sightings: List[Dict[str, Any]],
                    plausible_kmh: float = PLAUSIBLE_KMH) -> List[Leg]:
    return [leg for leg in legs(sightings, plausible_kmh) if leg.impossible]
