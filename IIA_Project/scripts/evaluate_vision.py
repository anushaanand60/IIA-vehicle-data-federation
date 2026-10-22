"""Tune on the training photos, then test: plate, make, model and colour from a photo, and what it does to the match.

PHASE3-ADDED (C2: the photo dataset, data/vehicle_photos, built by scripts/make_vehicle_photos.py) -- new in phase 3.

Split by SOURCE PHOTO, so no car appears in both halves (its full, partial, night, close-up and clone
images all go together):
    test   creta_3, nexon_0, seltos_1, xuv700_1, swift_3   (every variant: night, a clone, a close-up, a rear view)
    train  the other 8

What is trained, and why only that: with 13 cars of 10 models, a car-model classifier would see each model
once or twice, so nothing can honestly be fitted to the car itself. CLIP is used zero-shot (it already knows
car makes and colours), and the training photos choose the one thing they can support: how sure CLIP must be
before its answer is used (the abstention threshold per attribute). The choice maximises
    right answers - 2 x wrong answers
because a wrong colour is worse than none: it manufactures a clone signal against an honest car.

For every image: EasyOCR + plate-line filter (scripts/match_plate.run_ocr) -> the read and the plate box;
vehicle_vision -> make / model / colour; plate_match.match against REG twice, plate only and plate + photo.
Read-only. Writes docs/VISION_EVALUATION.md and data/vehicle_photos/results.csv (per-image, for the GUI).

    python scripts/evaluate_vision.py
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

from match_plate import run_ocr  # noqa: E402
from mediator import plate_match, plate_ocr, plate_resolve, vehicle_vision  # noqa: E402

DIR = ROOT / "data" / "vehicle_photos"
MANIFEST = DIR / "manifest.csv"
RESULTS = DIR / "results.csv"
DOC = ROOT / "docs" / "VISION_EVALUATION.md"
TEST = ("creta_3", "nexon_0", "seltos_1", "xuv700_1", "swift_3")
ATTRS = ("make", "model", "colour")
GRID = (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9)
WRONG_COST = 2.0


def _agree(a: Optional[str], b: Optional[str]) -> bool:
    return bool(a and b and plate_resolve._agrees(a, b))


def analyse_all(rows: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    out = []
    for r in rows:
        data = (ROOT / r["image"]).read_bytes()
        ocr = run_ocr(data)
        g = vehicle_vision.guesses(data, ocr.get("box") or [])
        out.append({**r, "ocr": ocr, "guesses": g, "split": "test" if r["source"] in TEST else "train"})
        print(f"  {Path(r['image']).name:28} read {ocr['raw'] or '-':12} ({ocr['confidence']})", flush=True)
    return out


def _guess(g: Dict[str, Any], attr: str) -> Tuple[Optional[str], float]:
    if attr not in g:
        return None, 0.0
    label, p = g[attr]
    return (label[1] if attr == "model" else label), p


def tune(items: List[Dict[str, Any]]) -> Dict[str, float]:
    """Per attribute, the threshold with the best right - 2 x wrong on the training cars' vehicle images."""
    cars = [i for i in items if i["split"] == "train" and i["variant"] != "plate_only"]
    chosen = {}
    for attr in ATTRS:
        def utility(t: float) -> float:
            right = wrong = 0
            for i in cars:
                label, p = _guess(i["guesses"], attr)
                if label and p >= t:
                    right += _agree(label, i[attr])
                    wrong += not _agree(label, i[attr])
            return right - WRONG_COST * wrong
        chosen[attr] = max(GRID, key=lambda t: (utility(t), t))  # ties: the stricter threshold
    return chosen


def evaluate(items: List[Dict[str, Any]], min_p: Dict[str, float]) -> None:
    reg = plate_match.load_registry()
    for i in items:
        view = vehicle_vision.decide(i["guesses"], min_p)
        read, conf = i["ocr"]["raw"], i["ocr"]["confidence"]
        plain = plate_match.match(read, confidence=conf, registry=reg)[0] if read else []
        ctx = plate_match.match(read, confidence=conf, observed_make=view.make, observed_model=view.model,
                                observed_colour=view.colour, registry=reg)[0] if read else []
        i.update(view=view, plain=plain[0] if plain else None, ctx=ctx[0] if ctx else None,
                 read_ok=i["true_plate"] in (read, (i["ocr"]["repaired"] or [""])[0]))


def _rate(n: int, d: int) -> str:
    return f"{n}/{d}" if d else "-"


