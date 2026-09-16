"""Unknown-plate onboarding wizard (Task 0.5).

When the mediator's decision is UNKNOWN VEHICLE — NOT REGISTERED, the plate exists in no source
we asked. Rather than a dead end, the Investigate tab offers this wizard: register the vehicle at
the RTO, insure it, optionally issue a PUC, then re-evaluate.

Every step is a POST to that agency's own wrapper (`/admin/mutate`, named actions from
`ADMIN_ACTIONS`) -- the mediator never opens a database connection, and nothing is written to a
central store. Each step prints the wrapper's own `detail` line so the SQL effect is visible.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # `streamlit run` puts only this file's folder on sys.path
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import streamlit as st  # noqa: E402

from mediator.catalog import get_source_catalog  # noqa: E402
from mediator.decide import UNKNOWN_VEHICLE  # noqa: E402

TIMEOUT_S = 8.0


def is_unknown(profile: dict) -> bool:
    """True when the decision engine's Rule 3b fired for this profile."""
    return bool(profile) and profile.get("decision") == UNKNOWN_VEHICLE


def _base_url(source_id: str) -> str | None:
    meta = get_source_catalog().get(source_id)
    if not meta:
        return None
    return str(meta.get("base_url") or "").rstrip("/") or None


def _mutate(source_id: str, action: str, plate: str, params: dict) -> None:
    """One wizard step. Shows what the agency's database actually did; never raises."""
    base = _base_url(source_id)
    if not base:
        st.error(f"{source_id} is not in the catalog, so this step cannot run.")
        return
    # Drop blanks so the wrapper's own documented defaults apply instead of empty strings.
    payload = {"action": action, "plate": plate,
               "params": {k: v for k, v in params.items() if v not in (None, "")}}
    try:
        r = httpx.post(f"{base}/admin/mutate", json=payload, timeout=TIMEOUT_S, trust_env=False)
    except Exception as exc:  # connect refused / timeout: a dead laptop is a message, not a crash
        kind = "TIMEOUT" if isinstance(exc, httpx.TimeoutException) else "DOWN"
        st.error(f"{source_id} is {kind}: could not reach {base} ({type(exc).__name__}). "
                 f"Nothing was written; start that laptop's wrapper and try again.")
        return
    if r.status_code == 404:
        st.error(f"{source_id} has writes disabled ({source_id}_ADMIN=off on that laptop). "
                 f"Unset it and restart the wrapper to onboard from the GUI.")
        return
    if r.status_code != 200:
        st.error(f"{source_id} rejected `{action}`: {_error_of(r)}")
        return
    body = r.json()
    st.success(f"{source_id}.{action}: {body.get('rows_affected')} row(s) affected — "
               f"{body.get('detail', 'done')}")
    st.caption(f"executed on {source_id} at {base} · {body.get('fetched_at', '')}")


def _error_of(r: httpx.Response) -> str:
    try:
        return str(r.json().get("error", r.text))
    except Exception:
        return r.text


def render_unknown_plate(plate: str, profile: dict) -> None:
    """Render the four-step wizard for `plate`. Safe to call on any plate string."""
    catalog = get_source_catalog()
    with st.expander("This plate is not registered — onboard it live", expanded=True):
        st.caption(f"No asked source holds a record for **{plate}**. Each step below writes to "
                   f"that agency's own database through its wrapper; the mediator stores nothing.")

        st.markdown("**Step 1 — Register at the RTO (REG)**")
        c1, c2 = st.columns(2)
        owner = c1.text_input("Owner name", value="Live Demo Owner", key="ob_owner")
        make = c1.text_input("Make", value="Maruti Suzuki", key="ob_make")
        model = c2.text_input("Model", value="Swift", key="ob_model")
        colour = c2.text_input("Colour", value="White", key="ob_colour")
        rto = st.text_input("RTO code", value=plate[:4].upper(), key="ob_rto")
        if st.button("Register vehicle", key="ob_register", type="primary"):
            _mutate("REG", "register", plate, {"owner": owner, "make": make, "model": model,
                                               "colour": colour, "rto_code": rto})

        st.markdown("**Step 2 — Insure it (INS)**")
        c3, c4 = st.columns(2)
        insurer = c3.text_input("Insurer id", value="", key="ob_insurer_id",
                                help="blank = the first insurer on that source")
        ptype = c3.text_input("Policy type", value="COMPREHENSIVE", key="ob_policy_type")
        until = c4.text_input("Valid until (DD/MM/YYYY)", value="31/12/2027", key="ob_until")
        if st.button("Add policy", key="ob_insure"):
            _mutate("INS", "add_policy", plate, {"insurer_id": insurer, "policy_type": ptype,
                                                 "until": until})

        if "PUC" in catalog:
            st.markdown("**Step 3 — Pollution certificate (PUC, optional)**")
            valid_upto = st.text_input("Valid upto (YYYY-MM-DD)", value="", key="ob_puc_valid",
                                       help="blank = one year from today")
            if st.button("Issue PUC", key="ob_puc"):
                _mutate("PUC", "issue", plate, {"valid_upto": valid_upto})
        else:
            st.caption("No PUC source is registered in this cluster; skipping the certificate step.")

        st.markdown("**Step 4 — Re-evaluate**")
        if st.button("Re-evaluate this plate", key="ob_requery"):
            st.session_state["selected_plate"] = plate
            st.session_state["ob_done"] = True
            st.info(f"Search plate set to {plate} — the next query reads the sources live, so the "
                    f"decision reflects what was just written.")
