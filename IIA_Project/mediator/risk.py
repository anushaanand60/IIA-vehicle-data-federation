"""Explainable risk score (Task 2.3).

The decision engine already says *what* the mediator concluded. This module says *how hard to
look*: one 0–100 number an operator can triage a queue by, plus the list of named factors that
produced it. It is a presentation of the decision, never a second opinion — no model, no
probability, no learned weights (CLAUDE.md §8: no ML, no probabilistic fusion). Every point on
screen is attributable to one line of this file, which is the only reason a number like this is
defensible in a viva.

Mirrors the marker Operation Tutelage sets on a vehicle whose ANPR sightings keep disagreeing with
the MIB policy database (docs/FIELD_RESEARCH.md fact 7), and the IIB flags behind India's
ANPR-based uninsured-vehicle challans (facts 1 and 3): a persistent flag with a stated reason,
not an automatic penalty.

Design notes worth defending:
  * The base is the decision, so risk can never contradict the verdict.
  * Modifiers only ever *raise* the score, and only on evidence a source actually returned. A
    source that was never asked contributes nothing — the same honesty rule as Task 3.1.
  * The factors always sum exactly to the reported value, clamp included, so the gauge can be
    read as an arithmetic explanation rather than an opaque index.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from mediator.decide import CORE_SOURCES, FAILED_STATUSES, REFERENCE_TODAY

# Upper bound of each band; the first band whose bound the value is below wins.
LEVELS: Tuple[Tuple[str, int], ...] = (
    ("LOW", 25),
    ("MEDIUM", 55),
    ("HIGH", 80),
    ("CRITICAL", 101),
)

# Ordered: first substring that matches wins, so "UNREGISTERED / SUSPICIOUS" is scored as
# unregistered rather than as a cloned plate. Substrings, not an enum, because the decision engine
# owns its wording and Task 2.5 will add more verdicts — an unseen string degrades to UNDETERMINED
# rather than raising.
BASE_RULES: Tuple[Tuple[str, int], ...] = (
    ("STOLEN", 90),
    ("SCRAPPED", 90),
    ("SHREDDED", 90),
    ("UNINSURED", 70),
    ("REGISTRATION INVALID", 60),
    ("UNREGISTERED", 50),
    ("SUSPICIOUS", 55),
    ("UNKNOWN VEHICLE", 40),
    ("CLEAR", 5),
)
DEFAULT_BASE = 30  # UNDETERMINED: we could not decide, which is itself worth a look

CONFLICT_POINTS = 5
CONFLICT_CAP = 15
EXPIRING_SOON_DAYS = 30
EXPIRING_SOON_POINTS = 10
STALE_SIGHTING_DAYS = 60
STALE_SIGHTING_POINTS = 5
SOURCE_DOWN_POINTS = 10
LOW_TRUST_THRESHOLD = 0.8
LOW_TRUST_POINTS = 5


@dataclass
class RiskFactor:
    """One named contribution to the score. `points` is what it added; `note` is why."""

    name: str
    points: int
    note: str


@dataclass
class RiskScore:
    value: int
    level: str
    factors: List[RiskFactor] = field(default_factory=list)


def level_for(value: int) -> str:
    for name, upper in LEVELS:
        if value < upper:
            return name
    return LEVELS[-1][0]


def _base(decision: str) -> Tuple[int, str]:
    text = (decision or "").upper()
    for needle, points in BASE_RULES:
        if needle in text:
            return points, text
    return DEFAULT_BASE, text or "UNDETERMINED"


def _as_date(value: Any) -> Optional[date]:
    """ISO date or ISO timestamp → date. Anything else is simply not a date we can reason about."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip().replace("Z", "")
    for candidate in (text, text.replace(" ", "T"), text[:10]):
        try:
            return datetime.fromisoformat(candidate).date()
        except ValueError:
            continue
    return None


def _conflict_factor(profile: Dict[str, Any]) -> Optional[RiskFactor]:
    count = len(profile.get("conflicts") or [])
    if not count:
        return None
    points = min(count * CONFLICT_POINTS, CONFLICT_CAP)
    return RiskFactor(
        "Attribute conflicts",
        points,
        f"{count} attribute(s) disagree across sources (capped at {CONFLICT_CAP} points so a "
        f"noisy camera cannot dominate the verdict).",
    )


