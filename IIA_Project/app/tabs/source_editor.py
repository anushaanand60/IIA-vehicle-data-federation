"""Source Editor sidebar: writes driven entirely by each wrapper's own admin action menu.

Replaces the old hard-coded "Live Source Data Mutator" (five fixed forms baked into app.py). That
panel assumed it knew every source's actions and parameters ahead of time; this one asks the
wrapper (`GET /admin/actions`) what it can do and builds the form from the answer, so a new action
added to `sources/wrapper_template.ADMIN_ACTIONS` shows up here with no GUI change. The write
itself still goes only through `POST /admin/mutate` -- never arbitrary SQL, never a direct DB
connection -- and is disabled wherever that laptop set `<ID>_ADMIN=off`, exactly like the CLI
fallback (`scripts/mutate_source.py`) that is shown alongside it for when the GUI can't reach it.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # `streamlit run` puts only this file's folder on sys.path
    sys.path.insert(0, str(ROOT))
_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import httpx  # noqa: E402
import streamlit as st  # noqa: E402

from components import group_label, section  # noqa: E402
from mediator.catalog import get_source_catalog  # noqa: E402

ACTIONS_TIMEOUT_S = 3.0
MUTATE_TIMEOUT_S = 8.0
DEFAULT_PLATE = "DL01AB1234"


def _base_url(meta: dict) -> str:
    return str(meta.get("base_url") or "").rstrip("/")


def _fetch_actions(base: str) -> dict | None:
    """None means unreachable; the caller shows a DOWN warning and stops. A reachable wrapper
    always answers 200 here, even with admin disabled -- that's the whole point of the endpoint:
    it can explain *why* nothing is possible instead of a bare 404 on first contact."""
    try:
        r = httpx.get(f"{base}/admin/actions", timeout=ACTIONS_TIMEOUT_S, trust_env=False)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    return r.json()


def _cli_line(source_id: str, action: str, plate: str, params: dict[str, str]) -> str:
    """The same write, run from the laptop that owns the source -- the fallback path when the
    GUI can't reach that wrapper at all (see docs/CONTRACTS.md / scripts/mutate_source.py)."""
    parts = [f"python scripts/mutate_source.py {action} {source_id} {plate}"]
    for name, value in params.items():
        parts.append(f"--{name.replace('_', '-')} {value}")
    return " ".join(parts)


def _apply(source_id: str, base: str, action: str, plate: str, params: dict[str, str]) -> None:
    try:
        r = httpx.post(f"{base}/admin/mutate",
                       json={"action": action, "plate": plate, "params": params},
                       timeout=MUTATE_TIMEOUT_S)
    except httpx.TimeoutException:
        st.error(f"{source_id} is TIMEOUT: no response from {base} within {MUTATE_TIMEOUT_S:.0f}s.")
        return
    except Exception as exc:
        st.error(f"{source_id} is DOWN: could not reach {base} ({type(exc).__name__}: {exc}).")
        return
    if r.status_code == 404:
        st.info(f"Writes disabled on that laptop ({source_id}_ADMIN=off).")
        return
    if r.status_code == 400:
        try:
            message = r.json().get("error", r.text)
        except Exception:
            message = r.text
        st.error(f"Rejected: {message}")
        return
    if r.status_code != 200:
        st.error(f"{source_id} rejected the mutation: {r.text}")
        return
    body = r.json()
    st.success(f"{body.get('rows_affected')} row(s) affected on {source_id}: {body.get('detail', '')}")
    st.session_state["selected_plate"] = plate
    st.caption("Re-run the plate in Investigate — the mediator reads it live.")


def render_sidebar() -> None:
    with st.sidebar:
        section("Source editor",
                "Writes go to the agency's own database, through its wrapper's named admin "
                "actions — never arbitrary SQL, never a direct connection.")

        catalog = get_source_catalog()
        if not catalog:
            st.warning("No sources are registered in the catalog.")
            return

        source_id = st.selectbox("Source", list(catalog), key="se_source")
        meta = catalog[source_id]
        base = _base_url(meta)
        if not base:
            st.error(f"{source_id} has no base URL in the catalog.")
            return

        body = _fetch_actions(base)
        if body is None:
            st.warning(f"{source_id} is DOWN: could not reach {base}/admin/actions.")
            return

        if not body.get("enabled", False):
            st.info(f"Writes disabled on that laptop ({source_id}_ADMIN=off).")
            return

        actions: dict[str, Any] = body.get("actions", {})
        if not actions:
            st.info(f"{source_id} publishes no admin actions.")
            return

        action_names = list(actions)
        default_action = st.session_state.get("se_action")
        if default_action not in actions:
            default_action = action_names[0]
        action = st.selectbox("Action", action_names, key="se_action",
                              help=actions[default_action].get("help", ""))
        spec = actions[action]

        plate = st.text_input(
            "Plate", st.session_state.get("selected_plate", DEFAULT_PLATE), key="se_plate")

        params: dict[str, str] = {}
        for p in spec.get("params", []):
            name = p.get("name", "")
            default = p.get("default")
            value = st.text_input(
                name.replace("_", " ").title(),
                "" if default is None else str(default),
                key=f"se_p_{action}_{name}",
                help=p.get("help"),
            )
            if value != "":
                params[name] = value

        group_label("CLI fallback — run on the laptop that owns this source")
        st.code(_cli_line(source_id, action, plate, params), language="bash")

        if st.button("Apply", key="se_apply", type="primary", width="stretch"):
            _apply(source_id, base, action, plate, params)


if __name__ == "__main__":
    import streamlit as _st
    _st.set_page_config(page_title="Source Editor", page_icon=":material/edit:", layout="wide")
    render_sidebar()
