"""'Watch the data change' panel for the Data Cleansing page.

Shows the camera agency's own raw row before and after the last write (Apply, Undo, Resolve or an
examiner-toolbox insert / modify / delete), the exact SQL the mediator sent to the source, and, when
a plate was corrected, the registration row of the corrected plate. Rows come from
`mediator.change_audit` (read live, raw source column names); this module only compares and draws.

How "before" is captured without touching the write path: every render snapshots the raw rows of the
captures the operator is pointing at (the selected capture and the toolbox's id fields). A write
always ends in `st.rerun()`, so the snapshot from the run before the click is the row as it was,
and the next run reads the row as it is. (Trade-off: a change made by someone else between two
reruns is attributed to the next write; the SQL list below shows exactly what this mediator sent.)
Keys carry `dc_cw_`.
"""
from __future__ import annotations

import html
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import streamlit as st  # noqa: E402

from components import fmt, group_label, section, styled_table  # noqa: E402
from theme import TOKENS  # noqa: E402

SNAP = "dc_cw_snap"
EVENT = "dc_cw_event"
LAST = "dc_cw_last"


def audit() -> Any:
    try:
        from mediator import change_audit
        return change_audit
    except Exception:  # not delivered yet / broken import: the panel says so, the page lives on
        return None


def note_write(message: str, capture: Any) -> None:
    """Called by the cleansing page after every write: which capture changed, and why."""
    rid = str(capture).strip() if capture not in (None, "") else str(st.session_state.get("dc_tb_del_id", "")).strip()
    if rid:
        st.session_state[EVENT] = {"rid": rid, "message": message, "at": datetime.now().strftime("%H:%M:%S")}


def _row(mod: Any, rid: str) -> Optional[Dict[str, Any]]:
    try:
        row = mod.capture_row(int(rid) if rid.isdigit() else rid)
    except Exception:
        return None
    return dict(row) if row else None


def diff_rows(before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One entry per column: {column, before, after, changed}. A missing row is all-None."""
    cols = list(dict.fromkeys(list((before or {}).keys()) + list((after or {}).keys())))
    return [{"column": c, "before": (before or {}).get(c), "after": (after or {}).get(c),
             "changed": (before or {}).get(c) != (after or {}).get(c)} for c in cols]


def diff_html(rows: List[Dict[str, Any]], before_exists: bool, after_exists: bool) -> str:
    head = ("<tr><th>Column (as the source names it)</th><th>Before</th><th>After</th><th>Changed?</th></tr>")
    body = []
    for r in rows:
        style = f' style="background:#FFF4E0;box-shadow:inset 4px 0 0 {TOKENS["warn"]};"' if r["changed"] else ""
        b = fmt(r["before"]) if before_exists else "(row did not exist)"
        a = fmt(r["after"]) if after_exists else "(row deleted)"
        mark = "<strong>CHANGED</strong>" if r["changed"] else "same"
        body.append(f"<tr{style}><td>{html.escape(r['column'])}</td><td>{html.escape(b)}</td>"
                    f"<td>{html.escape(a)}</td><td>{mark}</td></tr>")
    return (f'<div class="cv-tablewrap"><table class="cv-table"><thead>{head}</thead>'
            f'<tbody>{"".join(body)}</tbody></table></div>')


def _plate_column() -> Optional[str]:
    try:
        from mediator import cleansing
        from tabs.cleansing_toolbox import cam_spec
        return cleansing._column(cam_spec(), "plate_number")
    except Exception:
        return None


def _statements(mod: Any) -> None:
    try:
        stmts = list(mod.last_admin_statements(5))
    except Exception as exc:
        st.info(f"The SQL log could not be read: {exc}")
        return
    if not stmts:
        st.markdown("No SQL has been sent to a source by this mediator yet.")
        return
    group_label("The exact SQL the mediator sent to the source (newest first)")
    if all(isinstance(s, dict) for s in stmts):
        styled_table(stmts)
    else:
        st.code("\n".join(str(s) for s in stmts), language="sql")


def render(selected: Optional[str]) -> None:
    section("Watch the data change",
            "What this shows: the camera agency's own row, exactly as its database stores it, before and after "
            "your last Apply, Undo, Resolve or toolbox edit. Changed cells are highlighted and marked CHANGED.")
    mod = audit()
    if mod is None:
        st.warning("Not available yet: mediator/change_audit.py could not be loaded, so raw before/after rows "
                   "cannot be shown. Apply, Undo and Resolve still work.")
        return
    snaps: Dict[str, Optional[Dict[str, Any]]] = st.session_state.setdefault(SNAP, {})
    event = st.session_state.pop(EVENT, None)
    if event:  # compare with the snapshot taken before the click, then read the row as it is now
        rid = event["rid"]
        st.session_state[LAST] = {**event, "before": snaps.get(rid), "after": _row(mod, rid)}
    watch = {str(x).strip() for x in (selected, st.session_state.get("dc_tb_mod_id"),
                                     st.session_state.get("dc_tb_del_id")) if str(x or "").strip()}
    for rid in watch:
        snaps[rid] = _row(mod, rid)
    last = st.session_state.get(LAST)
    if last:
        group_label(f"Capture {last['rid']} — {last['message']} (at {last['at']})")
        rows = diff_rows(last["before"], last["after"])
        if rows:
            st.markdown(diff_html(rows, last["before"] is not None, last["after"] is not None), unsafe_allow_html=True)
            changed = [r["column"] for r in rows if r["changed"]]
            st.markdown(f"**{len(changed)} column(s) changed:** {', '.join(changed) or 'none'}.")
        else:
            st.markdown("The row could not be read before or after the write.")
        plate_col = _plate_column()
        new_plate = (last["after"] or {}).get(plate_col) if plate_col else None
        if plate_col and new_plate and new_plate != (last["before"] or {}).get(plate_col):
            try:
                reg = mod.registration_row(new_plate)
            except Exception as exc:
                reg, _ = None, st.info(f"The registration row could not be read: {exc}")
            group_label(f"Registration authority's row for the corrected plate {new_plate}")
            if reg:
                styled_table([dict(reg)])
            else:
                st.markdown(f"No registration row exists for {new_plate}.")
    elif selected:
        current = snaps.get(str(selected))
        group_label(f"Capture {selected} as the camera agency stores it now (no write yet in this session)")
        if current:
            styled_table([{"Column": k, "Value": v} for k, v in current.items()])
        else:
            st.markdown("The row could not be read.")
    _statements(mod)
