"""Data Cleansing page (rubric 2): live anomaly scan of camera captures, RDR proposals, resolution.

Everything on this page is a view of `mediator.cleansing`: `scan()` for the list and the counts,
`propose()` for one record's fixes with the Ripple-Down-Rules path that produced each, `apply()` /
`undo()` through the CAM wrapper's own /admin/sql, `resolve()` for what the proposals leave open,
and `teach()` to grow the tree. A scan is re-run after every write, never cached across one (the
examiner's insert / modify / delete must show on the next view). Keys carry the `dc_` prefix.
"""
from __future__ import annotations

import json
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

from components import chip, chip_strip, group_label, kpi_row, page_head, panel, section, stepper, styled_table  # noqa: E402
from mediator import cleansing  # noqa: E402
from mediator.rdr import RDRConflict, RDRTree  # noqa: E402
from tabs import cleansing_toolbox  # noqa: E402
from theme import TOKENS  # noqa: E402

ACTION_TOKEN = {"keep": "neutral", "set": "ok", "delete": "warn", "quarantine": "warn", "flag": "bad"}
DISPOSITION_TOKEN = {"flagged": "bad", "quarantined": "warn", "unhandled": "alert", "resolved": "ok"}
OPS = ("eq", "ne", "in", "not_in", "lt", "le", "gt", "ge", "is_null", "not_null", "is_true", "is_false",
       "in_lexicon", "contains_digit")
ACTIONS = ("keep", "set", "quarantine", "flag", "delete")


def _scan() -> Dict[str, Any]:
    report = cleansing.scan()
    st.session_state["dc_scan"] = {"report": report, "at": datetime.now().strftime("%H:%M:%S")}
    return report


def _after_write(message: str, capture: Any = None) -> None:
    st.session_state["dc_flash"] = message
    if capture is not None:
        st.session_state["dc_pending_capture"] = capture
    _scan()
    st.rerun()


def _kpis(report: Dict[str, Any]) -> None:
    before = sum(report["anomalies_before"].values())
    after = sum(report["remaining_after"].values())
    disp = report["remaining_by_disposition"]
    kpi_row([("Records scanned", report["records_scanned"]), ("Anomalies before", before),
             ("Auto-fixed by rules", before - after), ("Quarantined", disp.get("quarantined", 0)),
             ("Flagged clone evidence", disp.get("flagged", 0)), ("Unhandled (must be 0)", disp.get("unhandled", 0)),
             ("Resolved by operator", disp.get("resolved", 0)),
             ("Remaining after resolution", report["remaining_after_resolution"])])


def _table(report: Dict[str, Any]) -> None:
    rows = []
    for it in report["items"]:
        rows.append({"Capture": it["record_id"], "Plate": it["record"].get("plate_number"),
                     "Anomalies": ", ".join(a["code"] for a in it["anomalies"]) or "—",
                     "Proposed": "; ".join(f"{f['field']} {f['action']}" + (f" → {f['after']}" if f["action"] == "set" else "")
                                           for f in it["fixes"] if f["action"] != "keep") or "keep",
                     "Left after proposals": ", ".join(f"{c} ({d})" for c, d in zip(it["remaining"], it["dispositions"])) or "none"})
    styled_table(rows, max_rows=400)


def _steps(path: List[Dict[str, Any]], rule: Optional[str]) -> List[Dict[str, Any]]:
    return [{"title": f"{p['rule']} {'fired' if p['fired'] else 'did not fire'}" + (" — concludes" if p["rule"] == rule else ""),
             "detail": p["because"], "done": p["fired"]} for p in path]


