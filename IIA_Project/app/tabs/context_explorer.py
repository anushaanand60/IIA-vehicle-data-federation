"""CONTEXT-ADDED (R2/R3): Context Explorer — one search box, the related records every agency holds.

A plate opens its context: each database ranks its own rows and ships only its best few, follow-ups
cross into other databases only while they can still beat what is already found, and documents are
retrieved with that context rather than with the raw question. Free text ("stolen white Creta
Gurgaon") first ranks the vehicles that fit; clicking one opens its context.

Above the fold: the search box, one verdict line, the data-moved counter. Everything else sits in
tabs (progressive disclosure). Work happens in `context_flow.run_query`; markup in `context_widgets`.
Keys carry the `cx_` prefix. The latest answer is kept in session state for widget reruns only;
asking (or asking again) always re-reads the sources.

CONTEXT-ADDED (R2/R3: case file): the five tabs are replaced by one scroll -- header (plate, identity, verdict,
robustness, one facts line), what happened, timeline, warning signs, documents, who says what, related records,
how it was found, what this answer cost. Timeline, documents, claims, robustness, why and receipt are drawn by
other modules (context_timeline / context_docs / context_receipt), imported per block: a missing one is skipped
and listed in res["missing"].
"""
from __future__ import annotations

import importlib  # CONTEXT-ADDED (R2/R3: the case file's blocks from other modules, imported one at a time)
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

# CONTEXT-REPLACED (R2/R3: no KPI tiles or bar chart on the case file; the receipt module draws the cost):
# import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

# from components import kpi_row, page_head, panel, section  # noqa: E402
from components import page_head, panel, section  # noqa: E402
from tabs import context_flow as flow  # noqa: E402
from tabs import context_widgets as w  # noqa: E402
# from theme import FONTS, TOKENS  # noqa: E402
# CONTEXT-END

# CONTEXT-ADDED (R2/R3: the case file, top to bottom; (title, anchor, [(module, function)]) -- one dict of order)
SECTIONS = [("What happened", "cx-what", []), ("Timeline", "cx-timeline", [("context_timeline", "render_timeline")]),
            ("Warning signs", "cx-signs", []), ("Documents", "cx-docs", [("context_docs", "render_documents")]),
            ("Who says what", "cx-claims", [("context_docs", "render_claims")]),
            ("Related records", "cx-records", []), ("How it was found", "cx-how", [("context_receipt", "render_why")]),
            ("What this answer cost", "cx-cost", [("context_receipt", "render_receipt")])]
# CONTEXT-END

SCENARIOS = _ROOT / "data" / "phase3_scenarios.json"
BASE_EXAMPLES = ["DL05CD9876", "stolen white Creta Gurgaon", "expired insurance Hyundai seen in Noida", "HR26EF4455"]


def examples() -> List[str]:
    """Story plate, a planted ALIBI plate if the generator has written one, and two free-text questions."""
    out = list(BASE_EXAMPLES)
    try:
        data = json.loads(SCENARIOS.read_text(encoding="utf-8"))
        items = data.get("scenarios", data) if isinstance(data, dict) else data
        alibi = next(s for s in items if "ALIBI" in json.dumps(s).upper() and (s.get("plate") or s.get("plates")))
        out.insert(1, str(alibi.get("plate") or alibi["plates"][0]))
    except Exception:  # no scenarios file yet: the four built-in examples are enough
        pass
    return out


def _ask(text: str) -> None:
    st.session_state["cx_q"] = text
    st.session_state["cx_go_flag"] = True


def _pick_example() -> None:
    if st.session_state.get("cx_example"):
        _ask(st.session_state["cx_example"])
        st.session_state["cx_example"] = None


def _open_vehicle(plate: str, back_to: str) -> None:
    st.session_state["cx_back"] = back_to
    _ask(plate)


