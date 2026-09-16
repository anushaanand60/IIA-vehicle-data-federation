"""Ground truth for every generated vehicle (design PDF section 4).

Reads the generated source CSVs directly and applies the same ordered decision rules as
mediator/decide.py, without the planner, wrappers, mappings or integrator, so the federated
pipeline can be graded against it (scripts/evaluate_ground_truth.py). The only knowledge repeated
here is what a person reading the raw data would need: plates written with spaces or hyphens,
camera OCR swapping O/0 and I/1, dates stored as DD/MM/YYYY text or epoch seconds, and misspelt
makes. The decision strings themselves are not repeated as literals except where mediator/decide.py
does not export them as constants -- see the module docstring note below.
"""
from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from itertools import zip_longest
from pathlib import Path
from typing import Callable, Iterable

from mediator.decide import (
    REFERENCE_TODAY,
    LAPSE_ADVISORY_DAYS,
    LAPSE_WARNING_DAYS,
    UNINSURED_ADVISORY,
    UNINSURED_WARNING,
    UNINSURED_REPORT,
)

TODAY = REFERENCE_TODAY  # the reference day of the generators and the decision engine

# mediator/decide.py does not export most of these as module-level constants (only
# UNKNOWN_VEHICLE and the lapsed-policy ladder -- UNINSURED_ADVISORY/WARNING/REPORT -- are
# exported), so the remaining exact strings are copied here from mediator/decide.py's return
# statements. Whoever owns decide.py is expected to keep these in sync; a decision-string change
# without a matching change here will show up immediately as ground-truth mismatches.
CLEAR = "CLEAR"
STOLEN = "STOLEN — ALERT POLICE"
SCRAPPED_SEEN = "SCRAPPED — ALERT POLICE"          # shredded, then sighted again by a camera
SCRAPPED_UNSEEN = "SCRAPPED — REGISTRATION VOID"    # shredded, never sighted since
UNREGISTERED = "UNREGISTERED / SUSPICIOUS"
CLONED = "SUSPICIOUS — POSSIBLE CLONED PLATE"
UNINSURED = UNINSURED_REPORT
REGISTRATION_INVALID = "REGISTRATION INVALID — REPORT"

FIELDS = ["plate_number", "expected_decision", "expected_confidence", "basis"]
MAKE_SPELLINGS = {"hyundia": "hyundai", "maruti": "maruti suzuki"}


def plate_key(raw) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(raw or "")).upper()


def ocr_key(raw) -> str:
    """Camera plates are compared with O read as 0 and I read as 1 (mediator/decomposer.py does the same fold)."""
    return plate_key(raw).replace("O", "0").replace("I", "1")


def _text(value) -> str:
    return str(value or "").strip().lower()


def _make(value) -> str:
    return MAKE_SPELLINGS.get(_text(value), _text(value))


def _reported(incident: dict) -> int:
    try:
        return int(incident["reported_date"])
    except (TypeError, ValueError):
        return 0


def _captured(capture: dict) -> datetime:
    try:
        return datetime.fromisoformat(str(capture["captured_at"]).strip())
    except ValueError:
        return datetime.min


def _policy_end(policy: dict) -> date:
    try:
        return datetime.strptime(str(policy["policy_until"]).strip(), "%d/%m/%Y").date()
    except ValueError:
        return date.min


def expected_decision(reg: dict | None, policies: list[dict], incidents: list[dict], captures: list[dict],
                      today: date = TODAY) -> tuple[str, str, str]:
    """(decision, confidence, basis) for one vehicle from its raw rows in each source; the first rule that matches wins.

    Mirrors mediator/decide.py's ordered rules, skipping the two rules that need a live query
    (source-down => UNDETERMINED, and the "no source has ever heard of this plate" => UNKNOWN_VEHICLE
    rule, which cannot fire here because a plate only reaches this function by already appearing in
    at least one source's CSV or in a camera capture).
    """
    if incidents:
        latest = max(incidents, key=_reported)
        if _text(latest["stolen_flag"]) == "y" and _text(latest["recovered_flag"]) != "y" and _text(latest["case_status"]) == "open":
            reported = datetime.fromtimestamp(_reported(latest), timezone.utc).date().isoformat()
            return STOLEN, "HIGH", f"open theft case reported {reported}"
        if _text(latest.get("incident_type")) == "shredding":
            reported = datetime.fromtimestamp(_reported(latest), timezone.utc).date().isoformat()
            if captures:
                return SCRAPPED_SEEN, "HIGH", f"scrapped {reported} but sighted again by a camera since"
            return SCRAPPED_UNSEEN, "HIGH", f"scrapped {reported}, never sighted since"

    capture = max(captures, key=_captured) if captures else None
    if capture and reg is None:
        return UNREGISTERED, "MEDIUM", f"seen by camera {capture['captured_at']} but never registered"

    if capture:
        compared = [("make", _make, "make", "observed_make"), ("model", _text, "model", "observed_model"),
                    ("colour", _text, "colour", "observed_colour")]
        differs = [name for name, clean, official, seen in compared
                   if clean(reg.get(official)) and clean(capture.get(seen)) and clean(reg.get(official)) != clean(capture.get(seen))]
        if len(differs) >= 2:
            return CLONED, "MEDIUM", f"latest camera sighting disagrees with the registration on {', '.join(differs)}"

    if not policies:
        return UNINSURED, "HIGH", "no insurance policy on record"
    policy_end = max(_policy_end(p) for p in policies)
    if policy_end < today:
        days_lapsed = (today - policy_end).days
        if 1 <= days_lapsed <= LAPSE_ADVISORY_DAYS:
            return UNINSURED_ADVISORY, "MEDIUM", f"latest policy ended {policy_end.isoformat()}, lapsed {days_lapsed} days (advisory window)"
        if LAPSE_ADVISORY_DAYS < days_lapsed <= LAPSE_WARNING_DAYS:
            return UNINSURED_WARNING, "HIGH", f"latest policy ended {policy_end.isoformat()}, lapsed {days_lapsed} days (warning window)"
        return UNINSURED, "HIGH", f"latest policy ended {policy_end.isoformat()}, lapsed {days_lapsed} days"

    status = str((reg or {}).get("reg_status") or "").strip().upper()
    if status and status != "ACTIVE":
        return REGISTRATION_INVALID, "HIGH", f"registration {status}"

    seen = f"seen by camera {capture['captured_at']}" if capture else "never seen by a camera"
    registration = f"registration {status}" if status else "no registration record"
    return CLEAR, "HIGH" if capture else "MEDIUM", f"{registration}, policy valid until {policy_end.isoformat()}, {seen}"


