"""Task 2.4: citizen self-check — the askMID analogue (FIELD_RESEARCH.md fact 5).

A citizen enters their own plate and gets back only what they need to know about their own
vehicle: is it registered, is it insured, does its PUC certificate hold, has it been reported
stolen. Never the owner's name, never camera locations or timestamps, never a raw row from any
source — this page reads the same integrated `VEHICLE_PROFILE` the Investigate tab does, but shows
a hand-picked subset of it.

Data minimisation is the point, not a footnote: the page requests only the five attributes it
needs (`REQUESTED_ATTRS`), so `mediator/planner.py`'s attribute-driven source selection (Task 3.3
outcome) asks fewer sources than a full profile query would — CAM in particular is never asked,
because none of these attributes are covered by it. The caption at the bottom says exactly which
sources were asked and which were not, so that claim is checkable on screen rather than taken on
faith.

Session-state contract: none. This tab does not read or write `selected_plate` /
`latest_result` — those belong to the Investigate tab's full-profile flow; a citizen's own lookup
here does not need to interact with the demo/investigator surface.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:  # `streamlit run` puts only this file's folder on sys.path
    sys.path.insert(0, str(_ROOT))
_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import streamlit as st  # noqa: E402

from components import chip, chip_strip, group_label, kpi_row, section  # noqa: E402
from mediator import challan_guard  # noqa: E402
from mediator.catalog import get_source_catalog  # noqa: E402
from mediator.core import run_global_query  # noqa: E402
from mediator.decide import REFERENCE_TODAY  # noqa: E402
from theme import TOKENS, status_class  # noqa: E402

# The safe, own-vehicle subset (Task 2.4). Each of these has a covering source in the catalog
# (registration_status -> REG, insurance_expiry/insurance_status -> INS, stolen_status -> THEFT,
# puc_expiry -> PUC when registered); none of them route to CAM, which is exactly the point.
REQUESTED_ATTRS = [
    "registration_status",
    "insurance_status",
    "insurance_expiry",
    "puc_expiry",
    "stolen_status",
]

UNAVAILABLE = "could not verify right now (source unavailable)"
_DOWN = ("DOWN", "TIMEOUT", "ERROR")


# ------------------------------------------------------------------ source chips
# `components.source_chips` renders under the fixed key `fm_chip_row`, which the Investigate tab
# already claims on the same page, so this tab builds its own strip from `components.chip` — one
# markdown block, therefore no container key to collide.

def _chip_row(sources_detail: dict, asked: list[str], all_sources: list[str]) -> None:
    chips = []
    for source_id in asked:
        status = str((sources_detail.get(source_id) or {}).get("status") or "DOWN").upper()
        chips.append(chip(f"{source_id} · {status}", TOKENS[status_class(status)]))
    chips += [chip(f"{source_id} · NOT ASKED", TOKENS["undetermined"])
              for source_id in all_sources if source_id not in asked]
    chip_strip(chips)


# ------------------------------------------------------------------ per-question rendering
# Each answer is returned as a (label, value) pair rather than drawn, so the four of them render as
# one `kpi_row` grid — one block, one look, instead of four differently-sized metric cards.

def _registration_line(profile: dict, availability: dict) -> tuple[str, str]:
    status = profile.get("registration_status")
    reg_avail = availability.get("REG")
    if status:
        return ("Registration status", str(status).upper())
    if reg_avail in _DOWN or "REG" not in availability:
        return ("Registration status", UNAVAILABLE)
    return ("Registration status", "NOT REGISTERED")


def _insurance_line(profile: dict, availability: dict) -> tuple[str, str]:
    ins_status = profile.get("insurance_status")
    expiry = profile.get("insurance_expiry")
    if ins_status in (None, "UNKNOWN"):
        if availability.get("INS") in _DOWN or "INS" not in availability:
            return ("Insurance validity", UNAVAILABLE)
        return ("Insurance validity", "no policy on record")
    if ins_status in ("VALID", "EXPIRED"):
        return ("Insurance validity",
                f"{ins_status}" + (f" (expires {expiry})" if expiry else ""))
    return ("Insurance validity", "no policy on record")


def _puc_line(profile: dict, availability: dict) -> tuple[str, str] | None:
    """PUC only appears when a PUC source is in the catalog (Task 2.4: "if PUC in catalog")."""
    if "PUC" not in get_source_catalog():
        return None
    expiry = profile.get("puc_expiry")
    if availability.get("PUC") in _DOWN:
        return ("PUC validity", UNAVAILABLE)
    if not expiry:
        return ("PUC validity", "no certificate on record")
    try:
        exp_date = datetime.strptime(str(expiry), "%Y-%m-%d").date()
        valid = exp_date >= REFERENCE_TODAY
    except Exception:
        return ("PUC validity", f"on record (expiry {expiry})")
    return ("PUC validity", f"{'VALID' if valid else 'EXPIRED'} (expires {expiry})")


def _stolen_line(profile: dict, availability: dict) -> tuple[str, str]:
    stolen = profile.get("stolen_status")
    if stolen in (None, "UNKNOWN"):
        if availability.get("THEFT") in _DOWN or "THEFT" not in availability:
            return ("Reported stolen?", UNAVAILABLE)
        return ("Reported stolen?", "unknown")
    if stolen == "STOLEN":
        return ("Reported stolen?", "yes")
    if stolen == "RECOVERED":
        return ("Reported stolen?", "no (recovered)")
    return ("Reported stolen?", "no")  # NOT_REPORTED


def _minimisation_caption(plan_trace: dict) -> None:
    asked = list(plan_trace.get("sources_contacted") or [])
    catalog_ids = list(get_source_catalog().keys())
    sources_detail = plan_trace.get("sources_detail") or {}
    group_label("Who was asked")
    _chip_row(sources_detail, asked, catalog_ids)
    not_asked = [s for s in catalog_ids if s not in asked]
    st.caption(
        f"Data minimisation: asked {', '.join(asked) or '—'}; "
        f"not asked {', '.join(not_asked) or '—'}. "
        f"Only the attributes needed for this question were requested, so the mediator "
        f"contacted {len(asked)} of {len(catalog_ids)} sources."
    )


# ------------------------------------------------------------------ dispute a challan
# Same data-minimisation rule as the rest of this page: a citizen sees the outcome of their own
# case and the evidence that moved it, never the owner record, never where the camera stands.

MASK = "•"


def mask_plate(plate: Any) -> str:
    """Keep the ends, hide the middle: enough to recognise your own plate, not to identify it."""
    text = str(plate or "").strip().upper()
    if not text:
        return "—"
    if len(text) <= 6:
        return text[0] + MASK * (len(text) - 1)
    return text[:4] + MASK * (len(text) - 6) + text[-2:]


def _dispute_outcome(case: dict) -> None:
    status = str(case.get("status") or "").upper()
    plate = mask_plate(case.get("plate_resolved") or case.get("plate_read"))
    headline = {"CANCELLED": "Your challan has been CANCELLED.",
                "UPHELD": "Your challan has been UPHELD.",
                }.get(status, f"Your case is now {status}.")
    (st.success if status == "CANCELLED" else st.warning)(headline)
    st.markdown(f"**Case #{case.get('case_id')} — {plate} — {status}**")
    st.markdown(f"Sighting: {case.get('captured_at')} at {case.get('location') or 'a road camera'}")
    st.markdown(f"Why: {case.get('reason') or 'no reason recorded'}")
    if status == "CANCELLED":
        st.markdown("No payment is due. The check was re-run live against the insurer, the "
                    "registration authority and police records just now — not against a stored copy.")


def _dispute_block() -> None:
    section("Dispute a challan",
            "Enter the case number printed on your e-challan. The whole check is re-run live "
            "against the sources; if anything has changed since it was issued, it is cancelled.")
    case_id = st.number_input("Challan case number", key="sc_case_id", min_value=0, step=1, value=0)
    reason = st.text_area("Why do you believe this challan is wrong?", key="sc_reason",
                          placeholder="e.g. I renewed my policy before that date")
    if not st.button("Submit dispute", key="sc_dispute"):
        return
    if not case_id:
        st.warning("Enter the case number from your challan.")
        return
    try:
        case = challan_guard.dispute(int(case_id), reason.strip() or "no reason given")
    except ValueError as exc:  # a case that cannot be disputed is explained, not hidden
        st.warning(str(exc))
        return
    except Exception as exc:
        st.warning(f"Could not re-check this challan right now: {exc}")
        return
    _dispute_outcome(case or {})


def _lookup_block() -> None:
    section("Check your own vehicle",
            "Your own plate, and only the four answers you need about it: is it registered, is it "
            "insured, does its pollution certificate hold, has it been reported stolen. No owner "
            "record and no sighting data is shown, because none of it is requested.")

    group_label("Your plate")
    plate = st.text_input("Your vehicle's license plate number", key="sc_plate")
    check = st.button("Check my vehicle", key="sc_check")

    if not check:
        st.info("Enter your plate above and press Check to see its compliance status.")
        return
    if not plate.strip():
        st.warning("Enter a plate number first.")
        return

    try:
        result = run_global_query(plate, requested_attrs=REQUESTED_ATTRS)
        profile: dict[str, Any] = result.get("profile") or {}
        plan_trace: dict[str, Any] = result.get("plan_trace") or {}
    except Exception as exc:  # a citizen page must never crash the whole app
        st.error(f"Could not run this check right now: {exc}")
        return

    availability = profile.get("source_availability") or {}

    answers = [_registration_line(profile, availability),
               _insurance_line(profile, availability),
               _puc_line(profile, availability),
               _stolen_line(profile, availability)]
    kpi_row([answer for answer in answers if answer])

    _minimisation_caption(plan_trace)


def render() -> None:
    """Draw the whole Citizen Self-Check tab. Never raises — a broken lookup shows a warning."""
    _lookup_block()
    _dispute_block()