def _record_panel(report: Dict[str, Any], actor: str) -> Optional[Dict[str, Any]]:
    ids = [it["record_id"] for it in report["items"]]
    if not ids:
        st.info("No capture has an anomaly or a proposal.")
        return None
    if (pending := st.session_state.pop("dc_pending_capture", None)) is not None:
        st.session_state["dc_capture"] = int(pending) if str(pending).isdigit() else pending
    rid = st.selectbox("Capture", ids, key="dc_capture")
    ctx = cleansing.Context.build()
    prop = cleansing.propose(ctx.record(rid), ctx)
    section(f"Capture {rid} — read as {prop.record.get('plate_number')}",
            f"{prop.record.get('last_seen_location')} at {prop.record.get('last_seen_time')}: "
            f"{prop.record.get('observed_make')} {prop.record.get('observed_model')} {prop.record.get('observed_colour')}")
    styled_table([{"Anomaly": a.code, "Field": a.field, "Observed": a.observed, "Severity": a.severity,
                   "Evidence": json.dumps(a.evidence, default=str)[:160]} for a in prop.anomalies])
    for f in prop.changes:
        group_label(f"{f.field}: {f.action}" + (f" {f.before!r} → {f.after!r}" if f.action == "set" else ""))
        chip_strip([chip(f.action.upper(), TOKENS[ACTION_TOKEN[f.action]]), chip(f"rule {f.rule}", TOKENS["info"])])
        stepper(_steps(f.path, f.rule))
        st.markdown(f"**Because** {f.because}")
        corner = ctx.tree.find(f.rule).cornerstone if f.rule else None
        if corner:
            st.markdown(f"Cornerstone case: record {corner.get('record_id')} — {corner.get('_provenance', '')}")
    c1, c2 = st.columns(2)
    if c1.button("Apply proposals", key="dc_apply", type="primary", disabled=not prop.changes):
        done = cleansing.apply(prop, actor=actor, ctx=ctx)
        _after_write(f"Applied {len(done)} change(s) to capture {rid}", rid)
    entries = [e for e in cleansing.log_entries(500) if str(e["record_id"]) == str(rid) and not e["undone_at"]]
    with c2:
        for e in entries:
            if st.button(f"Undo log {e['log_id']}: {e['field']} {e['action']} {e.get('decision') or ''}",
                         key=f"dc_undo_{e['log_id']}"):
                cleansing.undo(e["log_id"], actor=actor)
                _after_write(f"Undid log {e['log_id']}", rid)
    return {"rid": rid, "prop": prop, "ctx": ctx}


def _resolve_panel(sel: Dict[str, Any], actor: str) -> None:
    section("Resolve what the rules leave open",
            "Flagged clone evidence and quarantined reads need a person: confirm the clone, accept a "
            "matcher candidate, delete the capture, or dismiss it with a reason. Each is logged and undoable.")
    decision = st.radio("Decision", cleansing.RESOLUTIONS, key="dc_res_decision", horizontal=True)
    plate_fix = next((f for f in sel["prop"].fixes if f.field == "plate_number"), None)
    cands = [c["plate"] for c in (plate_fix.evidence.get("match_candidates") if plate_fix else None) or []]
    candidate = st.selectbox("Candidate plate", cands or ["(no candidates)"], key="dc_res_candidate")
    note = st.text_input("Note (mandatory for dismiss)", "", key="dc_res_note")
    if st.button("Resolve", key="dc_resolve", type="primary"):
        res = cleansing.resolve(sel["rid"], decision, note, actor, candidate=candidate)
        _after_write(f"Resolved capture {sel['rid']}: {decision} (log {res['log_id']})", sel["rid"])


def _bulk(report: Dict[str, Any], actor: str) -> None:
    section("Apply all automatic fixes", "Writes every set and delete the rules propose, through CAM's /admin/sql.")
    ok = st.checkbox("I have read the proposals above and want them written to the camera source",
                     key="dc_bulk_confirm")
    if st.button("Apply all automatic fixes", key="dc_bulk_apply", disabled=not ok):
        ctx = cleansing.Context.build()
        n = 0
        for it in report["items"]:
            prop = cleansing.propose(ctx.record(it["record_id"]), ctx)
            auto = cleansing.Proposal(prop.source_id, prop.record_id, prop.record, prop.anomalies,
                                      [f for f in prop.fixes if f.action in ("set", "delete")])
            if auto.changes:
                n += len(cleansing.apply(auto, actor=actor, ctx=ctx))
        _after_write(f"Applied {n} automatic fix(es)")