def _read(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def build_ground_truth(root: Path | str, today: date = TODAY) -> list[dict]:
    """One row per plate known to an official source, then one per plate only a camera has seen.

    A capture whose plate differs from a known plate only by OCR swaps belongs to that plate, not to a new vehicle.
    """
    root = Path(root)
    registrations = {plate_key(r["registration_no"]): r for r in _read(root / "reg_vehicle_registration.csv")}
    policies: dict[str, list[dict]] = defaultdict(list)
    incidents: dict[str, list[dict]] = defaultdict(list)
    captures: dict[str, list[dict]] = defaultdict(list)
    for policy in _read(root / "ins_policy_records.csv"):
        policies[plate_key(policy["vehicle_reg"])].append(policy)
    for incident in _read(root / "theft_crime_records.csv"):
        incidents[plate_key(incident["vehicle_number"])].append(incident)
    for capture in _read(root / "cam_plate_captures.csv"):
        captures[ocr_key(capture["plate_id"])].append(capture)

    known = list(dict.fromkeys([*registrations, *policies, *incidents]))
    known_ocr = {ocr_key(plate) for plate in known}
    camera_only = [Counter(plate_key(c["plate_id"]) for c in rows).most_common(1)[0][0]  # the most common spelling
                   for key, rows in captures.items() if key not in known_ocr]

    truth = []
    for plate in [*known, *camera_only]:
        decision, confidence, basis = expected_decision(registrations.get(plate), policies.get(plate, []),
                                                        incidents.get(plate, []), captures.get(ocr_key(plate), []), today)
        truth.append({"plate_number": plate, "expected_decision": decision, "expected_confidence": confidence, "basis": basis})
    return truth


def write_ground_truth(root: Path | str, path: Path | str | None = None, today: date = TODAY) -> list[dict]:
    root = Path(root)
    truth = build_ground_truth(root, today)
    with open(Path(path) if path else root / "data" / "ground_truth.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(truth)
    return truth


def read_ground_truth(path: Path | str) -> list[dict]:
    return _read(Path(path))


def stratified_sample(rows: list[dict], limit: int | None) -> list[dict]:
    """At most `limit` rows, taken in turn from each (decision, confidence) class so a short run still covers them all."""
    if limit is None or limit >= len(rows):
        return list(rows)
    by_class: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        by_class[(row["expected_decision"], row["expected_confidence"])].append(row)
    in_turn = [row for batch in zip_longest(*by_class.values()) for row in batch if row is not None]
    return in_turn[:limit]


def _label(decision: str, confidence: str) -> str:
    return f"{decision} ({confidence})"


def grade(rows: Iterable[dict], query: Callable[[str], dict]) -> dict:
    """Ask `query` (mediator.core.run_global_query) about every plate and compare its decision with the expected one."""
    per_class: dict[str, dict[str, int]] = {}
    wrong = []
    for row in rows:
        profile = query(row["plate_number"])["profile"]
        expected = _label(row["expected_decision"], row["expected_confidence"])
        got = _label(profile["decision"], profile["confidence"])
        tally = per_class.setdefault(expected, {"total": 0, "correct": 0})
        tally["total"] += 1
        if got == expected:
            tally["correct"] += 1
        else:
            wrong.append({"plate_number": row["plate_number"], "expected": expected, "got": got, "basis": row["basis"]})
    total = sum(t["total"] for t in per_class.values())
    return {"total": total, "correct": total - len(wrong), "accuracy": (total - len(wrong)) / total if total else 0.0,
            "per_class": per_class, "wrong": wrong}
