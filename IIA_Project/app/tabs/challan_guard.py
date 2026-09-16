"""Challan Guard tab: the operator's queue of ANPR events waiting to become (or not become) fines.

Nothing on this page decides anything by itself. Pressing Verify calls
`mediator.challan_guard.verify`, which queries the live federation and writes its verdict, its
ordered steps and its evidence bundle into `meta.db`. What is drawn here is that record.

Composed on the Visibility primitives in `app/components.py` (Task C1): one `section` per logical
block, `kpi_row` for the headline counts, `styled_table` for the queue, `stepper` for the
verification run and `chip` for a case's status. Status colours come from `theme.TOKENS` and never
from the accent — the accent means "interactive", and a verdict is data. Every widget key carries
the `cg_` prefix because nine tabs share one Streamlit page.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:  # `streamlit run` puts only this file's folder on sys.path
    sys.path.insert(0, str(_ROOT))
_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import streamlit as st  # noqa: E402

from components import (chip, chip_strip, group_label, kpi_row,  # noqa: E402
                        page_head, panel, section, stepper, styled_table)
from mediator import challan_guard  # noqa: E402
from scripts.seed_challan_cases import cameras, seed  # noqa: E402
from theme import TOKENS  # noqa: E402

QUEUE_COLUMNS = ("case_id", "plate_read", "plate_resolved", "location", "captured_at",
                 "status", "verdict", "amount_inr")
HEADINGS = ("Case", "Read as", "Resolved to", "Where", "When", "Status", "Verdict", "Amount ₹")

# A case's state is data, so it wears a data colour: green where no fine was raised, red where one
# was, amber where the guard refused to decide. The accent is never used here.
STATUS_TOKEN = {"CANDIDATE": "undetermined", "HOLD": "suspicious", "ISSUED": "report",
                "REJECTED": "clear", "DISPUTED": "unknown", "UPHELD": "report",
                "CANCELLED": "clear"}
VERDICT_TOKEN = {challan_guard.ISSUE: "report", challan_guard.HOLD: "suspicious",
                 challan_guard.REJECT: "clear"}

KPI_LABELS = (("Candidates", "CANDIDATE"), ("Held", "HOLD"), ("Issued", "ISSUED"),
              ("Rejected", "REJECTED"), ("Wrongful fines prevented", "PREVENTED"))


def status_chip(status: Any, case_id: Any = None) -> str:
    text = str(status or "—").upper()
    label = text if case_id is None else f"#{case_id} · {text}"
    return chip(label, TOKENS[STATUS_TOKEN.get(text, "undetermined")])


def _kpis() -> None:
    counts = challan_guard.summary()
    kpi_row([(label, counts.get(key, 0)) for label, key in KPI_LABELS])
    st.caption("“Wrongful fines prevented” counts challans rejected at verification plus challans "
               "cancelled after a citizen dispute — the number this feature exists to move.")


def _cell(value: Any) -> str:
    return "—" if value in (None, "") else str(value)


def _queue_table(cases: list[dict]) -> None:
    """`components.styled_table` escapes every cell, so colour cannot live inside the table: the
    statuses repeat underneath as a chip strip, tagged with the case number that carries them."""
    styled_table([{head: _cell(case.get(column)) for head, column in zip(HEADINGS, QUEUE_COLUMNS)}
                  for case in cases], columns=list(HEADINGS))
    chip_strip([status_chip(case.get("status"), case.get("case_id")) for case in cases])


def _label(case: dict) -> str:
    return (f"#{case['case_id']} · {case['plate_read']} · {case['captured_at']} · "
            f"{case['status']}")


def _steps(case: dict) -> None:
    steps = (case.get("evidence") or {}).get("steps") or []
    if not steps:
        st.info("Not verified yet. Press Verify to query registration, insurance, police and "
                "camera records live for this sighting.")
        return
    stepper(steps)


def _verdict_line(case: dict) -> None:
    verdict, reason = case.get("verdict"), case.get("reason")
    if not verdict:
        return
    amount = case.get("amount_inr")
    tail = f" (₹{amount})" if verdict == challan_guard.ISSUE and amount else ""
    st.markdown(
        f'<div class="vz-card">{chip(str(verdict), TOKENS[VERDICT_TOKEN.get(str(verdict), "unknown")])} '
        f"<span>{_cell(reason)}{tail}</span></div>",
        unsafe_allow_html=True)


def _detail(case: dict) -> None:
    """The verdict on the left, the evidence it was read off on the right."""
    section(f"Case #{case['case_id']} — read as {case['plate_read']}",
            "The verdict, the steps that produced it, and every source answer it rests on.")
    with st.container(horizontal=True, key="cg_status_row"):
        st.markdown(status_chip(case.get("status")), unsafe_allow_html=True)
        if case.get("plate_resolved") and case["plate_resolved"] != case["plate_read"]:
            st.markdown(f"resolved to **{case['plate_resolved']}**")
    _verdict_line(case)

    steps_col, evidence_col = st.columns([3, 2])
    with steps_col:
        group_label("Verification steps")
        _steps(case)
    with evidence_col:
        group_label("Evidence and history")
        with st.expander("Evidence bundle (what each source said, and when)"):
            st.json(case.get("evidence") or {})
        for event in challan_guard.events(case["case_id"]):
            note = (event.get("evidence") or {}).get("reason") or ""
            st.markdown(f"- `{event['at']}` **{event['event']}** by {event['actor']} {note}")


def _actions(case_id: int) -> None:
    section("Actions",
            "Verify queries every source live for this sighting. The three overrides exist so a "
            "human can disagree with the guard and leave a named, timestamped trace of doing so.")
    verify_col, issue_col, reject_col, hold_col = st.columns(4)
    if verify_col.button("Verify (live)", key="cg_verify", type="primary", width="stretch"):
        with st.spinner("Querying every source for this sighting…"):
            challan_guard.verify(case_id, actor="operator")
        st.rerun()
    if issue_col.button("Issue anyway", key="cg_issue", width="stretch"):
        challan_guard.issue(case_id, "operator", "issued by operator override")
        st.rerun()
    if reject_col.button("Reject", key="cg_reject", width="stretch"):
        challan_guard.reject(case_id, "operator", "rejected by operator override")
        st.rerun()
    if hold_col.button("Hold", key="cg_hold", width="stretch"):
        challan_guard.hold(case_id, "operator", "held by operator override")
        st.rerun()


def _new_candidate_form() -> None:
    section("Add a candidate sighting",
            "This is the ANPR event, not a fine: what a camera read, where and when.")
    known = cameras()
    by_label = {f"{c['camera_id']} — {c['location']}": c for c in known}
    left, right = st.columns(2)
    plate = left.text_input("Plate as read", key="cg_new_plate", placeholder="DL05CD9B76")
    camera_label = left.selectbox("Camera", key="cg_new_camera",
                                  options=list(by_label) or ["(no cameras configured)"])
    captured = left.text_input("Captured at (ISO)", key="cg_new_captured",
                               value="2026-09-04T12:00:00")
    make = right.text_input("Observed make", key="cg_new_make", placeholder="Maruti Suzuki")
    colour = right.text_input("Observed colour", key="cg_new_colour", placeholder="Silver")
    confidence = right.number_input("OCR confidence", key="cg_new_conf", min_value=0.0,
                                    max_value=1.0, value=0.9, step=0.01)
    if st.button("File candidate", key="cg_new_submit"):
        if not plate.strip():
            st.warning("Enter the plate exactly as the camera read it.")
            return
        camera = by_label.get(camera_label) or {}
        challan_guard.new_candidate(
            plate_read=plate.strip(), camera_id=camera.get("camera_id", "MANUAL"),
            location=camera.get("location", "manual entry"), lat=camera.get("lat"),
            lon=camera.get("lon"), captured_at=captured.strip(),
            observed_make=make.strip() or None, observed_colour=colour.strip() or None,
            ocr_confidence=float(confidence), actor="operator")
        st.rerun()


def render() -> None:
    """Draw the whole Challan Guard page. Never raises: a broken lookup shows an error, not a stack."""
    page_head("Challan Guard",
              "No e-challan leaves this queue until identity, clone signal, theft status and "
              "insurance on the day of the sighting have all been confirmed live. If a source "
              "cannot be reached, the fine is held rather than guessed.")

    with panel("cg_kpis"):
        section("Where the queue stands")
        try:
            _kpis()
            cases = challan_guard.queue(limit=200)
        except Exception as exc:  # a broken meta.db must not take the whole app down
            st.error(f"Could not read the challan queue: {exc}")
            return

    with panel("cg_queue"):
        section("Sightings waiting on a decision",
                "One row per ANPR event. A case is only a fine once the guard says so.")
        seed_col, refresh_col = st.columns(2)
        if seed_col.button("Seed demo candidates", key="cg_seed", width="stretch"):
            seed()
            st.rerun()
        if refresh_col.button("Refresh", key="cg_refresh", width="stretch"):
            st.rerun()
        if not cases:
            st.info("The queue is empty. Press “Seed demo candidates” for the five demo "
                    "sightings, or file one below.")
        else:
            _queue_table(cases)
            labels = {_label(case): case for case in cases}
            chosen = st.selectbox("Case", key="cg_case", options=list(labels))

    if not cases:
        with panel("cg_new"):
            _new_candidate_form()
        return

    case = labels.get(chosen) or cases[0]
    with panel("cg_actions"):
        _actions(int(case["case_id"]))

    with panel("cg_detail"):
        try:
            _detail(challan_guard.get(int(case["case_id"])) or case)
        except Exception as exc:
            st.error(f"Could not render case #{case['case_id']}: {exc}")

    with panel("cg_new"):
        _new_candidate_form()