def _expiry_factor(profile: Dict[str, Any]) -> Optional[RiskFactor]:
    expiry = _as_date(profile.get("insurance_expiry"))
    if expiry is None:
        return None
    days = (expiry - REFERENCE_TODAY).days
    # Already lapsed is the decision engine's business (UNINSURED); this modifier is only about a
    # policy that is about to lapse, so it never double-counts.
    if not 0 <= days <= EXPIRING_SOON_DAYS:
        return None
    return RiskFactor(
        "Policy expiring soon",
        EXPIRING_SOON_POINTS,
        f"Insurance expires on {expiry.isoformat()}, in {days} day(s).",
    )


def _sighting_factor(profile: Dict[str, Any]) -> Optional[RiskFactor]:
    availability = profile.get("source_availability") or {}
    if "CAM" not in availability:
        return None  # the camera network was never asked: silence is not staleness
    seen = _as_date(profile.get("last_seen_time"))
    if seen is not None and (REFERENCE_TODAY - seen).days <= STALE_SIGHTING_DAYS:
        return None
    if seen is None:
        note = (f"No camera sighting on record, though the camera network answered "
                f"(as of {REFERENCE_TODAY.isoformat()}).")
    else:
        note = (f"Last camera sighting was {(REFERENCE_TODAY - seen).days} days ago "
                f"({seen.isoformat()}), beyond the {STALE_SIGHTING_DAYS}-day window.")
    return RiskFactor("Stale camera evidence", STALE_SIGHTING_POINTS, note)


def _availability_factor(profile: Dict[str, Any]) -> Optional[RiskFactor]:
    availability = profile.get("source_availability") or {}
    failed = [s for s in CORE_SOURCES if availability.get(s) in FAILED_STATUSES]
    if not failed:
        return None
    # One flag, not a tally: the uncertainty is "we are working with an incomplete picture", which
    # is either true or it is not.
    return RiskFactor(
        "Incomplete evidence",
        SOURCE_DOWN_POINTS,
        f"Core source(s) {', '.join(failed)} did not answer, so this score rests on less than "
        f"the full picture.",
    )


def _trust_factor(profile: Dict[str, Any]) -> Optional[RiskFactor]:
    trusts = [
        float(info.get("trust"))
        for info in (profile.get("provenance") or {}).values()
        if isinstance(info, dict) and info.get("trust") is not None
    ]
    if not trusts:
        return None  # nothing was contributed, so there is no average trust to call low
    average = sum(trusts) / len(trusts)
    if average >= LOW_TRUST_THRESHOLD:
        return None
    return RiskFactor(
        "Low source trust",
        LOW_TRUST_POINTS,
        f"Average trust of the contributing sources is {average:.2f}, below "
        f"{LOW_TRUST_THRESHOLD:.2f} — the profile leans on observational evidence.",
    )


def score(profile: Dict[str, Any]) -> RiskScore:
    """A deterministic 0–100 risk score with the factors that produced it."""
    profile = profile or {}
    base_points, decision_text = _base(str(profile.get("decision") or ""))
    factors: List[RiskFactor] = [
        RiskFactor("Decision", base_points, f"Decision is {decision_text}.")
    ]
    for build in (_conflict_factor, _expiry_factor, _sighting_factor,
                  _availability_factor, _trust_factor):
        factor = build(profile)
        if factor is not None:
            factors.append(factor)

    raw = sum(f.points for f in factors)
    value = max(0, min(100, raw))
    if value != raw:
        # Keep the arithmetic honest: the gauge shows the clamp instead of a list that silently
        # fails to add up.
        factors.append(RiskFactor("Clamped to scale", value - raw,
                                  f"Raw total {raw} clamped into the 0–100 scale."))
    return RiskScore(value=value, level=level_for(value), factors=factors)


if __name__ == "__main__":  # pragma: no cover - demo helper
    import json
    import sys
    from dataclasses import asdict

    from mediator.core import run_global_query

    plate = sys.argv[1] if len(sys.argv) > 1 else "HR26EF4455"
    print(json.dumps(asdict(score(run_global_query(plate)["profile"])), indent=2))
