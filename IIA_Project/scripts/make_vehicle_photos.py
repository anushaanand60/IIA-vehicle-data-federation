"""Build the labelled vehicle-photo set: real car photos with a known plate, the car fully or partly visible.

PHASE3-ADDED (C2: a photo dataset to tune and test make / model / colour + plate on) -- new in phase 3.

Why: the photo pipeline (mediator/vehicle_vision.py + EasyOCR) had been tried on one photo. To show the
examiner how well it works, and where it stops working, it needs photos whose answer is known.

Sources: 13 freely licensed photos from Wikimedia Commons, one or two per REG model, in REG's colours
(data/vehicle_photos/sources.csv; authors and licences in ATTRIBUTION.md). Make and model come from the
photo's Commons title; colour was labelled by eye, never by the vision model being tested.

Each photo's own plate (a real person's, or a dealer plate) is covered with a plate rendered by
scripts/make_plate_images.render_clean: a REG plate registered as exactly that make, model and colour. So
every image has a true plate and a true vehicle, and nobody's real plate is in the set.

Variants of each source photo (39 images):
    full        the photo, plate replaced                                       13
    partial     a crop around the plate: bumper, part of grille and lamps;       13
                colour visible, the badge sometimes cut off
    hard        night (dark, blue cast), blur and heavy JPEG                       7
    plate_only  a tight crop of the plate: the close-up gate must take over        3
    clone       the photo with ANOTHER registered car's plate (make, model and     3
                colour all differ): the plate matches, the car must not

Deterministic: no randomness; re-running rebuilds byte-identical images.

    python scripts/make_vehicle_photos.py
"""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path
from typing import Dict, List, Tuple

from PIL import Image, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

from make_plate_images import render_clean  # noqa: E402

DIR = ROOT / "data" / "vehicle_photos"
SOURCES = DIR / "sources.csv"
ORIGINALS = DIR / "originals"
IMAGES = DIR / "images"
MANIFEST = DIR / "manifest.csv"
HARD = ("baleno_1", "creta_3", "nexon_0", "punch_0", "seltos_1", "innova_1", "city_4")
PLATE_ONLY = ("baleno_4", "i20_3", "xuv700_1")
FIELDS = ["image", "source", "variant", "true_plate", "make", "model", "colour", "view", "is_clone",
          "plate_box", "license", "author", "source_url"]


def _box(text: str) -> Tuple[int, int, int, int]:
    x0, y0, x1, y1 = (int(v) for v in text.split())
    return x0, y0, x1, y1


def paste_plate(photo: Image.Image, plate: str, box: Tuple[int, int, int, int]) -> Image.Image:
    """The photo with `plate` rendered over the box (the real plate, a dealer plate, or the bare bumper)."""
    out = photo.copy()
    x0, y0, x1, y1 = box
    tile = render_clean(plate).convert("RGB").resize((x1 - x0, y1 - y0), Image.LANCZOS)
    out.paste(tile, (x0, y0))
    return out


def partial(photo: Image.Image, box: Tuple[int, int, int, int]) -> Image.Image:
    """Bumper, part of the grille and the lamps around the plate: much of the car is out of frame."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    return photo.crop((max(0, int(x0 - 1.6 * w)), max(0, int(y0 - 3.5 * h)),
                       min(photo.width, int(x1 + 1.6 * w)), min(photo.height, int(y1 + 1.2 * h))))


def hard(photo: Image.Image) -> Image.Image:
    """Night: a third of the light, a blue cast, a little blur, and a camera's heavy compression."""
    dark = ImageEnhance.Brightness(photo).enhance(0.35)
    r, g, b = dark.split()
    tinted = Image.merge("RGB", (r.point(lambda v: int(v * 0.8)), g, b.point(lambda v: min(255, int(v * 1.25)))))
    buf = io.BytesIO()
    tinted.filter(ImageFilter.GaussianBlur(1.2)).save(buf, format="JPEG", quality=25)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


def plate_only(photo: Image.Image, box: Tuple[int, int, int, int]) -> Image.Image:
    x0, y0, x1, y1 = box
    mx, my = int(0.15 * (x1 - x0)), int(0.4 * (y1 - y0))
    return photo.crop((max(0, x0 - mx), max(0, y0 - my), min(photo.width, x1 + mx), min(photo.height, y1 + my)))


def build() -> List[Dict[str, str]]:
    IMAGES.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, str]] = []
    with SOURCES.open(encoding="utf-8") as fh:
        sources = list(csv.DictReader(fh))
    for s in sources:
        photo = Image.open(ORIGINALS / f"{s['source']}.jpg").convert("RGB")
        box = _box(s["plate_box"])
        honest = paste_plate(photo, s["plate"], box)
        variants = [("full", honest, s["plate"], False), ("partial", partial(honest, box), s["plate"], False)]
        if s["source"] in HARD:
            variants.append(("hard", hard(honest), s["plate"], False))
        if s["source"] in PLATE_ONLY:
            variants.append(("plate_only", plate_only(honest, box), s["plate"], False))
        if s["clone_plate"]:
            variants.append(("clone", paste_plate(photo, s["clone_plate"], box), s["clone_plate"], True))
        for variant, image, plate, clone in variants:
            name = f"{s['source']}__{variant}.jpg"
            image.save(IMAGES / name, format="JPEG", quality=90)
            rows.append({"image": f"data/vehicle_photos/images/{name}", "source": s["source"], "variant": variant,
                         "true_plate": plate, "make": s["make"], "model": s["model"], "colour": s["colour"],
                         "view": s["view"], "is_clone": int(clone), "plate_box": s["plate_box"],
                         "license": s["license"], "author": s["author"], "source_url": s["source_url"]})
    with MANIFEST.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return rows


def main() -> int:
    rows = build()
    counts: Dict[str, int] = {}
    for r in rows:
        counts[r["variant"]] = counts.get(r["variant"], 0) + 1
    print(f"{len(rows)} images in {IMAGES.relative_to(ROOT)}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    print(f"manifest: {MANIFEST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
