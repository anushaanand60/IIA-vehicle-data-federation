"""scripts/match_plate.py: the CLI fallback for the Plate Match page (workstream F).

Offline tests use `--local-db` (reg.db / cam.db on this laptop) and a fake OCR reader; the live tests
use the session `local_cluster`, so the registry and captures really come through the wrappers.
"""
from __future__ import annotations

import shutil
import sqlite3

from mediator import catalog
from scripts import match_plate


class FakeReader:
    """EasyOCR's readtext shape: (box, text, confidence), boxes deliberately out of order."""

    def readtext(self, image, allowlist=None):
        return [([[60, 0], [120, 0], [120, 20], [60, 20]], "CD 9B76", 0.8),
                ([[0, 0], [50, 0], [50, 20], [0, 20]], "DL05", 0.6)]


def test_run_ocr_keeps_the_raw_read_and_the_repairs():
    ocr = match_plate.run_ocr(b"not-an-image", reader=FakeReader())
    assert ocr["raw"] == "DL05CD9B76"                       # joined left to right
    assert ocr["confidence"] == round((4 * 0.6 + 6 * 0.8) / 10, 3)  # length-weighted, as the benchmark
    assert ocr["repaired"][0] == "DL05CD9876"               # B -> 8 where a digit belongs
    # read_plate repairs box by box and drops boxes under 6 characters, so a plate EasyOCR splits in
    # two comes back as a fragment -- the reason the page and the CLI match on the joined raw read
    assert ocr["guesses"] == [("CD9B76", 0.8)]


def test_offline_read_ranks_the_story_vehicle_first(capsys):
    assert match_plate.main(["--local-db", "--read", "DL05CD9B76", "--make", "Maruti Suzuki"]) == 0
    out = capsys.readouterr().out
    first = next(line for line in out.splitlines() if line.strip().startswith("1 "))
    assert "DL05CD9876" in first and "ACCEPT" in first
    assert "blocking funnel" in out and "position" in out


def test_live_read_and_reverse_go_through_the_wrappers(local_cluster, capsys):
    assert match_plate.main(["--read", "DL05CD9B76"]) == 0
    first = next(l for l in capsys.readouterr().out.splitlines() if l.strip().startswith("1 "))
    assert "DL05CD9876" in first and "ACCEPT" in first
    assert match_plate.main(["--reverse", "DL05CD9876"]) == 0
    out = capsys.readouterr().out
    assert "capture   916 DL05CD9B76" in out and "capture   911" in out


def test_reverse_rows_score_each_capture_with_its_own_context(local_cluster):
    registry, _ = match_plate.live_registry()
    cap = {"capture_id": 1, "read": "DL05CD9B76", "ocr_confidence": 0.71, "observed_make": "Maruti Suzuki",
           "observed_colour": "Silver", "observed_model": "Swift"}
    other = {**cap, "capture_id": 2, "observed_make": "Tata", "observed_colour": "Red", "observed_model": "Nexon"}
    rows = {r["capture_id"]: r for r in match_plate.reverse_rows("DL05CD9876", [cap, other], registry)}
    assert rows[1]["probability"] > rows[2]["probability"]   # contradicting make/colour lowers its own row


def test_a_dead_source_is_a_refusal_not_a_traceback(tmp_path, capsys):
    dead = tmp_path / "meta.db"
    shutil.copy(catalog.META_DB_PATH, dead)
    with sqlite3.connect(dead) as con:
        con.execute("UPDATE SOURCE_CATALOG SET base_url = 'http://127.0.0.1:9'")
    original = catalog.META_DB_PATH
    try:
        assert match_plate.main(["--meta", str(dead), "--read", "DL05CD9B76"]) == 2
    finally:
        catalog.META_DB_PATH = original
    err = capsys.readouterr().err
    assert err.startswith("error:") and "--local-db" in err
