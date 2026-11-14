"""CONTEXT-ADDED (R2/R3: cross-agency timeline with forks) - the case file's "When" block.

render_timeline(res) draws mediator.timeline.build_timeline: one lane per agency, a soft red band while the car
was officially stolen, the insurance cover as a band in the insurance lane, red-ringed events that happened while
stolen (the alibi), and dashed connectors where a document and a database disagree on a date. A one-sentence
takeaway sits above the chart; a chronological list sits below it. Never raises: any failure is one quiet caption.
The Timeline is kept in res["_timeline"] (per query, not a cache of source data). Keys carry the `tl_` prefix.
"""
from __future__ import annotations

import html
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict

_ROOT = Path(__file__).resolve().parents[2]
_APP_DIR = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _APP_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import plotly.graph_objects as go  # noqa: E402
import streamlit as st  # noqa: E402

from components import chip  # noqa: E402
from mediator.timeline import Timeline, build_timeline, day, takeaway  # noqa: E402
from tabs.context_widgets import SOURCE_NAMES, SOURCE_ORDER, source_colour  # noqa: E402
from theme import FONTS, TOKENS  # noqa: E402

LIST_OPEN_MAX = 8      # a longer list starts collapsed


def timeline_of(res: Dict[str, Any]) -> Timeline:
    if "_timeline" not in res:
        res["_timeline"] = build_timeline(res.get("context"), res.get("docs"))
    return res["_timeline"]


def _stamp(t: datetime) -> str:
    return day(t) + (f", {t:%H:%M}" if (t.hour, t.minute) != (0, 0) else "")


def _hover(e: Any) -> str:
    tail = []
    if e.inside_stolen:
        tail.append("<b>while the car was officially stolen</b>")
    if e.future:
        tail.append("scheduled (in the future)")
    where = f"document {e.doc_id}" + (f": {e.detail}" if e.detail else "") if e.kind == "text" else         SOURCE_NAMES.get(e.source_id, e.source_id)
    return f"<b>{html.escape(e.label)}</b><br>{_stamp(e.when)}<br>{html.escape(where)}" + "".join(f"<br>{t}" for t in tail)


