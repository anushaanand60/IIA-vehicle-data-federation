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
_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import streamlit as st  # noqa: E402

from components import group_label  # noqa: E402
from mediator.plate_ocr import (INSTALL_HINT, OCRUnavailable,  # noqa: E402
                                normalise_candidates, read_plate)
from mediator import vehicle_vision  # noqa: E402  (PHASE3-ADDED: C2 make, model and colour from the photo)

MAX_SHOWN = 4          # candidates from OCR
MAX_VARIANTS = 3       # "did you mean" repairs of the top candidate


def _pick(plate: str) -> None:
    st.session_state["selected_plate"] = plate
    st.session_state["inv_plate"] = plate
    st.rerun()


def render_ocr_slot() -> None:
    """Draw the uploader + candidate buttons. Safe to call anywhere; never raises."""
    group_label("Plate photo (optional)")
    upload = st.file_uploader("Photo of a number plate", type=["png", "jpg", "jpeg"],
                              key="ocr_file", label_visibility="collapsed")
    if upload is None:
        st.session_state.pop("photo_vehicle", None)  # PHASE3-ADDED (C2): no photo, nothing to compare
        # PHASE3-REPLACED (C2: a photo of the whole car also gives its make and colour):
        # st.caption("Optional: upload a plate photo (ANPR-style) instead of typing the number.")
        st.caption("Optional: upload a plate close-up, or a photo of the car showing its plate (ANPR-style), "
                   "instead of typing the number. A photo of the car also gives its make and colour.")
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

    _vehicle_caption(image_bytes, guesses[0].box)  # PHASE3-ADDED (C2)

    group_label("OCR candidates — pick one to investigate")
    with st.container(horizontal=True, key="ocr_candidates"):
        for index, guess in enumerate(guesses[:MAX_SHOWN]):
            if st.button(f"{guess.text} ({guess.confidence:.0%})", key=f"ocr_pick_{index}"):
                _pick(guess.text)

    top = guesses[0].text
    variants = [v for v in normalise_candidates(top) if v != top][:MAX_VARIANTS]
    if not variants:
        return
    # PHASE3-REPLACED (C3: plate_ocr repairs L->4 and A->4 too):
    # group_label("Did you mean — OCR confusions O↔0, I↔1, B↔8, S↔5, Z↔2, G↔6")
    group_label("Did you mean — OCR confusions O↔0, I↔1, B↔8, S↔5, Z↔2, G↔6, L→4, A→4")
    with st.container(horizontal=True, key="ocr_variants"):
        for offset, variant in enumerate(variants, start=MAX_SHOWN):
            if st.button(variant, key=f"ocr_pick_{offset}"):
                _pick(variant)


# PHASE3-ADDED (C2: make, model and colour from the photo, compared with the record of the plate picked)
def _vehicle_caption(image_bytes: bytes, box: list) -> None:
    """Say what the photo shows of the car; keep it for `render_photo_check` once a plate is queried."""
    import hashlib  # noqa: PLC0415

    key = hashlib.sha1(image_bytes).hexdigest()
    cache = st.session_state.setdefault("ocr_vision", {})
    if key not in cache:
        try:
            with st.spinner("Looking at the car…"):
                cache[key] = vehicle_vision.analyse(image_bytes, box)
        except vehicle_vision.VisionUnavailable as exc:
            cache[key] = exc
    view = cache[key]
    if isinstance(view, Exception):
        st.session_state.pop("photo_vehicle", None)
        st.caption(f"Make and colour are not inferred on this machine ({view}).")
        return
    st.session_state["photo_vehicle"] = view
    st.caption(("The photo shows a " if view.kind == "vehicle" else "") + view.words() + ".")


def photo_check(view: "vehicle_vision.VehicleView", profile: dict) -> list:
    """(attribute, seen in the photo, on record, agrees) for each attribute the photo is sure of."""
    from mediator import plate_resolve  # noqa: PLC0415

    out = []
    for attr, key in (("make", "vehicle_make"), ("model", "vehicle_model"), ("colour", "vehicle_colour")):
        seen, held = getattr(view, attr), profile.get(key)
        if seen and held:
            out.append((attr, seen, held, plate_resolve._agrees(seen, held)))
    return out


def render_photo_check(profile: dict) -> None:
    """On the Investigate page: does the car in the uploaded photo look like the vehicle on record?"""
    view = st.session_state.get("photo_vehicle")
    if view is None or view.kind != "vehicle":
        return
    rows = photo_check(view, profile)
    if not rows:
        return
    differ = [f"{a}: photo {s}, record {h}" for a, s, h, ok in rows if not ok]
    if len(differ) >= 2:
        st.error("The photo does not look like this vehicle (" + "; ".join(differ) + "): possible cloned plate "
                 "or wrong plate read. Check before acting.")
    elif differ:
        st.warning("The photo differs from the record in one attribute (" + differ[0] + "). Cameras misjudge "
                   "colour in some light, so one difference alone is not a clone signal.")
    else:
        st.success("The photo matches the vehicle on record: " + ", ".join(f"{a} {s}" for a, s, _, _ in rows) + ".")
# PHASE3-END