def _search() -> bool:
    with panel("cx_search"):
        st.pills("Try", examples(), key="cx_example", on_change=_pick_example)
        # CONTEXT-ADDED (R2: the search box is the page's one control; its text rendered at caption size)
        st.markdown('<style>.st-key-cx_q input{font-size:1.15rem !important;font-family:inherit !important;}'
                    '.st-key-cx_q [data-baseweb="input"]{min-height:52px;}</style>', unsafe_allow_html=True)
        # CONTEXT-END
        with st.container(horizontal=True, vertical_alignment="bottom"):
            st.text_input("A plate or a question in plain words", key="cx_q",
                          placeholder="DL05CD9876   or   stolen white Creta Gurgaon")
            clicked = st.button("Search", key="cx_go", type="primary")
    flagged = st.session_state.pop("cx_go_flag", False)
    return bool(clicked or flagged)


# CONTEXT-REPLACED (R2/R3: plain wording -- agency names, no "lower bound", no "sign(s)"):
# def _verdict(res: Dict[str, Any]) -> None:
#     ctx, inds = res.get("context"), res.get("indicators") or []
#     high = [i for i in inds if str(w.get(i, "severity")).lower() == "high"]
#     down = [s.source_id for s in (getattr(ctx, "stats", []) or []) if s.status != "OK"]
#     if ctx is None:
#         extra = f" {len(inds)} warning sign(s) were still found and are listed below." if inds else ""
#         variant, title = "undetermined", f"{res['plate']}: the context could not be gathered.{extra}"
#     elif not getattr(ctx, "complete", True) or down:
#         variant, title = "undetermined", (f"Partial answer for {res['plate']}: {', '.join(down) or 'a source'} did not "
#                                           "answer, so what is shown is a lower bound — nothing here clears the vehicle."
#                                           + (f" {len(inds)} warning sign(s) found so far." if inds else ""))
#     elif high:
#         variant, title = "alert", f"{res['plate']}: {len(inds)} warning sign(s), {len(high)} serious."
#     elif inds:
#         variant, title = "suspicious", f"{res['plate']}: {len(inds)} warning sign(s) worth a look."
#     else:
#         sources = len({r.source_id for r in ctx.rows})
#         variant, title = "clear", f"{res['plate']}: no warning signs in {len(ctx.rows)} related records from {sources} databases."
#     st.markdown(f'<div class="cv-banner cv-banner--{variant}"><p class="cv-banner-title">{w.esc(title)}</p></div>',
#                 unsafe_allow_html=True)
def _verdict(res: Dict[str, Any]) -> None:
    ctx, inds = res.get("context"), res.get("indicators") or []
    high = [i for i in inds if str(w.get(i, "severity")).lower() == "high"]
    down = [w.SOURCE_NAMES.get(s.source_id, s.source_id) for s in (getattr(ctx, "stats", []) or []) if s.status != "OK"]
    signs = f"{len(inds)} warning sign{'' if len(inds) == 1 else 's'}"
    if ctx is None:
        extra = f" {signs} were still found and are listed below." if inds else ""
        variant, title = "undetermined", f"The agencies could not be asked about {res['plate']}.{extra}"
    elif not getattr(ctx, "complete", True) or down:
        variant, title = "undetermined", (f"Incomplete: {', '.join(down) or 'one agency'} did not answer, so nothing "
                                          f"here clears {res['plate']}." + (f" {signs} found so far." if inds else ""))
    elif high:
        variant, title = "alert", f"{signs} for {res['plate']}, {len(high)} serious."
    elif inds:
        variant, title = "suspicious", f"{signs} for {res['plate']} worth a look."
    else:
        variant, title = "clear", (f"Nothing suspicious about {res['plate']} in {len(ctx.rows)} related records "
                                   f"from {len({r.source_id for r in ctx.rows})} agencies.")
    st.markdown(f'<div class="cv-banner cv-banner--{variant}"><p class="cv-banner-title">{w.esc(title)}</p></div>',
                unsafe_allow_html=True)
