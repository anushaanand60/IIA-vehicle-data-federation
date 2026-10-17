"""Approximate plate matching from the command line -- the demo's fallback when the GUI is not up.

    python scripts/match_plate.py --image data/plate_images/DL05CD9876__blur_s2.png
    python scripts/match_plate.py --read DL05CD9B76 [--make "Maruti Suzuki" --colour Silver]
    python scripts/match_plate.py --reverse DL05CD9876
    python scripts/match_plate.py --read DL05CD9B76 --local-db      # offline: reads reg.db / cam.db

Prints the OCR read, the blocking funnel (registry size -> candidates after each filter) and the
ranked candidates with every measure's score, the calibrated probability and the decision.

The registry and the camera captures come LIVE from the REG and CAM wrappers through the
federation (the same path cleansing uses), so a record the examiner inserts or deletes changes the
answer on the next run. `--local-db` is the escape hatch for a laptop with no wrappers running.
The Plate Match page imports `live_registry`, `live_captures`, `funnel` and `reverse_rows` from
here, so the GUI and this CLI cannot disagree.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from mediator import catalog, lexicons, plate_match  # noqa: E402
from mediator.blocking import candidate_plates  # noqa: E402
from mediator.cleansing import _global  # noqa: E402
from mediator.registry_loader import load_registry  # noqa: E402
from mediator.transforms import norm_plate  # noqa: E402

FUNNEL = ("none", "index", "size", "prefix", "position")
CAM_DB = ROOT / "sources" / "cam" / "cam.db"


def _specs() -> Tuple[Any, Any]:
    registry = load_registry(catalog.META_DB_PATH)
    reg = next((s for s in registry.sources if s.identity_authority), None)
    cam = next((s for s in registry.sources
                if s.authority == "observational" and "observed_colour" in s.covers), None)
    return reg, cam


def live_registry() -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """plate -> {make, model, colour}, read now from the identity authority's wrapper."""
    reg, _ = _specs()
    t0 = time.perf_counter()
    with httpx.Client(trust_env=False) as client:
        rows = lexicons.fetch_all(reg, lexicons.primary_key(reg, client), client)
    out = {}
    for row in rows:
        g = _global(row, reg, True)
        plate = norm_plate(g.get("plate_number"))
        if plate:
            out[plate] = {"make": g.get("vehicle_make"), "model": g.get("vehicle_model"),
                          "colour": g.get("vehicle_colour")}
    return out, {"source_id": reg.source_id, "rows": len(rows),
                 "elapsed_ms": round((time.perf_counter() - t0) * 1000)}


def live_captures() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Every camera capture, read now from the observational source's wrapper."""
    _, cam = _specs()
    t0 = time.perf_counter()
    with httpx.Client(trust_env=False) as client:
        pk = lexicons.primary_key(cam, client)
        rows = lexicons.fetch_all(cam, pk, client)
    caps = [_capture(row, cam, pk) for row in rows]
    return caps, {"source_id": cam.source_id, "rows": len(rows),
                  "elapsed_ms": round((time.perf_counter() - t0) * 1000)}


def _capture(row: Dict[str, Any], cam: Any, pk: str) -> Dict[str, Any]:
    g = _global(row, cam, False)
    return {"capture_id": row.get(pk), "read": g.get("plate_number"), "camera": g.get("last_seen_location"),
            "time": g.get("last_seen_time"), "observed_make": g.get("observed_make"),
            "observed_model": g.get("observed_model"), "observed_colour": g.get("observed_colour"),
            "ocr_confidence": g.get("ocr_confidence")}


def local_captures(path: Path = CAM_DB) -> List[Dict[str, Any]]:
    """Offline fallback: the CAM file on this laptop, queried with the registry's own SQL template."""
    _, cam = _specs()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        pk = next(r["name"] for r in con.execute(f"PRAGMA table_info({cam.table})") if r["pk"])
        rows = [dict(r) for r in con.execute(lexicons._page_sql(cam, pk, None).replace(
            f" LIMIT {lexicons.PAGE_SIZE}", ""))]
    finally:
        con.close()
    return [_capture(r, cam, pk) for r in rows]


