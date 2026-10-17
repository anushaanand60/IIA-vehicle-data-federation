"""Workstream A: the rendered-plate benchmark generator.

These tests never call EasyOCR — the model load alone is ~9 s and the reads are another 13 min.
They cover the part that has to be reproducible on four laptops: determinism, image geometry,
manifest/disk agreement, and that the benchmark CSV parses back.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pytest
from PIL import Image

from scripts import make_plate_images
from scripts.make_plate_images import (
    DEGRADATIONS,
    PLATE_H,
    PLATE_W,
    SEVERITIES,
    degrade,
    generate,
    pick_plates,
    render_clean,
)
from scripts.run_ocr_benchmark import confusions, load_benchmark

PLATE = "DL01AB1234"
SEED = 20261017


def _save(img: Image.Image, path: Path) -> bytes:
    img.save(path, format="PNG")
    return path.read_bytes()


@pytest.mark.parametrize("degradation", DEGRADATIONS)
@pytest.mark.parametrize("severity", SEVERITIES)
def test_same_seed_gives_byte_identical_images(tmp_path: Path, degradation: str,
                                               severity: int) -> None:
    """Reproducibility is the whole claim of the benchmark: same seed, same bytes."""
    first = _save(degrade(PLATE, degradation, severity, SEED), tmp_path / "a.png")
    second = _save(degrade(PLATE, degradation, severity, SEED), tmp_path / "b.png")
    assert first == second


def test_generate_is_byte_identical_end_to_end(tmp_path: Path) -> None:
    """The in-memory check above is not enough: the committed artefact is what `generate` writes,
    so re-running the script must reproduce every file on disk bit for bit."""
    for run in ("first", "second"):
        generate(tmp_path / run, tmp_path / run / "manifest.csv", [PLATE], SEED)
    for path in sorted((tmp_path / "first").glob("*.png")):
        assert path.read_bytes() == (tmp_path / "second" / path.name).read_bytes()


def test_different_seed_changes_the_randomised_degradations(tmp_path: Path) -> None:
    a = _save(degrade(PLATE, "occlude", 3, SEED), tmp_path / "a.png")
    b = _save(degrade(PLATE, "occlude", 3, SEED + 1), tmp_path / "b.png")
    assert a != b


@pytest.mark.parametrize("degradation", ["clean", *DEGRADATIONS])
def test_every_degradation_keeps_the_plate_geometry(degradation: str) -> None:
    severity = 0 if degradation == "clean" else 2
    img = degrade(PLATE, degradation, severity, SEED)
    assert img.size == (PLATE_W, PLATE_H)
    assert img.mode == "RGB"


@pytest.mark.parametrize("severity", SEVERITIES)
def test_severity_actually_changes_the_image(tmp_path: Path, severity: int) -> None:
    clean = _save(render_clean(PLATE), tmp_path / "clean.png")
    noisy = _save(degrade(PLATE, "blur", severity, SEED), tmp_path / f"s{severity}.png")
    assert clean != noisy


def test_renders_without_arial(monkeypatch: pytest.MonkeyPatch) -> None:
    """The font fallback has to work: three of the four laptops are not guaranteed to have
    arialbd.ttf, and a NameError there would take the whole benchmark down."""
    monkeypatch.setattr(make_plate_images, "FONT_PATH", r"C:\no\such\font.ttf")
    img = render_clean(PLATE, font=make_plate_images._font())
    assert img.size == (PLATE_W, PLATE_H)
    assert img.convert("L").getextrema()[0] < 128      # something black was actually drawn


def test_clean_plate_is_not_blank() -> None:
    low, high = render_clean(PLATE).convert("L").getextrema()
    assert low < 60 and high > 200                     # black characters on a white plate


def test_degrade_rejects_nonsense() -> None:
    with pytest.raises(ValueError):
        degrade(PLATE, "sandstorm", 1, SEED)
    with pytest.raises(ValueError):
        degrade(PLATE, "blur", 9, SEED)


def test_manifest_matches_the_files_on_disk(tmp_path: Path) -> None:
    out = tmp_path / "images"
    manifest = tmp_path / "manifest.csv"
    plates = ["DL01AB1234", "DL05CD9876"]
    written = generate(out, manifest, plates, SEED)

    rows = list(csv.DictReader(manifest.open(newline="", encoding="utf-8")))
    on_disk = sorted(p.name for p in out.glob("*.png"))
    assert written == len(rows) == len(on_disk)
    # one clean plus 7 degradations x 3 severities, per plate
    assert written == len(plates) * (1 + len(DEGRADATIONS) * len(SEVERITIES))
    assert sorted(Path(r["image_path"]).name for r in rows) == on_disk
    assert {r["true_plate"] for r in rows} == set(plates)
    assert {int(r["seed"]) for r in rows} == {SEED}
    for row in rows:
        assert Path(row["image_path"]).exists()


def test_story_plates_are_always_sampled() -> None:
    plates = pick_plates(40, SEED)
    assert len(plates) == 40
    for story in ("DL01AB1234", "DL05CD9876", "HR26EF4455",
                  "UP16GH1122", "MH12IJ7788", "DL01AB0002"):
        assert story in plates
    assert pick_plates(40, SEED) == plates          # sampling is seeded, not incidental
    assert pick_plates(40, SEED + 1) != plates


def test_benchmark_csv_parses_and_is_well_formed() -> None:
    path = Path(__file__).resolve().parents[1] / "data" / "match_benchmark.csv"
    if not path.exists():
        pytest.skip("run scripts/run_ocr_benchmark.py first")
    rows = load_benchmark(path)
    assert rows
    assert set(rows[0]) == {"image_path", "true_plate", "ocr_read", "ocr_confidence",
                            "degradation", "severity", "exact_match"}
    observed = [r for r in rows if r["degradation"] == "observed"]
    assert len(observed) == 49          # 47 unregistered CAM plates + 2 planted story misreads
    planted = {r["ocr_read"]: r for r in observed if r["true_plate"]}
    assert set(planted) == {"DL05CD9B76", "DLO1AB1234"}
    assert planted["DL05CD9B76"]["true_plate"] == "DL05CD9876"
    assert planted["DLO1AB1234"]["true_plate"] == "DL01AB1234"
    assert all(r["exact_match"] == "0" for r in planted.values())   # they are misreads
    unknown = [r for r in observed if not r["true_plate"]]
    assert len(unknown) == 47
    assert all(r["exact_match"] == "" for r in unknown)             # no truth, no verdict
    for row in rows:
        if row["true_plate"] and row["degradation"] != "observed":
            assert row["exact_match"] in {"0", "1"}


def test_confusions_counts_only_aligned_positions() -> None:
    rows = [
        {"true_plate": "DL01AB1234", "ocr_read": "DLO1AB1234"},   # one substitution
        {"true_plate": "DL01AB1234", "ocr_read": "DL01AB123"},    # length mismatch: ignored
        {"true_plate": "", "ocr_read": "DL01AB1234"},             # unknown truth: ignored
        {"true_plate": "DL01AB1234", "ocr_read": ""},             # empty read: ignored
    ]
    assert confusions(rows) == {("0", "O"): 1}