# CONTEXT-END


# CONTEXT-REPLACED (R2/R3: five tabs and KPI tiles -> one case-file scroll; old functions kept below, commented):
# def _counter(ctx: Any, plate: str) -> None:
#     m = flow.moved(ctx, plate)
#     base = m["baseline"]
#     tiles = [("Rows sent to the mediator", f"{m['rows']:,}")]
#     if base:
#         tiles += [("Copying every match would send", f"{base:,}"),
#                   ("Less data moved", f"{max(0, 1 - m['rows'] / base):.0%}")]
#     tiles += [("Kilobytes", f"{m['bytes'] / 1024:,.1f}"), ("Round trips", m["round_trips"])]
#     kpi_row(tiles)
#     line = w.data_moved_sentence(m["rows"], base, flow.N_PER_SOURCE)
#     if m["baseline_from_eval"]:
#         line += " (The copy-everything figure was measured for this plate in the last evaluation run.)"
#     if m["considered"]:
#         line += f" Follow-ups into other databases skipped by the bound: {m['pruned']:,} of {m['considered']:,}."
#     st.caption(line)
#
#
# def _tab_records(ctx: Any) -> None:
#     groups: Dict[str, List[Any]] = {}
#     for r in ctx.rows:
#         groups.setdefault(r.source_id, []).append(r)
#     st.caption("Grouped by database, best first. The chain shows the foreign keys followed from the vehicle; "
#                "'via' marks a record reached from another database through a shared chassis, plate or person.")
#     order = [s for s in w.SOURCE_ORDER if s in groups] + [s for s in groups if s not in w.SOURCE_ORDER]
#     for i, sid in enumerate(order):
#         rows = groups[sid]
#         best = max(r.score for r in rows)
#         with st.expander(f"{w.SOURCE_NAMES.get(sid, sid)} ({sid}) · {len(rows)} records · best score {best:.2f}",
#                          expanded=i == 0):
#             st.markdown(w.rows_table(rows), unsafe_allow_html=True)
#     if ctx.persons:
#         with st.expander(f"People matched across agencies · {len(ctx.persons)}"):
#             for p in ctx.persons:
#                 members = ", ".join(w.ref_text(m) for m in p.members)
#                 st.markdown(f"**{w.esc(p.canonical.get('name'))}** — {w.esc(members)} "
#                             f"(weakest link {p.probability:.0%})")
#
#
# def _tab_graph(ctx: Any) -> None:
#     st.caption("Each box is one record, coloured by its database; arrows follow foreign keys; a dashed arrow "
#                "is where the context crossed into another database.")
#     st.markdown(w.source_legend({r.source_id for r in ctx.rows}), unsafe_allow_html=True)
#     # CONTEXT-REPLACED (R2: pass the query so the graph is one connected picture):
#     # st.graphviz_chart(w.context_dot(ctx.rows, flow.table_sources()), width="stretch")
#     st.graphviz_chart(w.context_dot(ctx.rows, flow.table_sources(), query=getattr(ctx.query, "text", None)),
#                       width="stretch")
#     # CONTEXT-END
#
#
# def _tab_indicators(inds: List[Any]) -> None:
#     if not inds:
#         st.markdown("No cross-source contradictions, quick claims or suspicious transfers found for this vehicle.")
#     for ind in sorted(inds, key=lambda i: ["high", "medium", "low"].index(str(w.get(i, "severity", "low")).lower())
#                       if str(w.get(i, "severity", "low")).lower() in ("high", "medium", "low") else 3):
#         st.markdown(w.indicator_card(ind), unsafe_allow_html=True)
#
#
# def _tab_documents(docs: Any, summary: Any) -> None:
#     if summary:
#         section("What the records and documents say", "Every sentence cites the record or document it rests on.")
#         st.markdown(w.summary_html(summary), unsafe_allow_html=True)
#     if docs is None:
#         st.markdown("Documents are not available for this question.")
#         return
#     st.caption("Highlighted: shaded = an exact identifier from the context (plate, chassis, FIR or policy number); "
#                "dotted = a descriptive match (name, place, make, colour).")
#     for d in docs.docs:
#         with st.container(border=True):
#             st.markdown(f"**{w.esc(d.title)}** &nbsp; {w.chip(d.doc_type, TOKENS['neutral'])} "
#                         f"<span class='cv-conf'>score {d.score:.2f}</span>", unsafe_allow_html=True)
#             st.markdown(f'<p style="font-size:1rem;line-height:1.6;">{w.highlight(d.snippet, d.mentions)}</p>',
#                         unsafe_allow_html=True)
#             for c in d.conflicts:
#                 st.markdown(w.conflict_callout(c), unsafe_allow_html=True)
#             if d.linked_rows:
#                 st.markdown("".join(w.chip("supports " + w.ref_text(ref), w.source_colour(ref[0]))
#                                     for ref in d.linked_rows), unsafe_allow_html=True)
#
#
# def _tab_moved(ctx: Any) -> None:
#     stats = sorted(ctx.stats, key=lambda s: w.SOURCE_ORDER.index(s.source_id) if s.source_id in w.SOURCE_ORDER else 9)
#     fig = go.Figure(go.Bar(x=[s.rows_shipped for s in stats], y=[s.source_id for s in stats], orientation="h",
#                            marker_color=TOKENS["brand"], text=[f"{s.rows_shipped:,}" for s in stats],
#                            textposition="outside", hovertemplate="%{y}: %{x} rows<extra></extra>"))
#     fig.update_layout(height=80 + 44 * len(stats), margin=dict(l=10, r=40, t=10, b=30), showlegend=False,
#                       paper_bgcolor=TOKENS["panel"], plot_bgcolor=TOKENS["panel"],
#                       font=dict(color=TOKENS["ink"], family=FONTS["body"], size=14),
#                       xaxis=dict(title="rows sent by each database", gridcolor=TOKENS["line"]),
#                       yaxis=dict(autorange="reversed"))
#     st.plotly_chart(fig, key="cx_moved_chart", config={"displayModeBar": False})
#     st.markdown(w.rows_stats_table(stats), unsafe_allow_html=True)
#
#
# def _render_plate(res: Dict[str, Any]) -> None:
#     ctx = res.get("context")
#     _verdict(res)
#     if ctx is None:
#         _tab_indicators(res.get("indicators") or []) if res.get("indicators") else None
#         return
#     _counter(ctx, res["plate"])
#     inds, docs = res.get("indicators") or [], res.get("docs")
#     n_docs = len(docs.docs) if docs is not None else 0
#     t1, t2, t3, t4, t5 = st.tabs([f"Related records ({len(ctx.rows)})", "Graph", f"Warning signs ({len(inds)})",
#                                   f"Documents ({n_docs})", "Data moved"])
#     with t1:
#         _tab_records(ctx)
#     with t2:
#         _tab_graph(ctx)
#     with t3:
#         _tab_indicators(inds) if res.get("indicators") is not None else st.markdown("Warning signs are not available yet.")
#     with t4:
#         _tab_documents(docs, res.get("summary"))
#     with t5:
#         _tab_moved(ctx)
# CONTEXT-END


