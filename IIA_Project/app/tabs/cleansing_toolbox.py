"""Examiner toolbox for the Data Cleansing page: insert, modify and delete a camera capture.

These writes stand in for the camera agency changing its own data, so they go where the agency's
own console goes: the CAM wrapper's `/admin/sql`, through `cleansing._admin` (the admin-call helper
cleansing already uses; the Source Editor's and SQL Console's helpers draw Streamlit messages and
cannot be called from here). Tables, columns, the primary key and the camera foreign key all come
from the registry entry and the wrapper's `/schema`, never from this file. Keys: `dc_tb_`.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

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

EDITABLE = ("observed_colour", "observed_make", "observed_model", "plate_number", "last_seen_time",
            "ocr_confidence")
NUMERIC = {"ocr_confidence"}


def cam_spec() -> Any:
    """The observational camera source, found by role in the registry (as cleansing finds it)."""
    registry = load_registry(catalog.META_DB_PATH)
    cam = next((s for s in registry.sources if s.authority == "observational" and "observed_colour" in s.covers), None)
    if cam is None:
        raise lexicons.SourceUnavailable("the registry names no observational camera source")
    return cam


def _camera_join(cam: Any) -> Tuple[Any, str, str]:
    """(join, foreign-key column on the capture table, key column on the camera table)."""
    loc_table = next(m.source_table for m in cam.attribute_map if m.global_attr == "last_seen_location")
    join = next(j for j in cam.joins if j.table.lower() == loc_table.lower())
    left, right = join.left.split("."), join.right.split(".")
    fk, key = (left[1], right[1]) if left[0].lower() == cam.table.lower() else (right[1], left[1])
    return join, fk, key


def cameras(cam: Any) -> List[Dict[str, Any]]:
    join, _, key = _camera_join(cam)
    loc = next(m.source_attr for m in cam.attribute_map if m.global_attr == "last_seen_location")
    rows = lexicons._post(cam, f"SELECT {key}, {loc} FROM {join.table} ORDER BY {key}", None).get("rows", [])
    return [{"id": r[key], "location": r[loc]} for r in rows]


def _id(record_id: Any) -> Any:
    text = str(record_id).strip()
    return int(text) if text.isdigit() else text  # a number stays a number in the SQL literal


def insert_capture(cam: Any, camera_id: Any, values: Dict[str, Any]) -> Tuple[int, str]:
    pk = lexicons.primary_key(cam)
    rows = lexicons._post(cam, f"SELECT MAX({pk}) AS m FROM {cam.table}", None)["rows"]
    new_id = int((rows[0].get("m") if rows else 0) or 0) + 1  # MAX+1: no sequence is portable across 3 DBMSs
    _, fk, _ = _camera_join(cam)
    cols = [pk, fk] + [cleansing._column(cam, g) for g in values]
    vals = [new_id, camera_id] + list(values.values())
    sql = (f"INSERT INTO {cam.table} ({', '.join(cols)}) VALUES "
           f"({', '.join(lexicons.sql_literal(v) for v in vals)})")
    if cleansing._admin(cam, sql) != 1:
        raise cleansing.CleansingError(f"{cam.source_id} inserted nothing")
    return new_id, sql


def modify_capture(cam: Any, record_id: Any, field: str, value: Any) -> Tuple[int, str]:
    pk = lexicons.primary_key(cam)
    if field in NUMERIC:
        value = float(value)
    sql = (f"UPDATE {cam.table} SET {cleansing._column(cam, field)} = {lexicons.sql_literal(value)} "
           f"WHERE {pk} = {lexicons.sql_literal(_id(record_id))}")
    return cleansing._admin(cam, sql), sql


def delete_capture(cam: Any, record_id: Any) -> Tuple[int, str]:
    pk = lexicons.primary_key(cam)
    sql = f"DELETE FROM {cam.table} WHERE {pk} = {lexicons.sql_literal(_id(record_id))}"
    return cleansing._admin(cam, sql), sql


def _write(after_write: Callable[[str, Any], None], what: str, action: Callable[[], Tuple[Any, str]],
           capture: Any = None) -> None:
    try:
        result, sql = action()
    except (cleansing.CleansingError, lexicons.SourceUnavailable, ValueError) as exc:
        st.error(f"Refused: {exc}")
        return
    if result == 0:
        st.warning(f"{what}: no such capture, nothing changed.")
        return
    after_write(f"{what} — sent to CAM: {sql}", capture if capture is not None else result)


def render(after_write: Callable[[str, Any], None]) -> None:
    section("Examiner toolbox", "Insert, modify or delete a camera capture in CAM's own database, through its "
            "/admin/sql. Each write is followed by a fresh scan, so its effect shows at once.")
    try:
        cam = cam_spec()
        cams = cameras(cam)
    except Exception as exc:  # a dead camera source disables the toolbox, it does not break the page
        st.error(f"The camera source cannot be reached: {exc}")
        return
    ins, mod, dele = st.columns(3)
    with ins:
        group_label("Insert a capture")
        plate = st.text_input("Plate as read", "DL01AB1Z34", key="dc_tb_plate")
        camera = st.selectbox("Camera", [c["id"] for c in cams], key="dc_tb_camera",
                              format_func=lambda i: f"{i} — {next(c['location'] for c in cams if c['id'] == i)}")
        when = st.text_input("Captured at", datetime.now().isoformat(timespec="seconds"), key="dc_tb_time")
        make = st.text_input("Make", "Maruti Suzuki", key="dc_tb_make")
        model = st.text_input("Model", "Swift", key="dc_tb_model")
        colour = st.text_input("Colour", "White", key="dc_tb_colour")
        conf = st.number_input("OCR confidence", 0.0, 1.0, 0.9, 0.01, key="dc_tb_conf")
        if st.button("Insert", key="dc_tb_insert", type="primary", disabled=not plate.strip()):
            _write(after_write, "Inserted a capture", lambda: insert_capture(cam, camera, {
                "plate_number": plate.strip(), "last_seen_time": when.strip(), "observed_make": make.strip() or None,
                "observed_model": model.strip() or None, "observed_colour": colour.strip() or None,
                "ocr_confidence": float(conf)}))
    with mod:
        group_label("Modify one field")
        rid = st.text_input("Capture id", "", key="dc_tb_mod_id")
        field = st.selectbox("Field", EDITABLE, key="dc_tb_mod_field")
        value = st.text_input("New value", "", key="dc_tb_mod_value")
        if st.button("Modify", key="dc_tb_modify", disabled=not rid.strip()):
            _write(after_write, f"Modified {field} of capture {rid.strip()}",
                   lambda: modify_capture(cam, rid, field, value), capture=rid.strip())
    with dele:
        group_label("Delete a capture")
        did = st.text_input("Capture id", "", key="dc_tb_del_id")
        if st.button("Delete", key="dc_tb_delete", disabled=not did.strip()):
            _write(after_write, f"Deleted capture {did.strip()}", lambda: delete_capture(cam, did), capture="")
