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

from components import (alert_banner, conflict_panel, decision_banner,  # noqa: E402
                        group_label, page_head, panel, profile_sections, provenance_table,
                        risk_placeholder, section, source_chips)
from mediator import watchlist  # noqa: E402
from mediator.core import run_global_query  # noqa: E402
from mediator.report import file_ministry_report, generate_report_pdf  # noqa: E402
from tabs.ocr_upload import render_ocr_slot  # noqa: E402
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


def _demo_panel() -> None:
    """Five equal buttons, one per story vehicle, each with the case it demonstrates beneath it."""
    with panel("inv_demo"):
        section("Try a demo scenario",
                "Five vehicles that exercise the whole decision space. Each is spelled the way a "
                "human would actually type it, so normalisation is demonstrated rather than dodged.")
        st.session_state.setdefault("selected_plate", DEFAULT_PLATE)
        for column, (plate, description, raw) in zip(st.columns(len(DEMO_PLATES)), DEMO_PLATES):
            if column.button(plate, key=f"inv_demo_{plate}", width="stretch"):
                st.session_state["selected_plate"] = raw
                # The text input is created further down in this same run, so seeding its key
                # here is enough -- no rerun needed, and the widget cannot go stale behind
                # selected_plate.
                st.session_state["inv_plate"] = raw
            column.markdown(
                f'<p style="color:var(--cv-ink2);font-size:0.95rem;margin:6px 0 0;">'
                f"{description}</p>", unsafe_allow_html=True)


def _search_panel() -> tuple[str, str]:
    """Plate + scope, with the photo upload folded away until it is wanted."""
    with panel("inv_search"):
        section("Enter a plate",
                "Any spelling is accepted — the mediator normalises it to the canonical form "
                "before it asks anyone.")
        st.session_state.setdefault("inv_plate", st.session_state["selected_plate"])

        # The OCR slot must render *before* the text input: picking a candidate writes
        # `inv_plate`, and Streamlit forbids setting a widget's key after that widget has been
        # instantiated.
        with st.expander("Or upload a plate photo", expanded=False):
            render_ocr_slot()

        c_plate, c_scope = st.columns([3, 2])
        plate = c_plate.text_input("Plate number, in any format", key="inv_plate")
        scope = c_scope.selectbox("Query scope", SCOPES, key="inv_scope")
    return plate, scope


def _actions(profile: dict, plan_trace: dict) -> None:
    section("Act on this decision",
            "Filing writes the verdict and its evidence bundle to the Ministry audit log. "
            "Watching a plate makes every later query on it raise a timestamped alert.")
    with st.container(horizontal=True, key="inv_actions"):
        file_report = st.button("File report to the Ministry", key="inv_file_report",
                                type="primary")
        # One button, two states -- the marker is a toggle, so the page can never show "add" for
        # a plate that is already watched. The reason travels with every alert the plate raises,
        # which is why an unexplained marker is not offered.
        watched = watchlist.is_watched(profile.get("plate_number") or "")
        reason = st.text_input("Watchlist reason", key="inv_watch_reason",
                               placeholder="why this plate is being watched",
                               label_visibility="collapsed", disabled=watched)
        toggle_watch = st.button(
            "Remove from watchlist" if watched else "Add to watchlist",
            key="inv_watch",
            help="Every later query on a watched plate files a timestamped alert.")
    if toggle_watch:
        if watched:
            watchlist.remove(profile.get("plate_number") or "")
        else:
            watchlist.add(profile.get("plate_number") or "",
                          reason.strip() or "No reason recorded")
        st.rerun()  # re-query so the banner and the button both reflect the new state
    if not file_report:
        return
    report_id = file_ministry_report(profile, plan_trace)
    pdf_path = generate_report_pdf(report_id)
    st.success(f"Report MOT-{report_id:06d} filed in the Ministry audit log and rendered to PDF.")
    with open(pdf_path, "rb") as handle:
        st.download_button(
            label=f"Download the official Ministry audit PDF (MOT-{report_id:06d})",
            data=handle.read(),
            file_name=os.path.basename(pdf_path),
            mime="application/pdf",
            key="inv_download_pdf",
        )


def render() -> None:
    """Draw the whole Investigate page. Called once from `app/app.py`."""
    page_head("Investigate a vehicle",
              "One plate in, one defensible decision out. Every answer below is fetched from the "
              "agencies now — registration, insurance, police and camera — and nothing is copied "
              "into the mediator.")
    _demo_panel()
    plate, scope = _search_panel()
    if not plate:
        st.info("Enter a plate above, or pick one of the demo scenarios, to run a global query.")
        return

    requested_attrs = UC1_ATTRS if "UC1" in scope else ["all"]
    result = run_global_query(plate, requested_attrs)
    profile = result["profile"]
    plan_trace = result["plan_trace"]
    st.session_state["latest_result"] = result  # the Plan Trace page reads this

    with panel("inv_decision"):
        section("Decision", "The verdict, its confidence, and every reason it rests on.")
        decision_banner(profile)
        alert_banner(profile.get("alerts"))  # watchlist / hotlist hits raised by this query
        group_label("Who answered")
        st.caption("A source that returned zero rows still answered: that is data, not a "
                   "failure. A source that is down leaves the answer partial.")
        source_chips(plan_trace.get("sources_detail", {}))

    if is_unknown(profile):  # plate in no asked source: onboard it live (Task 0.5)
        with panel("inv_onboard"):
            render_unknown_plate(profile["plate_number"], profile)

    with panel("inv_profile"):
        section("Integrated vehicle profile",
                "One card per authority, each attribute attributed to the source that supplied it.")
        profile_sections(profile)
        conflict_panel(profile.get("conflicts", []))
        provenance_table(profile.get("provenance", {}))
        risk_placeholder()

    with panel("inv_actions_panel"):
        _actions(profile, plan_trace)
