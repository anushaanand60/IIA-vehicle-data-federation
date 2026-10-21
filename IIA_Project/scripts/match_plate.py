"""Approximate plate matching from the command line -- the demo's fallback when the GUI is not up.

    python scripts/match_plate.py --image data/plate_images/DL05CD9876__blur_s2.png [--repaired]
    python scripts/match_plate.py --read DL05CD9B76 [--make "Maruti Suzuki" --colour Silver]
    python scripts/match_plate.py --reverse DL05CD9876
    python scripts/match_plate.py --read DL05CD9B76 --local-db      # offline: reads reg.db / cam.db

Prints the OCR read (raw and repaired), the blocking funnel (registry size -> candidates after each
filter) and the ranked candidates with every measure's score, the calibrated probability and the
decision; `--reverse` prints every camera capture whose read could be that registration.

The registry and the camera captures come LIVE from the REG and CAM wrappers through the
federation (the same path cleansing uses), so a record the examiner inserts or deletes changes the
answer on the next run. `--local-db` is the escape hatch for a laptop with no wrappers running.
The Plate Match page imports `live_registry`, `live_captures`, `run_ocr`, `funnel` and
`reverse_rows` from here, so the GUI and this CLI cannot disagree.
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

from mediator import catalog, lexicons, plate_match, plate_ocr  # noqa: E402
from mediator import open_world  # noqa: E402  (PHASE3-ADDED: C2 picks the plate line by its state code)
from mediator.blocking import candidate_plates  # noqa: E402
from mediator.cleansing import _global  # noqa: E402  (the registry-driven column -> global attr map)
from mediator.registry_loader import load_registry  # noqa: E402
from mediator.transforms import norm_plate  # noqa: E402

FUNNEL = ("none", "index", "size", "prefix", "position")
CAM_DB = ROOT / "sources" / "cam" / "cam.db"
Registry = Dict[str, Dict[str, Any]]


def _specs() -> Tuple[Any, Any]:
    """REG and CAM as the registry names them: by role, never by id."""
    registry = load_registry(catalog.META_DB_PATH)
    reg = next((s for s in registry.sources if s.identity_authority), None)
    cam = next((s for s in registry.sources
                if s.authority == "observational" and "observed_colour" in s.covers), None)
    if reg is None or cam is None:
        raise lexicons.SourceUnavailable("the registry names no identity authority or no camera source")
    return reg, cam


def live_registry() -> Tuple[Registry, Dict[str, Any]]:
    """plate -> {make, model, colour}, read now from the identity authority's wrapper (no cache)."""
    reg, _ = _specs()
    t0 = time.perf_counter()
    with httpx.Client(trust_env=False) as client:
        rows = lexicons.fetch_all(reg, lexicons.primary_key(reg, client), client)
    out: Registry = {}
    for row in rows:
        g = _global(row, reg, True)
        plate = norm_plate(g.get("plate_number"))
        if plate:
            out[plate] = {"make": g.get("vehicle_make"), "model": g.get("vehicle_model"),
                          "colour": g.get("vehicle_colour")}
    return out, {"source_id": reg.source_id, "rows": len(rows),
                 "elapsed_ms": round((time.perf_counter() - t0) * 1000)}


def live_captures() -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Every camera capture, read now from the observational source's wrapper (no cache)."""
    _, cam = _specs()
    t0 = time.perf_counter()
    with httpx.Client(trust_env=False) as client:
        pk = lexicons.primary_key(cam, client)
        rows = lexicons.fetch_all(cam, pk, client)
    return [_capture(row, cam, pk) for row in rows], {
        "source_id": cam.source_id, "rows": len(rows), "elapsed_ms": round((time.perf_counter() - t0) * 1000)}


