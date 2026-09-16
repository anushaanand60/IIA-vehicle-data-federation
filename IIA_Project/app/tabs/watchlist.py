"""Watchlist & Alerts tab (Task 2.3).

Two tables and one form: which plates an operator has marked and why, and every alert the mediator
has raised since — the enforcement analogue of the MIB's Operation Tutelage marker list
(docs/FIELD_RESEARCH.md fact 7).

Nothing here queries a source. The alerts were filed by `mediator/watchlist.check()` during a
normal federated query, so this page is a *log* — reading it never contacts a laptop and never
caches a source row.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:  # `streamlit run` puts only this file's folder on sys.path
    sys.path.insert(0, str(_ROOT))
_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from mediator import watchlist  # noqa: E402

ALERT_LIMIT = 50


def _add_form() -> None:
    st.markdown("**Add a plate to the watchlist**")
    c_plate, c_reason, c_button = st.columns([2, 4, 1])
    plate = c_plate.text_input("Plate", key="wl_plate",
                               placeholder="DL05CD9876 / dl-05 cd 9876")
    reason = c_reason.text_input("Reason (recorded with every alert)", key="wl_reason",
                                 placeholder="e.g. repeat uninsured offender, patrol request")
    c_button.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
    if c_button.button("➕ Add", key="wl_add", width="stretch"):
        if not plate.strip():
            st.warning("Enter a plate to watch.")
            return
        # A reason is mandatory in spirit, not in code: an unexplained marker is exactly the kind
        # of enforcement record this project argues against, so the default says so out loud.
        watchlist.add(plate, reason.strip() or "No reason recorded")
        st.rerun()


def _watched_table() -> None:
    rows = watchlist.listing()
    st.markdown(f"**Watched plates ({len(rows)})**")
    if not rows:
        st.caption("No plate is being watched. Adding one makes every later query on it file an "
                   "alert, with the reason you give here.")
        return
    header = st.columns([2, 4, 2, 2, 1])
    for column, label in zip(header, ("Plate", "Reason", "Added by", "Added at", "")):
        column.markdown(f"<span style='color:var(--fm-ink_muted);font-size:0.8rem;'>{label}"
                        "</span>", unsafe_allow_html=True)
    for index, row in enumerate(rows):
        c_plate, c_reason, c_by, c_at, c_rm = st.columns([2, 4, 2, 2, 1])
        c_plate.markdown(f"`{row.get('plate', '')}`")
        c_reason.write(row.get("reason") or "—")
        c_by.write(row.get("added_by") or "—")
        c_at.write(str(row.get("added_at") or "")[:19] or "—")
        if c_rm.button("✕", key=f"wl_rm_{index}", help="Remove from watchlist"):
            watchlist.remove(row.get("plate", ""))
            st.rerun()


def _alert_table() -> None:
    alerts = watchlist.alerts(ALERT_LIMIT)
    st.markdown(f"**Recent alerts ({len(alerts)})**")
    if not alerts:
        st.caption("No alert has been raised yet. Alerts are filed when a watched plate is "
                   "queried, or when any query returns a stolen or scrapped verdict.")
        return
    st.dataframe(
        pd.DataFrame([
            {
                "Raised at": str(a.get("ts") or "")[:19],
                "Plate": a.get("plate"),
                "Reason": a.get("reason"),
                "Decision": a.get("decision"),
                "Seen at (camera)": a.get("seen_at") or "—",
                "Location": a.get("location") or "—",
            }
            for a in alerts
        ]),
        width="stretch",
        hide_index=True,
    )


def render() -> None:
    """Draw the whole Watchlist & Alerts tab. Called once from `app/app.py`."""
    st.subheader("Watchlist & alerts")
    st.caption(
        "A marked plate raises a timestamped alert on every later query, carrying whatever camera "
        "evidence the sighting sources returned — the mediator's analogue of the UK MIB's "
        "Operation Tutelage marker. A stolen or scrapped verdict alerts on its own, watched or not."
    )
    _add_form()
    st.divider()
    _watched_table()
    st.divider()
    _alert_table()
