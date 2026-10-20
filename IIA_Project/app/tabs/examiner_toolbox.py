"""Examiner toolbox, shared by Plate Match and Data Cleansing: insert, modify or delete a camera
record (the noisy data) or a registration (the reference data), live, with an undo for each change.

Every write goes to the owning agency's own database through its wrapper: camera records and
registration edits through `/admin/sql` (via `cleansing._admin`, which also logs them to ADMIN_LOG),
a new registration through `/admin/mutate register`, which also creates its owner row. Before a
modify or delete the row is read live, so the undo puts back exactly what was there. Tables and
columns come from the registry entry, never from this file. Keys: `<prefix>_`.
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

import httpx  # noqa: E402
import streamlit as st  # noqa: E402

from components import group_label, section  # noqa: E402
from mediator import catalog, cleansing, lexicons  # noqa: E402
from mediator.registry_loader import load_registry  # noqa: E402
from tabs.cleansing_toolbox import EDITABLE, NUMERIC, _id, cam_spec, cameras, insert_capture  # noqa: E402

REG_FIELDS = {"vehicle_make": "Make", "vehicle_model": "Model", "vehicle_colour": "Colour", "plate_number": "Plate"}
LOOKALIKE = {"0": "O", "1": "I", "8": "B", "5": "S", "2": "Z", "6": "G"}
CHANGES = "ex_changes"  # session list of changes, newest last; shared by both pages
ERRORS = (cleansing.CleansingError, lexicons.SourceUnavailable, ValueError, httpx.HTTPError)


def reg_spec() -> Any:
    """The registration authority, found by role in the registry (as cleansing finds it)."""
    reg = next((s for s in load_registry(catalog.META_DB_PATH).sources if s.identity_authority), None)
    if reg is None:
        raise lexicons.SourceUnavailable("the registry names no registration authority")
    return reg


def spec_for(source: str) -> Any:
    return cam_spec() if source == "CAM" else reg_spec()


def noisy_read(plate: str) -> str:
    """A typical OCR misread of a plate: the last digit that has a look-alike letter is swapped for it."""
    chars = list(plate)
    for i in range(len(chars) - 1, -1, -1):
        if chars[i] in LOOKALIKE:
            chars[i] = LOOKALIKE[chars[i]]
            break
    return "".join(chars)


def _rows(spec: Any, column: str, value: Any) -> List[Dict[str, Any]]:
    sql = f"SELECT * FROM {spec.table} WHERE {column} = {lexicons.sql_literal(value)}"
    return lexicons._post(spec, sql, None).get("rows", [])


def _insert_sql(spec: Any, row: Dict[str, Any]) -> str:
    return (f"INSERT INTO {spec.table} ({', '.join(row)}) VALUES "
            f"({', '.join(lexicons.sql_literal(v) for v in row.values())})")


def _mutate(spec: Any, action: str, plate: str, params: Dict[str, Any]) -> str:
    r = httpx.post(spec.base_url.rstrip("/") + "/admin/mutate", json={"action": action, "plate": plate, "params": params},
                   timeout=spec.timeout_ms / 1000 + 5, trust_env=False)
    if r.status_code != 200:
        raise cleansing.CleansingError(f"{spec.source_id} refused {action}: HTTP {r.status_code} {r.text[:200]}")
    return str(r.json().get("detail", "done"))


# ------------------------------------------------------------------ the six writes; each returns a change

def register(plate: str, make: str, model: str, colour: str) -> Dict[str, Any]:
    reg, p = reg_spec(), cleansing.norm_plate(plate)
    if not p:
        raise ValueError("type a plate to register")
    if _rows(reg, cleansing._column(reg, "plate_number"), p):
        raise ValueError(f"{p} is already registered")
    detail = _mutate(reg, "register", p, {"owner": "Examiner Test Owner", "make": make, "model": model, "colour": colour})
    read = noisy_read(p)
    return {"source": "REG", "what": f"Registered {p} ({make} {model}, {colour})", "sent": f"/admin/mutate register: {detail}",
            "undo": ("mutate", "unregister", p), "plate": p, "read": read, "make": make, "colour": colour,
            "match": (f"Step 1 now holds {read}, a misread of {p}" if read != p else f"Step 1 now holds {p}")
                     + ": press Match and it should find this car.",
            "clean": f"Camera records of {p} are now checked against this registration."}


def modify_registration(plate: str, attr: str, value: str) -> Dict[str, Any]:
    reg, p = reg_spec(), cleansing.norm_plate(plate)
    plate_col, col = cleansing._column(reg, "plate_number"), cleansing._column(reg, attr)
    rows = _rows(reg, plate_col, p)
    if not rows:
        raise ValueError(f"{p} is not registered")
    new = cleansing.norm_plate(value) if attr == "plate_number" else value.strip()
    if attr == "plate_number" and _rows(reg, plate_col, new):
        raise ValueError(f"{new} is already registered to another vehicle")
    sql =f"UPDATE {reg.table} SET {col} = {lexicons.sql_literal(new)} WHERE {plate_col} = {lexicons.sql_literal(p)}"
    cleansing._admin(reg, sql)
    after = new if attr == "plate_number" else p
    undo = (f"UPDATE {reg.table} SET {col} = {lexicons.sql_literal(rows[0][col])} "
            f"WHERE {plate_col} = {lexicons.sql_literal(after)}")
    label = REG_FIELDS[attr].lower()
    return {"source": "REG", "what": f"Changed the registered {label} of {p}: {rows[0][col]} → {new}", "sent": sql,
            "undo": ("sql", undo), "plate": after, "read": after,
            "match": f"Step 1 now holds {after}: press Match; 'Registered as' shows the new {label}.",
            "clean": f"Camera records of {after} are now checked against the new {label}."}


def delete_registration(plate: str) -> Dict[str, Any]:
    reg, p = reg_spec(), cleansing.norm_plate(plate)
    plate_col = cleansing._column(reg, "plate_number")
    rows = _rows(reg, plate_col, p)
    if not rows:
        raise ValueError(f"{p} is not registered")
    sql = f"DELETE FROM {reg.table} WHERE {plate_col} = {lexicons.sql_literal(p)}"
    cleansing._admin(reg, sql)
    return {"source": "REG", "what": f"Deleted the registration of {p}", "sent": sql,
            "undo": ("sql_many", [_insert_sql(reg, r) for r in rows]), "plate": p, "read": p,
            "match": f"Step 1 now holds {p}: press Match; its own row is gone, so it matches another car or nothing.",
            "clean": f"Camera records of {p} now show 'plate not registered'."}


def insert_camera_record(camera: Any, values: Dict[str, Any]) -> Dict[str, Any]:
    cam = cam_spec()
    new_id, sql = insert_capture(cam, camera, values)
    plate = values.get("plate_number")
    return {"source": "CAM", "what": f"Inserted camera record #{new_id} read as {plate}", "sent": sql,
            "undo": ("sql", f"DELETE FROM {cam.table} WHERE {lexicons.primary_key(cam)} = {new_id}"),
            "capture": new_id, "read": plate, "make": values.get("observed_make"), "colour": values.get("observed_colour"),
            "match": f"Step 1 now holds {plate} with the camera's make and colour: press Match.",
            "clean": f"Record #{new_id} is selected below with what the rules propose."}


def modify_camera_record(record_id: Any, field: str, value: Any) -> Dict[str, Any]:
    cam = cam_spec()
    pk, col = lexicons.primary_key(cam), cleansing._column(cam, field)
    rows = _rows(cam, pk, _id(record_id))
    if not rows:
        raise ValueError(f"there is no camera record #{record_id}")
    new = float(value) if field in NUMERIC else value
    where = f"WHERE {pk} = {lexicons.sql_literal(_id(record_id))}"
    sql = f"UPDATE {cam.table} SET {col} = {lexicons.sql_literal(new)} {where}"
    cleansing._admin(cam, sql)
    plate = new if field == "plate_number" else rows[0].get(cleansing._column(cam, "plate_number"))
    return {"source": "CAM", "what": f"Changed {field} of camera record #{record_id}: {rows[0][col]} → {new}", "sent": sql,
            "undo": ("sql", f"UPDATE {cam.table} SET {col} = {lexicons.sql_literal(rows[0][col])} {where}"),
            "capture": str(record_id).strip(), "read": plate,
            "match": f"Step 1 now holds this record's read {plate}: press Match.",
            "clean": f"Record #{str(record_id).strip()} is selected below with what the rules now propose."}


def delete_camera_record(record_id: Any) -> Dict[str, Any]:
    cam = cam_spec()
    pk = lexicons.primary_key(cam)
    rows = _rows(cam, pk, _id(record_id))
    if not rows:
        raise ValueError(f"there is no camera record #{record_id}")
    sql = f"DELETE FROM {cam.table} WHERE {pk} = {lexicons.sql_literal(_id(record_id))}"
    cleansing._admin(cam, sql)
    return {"source": "CAM", "what": f"Deleted camera record #{record_id}", "sent": sql,
            "undo": ("sql_many", [_insert_sql(cam, r) for r in rows]), "capture": "",
            "match": "Reverse search for its registration no longer lists it.",
            "clean": "It is gone from the next check."}


def undo(change: Dict[str, Any]) -> None:
    spec, (kind, *args) = spec_for(change["source"]), change["undo"]
    if kind == "mutate":
        _mutate(spec, args[0], args[1], {})
    else:
        for sql in (args[0] if kind == "sql_many" else [args[0]]):
            if cleansing._admin(spec, sql) == 0:  # the row changed or went since: say so, never claim an undo
                raise cleansing.CleansingError("the data changed after this edit, so nothing was undone")


# ------------------------------------------------------------------ the panel

def _do(after_write: Callable[..., None], action: Callable[[], Dict[str, Any]]) -> None:
    try:
        change = action()
    except ERRORS as exc:
        st.error(f"Refused: {exc}")
        return
    st.session_state.setdefault(CHANGES, []).append(change)
    st.session_state["ex_last"] = change
    after_write(f"{change['what']} — sent to {change['source']}: {change['sent']}", change.get("capture"))


def _camera_tab(after_write: Callable[..., None], k: str) -> None:
    try:
        cams = cameras(cam_spec())
    except Exception as exc:  # a dead camera source disables this tab, it does not break the page
        st.error(f"The camera source cannot be reached: {exc}")
        return
    group_label("Insert a camera record")
    a, b, c = st.columns(3)
    plate = a.text_input("Plate as read", "DL01AB1Z34", key=f"{k}_plate")
    camera = b.selectbox("Camera", [x["id"] for x in cams], key=f"{k}_camera",
                         format_func=lambda i: f"{i} — {next(x['location'] for x in cams if x['id'] == i)}")
    when = c.text_input("Captured at", datetime.now().isoformat(timespec="seconds"), key=f"{k}_time")
    d, e, f, g = st.columns(4)
    # defaults are a misread of DL01AB1234 seen as its registered car (Hyundai Creta, White), so the default insert
    # shows R5 correcting the plate; the earlier Maruti Suzuki Swift contradicted the registration and R12 held it
    make = d.text_input("Make", "Hyundai", key=f"{k}_make")
    model = e.text_input("Model", "Creta", key=f"{k}_model")
    colour = f.text_input("Colour", "White", key=f"{k}_colour")
    conf = g.number_input("OCR confidence", 0.0, 1.0, 0.9, 0.01, key=f"{k}_conf")
    if st.button("Insert", key=f"{k}_insert", type="primary", disabled=not plate.strip()):
        _do(after_write, lambda: insert_camera_record(camera, {
            "plate_number": plate.strip(), "last_seen_time": when.strip(), "observed_make": make.strip() or None,
            "observed_model": model.strip() or None, "observed_colour": colour.strip() or None,
            "ocr_confidence": float(conf)}))
    mod, dele = st.columns([3, 1])
    with mod:
        group_label("Modify one field")
        m1, m2, m3 = st.columns(3)
        rid = m1.text_input("Record id", "", key=f"{k}_mod_id")
        field = m2.selectbox("Field", EDITABLE, key=f"{k}_mod_field")
        value = m3.text_input("New value", "", key=f"{k}_mod_value")
        if st.button("Modify", key=f"{k}_modify", disabled=not rid.strip()):
            _do(after_write, lambda: modify_camera_record(rid, field, value))
    with dele:
        group_label("Delete a record")
        did = st.text_input("Record id", "", key=f"{k}_del_id")
        if st.button("Delete", key=f"{k}_delete", disabled=not did.strip()):
            _do(after_write, lambda: delete_camera_record(did))


def _registration_tab(after_write: Callable[..., None], k: str) -> None:
    group_label("Register a new vehicle")
    a, b, c, d = st.columns(4)
    plate = a.text_input("Plate", "DL09ZZ4321", key=f"{k}_r_plate")
    make = b.text_input("Make", "Hyundai", key=f"{k}_r_make")
    model = c.text_input("Model", "i20", key=f"{k}_r_model")
    colour = d.text_input("Colour", "Red", key=f"{k}_r_colour")
    if st.button("Register", key=f"{k}_r_insert", type="primary", disabled=not plate.strip()):
        _do(after_write, lambda: register(plate, make.strip(), model.strip(), colour.strip()))
    mod, dele = st.columns([3, 1])
    with mod:
        group_label("Modify a registration")
        m1, m2, m3 = st.columns(3)
        mplate = m1.text_input("Registered plate", "", key=f"{k}_r_mod_plate")
        attr = m2.selectbox("Field", list(REG_FIELDS), key=f"{k}_r_mod_field", format_func=REG_FIELDS.get)
        value = m3.text_input("New value", "", key=f"{k}_r_mod_value")
        if st.button("Modify", key=f"{k}_r_modify", disabled=not (mplate.strip() and value.strip())):
            _do(after_write, lambda: modify_registration(mplate, attr, value))
    with dele:
        group_label("Delete a registration")
        dplate = st.text_input("Registered plate", "", key=f"{k}_r_del_plate")
        if st.button("Delete", key=f"{k}_r_delete", disabled=not dplate.strip()):
            _do(after_write, lambda: delete_registration(dplate))


def _changes(after_write: Callable[..., None], k: str) -> None:
    changes = st.session_state.get(CHANGES, [])
    if not changes:
        return
    group_label("Your changes (newest first)")
    for i in range(len(changes) - 1, max(-1, len(changes) - 6), -1):
        ch = changes[i]
        left, right = st.columns([5, 1])
        left.markdown(f"{ch['source']}: {ch['what']}")
        if right.button("Undo", key=f"{k}_undo_{i}", width="stretch"):
            try:
                undo(ch)
            except ERRORS as exc:
                st.error(f"Undo refused: {exc}")
                return
            changes.pop(i)
            st.session_state["ex_last"] = {**ch, "match": "Undone: the data is back as it was.",
                                           "clean": "Undone: the data is back as it was."}
            after_write(f"Undid: {ch['what']}", ch.get("capture"))


def render(after_write: Callable[..., None], prefix: str) -> None:
    """after_write(message, capture) runs after every write; `st.session_state['ex_last']` holds the change."""
    section("Examiner toolbox", "Insert, modify or delete data live in the agency's own database. "
            "Every change can be undone below.")
    cam_tab, reg_tab = st.tabs(["Camera record (noisy data)", "Registration (reference data)"])
    with cam_tab:
        _camera_tab(after_write, prefix)
    with reg_tab:
        _registration_tab(after_write, prefix)
    _changes(after_write, prefix)