def _capture(row: Dict[str, Any], cam: Any, pk: str) -> Dict[str, Any]:
    g = _global(row, cam, False)  # raw values: the matcher sees what the camera recorded
    return {"capture_id": row.get(pk), "read": g.get("plate_number"), "camera": g.get("last_seen_location"),
            "time": g.get("last_seen_time"), "observed_make": g.get("observed_make"),
            "observed_model": g.get("observed_model"), "observed_colour": g.get("observed_colour"),
            "ocr_confidence": g.get("ocr_confidence")}


def local_registry() -> Registry:
    return plate_match.load_registry()


def local_captures(path: Path = CAM_DB) -> List[Dict[str, Any]]:
    """Offline fallback: the CAM file on this laptop, queried with the registry's own SQL template."""
    _, cam = _specs()
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        pk = next(r["name"] for r in con.execute(f"PRAGMA table_info({cam.table})") if r["pk"])
        sql = lexicons._page_sql(cam, pk, None).replace(f" LIMIT {lexicons.PAGE_SIZE}", "")
        rows = [dict(r) for r in con.execute(sql)]
    finally:
        con.close()
    return [_capture(r, cam, pk) for r in rows]


class _Recorder:
    """Wraps the EasyOCR reader so the raw text `read_plate` repairs is kept: one OCR pass, both views."""

    def __init__(self, inner: Any) -> None:
        self.inner, self.raw = inner, []

    def readtext(self, *args: Any, **kwargs: Any) -> Any:
        self.raw = self.inner.readtext(*args, **kwargs)
        return self.raw


def run_ocr(image: bytes, reader: Any = None) -> Dict[str, Any]:
    """`plate_ocr.read_plate` plus the raw read the benchmark (and so the matcher) was evaluated on.

    Raw = the boxes joined left to right, confidence weighted by box length -- the same rule as
    scripts/run_ocr_benchmark.read_image, so a benchmark image reproduces its CSV row.
    Raises plate_ocr.OCRUnavailable when EasyOCR is missing; the caller says so.
    """
    rec = _Recorder(reader if reader is not None else plate_ocr._get_reader())
    guesses = plate_ocr.read_plate(image, reader=rec)
    # PHASE3-REPLACED (C2: a photo of a whole car also has badge, sticker or shop-sign text; joining every box
    # would put it into the read, so only the boxes on the plate's own line(s) are joined now. A plate-only
    # photo keeps every box, so the benchmark rows reproduce as before):
    # parts = sorted((min(p[0] for p in e[0]) if e[0] else 0, plate_ocr.canonical(e[1]), float(e[2]))
    #                for e in rec.raw)
    plate_boxes = plate_line(rec.raw)
    parts = sorted((min(p[0] for p in e[0]) if e[0] else 0, plate_ocr.canonical(e[1]), float(e[2]))
                   for e in plate_boxes)
    parts = [p for p in parts if p[1]]
    raw = "".join(p[1] for p in parts)
    weight = sum(len(p[1]) for p in parts)
    conf = sum(len(p[1]) * p[2] for p in parts) / weight if weight else 0.0
    # PHASE3-REPLACED (C2: the plate's box is returned too, for the close-up gate in mediator/vehicle_vision):
    # return {"raw": raw, "confidence": round(conf, 3), "repaired": plate_ocr.normalise_candidates(raw),
    #         "guesses": [(g.text, round(g.confidence, 3)) for g in guesses]}
    return {"raw": raw, "confidence": round(conf, 3), "repaired": plate_ocr.normalise_candidates(raw),
            "guesses": [(g.text, round(g.confidence, 3)) for g in guesses], "box": union_box(plate_boxes)}


# PHASE3-ADDED (C2: the plate's own text line in a photo that shows the whole car)
def _bbox(entry: Any) -> Tuple[float, float, float, float]:
    xs, ys = [float(p[0]) for p in entry[0]], [float(p[1]) for p in entry[0]]
    return min(xs), min(ys), max(xs), max(ys)