# CONTEXT-ADDED (R2/R3: the case file)
def _resolve(res: Dict[str, Any]) -> Dict[str, Any]:
    """Each other module's render function, or None (then its block is skipped and logged in res["missing"])."""
    out: Dict[str, Any] = {}
    for mod, fn in [("context_receipt", "render_robustness")] + [b for _, _, bs in SECTIONS for b in bs]:
        try:
            out[fn] = getattr(importlib.import_module(f"tabs.{mod}"), fn)
        except (ImportError, AttributeError):   # not delivered yet: the rest of the page still renders
            out[fn] = None
            if f"{mod}.{fn}" not in res["missing"]:
                res["missing"].append(f"{mod}.{fn}")
    return out


def _draw(fn: Any, res: Dict[str, Any]) -> None:
    try:
        fn(res)
    except Exception as exc:  # another module's bug costs one quiet line, never the page
        st.caption(f"This part could not be drawn ({type(exc).__name__}).")


def _ident(sid: str, table: str, pk: Any) -> Any:
    return flow.display_id(sid, table, pk)


def _warning_signs(res: Dict[str, Any]) -> None:
    inds = res.get("indicators")
    if inds is None:
        st.markdown("Warning signs could not be checked for this vehicle.")
        return
    if not inds:
        st.markdown("None: no contradictions between agencies, no quick claims, no suspicious transfers.")
    rank = {"high": 0, "medium": 1, "low": 2}
    for ind in sorted(inds, key=lambda i: rank.get(str(w.get(i, "severity", "low")).lower(), 3)):
        st.markdown(w.indicator_card(ind, lambda e: w.plain_ref(e, _ident)), unsafe_allow_html=True)


