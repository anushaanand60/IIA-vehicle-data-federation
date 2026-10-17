"""Data Cleansing page (rubric 2): live anomaly scan of camera captures, RDR proposals, resolution.

Everything on this page is a view of `mediator.cleansing`: `scan()` for the list and the counts,
`propose()` for one record's fixes with the Ripple-Down-Rules path that produced each, `apply()` /
`undo()` through the CAM wrapper's own /admin/sql, `resolve()` for what the proposals leave open,
and `teach()` to grow the tree. The scan is re-run on demand and after every write; the record panel
re-reads its capture live on every view, so the examiner's insert / modify / delete shows at once.
Widget keys carry the `dc_` prefix.
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
from mediator import cleansing, lexicons  # noqa: E402
from mediator.rdr import RDRConflict, RDRTree  # noqa: E402
from tabs import change_watch, cleansing_toolbox, cleansing_validation_panel  # noqa: E402
from theme import TOKENS  # noqa: E402

ACTION_TOKEN = {"keep": "neutral", "set": "ok", "delete": "warn", "quarantine": "warn", "flag": "bad"}
DISPOSITION_TOKEN = {"flagged": "bad", "quarantined": "warn", "unhandled": "alert", "resolved": "ok"}
OPS = ("eq", "ne", "in", "not_in", "lt", "le", "gt", "ge", "is_null", "not_null", "is_true", "is_false",
       "in_lexicon", "contains_digit")
ACTIONS = ("keep", "set", "quarantine", "flag", "delete")
FILTERS = ("Still open after proposals", "Every capture with an anomaly", "Resolved by an operator")
OPERATOR_ERRORS = (cleansing.CleansingError, lexicons.SourceUnavailable, RDRConflict, ValueError, KeyError)


def _scan() -> None:
    with st.spinner("Scanning every camera capture live against the registration authority…"):
        report = cleansing.scan()
    st.session_state["dc_scan"] = {"report": report, "at": datetime.now().strftime("%H:%M:%S")}


def _after_write(message: str, capture: Any = None) -> None:
    """Every write is followed by a fresh scan, so the counts and the list never describe old data."""
    st.session_state["dc_flash"] = message
    change_watch.note_write(message, capture)  # the Watch panel compares this capture's row before/after
    if capture is not None:
        st.session_state["dc_pending_capture"] = str(capture)
    _scan()
    st.rerun()


def _attempt(action: Any, *args: Any, **kwargs: Any) -> Any:
    """Run one operator action; a refusal is shown verbatim, never raised into the page."""
    try:
        return action(*args, **kwargs)
    except OPERATOR_ERRORS as exc:
        st.error(f"Refused: {exc}")
        return None


def _kpis(report: Dict[str, Any]) -> None:
    before = sum(report["anomalies_before"].values())
    after = sum(report["remaining_after"].values())
    disp = report["remaining_by_disposition"]
    kpi_row([("Records scanned", report["records_scanned"]), ("Anomalies before", before),
             ("Fixed by rule proposals", before - after), ("Quarantined", disp.get("quarantined", 0)),
             ("Flagged clone evidence", disp.get("flagged", 0)), ("Unhandled (must be 0)", disp.get("unhandled", 0)),
             ("Resolved by an operator", disp.get("resolved", 0)),
             ("Remaining after resolution", report["remaining_after_resolution"])])


def _open(item: Dict[str, Any]) -> bool:
    return any(d != "resolved" for d in item["dispositions"])


def _table(report: Dict[str, Any]) -> None:
    choice = st.radio("Show", FILTERS, key="dc_filter", horizontal=True)
    keep = {FILTERS[0]: _open, FILTERS[1]: lambda i: bool(i["anomalies"]),
            FILTERS[2]: lambda i: "resolved" in i["dispositions"]}[choice]
    items = [i for i in report["items"] if keep(i)]
    if not items:
        st.info("Nothing in this view.")
        return
    st.caption(f"{len(items)} capture(s). The list scrolls; pick one below to see its rule path.")
    with st.container(height=440, key="dc_table_scroll"):  # 60+ rows would push every action off-screen
        _rows(items)


def _rows(items: List[Dict[str, Any]]) -> None:
    styled_table([{"Capture": it["record_id"], "Plate": it["record"].get("plate_number"),
                   "Anomalies": ", ".join(a["code"] for a in it["anomalies"]) or "—",
                   "Rule proposals": "; ".join(f"{f['field']} {f['action']}" + (f" → {f['after']}" if f["action"] == "set" else "")
                                               + f" ({f['rule']})" for f in it["fixes"] if f["action"] != "keep") or "keep",
                   "Left after proposals": ", ".join(f"{c} ({d})" for c, d in zip(it["remaining"], it["dispositions"])) or "none"}
                  for it in items], max_rows=400)


def _steps(path: List[Dict[str, Any]], rule: Optional[str]) -> List[Dict[str, Any]]:
    return [{"title": f"{p['rule']} {'fired' if p['fired'] else 'did not fire'}" + (" — its conclusion stands" if p["rule"] == rule else ""),
             "detail": p["because"], "done": p["fired"]} for p in path]


def _pick(report: Dict[str, Any]) -> Optional[str]:
    """Capture ids as strings: one type for ids that come from a scan, a toolbox insert or a delete."""
    ids = [str(it["record_id"]) for it in report["items"]]
    pending = st.session_state.pop("dc_pending_capture", None)
    if pending is not None and pending in ids:
        st.session_state["dc_capture"] = pending
    if st.session_state.get("dc_capture") not in ids:  # deleted, or cleansed out of the list
        st.session_state.pop("dc_capture", None)
    if not ids:
        st.info("No capture has an anomaly or a proposal.")
        return None
    items = {str(it["record_id"]): it for it in report["items"]}
    return st.selectbox("Capture", ids, key="dc_capture", format_func=lambda i: f"#{i} · "
                        f"{items[i]['record'].get('plate_number')} · {', '.join(a['code'] for a in items[i]['anomalies']) or 'proposal only'}")


def _fix_view(f: Any, ctx: Any) -> None:
    head = f"{f.field}: {f.action}" + (f" {f.before!r} → {f.after!r}" if f.action == "set" else "")
    group_label(head)
    chip_strip([chip(f.action.upper(), TOKENS[ACTION_TOKEN.get(f.action, "neutral")]), chip(f"rule {f.rule}", TOKENS["info"])])
    stepper(_steps(f.path, f.rule))
    st.markdown(f"**Because:** {f.because}")
    corner = ctx.tree.find(f.rule).cornerstone if f.rule else None
    if corner:
        st.markdown(f"**Cornerstone case:** record {corner.get('record_id')} — {corner.get('_provenance', 'seed case')}")


def _record_panel(report: Dict[str, Any], actor: str) -> Optional[Dict[str, Any]]:
    section("One capture: anomalies, proposals and the rule path", "Read live from the camera source on every view.")
    rid = _pick(report)
    if rid is None:
        return None
    try:
        ctx = cleansing.Context.build()
        prop = cleansing.propose(ctx.record(rid), ctx)
    except OPERATOR_ERRORS as exc:
        st.error(f"Capture {rid} could not be read: {exc}. Re-scan.")
        return None
    item = next(it for it in report["items"] if str(it["record_id"]) == rid)
    r = prop.record
    st.markdown(f"**Read as {r.get('plate_number')}** at {r.get('last_seen_location')}, {r.get('last_seen_time')}: "
                f"{r.get('observed_make')} {r.get('observed_model')} {r.get('observed_colour')} "
                f"(OCR confidence {r.get('ocr_confidence')})")
    styled_table([{"Anomaly": a.code, "Field": a.field, "Observed": a.observed, "Severity": a.severity,
                   "Evidence": json.dumps(a.evidence, default=str)[:140]} for a in prop.anomalies])
    if item["remaining"]:
        chip_strip([chip(f"{c} · {d}", TOKENS[DISPOSITION_TOKEN[d]]) for c, d in zip(item["remaining"], item["dispositions"])])
    if res := item.get("resolution"):
        st.markdown(f"**Resolved:** {res['decision']} by {res['actor']} at {res['at'][:19]} — {res['note'] or 'no note'}")
    for f in prop.changes:
        _fix_view(f, ctx)
    c1, c2 = st.columns(2)
    with c1:
        group_label("Apply")
        if st.button("Apply these proposals", key="dc_apply", type="primary", disabled=not prop.changes):
            if (done := _attempt(cleansing.apply, prop, actor=actor, ctx=ctx)) is not None:
                _after_write(f"Applied {len(done)} change(s) to capture {rid}", rid)
    with c2:
        group_label("Undo, from the cleansing log")
        entries = [e for e in cleansing.log_entries(1000) if str(e["record_id"]) == rid and not e["undone_at"]]
        if not entries:
            st.caption("Nothing applied to this capture yet.")
        for e in entries:
            what = e.get("decision") or f"{e['field']} {e['action']}"
            if st.button(f"Undo #{e['log_id']}: {what} by {e['actor']}", key=f"dc_undo_{e['log_id']}"):
                if _attempt(cleansing.undo, e["log_id"], actor=actor) is not None:
                    _after_write(f"Undid log entry {e['log_id']}", rid)
    return {"rid": rid, "prop": prop, "item": item}


def _resolve_panel(sel: Dict[str, Any], actor: str) -> None:
    section("Resolve what the rules leave open",
            "Clone evidence and quarantined reads need a person: confirm the clone (watchlists the plate), accept "
            "a matcher candidate, delete the capture, or dismiss it with a reason. Each is logged and undoable.")
    decision = st.radio("Decision", cleansing.RESOLUTIONS, key="dc_res_decision", horizontal=True)
    plate_fix = next((f for f in sel["prop"].fixes if f.field == "plate_number"), None)
    cands = (plate_fix.evidence.get("match_candidates") if plate_fix else None) or []
    candidate = None
    if decision == "accept_candidate":
        if not cands:
            st.info("The matcher listed no candidates: this capture's plate is registered, so it is not rewritten.")
        else:
            labels = {c["plate"]: f"{c['plate']} (p={c['probability']}, {c.get('decision', '')})" for c in cands}
            candidate = st.selectbox("Candidate plate", list(labels), key="dc_res_candidate", format_func=labels.get)
    note = st.text_input("Note (mandatory for dismiss)", "", key="dc_res_note")
    if st.button("Resolve", key="dc_resolve", type="primary"):
        res = _attempt(cleansing.resolve, sel["rid"], decision, note, actor, candidate=candidate)
        if res is not None:
            _after_write(f"Capture {sel['rid']}: {decision} recorded (log {res['log_id']})", sel["rid"])


def _bulk(report: Dict[str, Any], actor: str) -> None:
    auto = [it for it in report["items"] if any(f["action"] in ("set", "delete") for f in it["fixes"])]
    section("Apply all automatic fixes", f"{len(auto)} capture(s) have a set or delete proposal. Flags and "
            "quarantines are never written: they wait for a person.")
    ok = st.checkbox("I have read the proposals and want them written to the camera source", key="dc_bulk_confirm")
    if st.button("Apply all automatic fixes", key="dc_bulk_apply", disabled=not (ok and auto)):
        ctx = _attempt(cleansing.Context.build)
        if ctx is None:
            return
        n, failed = 0, []
        for it in auto:
            try:
                prop = cleansing.propose(ctx.record(it["record_id"]), ctx)
                writes = cleansing.Proposal(prop.source_id, prop.record_id, prop.record, prop.anomalies,
                                            [f for f in prop.fixes if f.action in ("set", "delete")])
                n += len(cleansing.apply(writes, actor=actor, ctx=ctx)) if writes.changes else 0
            except OPERATOR_ERRORS as exc:  # one stale record must not stop the rest
                failed.append(f"{it['record_id']}: {exc}")
        _after_write(f"Applied {n} automatic fix(es)" + (f"; {len(failed)} refused ({failed[0]})" if failed else ""))


def _teach(sel: Dict[str, Any], actor: str) -> None:
    section("Teach the tree", "Add a rule under the one that fired wrongly (except) or beside the one that failed "
            "to fire (false). This capture becomes its cornerstone; a rule that would change another cornerstone "
            "case's conclusion is refused, and the refusal is shown as the engine words it.")
    fixes = {f.field: f for f in sel["prop"].fixes}
    fld = st.selectbox("Field", list(fixes), key="dc_t_field")
    path = [p["rule"] for p in fixes[fld].path]
    under = st.selectbox("Under rule", path, key="dc_t_under",
                         index=path.index(fixes[fld].rule) if fixes[fld].rule in path else 0)
    branch = st.radio("Branch", ["except", "false"], key="dc_t_branch", horizontal=True)
    c1, c2, c3 = st.columns(3)
    cfield = c1.text_input("Condition field", "value", key="dc_t_cfield")
    op = c2.selectbox("Operator", OPS, key="dc_t_op")
    raw = c3.text_input("Condition value", "", key="dc_t_cvalue")
    a1, a2 = st.columns(2)
    action = a1.selectbox("Conclusion", ACTIONS, key="dc_t_action")
    extra = a2.text_input("Value (set) or flag name (flag)", "", key="dc_t_value")
    because = st.text_input("Because", "", key="dc_t_because")
    try:
        value = json.loads(raw) if raw.strip() else None
    except ValueError:
        value = raw  # plain text is a string literal; numbers, true/false and lists parse as JSON
    condition = {"field": cfield, "op": op, **({"value": value} if value is not None else {})}
    conclusion = {"action": action, **({"value": extra} if action == "set" and extra else {}),
                  **({"flag": extra or "OPERATOR_FLAG"} if action == "flag" else {})}
    st.code(json.dumps({"if": condition, "then": conclusion, "under": under, "branch": branch}, indent=1), language="json")
    if st.button("Add rule", key="dc_t_add", disabled=not because.strip()):
        out = _attempt(cleansing.teach, sel["rid"], fld, under, condition, conclusion, because, branch, actor)
        if out is not None:
            _after_write(f"Added {out['rule']} under {under} ({branch})", sel["rid"])


def _tree() -> None:
    section("The rule tree", "One row per rule, indented under the rule it is an exception to; else-siblings share a depth.")
    rows: List[Dict[str, Any]] = []

    def walk(rule: Any, depth: int, branch: str) -> None:
        while rule is not None:
            corner = rule.cornerstone or {}
            rows.append({"Rule": " " * depth + rule.id, "Branch": branch, "If": json.dumps(rule.condition),
                         "Then": json.dumps(rule.conclusion), "Because": rule.because,
                         "Cornerstone": f"{corner.get('field', '')} of record {corner.get('record_id', '—')}"})
            walk(rule.except_, depth + 1, f"except of {rule.id}")
            rule, branch = rule.if_false, "else"

    walk(RDRTree.load(cleansing.RULES_PATH).root, 0, "root")
    styled_table(rows, max_rows=200)


def render() -> None:
    page_head("Data Cleansing", "Camera captures checked live against the registration authority, cleansed by "
              "Ripple-Down Rules, and every leftover anomaly resolved by a named person with a reason.")
    if msg := st.session_state.pop("dc_flash", None):
        st.success(msg)
    with panel("dc_kpis"):
        section("Where the data stands")
        c1, c2 = st.columns([3, 1])
        actor = c1.text_input("Operator name (recorded with every write)", "operator", key="dc_actor").strip() or "operator"
        c2.markdown("<div style='height:34px'></div>", unsafe_allow_html=True)
        if c2.button("Re-scan (live)", key="dc_rescan", width="stretch") or "dc_scan" not in st.session_state:
            _scan()
        state = st.session_state["dc_scan"]
        report = state["report"]
        if report.get("status") != "OK":
            st.error(f"Scan refused: {report.get('error')}. Nothing is shown rather than a stale list.")
            return
        _kpis(report)
        st.caption(f"Scanned live at {state['at']}. Remaining after resolution counts flagged + quarantined + "
                   "unhandled anomalies no operator has resolved.")
    with panel("dc_table"):
        section("Anomalies", "What each capture carries, what the rules propose, and what is left after them.")
        _table(report)
    with panel("dc_record"):
        sel = _record_panel(report, actor)
    if sel:
        with panel("dc_resolve"):
            _resolve_panel(sel, actor)
    with panel("dc_watch"):  # before the toolbox, so its snapshot is taken before a toolbox write
        change_watch.render(sel["rid"] if sel else None)
    with panel("dc_bulk"):
        _bulk(report, actor)
    if sel:
        with panel("dc_teach"):
            _teach(sel, actor)
    with panel("dc_tree"):
        _tree()
    with panel("dc_toolbox"):
        cleansing_toolbox.render(_after_write)
    with panel("dc_validation"):
        cleansing_validation_panel.render()