def plate_line(entries: List[Any]) -> List[Any]:
    """EasyOCR boxes that belong to the plate: the anchor box (a plate-shaped read, else the longest text) and
    the boxes beside it on its line or directly above / below it (two-line plates)."""
    # "IND" is the emblem printed beside the number on every high-security plate, never part of the number
    boxed = [e for e in entries if e[0] and plate_ocr.canonical(e[1]) not in ("", "IND")]
    if len(boxed) <= 1:
        return list(entries)

    def rank(e: Any) -> Tuple[int, int, int, float]:
        # a real plate (official state code) first, then plate-shaped text, then the longest, most confident
        text = plate_ocr.canonical(e[1])
        fixed = (plate_ocr.normalise_candidates(text) or [text])[0]
        return int(open_world.is_valid(fixed)), int(bool(plate_ocr.PLATE_RE.match(fixed))), len(text), float(e[2])
    anchor = max(boxed, key=rank)
    ax0, ay0, ax1, ay1 = _bbox(anchor)
    h, w = max(1.0, ay1 - ay0), max(1.0, ax1 - ax0)
    keep = []
    for e in boxed:
        x0, y0, x1, y1 = _bbox(e)
        same_line = y1 >= ay0 - 0.5 * h and y0 <= ay1 + 0.5 * h and x1 >= ax0 - 2 * w and x0 <= ax1 + 2 * w
        stacked = y1 >= ay0 - 1.5 * h and y0 <= ay1 + 1.5 * h and x1 >= ax0 and x0 <= ax1
        if same_line or stacked:
            keep.append(e)
    return keep


def union_box(entries: List[Any]) -> List[List[float]]:
    boxes = [_bbox(e) for e in entries if e[0]]
    if not boxes:
        return []
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
# PHASE3-END


def max_edits() -> int:
    bundle, _ = plate_match.load_bundle()
    return int((bundle or {}).get("max_edits", plate_match.DEFAULT_MAX_EDITS))


def funnel(read: str, plates: List[str], edits: int) -> List[Dict[str, Any]]:
    """Candidates left after each cumulative blocking filter (deck p. 40-65), one row per stage.

    One `candidate_plates` call per strategy with a shared index, so each row is the real set that
    strategy returns, not an estimate."""
    _, index, _ = plate_match.registry_artefacts(plates)
    rows = []
    for strategy in FUNNEL:
        kw = {"index": index} if strategy != "none" else {}
        _, stats = candidate_plates(read, plates, strategy=strategy, max_edits=edits, **kw)
        rows.append({"stage": strategy, "filters": " + ".join(stats.filters_applied),
                     "candidates": stats.candidates_after, "elapsed_ms": round(stats.elapsed_ms, 2)})
    return rows


def _context(cap: Dict[str, Any]) -> Tuple[Any, ...]:
    return tuple(cap.get(k) for k in ("ocr_confidence", "observed_make", "observed_colour", "observed_model"))


def reverse_rows(plate: str, captures: List[Dict[str, Any]], registry: Registry) -> List[Dict[str, Any]]:
    """Every capture whose read could be `plate`, ranked, one row per capture.

    `reverse_match` scores each distinct read once, with the first capture's camera context; captures
    sharing a read but seen with a different make / colour / confidence are re-scored through
    `reverse_match` with that capture alone, so every row's probability is its own."""
    hits = {c.plate: c for c in plate_match.reverse_match(plate, captures, registry=registry)}
    first: Dict[str, Tuple[Any, ...]] = {}
    out = []
    for cap in captures:
        read = norm_plate(cap.get("read")) or ""
        if read not in hits:
            continue
        hit = hits[read]
        if first.setdefault(read, _context(cap)) != _context(cap):
            again = plate_match.reverse_match(plate, [cap], registry=registry)
            hit = again[0] if again else None
        if hit is not None:
            out.append({**cap, "probability": hit.probability, "decision": hit.decision})
    out.sort(key=lambda r: (-r["probability"], str(r["capture_id"])))
    return out


