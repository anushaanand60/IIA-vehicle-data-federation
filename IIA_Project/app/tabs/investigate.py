"""Investigate tab: one plate in, one defensible decision out.

The whole page is a straight line — search hero → decision → who answered → the integrated
profile → conflicts → provenance → what you can do about it — so the demo can be narrated in that
order. Nothing here queries a source directly: `run_global_query` owns planning, federation and
integration, and `app/components.py` owns how the answer looks.

Session-state contract with the rest of the app (unchanged from the old inline tab):
  * `selected_plate`  — the plate to investigate; the Source Editor sidebar, the SQL console and
    the onboarding wizard all hand a plate back through this key.
  * `latest_result`   — the last `{"profile", "plan_trace"}`; the Plan Trace tab reads it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:  # `streamlit run` puts only this file's folder on sys.path
    sys.path.insert(0, str(_ROOT))
_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import streamlit as st  # noqa: E402

from components import (conflict_panel, decision_banner, profile_sections,  # noqa: E402
                        provenance_table, risk_placeholder, source_chips)
from mediator.core import run_global_query  # noqa: E402
from mediator.report import file_ministry_report, generate_report_pdf  # noqa: E402
from tabs.onboarding import is_unknown, render_unknown_plate  # noqa: E402

DEFAULT_PLATE = "DL01AB1234"

# (canonical plate, what the scenario demonstrates, the plate as a human would actually type it).
# The third element is deliberately spelled a different way each time: normalisation to the
# canonical key is the mediator's job, and the demo should exercise it rather than dodge it.
DEMO_PLATES: list[tuple[str, str, str]] = [
    ("DL01AB1234", "Clean / clear", "DL01AB1234"),
    ("DL05CD9876", "Expired insurance", "DL-05-cd-9876"),
    ("HR26EF4455", "Stolen vehicle", "hr 26 ef 4455"),
    ("UP16GH1122", "Cloned plate / conflict", "UP16-GH-1122"),
    ("MH12IJ7788", "Unregistered, camera only", "mh12ij7788"),
]

SCOPES = [
    "UC2: Full Vehicle Profile (all sources)",
    "UC1: Insurance Verification Only (INS + REG)",
]
UC1_ATTRS = ["insurance_status", "insurance_expiry"]


def _search_hero() -> tuple[str, str]:
    """Plate + scope + demo chips + the slot Task 2.1's OCR upload drops into."""
    st.markdown("**Quick demo scenarios (named test plates)**")
    demo_cols = st.columns(len(DEMO_PLATES))
    st.session_state.setdefault("selected_plate", DEFAULT_PLATE)

    for column, (plate, description, raw) in zip(demo_cols, DEMO_PLATES):
        if column.button(f"**{plate}**\n\n_{description}_", key=f"inv_demo_{plate}",
                         width="stretch"):
            st.session_state["selected_plate"] = raw
            # The text input is created further down in this same run, so seeding its key here is
            # enough -- no rerun needed, and the widget cannot go stale behind selected_plate.
            st.session_state["inv_plate"] = raw

    st.session_state.setdefault("inv_plate", st.session_state["selected_plate"])

    c_plate, c_scope = st.columns([3, 2])
    plate = c_plate.text_input("License plate number (any format)", key="inv_plate")
    scope = c_scope.selectbox("Query scope", SCOPES, key="inv_scope")
    st.container(key="inv_ocr_slot")  # Task 2.1 renders the photo upload here
    return plate, scope


def _actions(profile: dict, plan_trace: dict) -> None:
    with st.container(horizontal=True, key="inv_actions"):
        file_report = st.button("📑 File report to Ministry", key="inv_file_report",
                                type="secondary")
        st.button("👁️ Add to watchlist", key="inv_watch", disabled=True,
                  help="coming in Task 2.3")
    if not file_report:
        return
    report_id = file_ministry_report(profile, plan_trace)
    pdf_path = generate_report_pdf(report_id)
    st.success(f"Report MOT-{report_id:06d} filed in the Ministry audit log and rendered to PDF.")
    with open(pdf_path, "rb") as handle:
        st.download_button(
            label=f"⬇️ Download official Ministry audit PDF (MOT-{report_id:06d})",
            data=handle.read(),
            file_name=os.path.basename(pdf_path),
            mime="application/pdf",
            key="inv_download_pdf",
        )


def render() -> None:
    """Draw the whole Investigate tab. Called once from `app/app.py`."""
    st.subheader("Vehicle compliance investigation")
    plate, scope = _search_hero()
    if not plate:
        st.info("Enter a plate above, or pick one of the demo scenarios, to run a global query.")
        return

    requested_attrs = UC1_ATTRS if "UC1" in scope else ["all"]
    result = run_global_query(plate, requested_attrs)
    profile = result["profile"]
    plan_trace = result["plan_trace"]
    st.session_state["latest_result"] = result  # the Plan Trace tab reads this

    decision_banner(profile)
    source_chips(plan_trace.get("sources_detail", {}))

    if is_unknown(profile):  # plate in no asked source: onboard it live (Task 0.5)
        render_unknown_plate(profile["plate_number"], profile)

    profile_sections(profile)
    conflict_panel(profile.get("conflicts", []))
    provenance_table(profile.get("provenance", {}))
    risk_placeholder()
    _actions(profile, plan_trace)