def table(items: List[Dict[str, Any]]) -> List[str]:
    lines = ["| variant | images | plate read right | close-up gate right | make: said / right | model: said / right | "
             "colour: said / right | top plate right | auto-ACCEPT, plate only | auto-ACCEPT, plate + photo |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for variant in ("full", "partial", "hard", "plate_only", "clone", "all"):
        sub = [i for i in items if variant == "all" or i["variant"] == variant]
        if not sub:
            continue
        gate = sum((i["view"].kind == "plate_closeup") == (i["variant"] == "plate_only") for i in sub)
        cells = []
        for attr in ATTRS:
            said = [i for i in sub if getattr(i["view"], attr)]
            right = sum(_agree(getattr(i["view"], attr), i[attr]) for i in said)
            cells.append(f"{len(said)} / {right}")
        top = sum(bool(i["ctx"]) and i["ctx"].plate == i["true_plate"] for i in sub)
        acc_plain = sum(bool(i["plain"]) and i["plain"].decision == "ACCEPT" for i in sub)
        acc_ctx = sum(bool(i["ctx"]) and i["ctx"].decision == "ACCEPT" for i in sub)
        lines.append(f"| {variant} | {len(sub)} | {_rate(sum(i['read_ok'] for i in sub), len(sub))} | "
                     f"{_rate(gate, len(sub))} | {' | '.join(cells)} | {_rate(top, len(sub))} | "
                     f"{_rate(acc_plain, len(sub))} | {_rate(acc_ctx, len(sub))} |")
    return lines


def main() -> int:
    with MANIFEST.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    print(f"reading {len(rows)} photos (EasyOCR + CLIP) ...")
    items = analyse_all(rows)
    tuned = tune(items)
    print("thresholds chosen on the training cars:", tuned)
    evaluate(items, tuned)
    test = [i for i in items if i["split"] == "test"]
    train = [i for i in items if i["split"] == "train"]
    clones = [i for i in items if i["variant"] == "clone"]
    lines = ["# Photo pipeline: plate, make, model and colour (tuned on 8 cars, tested on 5)", "",
             f"Generated by `python scripts/evaluate_vision.py` on the {len(rows)} images of `data/vehicle_photos` "
             "(13 freely licensed photos of REG's models, each with a REG plate of exactly that make, model and colour "
             "pasted over its own; see `scripts/make_vehicle_photos.py`). Split by source photo: no car is in both halves.",
             "", "## Thresholds, chosen on the training cars only", "",
             f"CLIP is used zero-shot. The training cars choose how sure it must be before an answer is used, maximising "
             f"*right - {WRONG_COST:g} x wrong* (a wrong colour invents a clone signal, so it costs more than silence):", "",
             "| attribute | threshold | (was) |", "|---|---|---|"]
    lines += [f"| {a} | {tuned[a]} | {vehicle_vision.MIN_P[a]} |" for a in ATTRS]
    lines += ["", "## Test cars (5 cars, never seen while tuning)", ""] + table(test)
    lines += ["", "## Training cars (8 cars; in-sample for the thresholds, shown for completeness)", ""] + table(train)
    lines += ["", "## Clones: another registered car's plate on the photo", "",
              "| image | plate read | REG says | photo says | plate only | plate + photo |", "|---|---|---|---|---|---|"]
    reg = plate_match.load_registry()
    for i in clones:
        held = reg.get(i["true_plate"], {})
        lines.append(f"| {Path(i['image']).name} | {i['ocr']['raw']} | {held.get('colour')} {held.get('make')} "
                     f"{held.get('model')} | {i['view'].words()} | {i['plain'].decision if i['plain'] else '-'} | "
                     f"{i['ctx'].decision if i['ctx'] else '-'} |")
    lines += ["", "Columns: *said / right* = how many images CLIP answered at all (above the threshold), and how many "
              "of those answers were right; abstaining is allowed and counts as no evidence. *Top plate right* = the "
              "matcher's first candidate is the plate on the car. Auto-ACCEPT for a clone is a failure: the plate is "
              "right but the car is not the registered one."]
    DOC.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with RESULTS.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["image", "split", "variant", "true_plate", "read", "confidence", "photo_kind", "photo_make",
                    "photo_model", "photo_colour", "top_plate", "decision_plate_only", "decision_with_photo"])
        for i in items:
            v = i["view"]
            w.writerow([i["image"], i["split"], i["variant"], i["true_plate"], i["ocr"]["raw"], i["ocr"]["confidence"],
                        v.kind, v.make or "", v.model or "", v.colour or "", i["ctx"].plate if i["ctx"] else "",
                        i["plain"].decision if i["plain"] else "", i["ctx"].decision if i["ctx"] else ""])
    print("\n".join(lines))
    print(f"\nwrote {DOC.relative_to(ROOT)} and {RESULTS.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
