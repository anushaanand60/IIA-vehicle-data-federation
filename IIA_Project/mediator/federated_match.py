"""Match a plate read against every agency, not only the registration authority.

PHASE3-ADDED (C1: match the entity with every database) -- the whole module is new in phase 3.

Phase 2 matched a read against REG alone, so a car that REG does not hold could never be the answer:
its read was "corrected" into the nearest registered plate, and a low REG score was the end of the
story. Here the candidate set is every plate any catalogued source holds, read live through each
wrapper (no copy is kept), and each plate carries what the sources say about it:

    sources       which agencies hold the plate (REG, INS, THEFT, PUC, CAM ...)
    prior         how strongly that says the plate is real, for open_world.posterior:
                  1.0 when an authoritative source (a register, a policy, an FIR, a certificate) holds it;
                  CAM_ONLY_PRIOR when only cameras have seen it, because a camera read may itself be a misread
    make/model/   REG's registration; where REG has none (the plate is not registered, or the field is
    colour        blank), the majority of CAM's confident sightings of that exact plate ("fingerprint")
    last_seen     CAM's latest sighting, for the evidence table

So a car that is not in REG but is in INS or PUC is still found, and is labelled "not registered,
known to INS, PUC"; a read that exactly matches such a plate is no longer pulled to a registered
neighbour; and REG's missing make or colour is filled from what cameras saw.

Refusal rule (phase 1): if the identity authority (REG) cannot be read, the match is refused. Any other
source that is down is named in the result and simply contributes nothing.
"""
from __future__ import annotations

import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

import httpx

from mediator import catalog, lexicons, open_world
from mediator.cleansing import _global
from mediator.registry_loader import load_registry
from mediator.transforms import norm_plate

CAM_ONLY_PRIOR = 0.3       # a plate only cameras have seen: real more often than not, but it may be a misread
FINGERPRINT_MIN_CONF = 0.6  # sightings below this OCR confidence do not vote on make / model / colour
CONTEXT = ("make", "model", "colour")
OBSERVED = {"make": "observed_make", "model": "observed_model", "colour": "observed_colour"}

Universe = Dict[str, Dict[str, Any]]


def _rows(spec: Any) -> List[Dict[str, Any]]:
    with httpx.Client(trust_env=False) as client:
        return lexicons.fetch_all(spec, lexicons.primary_key(spec, client), client)


def _majority(values: List[Any]) -> Optional[str]:
    vals = [str(v).strip() for v in values if v not in (None, "")]
    if not vals:
        return None
    (best, n), = Counter(v.title() for v in vals).most_common(1)
    return best if n * 2 > len(vals) else None  # a split vote is no fingerprint


def live_universe(registry: Any = None) -> Tuple[Universe, Dict[str, Any]]:
    """plate -> what every live source says about it, plus per-source row counts and failures."""
    specs = list((registry or load_registry(catalog.META_DB_PATH)).sources)
    identity = next((s for s in specs if s.identity_authority), None)
    if identity is None:
        raise lexicons.SourceUnavailable("the registry names no identity authority")
    with_plate = [s for s in specs if any(m.global_attr == "plate_number" for m in s.attribute_map)]
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=max(1, len(with_plate))) as pool:
        futures = {s.source_id: pool.submit(_rows, s) for s in with_plate}
    meta: Dict[str, Any] = {"rows": {}, "unavailable": {}}
    fetched: Dict[str, List[Dict[str, Any]]] = {}
    for spec in with_plate:
        try:
            fetched[spec.source_id] = futures[spec.source_id].result()
            meta["rows"][spec.source_id] = len(fetched[spec.source_id])
        except Exception as exc:
            if spec is identity:
                raise lexicons.SourceUnavailable(f"{spec.source_id} (identity authority): {exc}") from None
            meta["unavailable"][spec.source_id] = str(exc)
    meta["elapsed_ms"] = round((time.perf_counter() - t0) * 1000)

    universe: Universe = {}
    sightings: Dict[str, List[Dict[str, Any]]] = {}
    for spec in with_plate:
        for row in fetched.get(spec.source_id, []):
            g = _global(row, spec, spec.authority != "observational")
            plate = norm_plate(g.get("plate_number"))
            if not plate:
                continue
            if spec.authority == "observational":
                sightings.setdefault(plate, []).append(g)
                continue
            entry = universe.setdefault(plate, {"sources": [], "prior": 1.0})
            if spec.source_id not in entry["sources"]:
                entry["sources"].append(spec.source_id)
            if spec is identity:
                entry.update(make=g.get("vehicle_make"), model=g.get("vehicle_model"),
                             colour=g.get("vehicle_colour"), registered=True)
    cam_ids = sorted({s.source_id for s in with_plate if s.authority == "observational"})
    for plate, seen in sightings.items():
        # a camera-only plate joins the candidates only if it is a real plate shape: a camera's misread
        # (DLO1AB1Z34) is not evidence that such a car exists
        if plate not in universe and not open_world.is_valid(plate):
            continue
        entry = universe.setdefault(plate, {"sources": [], "prior": CAM_ONLY_PRIOR})
        entry["sources"] += [c for c in cam_ids if c not in entry["sources"]]
        confident = [s for s in seen if _conf(s.get("ocr_confidence")) >= FINGERPRINT_MIN_CONF]
        filled = []
        for key in CONTEXT:
            if not entry.get(key):
                value = _majority([s.get(OBSERVED[key]) for s in confident])
                if value:
                    entry[key] = value
                    filled.append(key)
        if filled:
            entry["fingerprint"] = f"{', '.join(filled)} from {len(confident)} camera sighting(s)"
        latest = max(seen, key=lambda s: str(s.get("last_seen_time") or ""))
        entry["last_seen"] = {"at": latest.get("last_seen_time"), "where": latest.get("last_seen_location")}
        entry["sightings"] = len(seen)
    for entry in universe.values():
        entry.setdefault("registered", False)
        entry["sources"] = sorted(entry["sources"], key=lambda s: (s != identity.source_id, s))
        entry["held_by"] = held_by(entry)
    meta["identity"] = identity.source_id
    return universe, meta


def _conf(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def held_by(entry: Dict[str, Any]) -> str:
    """'REG, INS, CAM' or 'not registered; INS, CAM' -- the one-line provenance of a candidate."""
    sources = ", ".join(entry.get("sources") or []) or "no source"
    return sources if entry.get("registered") else f"not registered; known to {sources}"
