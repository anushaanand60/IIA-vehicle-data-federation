"""Challan Guard tab: the operator's queue of ANPR events waiting to become (or not become) fines.

Nothing on this page decides anything by itself. Pressing Verify calls
`mediator.challan_guard.verify`, which queries the live federation and writes its verdict, its
ordered steps and its evidence bundle into `meta.db`. What is drawn here is that record.

Plain `st.*` widgets for now; Task C1 restyles this page on the Visibility primitives. Every
widget key carries the `cg_` prefix because nine tabs share one Streamlit page.
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

from mediator import challan_guard  # noqa: E402
from scripts.seed_challan_cases import cameras, seed  # noqa: E402

QUEUE_COLUMNS = ("case_id", "plate_read", "plate_resolved", "location", "captured_at",
                 "status", "verdict", "amount_inr")
HEADINGS = ("Case", "Read as", "Resolved to", "Where", "When", "Status", "Verdict", "Amount ₹")


def _kpis() -> None:
    counts = challan_guard.summary()
    columns = st.columns(5)
    for column, (label, key) in zip(columns, [
            ("Candidates", "CANDIDATE"), ("Held", "HOLD"), ("Issued", "ISSUED"),
            ("Rejected", "REJECTED"), ("Wrongful fines prevented", "PREVENTED")]):
        column.metric(label, counts.get(key, 0))
    st.caption("“Wrongful fines prevented” counts challans rejected at verification plus challans "
               "cancelled after a citizen dispute — the number this feature exists to move.")


def _cell(value: Any) -> str:
    text = "—" if value in (None, "") else str(value)
    return text.replace("|", "\\|")  # a pipe would break the markdown table


def _queue_table(cases: list[dict]) -> None:
    """Markdown, not st.dataframe: the queue is short and the plate text must be selectable."""
    lines = ["| " + " | ".join(HEADINGS) + " |", "|" + "---|" * len(HEADINGS)]
    for case in cases:
        lines.append("| " + " | ".join(_cell(case.get(c)) for c in QUEUE_COLUMNS) + " |")
    st.markdown("\n".join(lines))


def _label(case: dict) -> str:
    return (f"#{case['case_id']} · {case['plate_read']} · {case['captured_at']} · "
            f"{case['status']}")


def _steps(case: dict) -> None:
    steps = (case.get("evidence") or {}).get("steps") or []
    if not steps:
        st.info("Not verified yet. Press Verify to query registration, insurance, police and "
                "camera records live for this sighting.")
        return
    for step in steps:
        mark = "✓" if step.get("done") else "■"
        st.markdown(f"**{mark} {step.get('title')}** — {step.get('detail')}")


def _detail(case: dict) -> None:
    st.markdown(f"### Case #{case['case_id']} — {case['plate_read']}")
    verdict, reason = case.get("verdict"), case.get("reason")
    if verdict == challan_guard.ISSUE:
        st.error(f"ISSUE — {reason} (₹{case.get('amount_inr')})")
    elif verdict == challan_guard.HOLD:
        st.warning(f"HOLD — {reason}")
    elif verdict == challan_guard.REJECT:
        st.success(f"REJECT — {reason}")
    if case.get("status") in ("CANCELLED", "UPHELD", "DISPUTED"):
        st.info(f"Dispute outcome: {case['status']} — {reason}")

    st.markdown("**Verification steps**")
    _steps(case)

    with st.expander("Evidence bundle (what each source said, and when)"):
        st.json(case.get("evidence") or {})

    st.markdown("**Case history**")
    for event in challan_guard.events(case["case_id"]):
        note = (event.get("evidence") or {}).get("reason") or ""
        st.markdown(f"- `{event['at']}` **{event['event']}** by {event['actor']} {note}")


def _actions(case_id: int) -> None:
    st.markdown("**Operator actions**")
    verify_col, issue_col, reject_col, hold_col = st.columns(4)
    if verify_col.button("Verify (live)", key="cg_verify", type="primary", width="stretch"):
        with st.spinner("Querying every source for this sighting…"):
            challan_guard.verify(case_id, actor="operator")
        st.rerun()
    # The three overrides exist because a human must be able to disagree with the guard and leave
    # a named, timestamped trace of having done so — not because the guard is advisory.
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
    st.markdown("### File a new candidate from a sighting")
    st.caption("This is the ANPR event, not a fine: what a camera read, where and when.")
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
    """Draw the whole Challan Guard tab. Never raises: a broken lookup shows an error, not a stack."""
    st.subheader("Challan Guard — verify before you fine")
    st.caption("No e-challan leaves this queue until identity, clone signal, theft status and "
               "insurance on the day of the sighting have all been confirmed live. If a source "
               "cannot be reached, the fine is held rather than guessed.")

    try:
        _kpis()
        cases = challan_guard.queue(limit=200)
    except Exception as exc:  # a broken meta.db must not take the whole app down
        st.error(f"Could not read the challan queue: {exc}")
        return

    seed_col, refresh_col = st.columns(2)
    if seed_col.button("Seed demo candidates", key="cg_seed", width="stretch"):
        seed()
        st.rerun()
    if refresh_col.button("Refresh", key="cg_refresh", width="stretch"):
        st.rerun()

    if not cases:
        st.info("The queue is empty. Press “Seed demo candidates” for the five demo sightings, "
                "or file one below.")
        _new_candidate_form()
        return

    _queue_table(cases)
    labels = {_label(case): case for case in cases}
    chosen = st.selectbox("Case", key="cg_case", options=list(labels))
    case = labels.get(chosen) or cases[0]

    _actions(int(case["case_id"]))
    try:
        _detail(challan_guard.get(int(case["case_id"])) or case)
    except Exception as exc:
        st.error(f"Could not render case #{case['case_id']}: {exc}")

    _new_candidate_form()
