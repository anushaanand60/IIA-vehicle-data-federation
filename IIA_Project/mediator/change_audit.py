"""Watch the data change: the agency's own row before and after an examiner edit (workstream G).

The GUI's "watch the data change" view shows three things side by side:

    capture_row(id)           the camera agency's raw row, as its wrapper returns it now
    registration_row(plate)   the registration authority's raw row for a plate, now
    last_admin_statements()   the exact SQL the mediator most recently sent to any /admin/sql

Rows are read live through each wrapper's read-only /query, never from a cache and never from a
local file, so a row an examiner just changed shows its new values on the next call. Columns are
the source's own (`capture_id`, `plate_id`, ...), not global attribute names: the point of the view
is that the examiner sees the agency's row exactly as the agency stores it. Tables, key columns and
the plate column all come from the registry and the wrapper's /schema, never from this file.

A source that does not answer raises `lexicons.SourceUnavailable` (a refusal with a reason);
`None` always means "the source answered and has no such row".
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from mediator import catalog, cleansing, lexicons
from mediator.contract import SourceSpec
from mediator.executor import render_plate
from mediator.registry_loader import load_registry
from mediator.transforms import norm_plate


def _specs() -> Dict[str, SourceSpec]:
    registry = load_registry(catalog.META_DB_PATH)
    cam = next((s for s in registry.sources if s.authority == "observational" and "observed_colour" in s.covers), None)
    reg = next((s for s in registry.sources if s.identity_authority), None)
    if cam is None or reg is None:
        raise lexicons.SourceUnavailable("the registry names no camera source or no identity authority")
    return {"cam": cam, "reg": reg}


def _literal_id(record_id: Any) -> str:
    text = str(record_id).strip()
    return lexicons.sql_literal(int(text) if text.lstrip("-").isdigit() else text)


def _one(spec: SourceSpec, where: str) -> Optional[Dict[str, Any]]:
    rows = lexicons._post(spec, f"SELECT * FROM {spec.table} WHERE {where}", None).get("rows", [])
    return dict(rows[0]) if rows else None


def capture_row(capture_id: Any) -> Optional[Dict[str, Any]]:
    """The camera source's raw row for one capture, read now, or None if it has no such capture."""
    cam = _specs()["cam"]
    pk = lexicons.primary_key(cam)
    return _one(cam, f"{pk} = {_literal_id(capture_id)}")


def registration_row(plate: str) -> Optional[Dict[str, Any]]:
    """The registration authority's raw row for a plate (any spelling), read now, or None."""
    reg = _specs()["reg"]
    canonical = norm_plate(plate) or ""
    if not canonical:
        return None
    col = cleansing._column(reg, cleansing.PLATE)
    stored = render_plate(canonical, reg.key_predicate.render)  # the source's own plate spelling
    return _one(reg, f"{col} = {lexicons.sql_literal(stored)}")


def last_admin_statements(limit: int = 10) -> List[Dict[str, Any]]:
    """The most recent writes the mediator sent to any source's /admin/sql, newest first.

    ADMIN_LOG holds every call made through `cleansing._admin` (cleansing apply / undo / resolve,
    the examiner toolbox, the scenario runner). Writes logged before ADMIN_LOG existed are topped
    up from CLEANSING_LOG's `sql_sent` / `undo_sql`, so older history is not lost.
    """
    with cleansing._db() as conn:  # creates both tables if this meta.db has never seen a write
        admin = [dict(r) for r in conn.execute("SELECT * FROM ADMIN_LOG ORDER BY admin_id DESC LIMIT ?", (limit,))]
        oldest = conn.execute("SELECT MIN(sent_at) FROM ADMIN_LOG").fetchone()[0]
        legacy = [dict(r) for r in conn.execute(
            "SELECT * FROM CLEANSING_LOG WHERE (? IS NULL OR applied_at < ?) ORDER BY log_id DESC LIMIT ?",
            (oldest, oldest, limit))]
    out = [{"at": a["sent_at"], "source_id": a["source_id"], "origin": a["origin"], "sql": a["sql_text"],
            "http_status": a["http_status"], "rows_affected": a["rows_affected"], "error": a["error"],
            "log": "ADMIN_LOG", "id": a["admin_id"]} for a in admin]
    # Only the forward write of a legacy row: an undo of it, if any, ran after ADMIN_LOG existed and is
    # already one of the ADMIN_LOG rows above (listing undo_sql too would show that write twice).
    for row in legacy:
        if row.get("sql_sent"):
            out.append({"at": row.get("applied_at"), "source_id": row["source_id"],
                        "origin": f"cleansing {row.get('action')}", "sql": row["sql_sent"], "http_status": None,
                        "rows_affected": None, "error": None, "log": "CLEANSING_LOG", "id": row["log_id"]})
    out.sort(key=lambda r: str(r["at"] or ""), reverse=True)
    return out[:limit]
