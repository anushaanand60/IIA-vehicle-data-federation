"""SQL Console tab: type SQL, pick one source, watch that agency's own database answer.

Reads go to the wrapper's read-only POST /query. Writes go to POST /admin/sql, which exists only
on a laptop that opted in with <SOURCE_ID>_ADMIN_URL. Either way the statement executes inside the
agency's database over HTTP -- the mediator holds no copy and never writes through /query.
"""
from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # `streamlit run` puts only this file's folder on sys.path
    sys.path.insert(0, str(ROOT))
_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import httpx  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from components import group_label, section  # noqa: E402
from mediator.catalog import get_source_catalog  # noqa: E402

# Loose enough to catch a plate in any of the four house spellings (space or hyphen separated,
# 1-3 letters for the series block) and strict enough not to fire on ordinary SQL identifiers.
_PLATE_RE = re.compile(r"[A-Za-z]{2}[ -]?\d{2}[ -]?[A-Za-z]{1,3}[ -]?\d{4}")
_HISTORY_MAX = 10
_WRITE_TABLE_RE = re.compile(
    r"(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)

TIMEOUT_S = 8.0
# One worked example per agency, in that agency's own dialect of the plate: the point of the demo
# is that no two sources spell the same vehicle the same way.
EXAMPLES = {
    "REG": "SELECT v.registration_no, v.make, v.model, v.colour, v.reg_status, o.full_name\n"
           "FROM VEHICLE_REGISTRATION v JOIN OWNERS o ON v.owner_id = o.owner_id\n"
           "WHERE v.registration_no = 'DL01AB1234'",
    "INS": "SELECT policy_id, vehicle_reg, policy_type, policy_start, policy_until, is_active\n"
           "FROM POLICY_RECORDS WHERE vehicle_reg = 'DL-05-CD-9876'",
    "THEFT": "SELECT incident_id, vehicle_number, fir_no, stolen_flag, case_status\n"
             "FROM CRIME_RECORDS WHERE case_status = 'OPEN'",
    "CAM": "SELECT p.plate_id, p.captured_at, p.observed_make, p.observed_colour, c.location_name\n"
           "FROM PLATE_CAPTURES p JOIN CAMERAS c ON p.camera_id = c.camera_id",
    "PUC": "SELECT cert_no, regn_number, valid_upto, emission_norm FROM POLLUTION_CERT",
}
DEFAULT_EXAMPLE = "SELECT 1"


def _base_url(meta: dict) -> str:
    return str(meta.get("base_url") or "").rstrip("/")


def _fetch_schema(base: str) -> dict | None:
    try:
        r = httpx.get(f"{base}/schema", timeout=3.0, trust_env=False)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None


def _show_schema(base: str) -> None:
    body = _fetch_schema(base)
    if not body:
        st.warning(f"Could not read the published schema from {base}/schema — the source may be down.")
        return
    tables = body.get("tables", {})
    items = tables.items() if isinstance(tables, dict) else ((t.get("table"), t) for t in tables)
    with st.expander("Tables this source publishes", expanded=False):
        for name, spec in items:
            cols = ", ".join(c["name"] for c in spec.get("columns", []))
            st.markdown(f"**{name}** — `{cols}`")


def _unreachable(source_id: str, base: str, exc: Exception) -> None:
    kind = "TIMEOUT" if isinstance(exc, httpx.TimeoutException) else "DOWN"
    st.error(f"{source_id} is {kind}: could not reach {base} ({type(exc).__name__}). "
             f"Nothing was executed; start that laptop's wrapper and try again.")


def extract_plate(sql: str) -> str | None:
    """Best-effort plate literal inside a SQL string, in canonical uppercase-alphanumeric form.

    Looks for the shared plate shape (2 letters, 2 digits, 1-3 letters, 4 digits) regardless of
    which house style (spaces, hyphens, or none) wrote it -- used only to offer a shortcut back
    into Investigate, never to interpret the SQL itself.
    """
    if not sql:
        return None
    m = _PLATE_RE.search(sql)
    if not m:
        return None
    return re.sub(r"[ -]", "", m.group(0)).upper()


def _table_of(write_sql: str) -> str | None:
    m = _WRITE_TABLE_RE.search(write_sql)
    return m.group(1) if m else None


def _count_rows(base: str, table: str) -> int | None:
    """Best-effort `SELECT COUNT(*)` via the read-only /query -- failures here must never block
    or fail the write itself, so every error just means "no before/after number to show"."""
    try:
        r = httpx.post(f"{base}/query", json={"sql": f"SELECT COUNT(*) AS n FROM {table}"},
                       timeout=TIMEOUT_S, trust_env=False)
        if r.status_code != 200:
            return None
        rows = r.json().get("rows") or []
        n = rows[0].get("n") if rows else None
        return int(n) if n is not None else None
    except Exception:
        return None


def _run_select(source_id: str, base: str, sql: str) -> bool:
    try:
        r = httpx.post(f"{base}/query", json={"sql": sql}, timeout=TIMEOUT_S, trust_env=False)
    except Exception as exc:
        _unreachable(source_id, base, exc)
        return False
    if r.status_code != 200:
        st.error(f"Rejected by the wrapper guard: {_error_of(r)}")
        return False
    body = r.json()
    rows = body.get("rows", [])
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    else:
        st.info("The source answered, with zero rows. That is data, not a failure.")
    st.caption(f"{body.get('row_count', len(rows))} rows, {body.get('elapsed_ms')} ms, "
               f"fetched at {body.get('fetched_at')}")
    return True


def _run_write(source_id: str, base: str, sql: str) -> bool:
    # Snapshot the touched table's size before the write so the professor can see the effect of
    # the statement directly, not just trust the reported rows_affected.
    table = _table_of(sql)
    before = _count_rows(base, table) if table else None
    try:
        r = httpx.post(f"{base}/admin/sql", json={"sql": sql}, timeout=TIMEOUT_S, trust_env=False)
    except Exception as exc:
        _unreachable(source_id, base, exc)
        return False
    if r.status_code == 404:
        st.error(f"{source_id} has not enabled writes: set {source_id}_ADMIN_URL on that laptop "
                 f"and restart its wrapper.")
        return False
    if r.status_code != 200:
        st.error(f"Rejected by the wrapper guard: {_error_of(r)}")
        return False
    body = r.json()
    st.success(f"{body.get('rows_affected')} row(s) affected on {body.get('source_id', source_id)} "
               f"at {body.get('executed_at')} — re-run the plate in Investigate to see it live")
    if table:
        after = _count_rows(base, table)
        if before is not None and after is not None:
            st.caption(f"{table.upper()}: {before} → {after} rows")
        else:
            st.caption(f"{table.upper()}: row count unavailable before/after this write.")
    return True


def _error_of(r: httpx.Response) -> str:
    try:
        return str(r.json().get("error", r.text))
    except Exception:
        return r.text


def _push_history(source_id: str, sql: str, kind: str, ok: bool) -> None:
    history = st.session_state.setdefault("sqlc_history", [])
    history.append({
        "source": source_id, "sql": sql, "kind": kind, "ok": ok,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    del history[:-_HISTORY_MAX]  # keep only the last 10; no-op while shorter than that


def _render_history() -> None:
    """Query history with per-entry "Load". Must run before the `sqlc_sql` text_area is
    instantiated below, so a Load click can still set that session-state key this run."""
    history = st.session_state.get("sqlc_history", [])
    if not history:
        return
    with st.expander(f"Query history (last {len(history)})", expanded=False):
        for i, entry in enumerate(reversed(history)):
            c_sql, c_load = st.columns([5, 1])
            status = "OK" if entry["ok"] else "FAILED"
            c_sql.code(f"[{entry['source']} · {entry['kind']} · {status} · {entry['ts']}]\n"
                       f"{entry['sql']}", language="sql")
            if c_load.button("Load", key=f"sqlc_hist_{i}", width="stretch"):
                st.session_state["sqlc_sql"] = entry["sql"]


def _render_requery() -> None:
    plate = st.session_state.get("sqlc_last_write_plate")
    if not plate:
        return
    if st.button("Re-run this plate in Investigate", key="sqlc_requery"):
        st.session_state["selected_plate"] = plate
        st.info(f"Search plate set to {plate} — open the Investigate tab (tab 1) to see it live.")


def render() -> None:
    section("SQL console — one source, its own SQL",
            "The statement runs inside that agency's database through its wrapper. Reads use "
            "the read-only /query; writes use the opt-in /admin/sql. The mediator itself never "
            "writes and holds no copy.")

    catalog = get_source_catalog()
    if not catalog:
        st.warning("No sources are registered in the catalog.", icon=":material/warning:")
        return
    group_label("Source")
    source_id = st.selectbox("Source", list(catalog), key="sqlc_source",
                             label_visibility="collapsed")
    meta = catalog[source_id]
    base = _base_url(meta)
    st.caption(f"{meta.get('display_name', source_id)} · {meta.get('dbms', '?')} · `{base}`")
    if not base:
        st.error(f"{source_id} has no base URL in the catalog.")
        return
    _show_schema(base)

    # The example follows the chosen source, but only when the choice changes: otherwise switching
    # tabs or pressing Run would overwrite whatever the professor has typed.
    if st.session_state.get("sqlc_last_source") != source_id:
        st.session_state["sqlc_sql"] = EXAMPLES.get(source_id, DEFAULT_EXAMPLE)
        st.session_state["sqlc_last_source"] = source_id

    _render_history()  # may set sqlc_sql from a "Load" click -- must run before the widget below

    group_label("Statement")
    sql = st.text_area("SQL", key="sqlc_sql", height=160, label_visibility="collapsed")

    c_read, c_write = st.columns(2)
    run = c_read.button("Run SELECT", key="sqlc_run", type="primary", width="stretch")
    write = c_write.button("Execute write (INSERT / UPDATE / DELETE)", key="sqlc_write",
                           width="stretch")
    if run or write:
        statement = (sql or "").strip()
        if not statement:
            st.error("Nothing to run: the statement is empty.")
        else:
            section("Result", f"Sent to {source_id} at {base}.")
            st.code(statement, language="sql")
            ok = _run_select(source_id, base, statement) if run else _run_write(source_id, base, statement)
            _push_history(source_id, statement, "read" if run else "write", ok)
            st.session_state["sqlc_last_write_plate"] = extract_plate(statement) if (write and ok) else None

    _render_requery()


if __name__ == "__main__":
    st.set_page_config(page_title="SQL Console", page_icon=":material/terminal:", layout="wide")
    render()
