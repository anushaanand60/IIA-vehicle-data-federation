"""Render Indian-style number plates and degrade them in controlled ways (workstream A).

Why this exists: workstream B/C of the phase-2 plan needs a *labelled* noise distribution. The
47 unregistered CAM plates are real noise but have no ground truth. Rendering a plate we chose
ourselves and then degrading it gives us (true_plate, degradation, severity) labels for free.

Determinism is a hard requirement: the benchmark has to be reproducible on another laptop, so
every random draw comes from a `random.Random` seeded per image from (--seed, plate, degradation,
severity). Global `random` is never touched.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import random
import sqlite3
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO_ROOT = Path(__file__).resolve().parents[1]
REG_DB = REPO_ROOT / "sources" / "reg" / "reg.db"
FONT_PATH = r"C:\Windows\Fonts\arialbd.ttf"

PLATE_W, PLATE_H = 520, 120
FONT_SIZE = 72

# Story plates from the demo script: they must be in every benchmark run so the GUI demo and the
# benchmark talk about the same vehicles.
STORY_PLATES = [
    "DL01AB1234",
    "DL05CD9876",
    "HR26EF4455",
    "UP16GH1122",
    "MH12IJ7788",
    "DL01AB0002",
]

DEGRADATIONS = ["blur", "motion", "jpeg", "rotate", "glare", "dark", "occlude"]
SEVERITIES = (1, 2, 3)


def _font() -> ImageFont.ImageFont:
    """Bold Arial where it exists, Pillow's bitmap default elsewhere.

    Trade-off: the default font is much smaller and uglier, so OCR rates differ between machines.
    We accept that rather than vendoring a font file, and the note in docs/BENCHMARK_NOISE.md
    records which font produced the committed numbers.
    """
    try:
        return ImageFont.truetype(FONT_PATH, FONT_SIZE)
    except OSError:
        return ImageFont.load_default()


def render_clean(plate: str, *, font: ImageFont.ImageFont | None = None) -> Image.Image:
    """A clean white plate with a black border and black characters, spaced like a real plate."""
    font = font or _font()
    img = Image.new("RGB", (PLATE_W, PLATE_H), "white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([4, 4, PLATE_W - 5, PLATE_H - 5], outline="black", width=5)
    text = _spaced(plate)
    box = draw.textbbox((0, 0), text, font=font)
    x = (PLATE_W - (box[2] - box[0])) // 2 - box[0]
    y = (PLATE_H - (box[3] - box[1])) // 2 - box[1]
    draw.text((x, y), text, fill="black", font=font)
    return img


def _spaced(plate: str) -> str:
    """`DL01AB1234` -> `DL01 AB 1234`: the grouping a real plate is painted with."""
    if len(plate) >= 10:
        return f"{plate[:4]} {plate[4:-4]} {plate[-4:]}"
    return plate


def _key(seed: int, plate: str, degradation: str, severity: int) -> int:
    digest = hashlib.sha256(f"{seed}|{plate}|{degradation}|{severity}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _rng(seed: int, plate: str, degradation: str, severity: int) -> random.Random:
    """Per-image RNG. Never the global `random`: two runs must produce identical bytes."""
    rng = random.Random(_key(seed, plate, degradation, severity))
    # Carried on the object so a degrader that needs bulk numbers (the sensor noise in `_dark`)
    # can seed a numpy Generator from the same key instead of calling gauss() 62 400 times.
    rng.numpy_seed = _key(seed, plate, degradation, severity)  # type: ignore[attr-defined]
    return rng


def _blur(img: Image.Image, severity: int, rng: random.Random) -> Image.Image:
    return img.filter(ImageFilter.GaussianBlur(radius={1: 1.0, 2: 2.0, 3: 3.2}[severity]))


def _motion(img: Image.Image, severity: int, rng: random.Random) -> Image.Image:
    """Directional blur: average of copies shifted along one axis."""
    length = {1: 5, 2: 11, 3: 19}[severity]
    angle = rng.choice([0, 0, 15, -15])
    pad = length
    # Edge padding, not np.roll: rolling wraps the right-hand border onto the left and paints a
    # ghost of the plate frame across the characters, which is not a motion artefact.
    arr = np.pad(np.asarray(img).astype(np.float64), ((pad, pad), (pad, pad), (0, 0)), mode="edge")
    acc = np.zeros_like(arr)
    for step in range(length):
        dx = step - length // 2
        dy = int(round(dx * np.tan(np.radians(angle))))
        acc += np.roll(np.roll(arr, dx, axis=1), dy, axis=0)
    out = (acc / length)[pad:pad + PLATE_H, pad:pad + PLATE_W]
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def _jpeg(img: Image.Image, severity: int, rng: random.Random) -> Image.Image:
    """Low-quality re-encode. The *decoded* pixels are the degradation, so the file we then
    write to disk is a lossless PNG — saving it as JPEG again would degrade it twice."""
    quality = {1: 25, 2: 12, 3: 5}[severity]
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def _rotate(img: Image.Image, severity: int, rng: random.Random) -> Image.Image:
    """Small rotation plus a perspective skew — a camera is never square to the plate."""
    angle = {1: 2.0, 2: 5.0, 3: 9.0}[severity] * rng.choice([1, -1])
    out = img.rotate(angle, resample=Image.BICUBIC, fillcolor=(235, 235, 235))
    shift = {1: 6, 2: 14, 3: 24}[severity]
    quad = (
        0, shift,
        0, PLATE_H - 1,
        PLATE_W - 1, PLATE_H - 1 - shift,
        PLATE_W - 1, 0,
    )
    return out.transform((PLATE_W, PLATE_H), Image.QUAD, quad, resample=Image.BICUBIC,
                         fillcolor=(235, 235, 235))


def _glare(img: Image.Image, severity: int, rng: random.Random) -> Image.Image:
    strength = {1: 110, 2: 175, 3: 235}[severity]
    width = {1: 120, 2: 200, 3: 300}[severity]
    cx = rng.randint(width // 2, PLATE_W - width // 2)
    layer = Image.new("L", (PLATE_W, PLATE_H), 0)
    ImageDraw.Draw(layer).ellipse(
        [cx - width // 2, -PLATE_H // 3, cx + width // 2, PLATE_H + PLATE_H // 3], fill=strength)
    layer = layer.filter(ImageFilter.GaussianBlur(radius=width / 8))
    white = Image.new("RGB", (PLATE_W, PLATE_H), "white")
    return Image.composite(white, img, layer)


def _dark(img: Image.Image, severity: int, rng: random.Random) -> Image.Image:
    gain = {1: 0.55, 2: 0.35, 3: 0.20}[severity]
    sigma = {1: 8.0, 2: 16.0, 3: 26.0}[severity]
    arr = np.asarray(img).astype(np.float64) * gain
    generator = np.random.default_rng(getattr(rng, "numpy_seed", 0))
    arr += generator.normal(0.0, sigma, size=(PLATE_H, PLATE_W))[:, :, None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def _occlude(img: Image.Image, severity: int, rng: random.Random) -> Image.Image:
    specks = {1: 25, 2: 70, 3: 150}[severity]
    out = img.copy()
    draw = ImageDraw.Draw(out)
    for _ in range(specks):
        x, y = rng.randint(0, PLATE_W - 1), rng.randint(0, PLATE_H - 1)
        r = rng.randint(2, 4 + severity * 2)
        grey = rng.randint(40, 110)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=(grey, grey, grey))
    if severity >= 2:
        bar_h = {2: 14, 3: 28}[severity]
        top = rng.randint(0, PLATE_H - bar_h)
        draw.rectangle([0, top, PLATE_W - 1, top + bar_h], fill=(60, 55, 50))
    return out


DEGRADERS = {
    "blur": _blur,
    "motion": _motion,
    "jpeg": _jpeg,
    "rotate": _rotate,
    "glare": _glare,
    "dark": _dark,
    "occlude": _occlude,
}


def degrade(plate: str, degradation: str, severity: int, seed: int,
            *, font: ImageFont.ImageFont | None = None) -> Image.Image:
    """Render `plate` and apply one degradation at one severity. Deterministic in `seed`."""
    img = render_clean(plate, font=font)
    if degradation == "clean":
        return img
    if degradation not in DEGRADERS:
        raise ValueError(f"unknown degradation: {degradation}")
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {SEVERITIES}, got {severity}")
    return DEGRADERS[degradation](img, severity, _rng(seed, plate, degradation, severity))


def pick_plates(count: int, seed: int) -> list[str]:
    """Story plates first, then a seeded sample of REG registration numbers (read-only)."""
    conn = sqlite3.connect(f"file:{REG_DB}?mode=ro", uri=True)
    try:
        rows = sorted(r[0] for r in conn.execute(
            "SELECT registration_no FROM VEHICLE_REGISTRATION"))
    finally:
        conn.close()
    # A full run always keeps all six story plates; only a deliberately tiny run (the unit tests)
    # ever asks for fewer plates than there are stories.
    chosen = list(STORY_PLATES[:count]) if count < len(STORY_PLATES) else list(STORY_PLATES)
    pool = [p for p in rows if p not in chosen]
    rng = random.Random(seed)
    extra = max(0, count - len(chosen))
    chosen.extend(rng.sample(pool, min(extra, len(pool))))
    return chosen


def _manifest_path(path: Path) -> str:
    """Repo-relative POSIX path when the image lives in the repo, absolute otherwise.

    Repo-relative is what we commit (the manifest has to mean the same thing on four laptops);
    the absolute fallback keeps `--out` into a temp directory working for the unit tests.
    """
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def generate(out_dir: Path, manifest: Path, plates: list[str], seed: int) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    font = _font()
    rows = []
    for plate in plates:
        jobs = [("clean", 0)] + [(d, s) for d in DEGRADATIONS for s in SEVERITIES]
        for degradation, severity in jobs:
            path = out_dir / f"{plate}__{degradation}_s{severity}.png"
            img = degrade(plate, degradation, severity, seed, font=font)
            img.save(path, format="PNG")
            rows.append({
                "image_path": _manifest_path(path),
                "true_plate": plate,
                "degradation": degradation,
                "severity": severity,
                "seed": seed,
            })
    with manifest.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["image_path", "true_plate", "degradation", "severity", "seed"])
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plates", type=int, default=100)
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "data" / "plate_images")
    parser.add_argument("--seed", type=int, default=20261017)
    parser.add_argument("--manifest", type=Path, default=None)
    args = parser.parse_args(argv)
    manifest = args.manifest or (args.out / "manifest.csv")
    plates = pick_plates(args.plates, args.seed)
    written = generate(args.out, manifest, plates, args.seed)
    print(f"{len(plates)} plates -> {written} images in {args.out}")
    print(f"manifest: {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
