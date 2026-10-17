"""Sidebar block: what the app did about each local source wrapper on this rerun, in plain words.

`app.py`'s cluster logic (owned by the backend workstream) writes `st.session_state["source_status"]`
on every rerun: per source `running | started | remote | failed: <reason> | stopped: <reason>`. This
module only translates those states for a person and offers a restart; it starts nothing itself.
Keys carry `ss_`.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

import streamlit as st

from components import chip, chip_strip
from theme import TOKENS


def describe(state: Any) -> Tuple[str, str]:
    """(plain words, token name) for one state string. Unknown states are shown as they are."""
    text = str(state or "").strip()
    head, _, reason = text.partition(":")
    head = head.strip().lower()
    if head == "running":
        return "running", "ok"
    if head == "started":
        return "restarted just now", "ok"
    if head == "remote":
        return "on another laptop", "info"
    if head == "failed":
        return f"failed — {reason.strip() or 'no reason given'}", "bad"
    if head == "stopped":
        return f"stopped on purpose — {reason.strip() or 'held down'}", "warn"
    return text or "unknown", "neutral"


def render_sidebar() -> None:
    """Under the health badges: one chip per source, and a restart button."""
    status: Dict[str, Any] = st.session_state.get("source_status") or {}
    if status:
        chip_strip([chip(f"{sid}: {describe(s)[0]}", TOKENS[describe(s)[1]]) for sid, s in status.items()])
    else:
        st.caption("Start-up status not reported yet on this rerun.")
    if st.button("Restart local sources", key="ss_restart", width="stretch",
                 help="Clears the status and reruns: the app's start-up check restarts any local wrapper that "
                      "is not answering. Wrappers on other laptops are never touched."):
        st.session_state.pop("source_status", None)
        st.rerun()
