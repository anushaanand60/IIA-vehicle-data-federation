"""Plate photo -> candidate plate numbers (Task 2.1).

Why this exists: the field research (docs/FIELD_RESEARCH.md, facts 1 and 4) shows the production
analogue of this project is an ANPR camera whose OCR output is fed straight into VAHAN/IIB to
raise an e-challan. Our CAM source already models OCR noise (`DLO1AB1Z34`); this module lets the
operator reproduce that end of the pipeline from a photo instead of typing the plate.

Design boundaries, deliberately:
  * OCR is *optional*. easyocr (and its torch wheels) may not be installed on every laptop, so the
    import is lazy and failure surfaces as `OCRUnavailable`, never as a crashed GUI.
  * The confusion repair (`canonical`, `normalise_candidates`) is pure string work with no
    dependency on easyocr at all — it is the part that has to be defensible in the viva, and it is
    testable on any machine.
  * Nothing here queries a source or decides anything. It returns *candidates*; the mediator's
    normal planner/executor path answers them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

# Indian civilian plate: 2 state letters, 2 district digits, 1-3 series letters, 4 digits.
PLATE_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z]{1,3}\d{4}$")

ALLOWLIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -"
INSTALL_HINT = "pip install easyocr"

# Glyph pairs OCR actually confuses on plates. Applied one way only per position: at a position
# where the plate pattern wants a digit we only ever letter->digit, and vice versa. Substituting
# blindly would explode the candidate list and invent plates the pattern forbids.
_LETTER_TO_DIGIT = {"O": "0", "I": "1", "B": "8", "S": "5", "Z": "2", "G": "6"}
_DIGIT_TO_LETTER = {digit: letter for letter, digit in _LETTER_TO_DIGIT.items()}

MAX_CANDIDATES = 16
MIN_TEXT_LEN = 6


class OCRUnavailable(RuntimeError):
    """easyocr (or its English model) is not available on this machine."""


@dataclass
class PlateGuess:
    text: str                      # canonical form: uppercase alphanumerics only
    confidence: float
    box: list = field(default_factory=list)   # easyocr's 4-point polygon, carried through untouched


def canonical(text: str) -> str:
    """Uppercase alphanumerics only — the team's canonical plate spelling."""
    return "".join(ch for ch in str(text).upper() if ch.isalnum())


def _roles(length: int) -> dict[int, str]:
    """Position -> "alpha" | "digit" under the Indian plate pattern, for a string of `length`.

    Only meaningful for plate-length strings; anything else gets no roles and therefore no
    variants, which is the conservative answer.
    """
    if not 8 <= length <= 12:
        return {}
    roles = {0: "alpha", 1: "alpha", 2: "digit", 3: "digit"}
    for index in range(length - 4, length):
        roles[index] = "digit"
    for index in range(4, length - 4):
        roles.setdefault(index, "alpha")
    return roles


def normalise_candidates(text: str) -> list[str]:
    """Canonical text plus OCR-confusion variants, best guess first.

    "Best" = a candidate that actually matches the plate pattern, then fewest substitutions. So
    `DLO1AB1234` (a letter O read where a digit belongs) yields `DL01AB1234` first, while a plate
    that is already well-formed is returned unchanged at the head of the list.
    """
    base = canonical(text)
    if not base:
        return []

    roles = _roles(len(base))
    swaps: list[tuple[int, str]] = []
    for index, char in enumerate(base):
        role = roles.get(index)
        if role == "digit" and char in _LETTER_TO_DIGIT:
            swaps.append((index, _LETTER_TO_DIGIT[char]))
        elif role == "alpha" and char in _DIGIT_TO_LETTER:
            swaps.append((index, _DIGIT_TO_LETTER[char]))

    ordered: list[str] = [base]
    for size in range(1, len(swaps) + 1):          # fewest substitutions first
        for chosen in combinations(swaps, size):
            chars = list(base)
            for index, replacement in chosen:
                chars[index] = replacement
            ordered.append("".join(chars))
            if len(ordered) >= MAX_CANDIDATES * 4:  # bounded work before the sort below
                break
        if len(ordered) >= MAX_CANDIDATES * 4:
            break

    seen: set[str] = set()
    unique = [c for c in ordered if not (c in seen or seen.add(c))]
    # Stable sort: pattern-matching candidates float to the front, order within a group is the
    # substitution order built above.
    unique.sort(key=lambda candidate: 0 if PLATE_RE.match(candidate) else 1)
    return unique[:MAX_CANDIDATES]


_READER: Any = None


def _get_reader() -> Any:
    """Lazy singleton EasyOCR reader. CPU only — no GPU on the demo laptops."""
    global _READER
    if _READER is not None:
        return _READER
    try:
        import easyocr  # noqa: PLC0415 - deliberately lazy: torch import costs seconds
    except Exception as exc:  # ImportError, or a broken torch install
        raise OCRUnavailable(f"easyocr is not installed ({exc}). Install it with: "
                             f"{INSTALL_HINT}") from exc
    try:
        _READER = easyocr.Reader(["en"], gpu=False)
    except Exception as exc:  # model weights missing and no network to fetch them
        raise OCRUnavailable(f"easyocr could not load its English model ({exc}). Pre-download it "
                             f"with: python -c \"import easyocr; easyocr.Reader(['en'])\" "
                             f"(after {INSTALL_HINT})") from exc
    return _READER


def read_plate(image_bytes: bytes, *, reader: Any = None) -> list[PlateGuess]:
    """OCR an image and return plate candidates, most plausible first.

    Each reading is repaired by `normalise_candidates` first — EasyOCR really does return
    `DLOIAB1234` for a clean rendering of `DL01AB1234`, and a repair that the pattern licenses is
    a better *guess* than the raw glyphs. The unrepaired reading stays available to the caller as
    one of `normalise_candidates`' alternatives ("did you mean").

    Ordering: text whose repaired form matches the plate pattern comes first (by confidence),
    then any other alphanumeric run of >= 6 characters (by confidence). Everything shorter is
    dropped — on a real plate photo it is the state emblem, "IND", or a dealer sticker.
    """
    engine = reader if reader is not None else _get_reader()
    try:
        raw = engine.readtext(image_bytes, allowlist=ALLOWLIST)
    except OCRUnavailable:
        raise
    except Exception as exc:
        raise OCRUnavailable(f"easyocr failed to read the image ({exc}).") from exc

    plate_shaped: list[PlateGuess] = []
    other: list[PlateGuess] = []
    for entry in raw:
        box, text, confidence = entry[0], entry[1], entry[2]
        cleaned = canonical(text)
        if len(cleaned) < MIN_TEXT_LEN:
            continue
        best = normalise_candidates(cleaned)[0]
        guess = PlateGuess(text=best, confidence=float(confidence), box=list(box or []))
        (plate_shaped if PLATE_RE.match(best) else other).append(guess)

    plate_shaped.sort(key=lambda g: g.confidence, reverse=True)
    other.sort(key=lambda g: g.confidence, reverse=True)
    return plate_shaped + other