def focus_range(tl: Timeline) -> Any:
    """Open the chart on the part that matters (stolen periods, forks) when the record spans years; the rest is a
    double-click away and always in the list below. Trade-off: a far-off registration is off-screen at first."""
    xs = [e.when for e in tl.events] + [iv.end for iv in tl.intervals] + ([tl.now] if tl.now else [])
    if not tl.events:
        return None
    lo, hi = min(xs), max(xs)
    ids = {f.text_eid for f in tl.forks} | {f.row_eid for f in tl.forks}
    hot = [iv.start for iv in tl.intervals if iv.kind == "stolen"] + [iv.end for iv in tl.intervals if iv.kind == "stolen"]
    hot += [e.when for e in tl.events if e.inside_stolen or e.eid in ids]
    if hot and (hi - lo).days > 540:
        lo, hi = max(lo, min(hot) - timedelta(days=120)), min(hi, max(hot) + timedelta(days=90))
    pad = timedelta(days=max(10, (hi - lo).days // 25))
    return [lo - pad, hi + pad]


def figure(tl: Timeline) -> go.Figure:
    """Pure plotly figure, so it can be tested without Streamlit."""
    present = {e.source_id for e in tl.events}
    lanes = [s for s in SOURCE_ORDER if s in present] + sorted(present - set(SOURCE_ORDER))
    y = {s: len(lanes) - 1 - i for i, s in enumerate(lanes)}           # first agency at the top
    by_id = {e.eid: e for e in tl.events}
    view = focus_range(tl)
    fig = go.Figure()
    for iv in tl.intervals:
        if iv.kind == "stolen":
            fig.add_vrect(x0=iv.start, x1=iv.end, fillcolor=TOKENS["bad"], opacity=0.09, line_width=0, layer="below",
                          annotation_text="reported stolen" + (" (still open)" if iv.open_ended else ""),
                          annotation_position="top left", annotation_font=dict(color=TOKENS["bad"], size=13))
        elif iv.source_id in y:
            fig.add_shape(type="rect", x0=iv.start, x1=iv.end, y0=y[iv.source_id] - 0.2, y1=y[iv.source_id] + 0.2,
                          fillcolor=source_colour(iv.source_id), opacity=0.16, layer="below",
                          line=dict(width=1, color=source_colour(iv.source_id), dash="dot"))
            early = bool(view) and iv.start < view[0]          # band starts off-screen: say since when
            fig.add_annotation(x=view[0] if early else iv.start, y=y[iv.source_id] + 0.2, showarrow=False,
                               text="insurance cover" + (f" (since {day(iv.start)})" if early else ""),
                               xanchor="left", yanchor="bottom", font=dict(size=12, color=TOKENS["ink2"]))
    for s in lanes:
        evs = [e for e in tl.events if e.source_id == s and not e.inside_stolen]
        if evs:
            fig.add_trace(go.Scatter(
                x=[e.when for e in evs], y=[y[s]] * len(evs), mode="markers", hoverinfo="text",
                hovertext=[_hover(e) for e in evs], showlegend=False,
                marker=dict(size=12, symbol="diamond" if s == "DOCS" else "circle", color=source_colour(s),
                            opacity=[0.5 if e.future else 1.0 for e in evs], line=dict(width=1, color=TOKENS["panel"]))))
    hot = [e for e in tl.events if e.inside_stolen]
    if hot:
        for size, symbol in ((26, "circle-open"), (13, "circle")):     # the ring, then the dot
            fig.add_trace(go.Scatter(x=[e.when for e in hot], y=[y[e.source_id] for e in hot], mode="markers",
                                     hoverinfo="text", hovertext=[_hover(e) for e in hot], showlegend=False,
                                     marker=dict(size=size, symbol=symbol, color=TOKENS["bad"], line=dict(width=3))))
    for f in tl.forks:
        a, b = by_id.get(f.text_eid), by_id.get(f.row_eid)
        if a is None or b is None or b.source_id not in y:
            continue
        tip = (f"<b>Text says:</b> {html.escape(f.text_value)}<br><b>Database says:</b> {html.escape(f.db_value)}"
               f"<br>from document {html.escape(f.doc_id)}")
        fig.add_trace(go.Scatter(x=[a.when, b.when], y=[y[a.source_id], y[b.source_id]], mode="lines+markers",
                                 hoverinfo="text", hovertext=[tip, tip], showlegend=False,
                                 line=dict(color=TOKENS["warn"], width=2, dash="dash"),
                                 marker=dict(size=20, symbol="circle-open", color=TOKENS["warn"], line=dict(width=2))))
    if tl.now:
        fig.add_vline(x=tl.now, line=dict(color=TOKENS["ink_muted"], width=1, dash="dot"))
        fig.add_annotation(x=tl.now, y=1, yref="paper", text="today", showarrow=False, yanchor="bottom", xanchor="left",
                           font=dict(size=12, color=TOKENS["ink_muted"]))
    fig.update_layout(height=120 + 58 * len(lanes), margin=dict(l=10, r=16, t=40, b=40), hovermode="closest",
                      paper_bgcolor=TOKENS["panel"], plot_bgcolor=TOKENS["panel"], showlegend=False,
                      font=dict(color=TOKENS["ink"], family=FONTS["body"], size=14),
                      hoverlabel=dict(bgcolor=TOKENS["panel"], font=dict(color=TOKENS["ink"], size=14)))
    fig.update_yaxes(tickvals=list(y.values()), ticktext=[SOURCE_NAMES.get(s, s) for s in y], automargin=True,
                     range=[-0.7, len(lanes) - 0.3], showgrid=True, gridcolor=TOKENS["line"], zeroline=False,
                     fixedrange=True)
    fig.update_xaxes(type="date", showgrid=False, range=view, automargin=True)
    return fig


def list_html(tl: Timeline) -> str:
    forked = {f.text_eid for f in tl.forks} | {f.row_eid for f in tl.forks}
    rows = []
    for e in tl.events:
        flags = (chip("while stolen", TOKENS["bad"]) if e.inside_stolen else "") + \
                (chip("dates disagree", TOKENS["warn"]) if e.eid in forked else "")
        note = (f'<span style="color:var(--cv-ink2);"> · from document {html.escape(str(e.doc_id))}'
                f'{": " + html.escape(e.detail) if e.detail else ""}</span>' if e.kind == "text" else "")
        rows.append(f'<li style="display:grid;grid-template-columns:minmax(7.5em,auto) minmax(0,1fr);gap:4px 12px;'
                    f'align-items:baseline;padding:8px 0;border-bottom:1px solid var(--cv-line);">'
                    f'<span style="font-family:var(--cv-font-mono);font-size:0.95rem;">{html.escape(_stamp(e.when))}</span>'
                    f'<span>{chip(SOURCE_NAMES.get(e.source_id, e.source_id), source_colour(e.source_id))} '
                    f'{html.escape(e.label)}{note} {flags}</span></li>')
    return f'<ul style="list-style:none;margin:0;padding:0;">{"".join(rows)}</ul>'


def _takeaway_html(tl: Timeline) -> str:
    tone = "bad" if tl.counts.get("inside_stolen") else "warn" if tl.forks else "info"
    return (f'<div class="cv-banner" style="border-left:5px solid var(--cv-{tone});padding:12px 16px;">'
            f'<p style="margin:0;font-size:1.05rem;color:var(--cv-ink);">{html.escape(takeaway(tl))}</p></div>')


def render_timeline(res: Dict[str, Any]) -> None:
    try:
        if not res or res.get("context") is None:
            st.caption("No timeline: no agency records were found for this query.")
            return
        tl = timeline_of(res)
        if not tl.events:
            st.caption("No dated records were found for this vehicle, so there is no timeline to draw.")
            return
        st.markdown(_takeaway_html(tl), unsafe_allow_html=True)
        st.plotly_chart(figure(tl), key="tl_chart", config={"displayModeBar": False})
        st.caption("One row per agency. Shaded red: officially stolen. Red ring: happened while stolen. "
                   "Dashed line: a document and a database give different dates. Faded: a date still to come.")
        n = len(tl.events)
        if n > LIST_OPEN_MAX:
            with st.expander(f"All {n} events, in order", expanded=False):
                st.markdown(list_html(tl), unsafe_allow_html=True)
        else:
            st.markdown(list_html(tl), unsafe_allow_html=True)
    except Exception:  # noqa: BLE001 - the case file must keep rendering whatever this block hits
        st.caption("The timeline could not be drawn for this vehicle.")
