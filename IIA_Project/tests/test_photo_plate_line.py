"""PHASE3-ADDED (C2 + C3): reading the plate out of a photo of the whole car, and checking the car against the record."""
from __future__ import annotations

from mediator import plate_ocr
from mediator.vehicle_vision import VehicleView
from scripts.match_plate import plate_line, run_ocr, union_box
from app.tabs.ocr_upload import photo_check


def _e(x0, y0, x1, y1, text, conf=0.9):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]], text, conf


PLATE = _e(176, 302, 304, 326, "KL L8 S 5665", 0.95)   # the real read of a Kerala i20's front plate
BADGE = _e(200, 250, 240, 262, "HYUNDAI", 0.5)          # above the plate, not on its line
STICKER = _e(60, 0, 120, 20, "DEALER 1234")             # repairs to plate shape (DE44ER1234), but no state DE
EMBLEM = _e(150, 302, 172, 326, "IND")                  # printed beside every HSRP number


def test_the_plate_line_is_the_real_plate_not_the_sticker_badge_or_emblem():
    assert [e[1] for e in plate_line([STICKER, BADGE, PLATE, EMBLEM])] == ["KL L8 S 5665"]


def test_a_plate_split_into_two_boxes_stays_whole():
    left, right = _e(0, 0, 50, 20, "DL05", 0.6), _e(60, 0, 120, 20, "CD 9B76", 0.8)
    assert len(plate_line([right, left])) == 2
    assert union_box([left, right]) == [[0, 0], [120, 0], [120, 20], [0, 20]]


class _Reader:
    def readtext(self, image, allowlist=None):
        return [STICKER, BADGE, PLATE, EMBLEM]


def test_run_ocr_on_a_car_photo_reads_only_the_plate_and_returns_its_box():
    ocr = run_ocr(b"not-an-image", reader=_Reader())
    assert ocr["raw"] == "KLL8S5665" and ocr["repaired"][0] == "KL48S5665"
    assert ocr["box"] == [[176, 302], [304, 302], [304, 326], [176, 326]]


# PHASE3-REPLACED (C2 dataset: A->4 removed -- in the HSRP font a 1 is read as A, and the repair made it 4):
# def test_an_l_or_a_where_a_digit_belongs_is_repaired_to_4():
#     assert plate_ocr.normalise_candidates("KLL8S5665")[0] == "KL48S5665"
#     assert plate_ocr.normalise_candidates("MHA2AB1234")[0] == "MH42AB1234"
def test_an_l_where_a_digit_belongs_is_repaired_to_4_but_an_a_is_left_to_the_matcher():
    assert plate_ocr.normalise_candidates("KLL8S5665")[0] == "KL48S5665"
    assert "DL04EF0297" not in plate_ocr.normalise_candidates("DLOAEF0297")  # the A is a misread 1, not a 4


def test_the_photo_is_compared_with_the_record():
    view = VehicleView("vehicle", make="Hyundai", model="i20", colour="White")
    rows = photo_check(view, {"vehicle_make": "Hyundai", "vehicle_model": "Creta", "vehicle_colour": "white"})
    assert [(a, ok) for a, _, _, ok in rows] == [("make", True), ("model", False), ("colour", True)]