def _print_forward(read: str, conf: Optional[float], make: Optional[str], colour: Optional[str],
                   registry: Registry) -> None:
    me = max_edits()
    print(f"matching {read!r}  confidence {conf}  make {make!r}  colour {colour!r}  ({len(registry)} registered)")
    print(f"blocking funnel (max_edits={me}):")
    for row in funnel(read, sorted(registry), me):
        print(f"  {row['stage']:9} {row['candidates']:5}  {row['elapsed_ms']:7.2f} ms  {row['filters']}")
    cands, _ = plate_match.match(read, confidence=conf, observed_make=make, observed_colour=colour,
                                 registry=registry)
    names = plate_match.SCORE_NAMES
    print("rank plate        p       decision " + " ".join(n[:8].rjust(8) for n in names) + "  registered as")
    for i, c in enumerate(cands, 1):
        reg = registry.get(c.plate, {})
        print(f"{i:4} {c.plate:12} {c.probability:.4f}  {c.decision:8} "
              + " ".join(f"{c.scores[n]:.3f}".rjust(8) for n in names)
              + f"  {reg.get('make')} {reg.get('model')} {reg.get('colour')}")
    if cands:
        print("why: " + "; ".join(cands[0].reasons))


def main(argv: Optional[List[str]] = None) -> int:
    try:
        return _main(argv)
    except (lexicons.SourceUnavailable, httpx.HTTPError, plate_ocr.OCRUnavailable, OSError) as exc:
        # a dead source is a refusal with a way out, never a traceback (phase-1 rule)
        print(f"error: {exc}\n(no wrappers running? add --local-db to read this laptop's files)", file=sys.stderr)
        return 2


def _main(argv: Optional[List[str]]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    act = ap.add_mutually_exclusive_group(required=True)
    act.add_argument("--image", help="plate photo (png/jpg): OCR, then match")
    act.add_argument("--read", help="a plate as the camera read it")
    act.add_argument("--reverse", metavar="PLATE", help="registration -> camera captures that could be it")
    ap.add_argument("--make")
    ap.add_argument("--colour")
    ap.add_argument("--confidence", type=float)
    ap.add_argument("--repaired", action="store_true", help="--image: match the repaired guess, not the raw read")
    ap.add_argument("--local-db", action="store_true", help="offline: read reg.db / cam.db on this laptop")
    ap.add_argument("--meta", help="meta.db to use (registry and catalog)")
    args = ap.parse_args(argv)
    if args.meta:
        catalog.META_DB_PATH = args.meta

    registry = local_registry() if args.local_db else live_registry()[0]
    if args.reverse:
        captures = local_captures() if args.local_db else live_captures()[0]
        rows = reverse_rows(args.reverse, captures, registry)
        print(f"{len(rows)} of {len(captures)} captures could be {norm_plate(args.reverse)}")
        if norm_plate(args.reverse) not in registry:
            print(f"  note: {norm_plate(args.reverse)} is not registered; a capture is only claimed for a "
                  "plate its forward match can rank, so an unregistered plate gets none")
        for r in rows:
            print(f"  capture {str(r['capture_id']):>5} {str(r['read']):12} p={r['probability']:.4f} "
                  f"{r['decision']:7} {r['camera']} {r['time']}  {r['observed_make']} {r['observed_model']} "
                  f"{r['observed_colour']} (ocr {r['ocr_confidence']})")
        return 0
    read, conf = args.read, args.confidence
    if args.image:
        ocr = run_ocr(Path(args.image).read_bytes())
        print(f"OCR raw read {ocr['raw']!r} confidence {ocr['confidence']}")
        print(f"repaired guesses {ocr['repaired'][:6]}  read_plate {ocr['guesses']}")
        if not ocr["raw"]:
            print("OCR found no text; type the plate with --read instead")
            return 1
        read = ocr["repaired"][0] if args.repaired and ocr["repaired"] else ocr["raw"]
        conf = ocr["confidence"] if conf is None else conf
    _print_forward(norm_plate(read) or "", conf, args.make, args.colour, registry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