def funnel(read: str, plates: List[str], max_edits: int) -> List[Dict[str, Any]]:
    """Candidates left after each cumulative blocking filter (deck p. 40-65), one row per stage."""
    rows = []
    for strategy in FUNNEL:
        cands, stats = candidate_plates(read, plates, strategy=strategy, max_edits=max_edits)
        rows.append({"stage": strategy, "filters": " + ".join(stats.filters_applied),
                     "candidates": stats.candidates_after, "elapsed_ms": round(stats.elapsed_ms, 2)})
    return rows


def max_edits() -> int:
    bundle, _ = plate_match.load_bundle()
    return int((bundle or {}).get("max_edits", plate_match.DEFAULT_MAX_EDITS))


def reverse_rows(plate: str, captures: List[Dict[str, Any]],
                 registry: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every capture whose read could be `plate`, ranked, one row per capture."""
    hits = plate_match.reverse_match(plate, captures, registry=registry)
    by_read = {c.plate: c for c in hits}
    out = []
    for cap in captures:
        hit = by_read.get(norm_plate(cap.get("read")) or "")
        if hit is not None:
            out.append({**cap, "probability": hit.probability, "decision": hit.decision})
    out.sort(key=lambda r: -r["probability"])
    return out


def _print_forward(read: str, conf: Optional[float], make: Optional[str], colour: Optional[str],
                   registry: Dict[str, Dict[str, Any]]) -> None:
    me = max_edits()
    print(f"read {read!r} confidence {conf}  make {make!r} colour {colour!r}")
    print(f"blocking funnel (max_edits={me}):")
    for row in funnel(read, sorted(registry), me):
        print(f"  {row['stage']:9} {row['candidates']:5}  {row['elapsed_ms']:7.2f} ms  {row['filters']}")
    cands, _ = plate_match.match(read, confidence=conf, observed_make=make, observed_colour=colour,
                                 registry=registry)
    names = plate_match.SCORE_NAMES
    print("rank plate        p       decision " + " ".join(n[:8] for n in names))
    for i, c in enumerate(cands, 1):
        print(f"{i:4} {c.plate:12} {c.probability:.4f}  {c.decision:8} "
              + " ".join(f"{c.scores[n]:.3f}".rjust(8) for n in names))
    if cands:
        print("why: " + "; ".join(cands[0].reasons))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    act = ap.add_mutually_exclusive_group(required=True)
    act.add_argument("--image")
    act.add_argument("--read")
    act.add_argument("--reverse")
    ap.add_argument("--make")
    ap.add_argument("--colour")
    ap.add_argument("--confidence", type=float)
    ap.add_argument("--local-db", action="store_true")
    args = ap.parse_args(argv)

    registry = plate_match.load_registry() if args.local_db else live_registry()[0]
    if args.reverse:
        captures = local_captures() if args.local_db else live_captures()[0]
        for r in reverse_rows(args.reverse, captures, registry):
            print(f"capture {r['capture_id']:>5} {r['read']:12} {r['probability']:.4f} {r['decision']:8} "
                  f"{r['camera']} {r['time']} {r['observed_make']} {r['observed_colour']}")
        return 0
    read, conf = args.read, args.confidence
    if args.image:
        from mediator.plate_ocr import read_plate
        guesses = read_plate(Path(args.image).read_bytes())
        if not guesses:
            print("OCR found no text")
            return 1
        read, conf = guesses[0].text, guesses[0].confidence
        print(f"OCR: {[(g.text, round(g.confidence, 3)) for g in guesses]}")
    _print_forward(norm_plate(read) or "", conf, args.make, args.colour, registry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
