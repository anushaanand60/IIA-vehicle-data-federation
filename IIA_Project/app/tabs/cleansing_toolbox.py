"""Examiner toolbox for the Data Cleansing page: insert, modify and delete a camera capture.

These writes stand in for the camera agency changing its own data, so they go where the agency's
own console goes: the CAM wrapper's `/admin/sql`, through `cleansing._admin` (the one admin-call
helper the mediator already has). Tables, columns, the primary key and the camera foreign key all
come from the registry entry and the wrapper's `/schema`, never from this file. Keys: `dc_tb_`.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import streamlit as st  # noqa: E402

from components import group_label, section  # noqa: E402
from mediator import catalog, cleansing, lexicons  # noqa: E402
from mediator.registry_loader import load_registry  # noqa: E402

EDITABLE = ("plate_number", "last_seen_time", "observed_make", "observed_model", "observed_colour",
            "ocr_confidence")


def cam_spec() -> Any:
    registry = load_registry(catalog.META_DB_PATH)
    return next(s for s in registry.sources if s.authority == "observational" and "observed_colour" in s.covers)


def cameras(cam: Any) -> List[Dict[str, Any]]:
    join = cam.joins[0]
    key = join.right.split(".")[1]
    loc = next(m.source_attr for m in cam.attribute_map if m.global_attr == "last_seen_location")
    rows = lexicons._post(cam, f"SELECT {key}, {loc} FROM {join.table}", None).get("rows", [])
    return [{"id": r[key], "location": r[loc]} for r in rows]


def insert_capture(cam: Any, camera_id: Any, values: Dict[str, Any]) -> int:
    pk = lexicons.primary_key(cam)
    top = lexicons._post(cam, f"SELECT MAX({pk}) AS m FROM {cam.table}", None)["rows"][0]["m"] or 0
    new_id = int(top) + 1
    fk = cam.joins[0].left.split(".")[1]
    cols = [pk, fk] + [cleansing._column(cam, g) for g in values]
    vals = [new_id, camera_id] + list(values.values())
    cleansing._admin(cam, f"INSERT INTO {cam.table} ({', '.join(cols)}) VALUES "
                          f"({', '.join(lexicons.sql_literal(v) for v in vals)})")
    return new_id


def modify_capture(cam: Any, record_id: Any, field: str, value: Any) -> int:
    pk = lexicons.primary_key(cam)
    return cleansing._admin(cam, f"UPDATE {cam.table} SET {cleansing._column(cam, field)} = "
                                 f"{lexicons.sql_literal(value)} WHERE {pk} = {lexicons.sql_literal(record_id)}")


def delete_capture(cam: Any, record_id: Any) -> int:
    pk = lexicons.primary_key(cam)
    return cleansing._admin(cam, f"DELETE FROM {cam.table} WHERE {pk} = {lexicons.sql_literal(record_id)}")


def render(after_write: Callable[[str, Any], None]) -> None:
    section("Examiner toolbox", "Insert, modify or delete a camera capture in CAM's own database. Each "
            "write is followed by a fresh scan, so its effect on the anomaly list shows at once.")
    try:
        cam = cam_spec()
        cams = cameras(cam)
    except Exception as exc:
        st.error(f"The camera source cannot be reached: {exc}")
        return
    ins, mod, dele = st.columns(3)
    with ins:
        group_label("Insert a capture")
        plate = st.text_input("Plate as read", "DL01AB1Z34", key="dc_tb_plate")
        cam_ix = st.selectbox("Camera", range(len(cams)), key="dc_tb_camera",
                              format_func=lambda i: f"{cams[i]['id']} — {cams[i]['location']}")
        when = st.text_input("Captured at", datetime.now().isoformat(timespec="seconds"), key="dc_tb_time")
        make = st.text_input("Make", "Maruti Suzuki", key="dc_tb_make")
        model = st.text_input("Model", "Swift", key="dc_tb_model")
        colour = st.text_input("Colour", "White", key="dc_tb_colour")
        conf = st.number_input("OCR confidence", 0.0, 1.0, 0.9, 0.01, key="dc_tb_conf")
        if st.button("Insert", key="dc_tb_insert", type="primary"):
            new_id = insert_capture(cam, cams[cam_ix]["id"], {
                "plate_number": plate, "last_seen_time": when, "observed_make": make,
                "observed_model": model, "observed_colour": colour, "ocr_confidence": float(conf)})
            after_write(f"Inserted capture {new_id}", new_id)
    with mod:
        group_label("Modify a field")
        rid = st.text_input("Capture id", "", key="dc_tb_mod_id")
        field = st.selectbox("Field", EDITABLE, key="dc_tb_mod_field")
        value = st.text_input("New value", "", key="dc_tb_mod_value")
        if st.button("Modify", key="dc_tb_modify"):
            n = modify_capture(cam, rid, field, value)
            after_write(f"Modified {field} of capture {rid} ({n} row)", rid)
    with dele:
        group_label("Delete a capture")
        did = st.text_input("Capture id", "", key="dc_tb_del_id")
        if st.button("Delete", key="dc_tb_delete"):
            n = delete_capture(cam, did)
            after_write(f"Deleted capture {did} ({n} row)", None)
