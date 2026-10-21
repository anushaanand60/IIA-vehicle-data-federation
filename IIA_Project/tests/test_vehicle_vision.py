"""PHASE3-ADDED (C2: make, model and colour from the photo): gate and abstention, with a fake CLIP."""
from __future__ import annotations

import io

import pytest
from PIL import Image

from mediator import vehicle_vision as vv


def _png(w: int = 400, h: int = 400) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, format="PNG")
    return buf.getvalue()


def _box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def fake(picks):
    """A scorer that puts p on the prompt containing the earliest of `picks` it has, the rest spread evenly."""
    def score(_image, prompts):
        hit = next((i for k in picks for i, p in enumerate(prompts) if k in p), None)
        if hit is None:
            return [1.0 / len(prompts)] * len(prompts)
        rest = 0.05 / max(1, len(prompts) - 1)
        return [0.95 if i == hit else rest for i in range(len(prompts))]
    return score


def never(_image, _prompts):
    raise AssertionError("a close-up must not run the vision model")


def test_a_plate_close_up_is_recognised_without_the_model():
    view = vv.analyse(_png(), _box(10, 100, 390, 300), scorer=never)
    assert view.kind == "plate_closeup" and view.make is None and "close-up" in view.words()


def test_a_whole_car_gives_make_model_and_colour():
    view = vv.analyse(_png(), _box(176, 302, 304, 326), scorer=fake(["car on a road", "Hyundai i20", "Hyundai", "white"]))
    assert (view.kind, view.make, view.model, view.colour) == ("vehicle", "Hyundai", "i20", "White")
    assert view.words() == "white Hyundai i20"


def test_clip_can_still_call_it_a_close_up():
    view = vv.analyse(_png(), (), scorer=fake(["close-up"]))
    assert view.kind == "plate_closeup"


def test_an_unsure_model_abstains():
    view = vv.analyse(_png(), (), scorer=fake(["car on a road"]))   # flat for make, model and colour
    assert view.kind == "vehicle" and (view.make, view.model, view.colour) == (None, None, None)
    assert len(view.notes) == 2 and "not clear enough" in view.words()


def test_a_model_under_another_make_is_left_out():
    view = vv.analyse(_png(), (), scorer=fake(["car on a road", "Kia Seltos", "Hyundai car", "red"]))
    assert view.make == "Hyundai" and view.model is None and any("disagrees" in n for n in view.notes)


@pytest.mark.parametrize("box, closeup", [(_box(176, 302, 304, 326), False), (_box(0, 0, 300, 100), True)])
def test_close_up_gate_geometry(box, closeup):
    assert vv.is_closeup((447, 447), box) is closeup