def _records(ctx: Any) -> None:
    groups: Dict[str, List[Any]] = {}
    for r in ctx.rows:
        groups.setdefault(r.source_id, []).append(r)
    order = [s for s in w.SOURCE_ORDER if s in groups] + [s for s in groups if s not in w.SOURCE_ORDER]
    for i, sid in enumerate(order):
        rows = groups[sid]
        with st.expander(f"{w.SOURCE_NAMES.get(sid, sid)} · {len(rows)} record{'' if len(rows) == 1 else 's'}",
                         expanded=i == 0):
            st.markdown("".join(w.record_line(r, flow.record_facts(r, ctx.rows)) for r in rows),
                        unsafe_allow_html=True)
    if ctx.persons:
        with st.expander(f"The same person in several agencies · {len(ctx.persons)}"):
            for p in ctx.persons:
                members = ", ".join(w.plain_ref(m, _ident) for m in p.members)
                st.markdown(f"**{w.esc(p.canonical.get('name'))}**: {w.esc(members)} "
                            f"(matched with at least {p.probability:.0%} confidence)")


def _graph(ctx: Any) -> None:
    with st.expander("Show the links as a picture"):
        st.caption("Each box is one record, coloured by its agency; arrows follow the links between records; a "
                   "dashed arrow is where the search crossed into another agency.")
        st.markdown(w.source_legend({r.source_id for r in ctx.rows}), unsafe_allow_html=True)
        st.graphviz_chart(w.context_dot(ctx.rows, flow.table_sources(), query=getattr(ctx.query, "text", None)),
                          width="stretch")


def _header(res: Dict[str, Any], fns: Dict[str, Any], shown: List[tuple]) -> None:
    ctx = res.get("context")
    st.markdown(w.case_header(res["plate"], flow.identity(ctx) if ctx is not None else {}), unsafe_allow_html=True)
    _verdict(res)
    if fns.get("render_robustness"):
        _draw(fns["render_robustness"], res)
    if ctx is not None:
        m = flow.moved(ctx, res["plate"])
        rows = m["rows"] + int(getattr(res.get("docs"), "rows_shipped", 0) or 0)   # same total as the receipt
        st.markdown(w.facts_line(res.get("elapsed_ms"), flow.agencies_answered(res), rows, m["baseline"],
                                 m["baseline_from_eval"]), unsafe_allow_html=True)
    st.markdown(w.toc([(t, a) for t, a, _ in shown]), unsafe_allow_html=True)


