"""Photo of a plate -> the Investigate tab's plate box (Task 2.1).

This is the ANPR end of the story (docs/FIELD_RESEARCH.md fact 1/4): a camera reads a plate, the
reading is *uncertain*, and the operator confirms which reading to query. So the slot never
auto-submits — it offers candidates as buttons, including "did you mean" repairs of the classic
OCR confusions, and the human picks. Picking writes both `selected_plate` (the app-wide contract)
and `inv_plate` (the text widget's key) and reruns.

Nothing here raises: OCR is optional, and a missing easyocr must degrade to an install hint, not a
broken tab.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st  # noqa: E402

from mediator.plate_ocr import (INSTALL_HINT, OCRUnavailable,  # noqa: E402
                                normalise_candidates, read_plate)

MAX_SHOWN = 4          # candidates from OCR
MAX_VARIANTS = 3       # "did you mean" repairs of the top candidate


def _pick(plate: str) -> None:
    st.session_state["selected_plate"] = plate
    st.session_state["inv_plate"] = plate
    st.rerun()


def render_ocr_slot() -> None:
    """Draw the uploader + candidate buttons. Safe to call anywhere; never raises."""
    upload = st.file_uploader("Photo of a number plate", type=["png", "jpg", "jpeg"],
                              key="ocr_file")
    if upload is None:
        st.caption("Optional: upload a plate photo (ANPR-style) instead of typing the number.")
        return

    image_bytes = upload.getvalue()
    st.image(image_bytes, caption=upload.name, width=260)

    try:
        with st.spinner("Reading the plate…"):
            guesses = read_plate(image_bytes)
    except OCRUnavailable as exc:
        st.warning(f"Plate OCR is unavailable on this machine — {exc} "
                   f"Install it with `{INSTALL_HINT}`, then pre-download the English model: "
                   f"`python -c \"import easyocr; easyocr.Reader(['en'])\"`.")
        return
    except Exception as exc:  # a corrupt upload must not take the tab down
        st.warning(f"Could not read that image ({exc}).")
        return

    if not guesses:
        st.info("No plate-like text found in that photo. Try a closer, straighter shot.")
        return

    st.caption("OCR candidates — pick one to investigate:")
    with st.container(horizontal=True, key="ocr_candidates"):
        for index, guess in enumerate(guesses[:MAX_SHOWN]):
            if st.button(f"{guess.text} ({guess.confidence:.0%})", key=f"ocr_pick_{index}"):
                _pick(guess.text)

    top = guesses[0].text
    variants = [v for v in normalise_candidates(top) if v != top][:MAX_VARIANTS]
    if not variants:
        return
    st.caption("Did you mean (OCR confusions O↔0, I↔1, B↔8, S↔5, Z↔2, G↔6):")
    with st.container(horizontal=True, key="ocr_variants"):
        for offset, variant in enumerate(variants, start=MAX_SHOWN):
            if st.button(variant, key=f"ocr_pick_{offset}"):
                _pick(variant)
