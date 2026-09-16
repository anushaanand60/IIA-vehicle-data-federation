"""Task 2.1 — plate photo → OCR → candidate plates.

Two halves, deliberately separable:
  * `canonical` / `normalise_candidates` are pure string functions and are tested without
    easyocr at all — that is the point of keeping the confusion logic out of the OCR call.
  * the OCR half is tested twice: once against a stub `reader` (filtering/ordering, no model)
    and once against the real EasyOCR model on a PIL-rendered plate (`importorskip`), so a
    laptop without the model still runs the rest of the suite.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mediator.plate_ocr import (OCRUnavailable, PlateGuess, canonical,  # noqa: E402
                                normalise_candidates, read_plate)

FIXTURE_DIR = _ROOT / "tests" / "fixtures" / "plates"


# --------------------------------------------------------------------------- pure functions
def test_canonical_strips_every_separator_and_uppercases():
    assert canonical("dl-01 ab 1234") == "DL01AB1234"
    assert canonical("DL01AB1234") == "DL01AB1234"
    assert canonical("  dl_01/ab.1234\n") == "DL01AB1234"
    assert canonical("") == ""


def test_valid_plate_is_its_own_first_candidate():
    assert normalise_candidates("DL01AB1234")[0] == "DL01AB1234"
    # a differently-spelled but valid plate still canonicalises to itself first
    assert normalise_candidates("dl 01 ab 1234")[0] == "DL01AB1234"


def test_ocr_confusions_are_repaired_position_aware():
    # 'O' sits where the pattern wants a digit -> 0
    assert normalise_candidates("DLO1AB1234")[0] == "DL01AB1234"
    # '0' sits where the pattern wants a letter -> O
    assert normalise_candidates("DL01A81234")[0] == "DL01AB1234"
    # digit-position confusions at the tail
    assert normalise_candidates("DL01AB1Z34")[0] == "DL01AB1234"


def test_candidates_are_unique_and_capped():
    out = normalise_candidates("OOOOOOOOOO")
    assert len(out) <= 16
    assert len(set(out)) == len(out)
    assert out[0] == canonical("OOOOOOOOOO") or len(out) > 1


def test_candidates_of_junk_text_are_harmless():
    assert normalise_candidates("") == []
    assert normalise_candidates("!!!") == []


# --------------------------------------------------------------------------- read_plate, stubbed
class _StubReader:
    """Mimics `easyocr.Reader`: readtext -> list of (box, text, confidence)."""

    def __init__(self, results):
        self._results = results
        self.calls = []

    def readtext(self, image, **kwargs):
        self.calls.append(kwargs)
        return self._results


def test_read_plate_prefers_plate_shaped_text_then_confidence():
    reader = _StubReader([
        ([[0, 0], [10, 0], [10, 5], [0, 5]], "IND", 0.99),          # too short -> dropped
        ([[0, 0], [10, 0], [10, 5], [0, 5]], "DL 01 AB 1234", 0.71),  # plate-shaped
        ([[0, 0], [10, 0], [10, 5], [0, 5]], "GOVTOFDELHI", 0.80),   # long alnum, not a plate
    ])
    guesses = read_plate(b"not-really-an-image", reader=reader)
    assert [g.text for g in guesses] == ["DL01AB1234", "GOVTOFDELHI"]
    assert isinstance(guesses[0], PlateGuess)
    assert guesses[0].confidence == pytest.approx(0.71)
    assert guesses[0].box  # box is carried through untouched
    assert reader.calls[0]["allowlist"].startswith("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")


def test_read_plate_sorts_within_each_group_by_confidence():
    reader = _StubReader([
        ([], "DL05CD9876", 0.40),
        ([], "HR26EF4455", 0.90),
        ([], "SOMEOTHERTEXT", 0.95),
    ])
    assert [g.text for g in read_plate(b"x", reader=reader)] == [
        "HR26EF4455", "DL05CD9876", "SOMEOTHERTEXT"]


def test_read_plate_repairs_the_reading_it_returns():
    # EasyOCR genuinely returns this for a clean rendering of DL01AB1234.
    reader = _StubReader([([], "DLOIAB1234", 0.62)])
    guesses = read_plate(b"x", reader=reader)
    assert [g.text for g in guesses] == ["DL01AB1234"]


def test_missing_easyocr_raises_ocr_unavailable(monkeypatch):
    import mediator.plate_ocr as plate_ocr

    monkeypatch.setattr(plate_ocr, "_READER", None)
    monkeypatch.setitem(sys.modules, "easyocr", None)  # makes `import easyocr` raise ImportError
    with pytest.raises(OCRUnavailable) as excinfo:
        read_plate(b"x")
    assert "pip install easyocr" in str(excinfo.value)


# --------------------------------------------------------------------------- real model
def _render_plate_png(text: str, path: Path) -> bytes:
    """A 600x160 white plate with black text — the cleanest possible ANPR input."""
    from PIL import Image, ImageDraw, ImageFont

    font = None
    for candidate in ("arialbd.ttf", "DejaVuSans-Bold.ttf", "arial.ttf"):
        try:
            font = ImageFont.truetype(candidate, 90)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default(size=90)

    image = Image.new("RGB", (600, 160), "white")
    draw = ImageDraw.Draw(image)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    draw.text(((600 - (right - left)) / 2 - left, (160 - (bottom - top)) / 2 - top),
              text, fill="black", font=font)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    data = buffer.getvalue()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)  # regenerated every run; nothing binary is committed
    return data


def test_real_easyocr_reads_a_rendered_plate():
    pytest.importorskip("easyocr")
    png = _render_plate_png("DL01AB1234", FIXTURE_DIR / "DL01AB1234.png")
    try:
        guesses = read_plate(png)
    except OCRUnavailable as exc:  # model weights not downloaded on this machine
        pytest.skip(str(exc))
    assert guesses, "EasyOCR returned no usable text for a clean synthetic plate"
    assert canonical(guesses[0].text) == "DL01AB1234"
    assert guesses[0].confidence >= 0.5


# --------------------------------------------------------------------------- Streamlit slot
def _ocr_slot_script() -> None:
    # AppTest re-executes this body as its own script, so it imports what it uses. The module is
    # loaded from its file under a private name: putting `<root>/app` on sys.path would let the
    # top-level name `app` resolve to `app/app.py` instead of the `app/` package and re-run the
    # whole GUI inside this test.
    import importlib.util
    from pathlib import Path

    import mediator

    path = Path(mediator.__file__).resolve().parents[1] / "app" / "tabs" / "ocr_upload.py"
    spec = importlib.util.spec_from_file_location("_ocr_upload_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.render_ocr_slot()


def test_ocr_slot_renders_without_an_upload():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_function(_ocr_slot_script)
    app.run()
    assert not app.exception