def _render_plate(res: Dict[str, Any]) -> None:
    ctx, fns = res.get("context"), _resolve(res)
    own = {"cx-what": bool(res.get("summary")), "cx-signs": True, "cx-records": ctx is not None,
           "cx-how": ctx is not None or bool(fns.get("render_why"))}   # the graph alone is enough for this one
    shown = [(t, a, bs) for t, a, bs in SECTIONS if own.get(a, bool(bs) and all(fns.get(f) for _, f in bs))]
    _header(res, fns, shown)
    for title, anchor, blocks in shown:
        st.markdown(w.heading(title, anchor), unsafe_allow_html=True)
        if anchor == "cx-what":
            st.markdown(w.summary_html(res["summary"], w.cite_namer(res.get("docs"), _ident), _ident),
                        unsafe_allow_html=True)
        elif anchor == "cx-signs":
            _warning_signs(res)
        elif anchor == "cx-records":
            _records(ctx)
        for _, fn in blocks:
            if fns.get(fn):
                _draw(fns[fn], res)
        if anchor == "cx-how" and ctx is not None:
            _graph(ctx)
# CONTEXT-END


def _render_topk(res: Dict[str, Any]) -> None:
    tk = res.get("topk")
    if tk is None:
        return
    with panel("cx_topk"):
        section("Vehicles that fit best", "Each database scored the vehicles by its own part of the question; the "
                "search stopped as soon as no unseen vehicle could still make the list.")
        if tk.baseline_rows:
            st.caption(f"Found these {len(tk.vehicles)} by reading {tk.rows_shipped:,} rows instead of "
                       f"{tk.baseline_rows:,} ({max(0, 1 - tk.rows_shipped / tk.baseline_rows):.0%} less): "
                       f"{tk.sorted_accesses} ranked reads and {tk.random_accesses} look-ups.")
        if not tk.vehicles:
            st.markdown("No vehicle matches this description.")
        for i, v in enumerate(tk.vehicles):
            with st.container(horizontal=True, vertical_alignment="center", key=f"cx_v_{i}"):
                st.markdown(f"**{i + 1}. {w.esc(v.plate)}** &nbsp; {w.score_bar(v.score)}", unsafe_allow_html=True)
                st.markdown(w.esc("; ".join(v.explanation[:4])) or "&nbsp;", unsafe_allow_html=True)  # CONTEXT-REPLACED (R2: every agency's reason, not just the first two): was v.explanation[:2]
                st.button("Open context", key=f"cx_open_{i}", on_click=_open_vehicle, args=(v.plate, res["query"]))


def render() -> None:
    page_head("Context Explorer", "Type a plate or a question. Every agency ranks its own records and sends only "
              "its best few; related records, warning signs and documents come back together.")
    if _search() and st.session_state.get("cx_q", "").strip():
        with st.spinner("Asking every database for its best related records…"):
            st.session_state["cx_result"] = flow.run_query(st.session_state["cx_q"].strip())
    res = st.session_state.get("cx_result")
    if not res:
        st.info("Pick an example above or type your own — a plate opens its full context, plain words find the "
                "vehicles that fit.")
        return
    # CONTEXT-REPLACED (R2/R3: the understood-query pills only for free text -- a plate is shown large in the header;
    # the "not available yet" note moves to the bottom so it also lists case-file blocks found missing while drawing):
    # parsed = w.predicate_chips(res.get("parsed"))
    parsed = w.predicate_chips(res.get("parsed")) if not res.get("plate") else ""
    if parsed:
        st.markdown(parsed, unsafe_allow_html=True)
    back = st.session_state.get("cx_back")
    if back and res.get("plate") and back != res["query"]:
        st.button(f"Back to results for “{back}”", key="cx_back_btn", on_click=_ask, args=(back,))
    # if res["missing"]:
    #     st.caption("Not available yet: " + ", ".join(res["missing"]) + ". The rest of the page still works.")
    for problem in res["problems"]:
        st.warning(problem)
    _render_plate(res) if res.get("plate") else _render_topk(res)
    if res["missing"]:
        st.caption("Not available yet: " + ", ".join(res["missing"]) + ". The rest of the page still works.")
    # CONTEXT-END
