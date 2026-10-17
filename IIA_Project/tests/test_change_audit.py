"""Workstream G item 4: the "watch the data change" helpers and the admin SQL audit trail.

Offline tests use a tmp meta.db; live tests use the session `local_cluster` (a tmp copy of the
catalog pinned to 127.0.0.1) and undo every write they make.
"""
from __future__ import annotations

import sqlite3

import pytest

from mediator import catalog, change_audit, cleansing, lexicons


@pytest.fixture()
def tmp_meta(tmp_path, monkeypatch):
    path = tmp_path / "meta.db"
    monkeypatch.setattr(catalog, "META_DB_PATH", str(path))
    return path


def test_empty_meta_has_no_statements(tmp_meta):
    assert change_audit.last_admin_statements() == []


def test_legacy_cleansing_rows_top_up_and_admin_log_wins(tmp_meta):
    with cleansing._db() as conn:
        conn.execute("INSERT INTO CLEANSING_LOG (source_id, record_id, field, action, applied_at, sql_sent) "
                     "VALUES ('CAM', '7', 'observed_colour', 'set', '2026-10-01T00:00:00+00:00', 'UPDATE t SET c = 1')")
    out = change_audit.last_admin_statements()
    assert [(r["log"], r["sql"]) for r in out] == [("CLEANSING_LOG", "UPDATE t SET c = 1")]

    class Spec:
        source_id = "CAM"
    cleansing._log_admin(Spec, "pytest", "DELETE FROM t WHERE k = 1", 200, 1, None)
    out = change_audit.last_admin_statements()
    assert out[0]["sql"] == "DELETE FROM t WHERE k = 1" and out[0]["origin"] == "pytest"
    assert out[1]["log"] == "CLEANSING_LOG"  # older history still listed under the new log


def test_log_admin_never_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(catalog, "META_DB_PATH", str(tmp_path / "no" / "such" / "dir" / "meta.db"))

    class Spec:
        source_id = "CAM"
    cleansing._log_admin(Spec, "pytest", "SELECT 1", 200, 0, None)  # unwritable meta.db: swallowed


def test_live_rows_and_exact_sql_of_an_insert_delete(local_cluster):
    cam = change_audit._specs()["cam"]
    pk = lexicons.primary_key(cam)
    assert change_audit.capture_row(911)[pk] == 911
    assert change_audit.capture_row(10 ** 9) is None
    reg_row = change_audit.registration_row("dl 05 cd 9876")  # any spelling finds the stored row
    assert reg_row is not None and "DL05CD9876" in [str(v) for v in reg_row.values()]
    assert change_audit.registration_row("ZZ99ZZ9999") is None and change_audit.registration_row("") is None

    template = change_audit.capture_row(911)
    new_id = max(int(r[pk]) for r in lexicons.fetch_all(cam, pk)) + 1
    row = {**template, pk: new_id}
    insert = (f"INSERT INTO {cam.table} ({', '.join(row)}) VALUES "
              f"({', '.join(lexicons.sql_literal(v) for v in row.values())})")
    delete = f"DELETE FROM {cam.table} WHERE {pk} = {new_id}"
    try:
        assert cleansing._admin(cam, insert) == 1
        assert change_audit.capture_row(new_id)[pk] == new_id  # the examiner sees the new row at once
    finally:
        assert cleansing._admin(cam, delete) == 1
    assert change_audit.capture_row(new_id) is None
    recent = change_audit.last_admin_statements(2)
    assert [r["sql"] for r in recent] == [delete, insert]
    assert recent[0]["rows_affected"] == 1 and recent[0]["origin"] == "test_live_rows_and_exact_sql_of_an_insert_delete"


def test_undo_stores_its_sql(local_cluster):
    ctx = cleansing.Context.build()
    rid = next(r["record_id"] for r in ctx.records if r["plate_number"] == "DL05CD9B76")
    prop = cleansing.propose(ctx.record(rid), ctx)
    prop.fixes = [f for f in prop.fixes if f.field == "plate_number"]
    done = cleansing.apply(prop, actor="pytest", ctx=ctx)
    try:
        out = cleansing.undo(done[0]["log_id"], actor="pytest")
    finally:
        col = cleansing._column(ctx.cam, "plate_number")
        cleansing._admin(ctx.cam, f"UPDATE {ctx.cam.table} SET {col} = 'DL05CD9B76' WHERE {ctx.pk} = {rid}")
    con = sqlite3.connect(catalog.META_DB_PATH)
    stored = con.execute("SELECT undo_sql FROM CLEANSING_LOG WHERE log_id = ?", (done[0]["log_id"],)).fetchone()[0]
    con.close()
    assert stored == out["sql"] and stored.startswith("UPDATE")
    origins = [r["origin"] for r in change_audit.last_admin_statements(5)]
    assert "undo" in origins and "apply" in origins
