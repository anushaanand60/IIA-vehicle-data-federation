"""CONTEXT-ADDED (R2: proof and cost of one answer): robustness line, "why is this here?", leads not followed, receipt.

Three render functions the case file calls with the `res` dict of context_flow.run_query. Each one never raises
(a failure becomes one quiet caption) and renders nothing when there is no context. Numbers come from the
ContextResult already in memory; only the "Pursue" button contacts a source, once, when clicked. Derived data
is kept in `res` under `_`-keys (per query, never source data across queries). Widget keys carry `cr_`.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Dict, List

_ROOT, _APP_DIR = Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[1]
for _p in (str(_ROOT), str(_APP_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from mediator import context_explain as X  # noqa: E402
from mediator import robustness as R  # noqa: E402
from tabs import context_flow as flow  # noqa: E402
from tabs import context_widgets as w  # noqa: E402
from theme import FONTS, TOKENS  # noqa: E402

# Display names only (the case file never shows a table name as the main label); unknown tables are humanised.
RECORDS = {"VEHICLE_REGISTRATION": "Vehicle registration", "OWNERS": "Owner", "VEHICLE_IDENTITY": "Chassis record",
           "OWNERSHIP_HISTORY": "Ownership transfer", "OWNER_CONTACTS": "Owner contact",
           "POLICY_RECORDS": "Insurance policy", "INSURERS": "Insurer", "POLICY_HOLDERS": "Policy holder",
           "POLICY_PARTIES": "Policy party", "CLAIMS": "Insurance claim", "CRIME_RECORDS": "Police FIR",
           "STATIONS": "Police station", "STOLEN_PROPERTY": "Stolen-vehicle record", "PERSONS": "Person in a case",
           "INCIDENT_PERSONS": "Person named in an FIR", "PLATE_CAPTURES": "Camera sighting", "CAMERAS": "Camera",
           "ZONES": "Camera zone", "CAMERA_ZONES": "Camera location", "CHALLANS": "Traffic challan",
           "POLLUTION_CERT": "PUC certificate", "TEST_CENTRES": "PUC test centre", "ANALYSERS": "Emission analyser",
           "CERT_TESTS": "PUC test"}
LEAD_WHAT = {"CAM": "Camera sightings", "INS": "Insurance records", "REG": "Registration records",
             "THEFT": "Police records", "PUC": "Pollution certificates"}
LEADS_SHOWN = 8


def record(table: str) -> str:
    return RECORDS.get(str(table), X.humanise(table))


def agency(sid: str) -> str:
    return w.SOURCE_NAMES.get(str(sid), str(sid))


def _safe(label: str, fn: Callable[[Dict[str, Any]], None], res: Dict[str, Any]) -> None:
    try:
        fn(res)
    except Exception as exc:  # the case file must keep rendering whatever this block hits
        st.caption(f"{label} could not be shown ({type(exc).__name__}).")


def _html(body: str) -> None:
    st.markdown(body, unsafe_allow_html=True)


# ------------------------------------------------------------------ robustness (header line)

def _asked(res: Dict[str, Any]) -> List[str]:
    sids = [s.source_id for s in getattr(res.get("context"), "stats", []) or []]
    sids += ["DOCS"] if res.get("docs") is not None else []
    return sorted(set(sids), key=lambda s: (w.SOURCE_ORDER + [s]).index(s))


def robustness(res: Dict[str, Any]) -> Dict[str, Any]:
    if "_robustness" not in res:
        res["_robustness"] = R.knock_out(res.get("plate") or "", res.get("context"), asked=_asked(res))
    return res["_robustness"]


def render_robustness(res: Dict[str, Any]) -> None:
    _safe("The robustness line", _robustness, res)


def _robustness(res: Dict[str, Any]) -> None:
    if res.get("context") is None:
        return
    ko = robustness(res)
    titles = {w.get(i, "code"): w.get(i, "title") for i in res.get("indicators") or []}
    _html(f'<p style="margin:4px 0;color:var(--cv-ink2);font-size:1rem;">{w.esc(R.summary_line(ko))}</p>')
    with st.expander("Which agencies the verdict rests on"):
        if not ko["indicators"]:
            st.markdown("No warning sign was found. A clear verdict is only as complete as the agencies that "
                        "answered: had any one of them been down, the answer would be a lower bound.")
        for code in ko["indicators"]:
            need = " + ".join(agency(s) for s in ko["minimal"].get(code, [])) or "the agencies together"
            st.markdown(f"**{titles.get(code, code.replace('_', ' ').capitalize())}** needs {need}.")
        for s in ko["asked"]:
            lost = ko["falls_without"].get(s) or []
            st.markdown(f"Without {agency(s)}: " + (", ".join(titles.get(c, c) for c in lost) + " would disappear."
                                                     if lost else "nothing changes."))


# ------------------------------------------------------------------ why is this here + leads not followed

def render_why(res: Dict[str, Any]) -> None:
    _safe("How the records were found", _why, res)


def _why(res: Dict[str, Any]) -> None:
    ctx = res.get("context")
    if ctx is None or not ctx.rows:
        return
    graph = _graph(res)
    with st.expander("How each record was found", expanded=False):
        labels = [f"{i + 1}. {record(r.table)} #{r.pk} · {agency(r.source_id)}" for i, r in enumerate(ctx.rows)]
        pick = st.selectbox("Record", range(len(labels)), format_func=lambda i: labels[i], key="cr_why_pick")
        proof = X.explain_row(ctx.rows[pick or 0], ctx, graph, label=record, name=agency)
        _html('<div class="cv-banner" style="border-left:5px solid var(--cv-info);">'
              f'<p style="margin:0 0 8px;font-weight:600;">{w.esc(proof["path"])}</p>'
              + "".join(f'<p style="margin:2px 0;color:var(--cv-ink);">{w.esc(proof[k])}</p>'
                        for k in ("affinity", "relevance", "admitted", "guarantee")) + "</div>")
    _leads(res, ctx, graph)


def _graph(res: Dict[str, Any]) -> Dict[str, Any]:
    if "_graph" not in res:   # metadata only (data/context_graph.json), read once per query
        try:
            from mediator.context_scoring import load_graph
            res["_graph"] = load_graph()
        except Exception:
            res["_graph"] = {}
    return res["_graph"]


def lead_text(lead: Dict[str, Any], cut: float) -> str:
    what = LEAD_WHAT.get(lead["target"], f"{agency(lead['target'])} records")
    conf, value = float(lead.get("confidence", 1.0)), str(lead["value"])
    if lead.get("kind") == "pk":
        target = f"of a person matched to this case ({conf:.0%} sure)"
    elif lead["attr"] == "chassis":
        target = f"with chassis number {value}"
    else:
        target = f"of a plate that looks like {value}" if conf < 0.999 else f"of plate {value}"
    origin = lead.get("from")
    src = f" (found through {agency(origin[0])}: {record(origin[1])} #{origin[2]})" if origin else ""
    alike = int(lead.get("alike", 0) or 0)   # discovery already folded look-alikes from one record into one lead
    noun = ("other possible match", "other possible matches") if lead.get("kind") == "pk" else (
        "look-alike reading", "look-alike readings")
    more = f", and {alike} {noun[alike > 1]}" if alike else ""
    verdict = (f"below the cut-off {cut:.2f}" if lead["bound"] <= cut + 1e-12
               else f"not followed because the round limit was reached (cut-off {cut:.2f})")
    return f"{what} {target}{more}{src} — best possible score {lead['bound']:.2f}, {verdict}"


def _leads(res: Dict[str, Any], ctx: Any, graph: Dict[str, Any]) -> None:
    leads, cut = sorted(ctx.pruned_leads or [], key=lambda x: -x["bound"])[:LEADS_SHOWN], X.cutoff(ctx)
    if not leads:
        return
    _html('<p class="cv-grouplabel">Leads not followed (the bound proved they could not change the answer)</p>')
    done = res.setdefault("_pursued", {})
    for i, lead in enumerate(leads):
        key = f"{lead['target']}|{lead['kind']}|{lead['value']}"
        text_col, button_col = st.columns([6, 1], vertical_alignment="center")
        text_col.markdown(w.esc(lead_text(lead, cut)))
        if button_col.button("Pursue", key=f"cr_pursue_{i}", help="Ask that agency anyway, to check the bound"):
            try:
                done[key] = X.pursue(lead, ctx, graph=graph or None)
            except Exception as exc:  # a source failure degrades to a note
                done[key] = {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}
        if key in done:
            st.caption(pursued_note(done[key]))


def pursued_note(out: Dict[str, Any]) -> str:
    if out.get("error"):
        return f"Could not follow this lead: {out['error']}"
    if out.get("complete") is False:
        return "Could not follow this lead: that agency did not answer."
    rows, above, cut = out.get("rows") or [], out.get("above") or [], float(out.get("cutoff", 0.0))
    best = max((r.score for r in rows), default=0.0)
    if above:
        names = ", ".join(f"{record(r.table)} #{r.pk} ({r.score:.2f})" for r in above[:3])
        return f"Followed anyway: {len(rows)} more records; {len(above)} scored above the cut-off {cut:.2f}: {names}."
    return (f"Followed anyway: {len(rows)} more record{'' if len(rows) == 1 else 's'}, none scored above the cut-off "
            f"{cut:.2f} (best {best:.2f}, bound {float(out.get('bound', 0.0)):.2f}).")


# ------------------------------------------------------------------ receipt

def render_receipt(res: Dict[str, Any]) -> None:
    _safe("The receipt", _receipt, res)


def bill(res: Dict[str, Any]) -> Dict[str, Any]:
    ctx, docs = res.get("context"), res.get("docs")
    m = flow.moved(ctx, res.get("plate"))
    lines = [{"sid": s.source_id, "rows": int(s.rows_shipped), "kb": s.bytes_shipped / 1024, "trips": s.round_trips,
              "held": X.guarantee(ctx, s.source_id, agency)} for s in sorted(
                  m["stats"], key=lambda s: (w.SOURCE_ORDER + [s.source_id]).index(s.source_id))]
    if docs is not None:
        lines.append({"sid": "DOCS", "rows": int(getattr(docs, "rows_shipped", 0) or 0), "kb": None, "trips": None,
                      "held": "Documents are ranked by text match; only the shown ones were sent."})
    rows = sum(x["rows"] for x in lines)
    base = m["baseline"]
    return {"lines": lines, "rows": rows, "kb": m["bytes"] / 1024, "agencies": len(lines), "baseline": base,
            "from_eval": m["baseline_from_eval"], "saved": (max(0.0, 1 - rows / base) if base else None),
            "pruned": m["pruned"], "considered": m["considered"]}


def headline(b: Dict[str, Any]) -> str:
    cost = f"This answer cost {b['rows']:,} rows ({b['kb']:,.0f} KB) from {b['agencies']} agencies"
    if not b["baseline"]:
        return cost + "."
    return cost + f"; copying everything would have moved {b['baseline']:,} ({b['saved']:.0%} less)."


def _receipt(res: Dict[str, Any]) -> None:
    if res.get("context") is None:
        return
    b = bill(res)
    _html(f'<p style="font-size:1.15rem;font-weight:600;margin:0 0 12px;">{w.esc(headline(b))}</p>')
    body = "".join(_bill_row(x) for x in b["lines"])
    body += f"<tr><td><b>Total</b></td><td><b>{b['rows']:,}</b></td><td><b>{b['kb']:,.1f}</b></td><td></td><td></td></tr>"
    if b["baseline"]:
        note = " (measured in the last evaluation run)" if b["from_eval"] else " (counted live inside each database)"
        body += (f"<tr><td>A copy-everything warehouse</td><td>{b['baseline']:,}</td><td></td><td></td>"
                 f"<td>{w.esc('would move every linked row' + note)}</td></tr>")
    _html('<div class="cv-tablewrap"><table class="cv-table"><thead><tr><th>Agency</th><th>Rows</th><th>KB</th>'
          f'<th>Round trips</th><th>Held back</th></tr></thead><tbody>{body}</tbody></table></div>')
    if b["considered"]:
        _html(f'<p style="margin:12px 0 4px;">Follow-ups into other agencies: {b["considered"]:,} considered, '
              f'{b["considered"] - b["pruned"]:,} followed, {b["pruned"]:,} pruned because they could not '
              'beat the records already found.</p>')
    fig = stop_chart(res["context"].trace or [])
    if fig is not None:
        st.plotly_chart(fig, key="cr_stop_chart", config={"displayModeBar": False})
        st.caption(f"Discovery stops in the first round where the solid line (the {X._ordinal(len(res['context'].rows))} "
                   "best score so far) is at or above the dashed one (the best any remaining follow-up could reach).")


def _bill_row(x: Dict[str, Any]) -> str:
    kb = "—" if x["kb"] is None else f"{x['kb']:,.1f}"
    trips = "—" if x["trips"] is None else str(x["trips"])
    return (f"<tr><td>{w.source_chip(x['sid'])} {w.esc(agency(x['sid']))}</td><td>{x['rows']:,}</td><td>{kb}</td>"
            f"<td>{trips}</td><td>{w.esc(x['held'])}</td></tr>")


def stop_chart(trace: List[Dict[str, Any]]) -> Any:
    rounds = [t for t in trace if not t.get("final") and "tau_n" in t]
    if not rounds:
        return None
    x = [int(t["round"]) for t in rounds]
    reach = [max(float(t.get("max_frontier_bound", 0) or 0), float(t.get("max_source_bound", 0) or 0)) for t in rounds]
    fig = go.Figure([go.Scatter(x=x, y=[float(t["tau_n"]) for t in rounds], name="n-th best score so far",
                                mode="lines+markers", line=dict(color=TOKENS["brand"], width=3),
                                hovertemplate="Round %{x}: %{y:.2f}<extra></extra>"),
                     go.Scatter(x=x, y=reach, name="best a remaining follow-up could reach", mode="lines+markers",
                                line=dict(color=TOKENS["warn"], width=3, dash="dash"),
                                hovertemplate="Round %{x}: %{y:.2f}<extra></extra>")])
    fig.update_layout(height=260, margin=dict(l=10, r=20, t=30, b=10), paper_bgcolor=TOKENS["panel"],
                      plot_bgcolor=TOKENS["panel"], font=dict(color=TOKENS["ink"], family=FONTS["body"], size=14),
                      legend=dict(orientation="h", y=1.18, x=0, title=None), hovermode="x unified")
    fig.update_xaxes(title="Round", dtick=1, showgrid=False)
    fig.update_yaxes(title="Score", range=[0, 1.05], gridcolor=TOKENS["line"], zeroline=False)
    return fig
