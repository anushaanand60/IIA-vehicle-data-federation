"""SQL Console tab: type SQL, pick one source, watch that agency's own database answer.

Reads go to the wrapper's read-only POST /query. Writes go to POST /admin/sql, which exists only
on a laptop that opted in with <SOURCE_ID>_ADMIN_URL. Either way the statement executes inside the
agency's database over HTTP -- the mediator holds no copy and never writes through /query.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # `streamlit run` puts only this file's folder on sys.path
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from mediator.catalog import get_source_catalog  # noqa: E402

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


def _run_select(source_id: str, base: str, sql: str) -> None:
    try:
        r = httpx.post(f"{base}/query", json={"sql": sql}, timeout=TIMEOUT_S, trust_env=False)
    except Exception as exc:
        _unreachable(source_id, base, exc)
        return
    if r.status_code != 200:
        st.error(f"Rejected by the wrapper guard: {_error_of(r)}")
        return
    body = r.json()
    rows = body.get("rows", [])
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("The source answered, with zero rows. That is data, not a failure.")
    st.caption(f"{body.get('row_count', len(rows))} rows, {body.get('elapsed_ms')} ms, "
               f"fetched at {body.get('fetched_at')}")


def _run_write(source_id: str, base: str, sql: str) -> None:
    try:
        r = httpx.post(f"{base}/admin/sql", json={"sql": sql}, timeout=TIMEOUT_S, trust_env=False)
    except Exception as exc:
        _unreachable(source_id, base, exc)
        return
    if r.status_code == 404:
        st.error(f"{source_id} has not enabled writes: set {source_id}_ADMIN_URL on that laptop "
                 f"and restart its wrapper.")
        return
    if r.status_code != 200:
        st.error(f"Rejected by the wrapper guard: {_error_of(r)}")
        return
    body = r.json()
    st.success(f"{body.get('rows_affected')} row(s) affected on {body.get('source_id', source_id)} "
               f"at {body.get('executed_at')} — re-run the plate in Investigate to see it live")


def _error_of(r: httpx.Response) -> str:
    try:
        return str(r.json().get("error", r.text))
    except Exception:
        return r.text


def render() -> None:
    st.subheader("SQL Console — one source, its own SQL")
    st.caption("Runs on the agency's own database through its wrapper. Reads use the read-only "
               "/query; writes use the opt-in /admin/sql. The mediator itself never writes.")

    catalog = get_source_catalog()
    if not catalog:
        st.warning("No sources are registered in the catalog.", icon=":material/warning:")
        return
    source_id = st.selectbox("Source", list(catalog), key="sqlc_source")
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
    sql = st.text_area("SQL statement", key="sqlc_sql", height=160)

    c_read, c_write = st.columns(2)
    run = c_read.button("Run SELECT", key="sqlc_run", type="primary", use_container_width=True)
    write = c_write.button("Execute write (INSERT/UPDATE/DELETE)", key="sqlc_write",
                           use_container_width=True)
    if not (run or write):
        return
    statement = (sql or "").strip()
    if not statement:
        st.error("Nothing to run: the statement is empty.")
        return
    st.markdown(f"**Sent to {source_id} at `{base}`**")
    st.code(statement, language="sql")
    (_run_select if run else _run_write)(source_id, base, statement)


if __name__ == "__main__":
    st.set_page_config(page_title="SQL Console", page_icon=":material/terminal:", layout="wide")
    render()