def _teach(sel: Dict[str, Any], actor: str) -> None:
    section("Teach the tree", "Add a rule under the one that fired wrongly (except) or failed to fire (else). "
            "The case becomes its cornerstone; a rule that would change another cornerstone is refused.")
    fields = [f.field for f in sel["prop"].fixes]
    fld = st.selectbox("Field", fields, key="dc_t_field")
    fix = next(f for f in sel["prop"].fixes if f.field == fld)
    under = st.selectbox("Under rule", [p["rule"] for p in fix.path], key="dc_t_under")
    branch = st.radio("Branch", ["except", "false"], key="dc_t_branch", horizontal=True)
    c1, c2, c3 = st.columns(3)
    cfield = c1.text_input("Condition field", "value", key="dc_t_cfield")
    op = c2.selectbox("Operator", OPS, key="dc_t_op")
    raw = c3.text_input("Condition value", "", key="dc_t_cvalue")
    a1, a2 = st.columns(2)
    action = a1.selectbox("Conclusion", ACTIONS, key="dc_t_action")
    cvalue = a2.text_input("Conclusion value (for set)", "", key="dc_t_value")
    because = st.text_input("Because", "", key="dc_t_because")
    try:
        value = json.loads(raw)
    except ValueError:
        value = raw
    condition = {"field": cfield, "op": op, "value": value}
    conclusion = {"action": action, **({"value": cvalue} if action == "set" else {})}
    st.code(json.dumps({"if": condition, "then": conclusion}, indent=1), language="json")
    if st.button("Add rule", key="dc_t_add"):
        try:
            out = cleansing.teach(sel["rid"], fld, under, condition, conclusion, because, branch, actor)
            _after_write(f"Added {out['rule']} under {under}", sel["rid"])
        except RDRConflict as exc:
            st.error(str(exc))


def _tree() -> None:
    section("The rule tree", "Indented: an except child under the rule it corrects; else siblings at the same depth.")
    tree = RDRTree.load(cleansing.RULES_PATH)
    rows = []

    def walk(rule: Any, depth: int, branch: str) -> None:
        while rule is not None:
            rows.append({"Rule": "· " * depth + rule.id, "Branch": branch, "If": json.dumps(rule.condition),
                         "Then": json.dumps(rule.conclusion), "Cornerstone": (rule.cornerstone or {}).get("record_id")})
            walk(rule.except_, depth + 1, "except")
            rule, branch = rule.if_false, "else"

    walk(tree.root, 0, "root")
    styled_table(rows, max_rows=200)


def render() -> None:
    page_head("Data Cleansing", "Camera captures checked live against the registration authority, cleansed by "
              "Ripple-Down Rules, and every leftover anomaly resolved by a named person with a reason.")
    if msg := st.session_state.pop("dc_flash", None):
        st.toast(msg)
    actor = st.sidebar.text_input("Operator name", "operator", key="dc_actor")
    with panel("dc_kpis"):
        section("Where the data stands")
        if st.button("Re-scan (live)", key="dc_rescan") or "dc_scan" not in st.session_state:
            _scan()
        state = st.session_state["dc_scan"]
        report = state["report"]
        if report.get("status") != "OK":
            st.error(f"Scan refused: {report.get('error')}")
            return
        _kpis(report)
        st.caption(f"Scanned live at {state['at']}.")
    with panel("dc_table"):
        section("Anomalies", "Every capture with an anomaly or a proposal, and what is left after the proposals.")
        _table(report)
    with panel("dc_record"):
        sel = _record_panel(report, actor)
    if sel:
        with panel("dc_resolve"):
            _resolve_panel(sel, actor)
    with panel("dc_bulk"):
        _bulk(report, actor)
    if sel:
        with panel("dc_teach"):
            _teach(sel, actor)
    with panel("dc_tree"):
        _tree()
    with panel("dc_toolbox"):
        cleansing_toolbox.render(_after_write)
