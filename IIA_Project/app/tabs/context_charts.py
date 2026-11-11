"""CONTEXT-ADDED (R2/R3): Plotly figure builders for the Context Evaluation page.

One look for every chart: white panel, one y-axis, recessive grid, text in ink colours, a legend
whenever there are two or more series, hover on every mark. Approaches always get the same colour in
the same order (ours first), so a chart that shows three of them does not repaint the survivors.
Three of the colours sit below 3:1 on white, so every chart has a "Show the numbers" table beside it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import plotly.graph_objects as go

from theme import FONTS, TOKENS

MODE_ORDER = ["bounded", "closure", "topn", "mediator"]
MODE_COLOURS = {"bounded": "#2a78d6", "closure": "#eb6834", "topn": "#1baf7a", "mediator": "#eda100"}
MODE_SHORT = {"bounded": "Ours (bounded)", "closure": "B0 every match + closure", "topn": "B1 top-n, no bounds",
              "mediator": "B2 mediator walks the FKs"}
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]   # generic fixed order


def _layout(fig: go.Figure, y_title: str, x_title: str = "", log: bool = False, height: int = 380,
            y_range: Optional[Sequence[float]] = None) -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=10, r=20, t=40, b=10), paper_bgcolor=TOKENS["panel"],
                      plot_bgcolor=TOKENS["panel"], font=dict(color=TOKENS["ink"], family=FONTS["body"], size=14),
                      legend=dict(orientation="h", y=1.12, x=0, title=None), hovermode="closest",
                      bargap=0.25, bargroupgap=0.08)
    fig.update_yaxes(title=y_title + (" (log scale)" if log else ""), type="log" if log else "linear",
                     gridcolor=TOKENS["line"], zeroline=False, range=y_range)
    fig.update_xaxes(title=x_title, showgrid=False, type="category")
    return fig


def modes_in(records: Sequence[Dict[str, Any]]) -> List[str]:
    present = {r.get("mode") for r in records}
    return [m for m in MODE_ORDER if m in present]


def by_mode_bars(records: Sequence[Dict[str, Any]], field: str, y_title: str, pct: bool = False) -> go.Figure:
    """One bar per approach (e.g. overlap with the oracle)."""
    modes = modes_in(records)
    vals = {r["mode"]: r.get(field) for r in records}
    fmt = (lambda v: f"{v:.0%}") if pct else (lambda v: f"{v:,.3g}")
    fig = go.Figure(go.Bar(x=[MODE_SHORT[m] for m in modes], y=[vals[m] or 0 for m in modes],
                           marker_color=[MODE_COLOURS[m] for m in modes],
                           text=[fmt(vals[m]) if vals[m] is not None else "—" for m in modes], textposition="outside",
                           hovertemplate="%{x}: %{text}<extra></extra>"))
    fig.update_traces(marker_line_color=TOKENS["panel"], marker_line_width=2)
    return _layout(fig, y_title, y_range=[0, 1.12] if pct else None)


def scale_chart(records: Sequence[Dict[str, Any]], field: str, y_title: str, log: bool) -> go.Figure:
    """Metric per approach across generator scales: lines when there are several scales, bars for one."""
    scales = list(dict.fromkeys(r["scale"] for r in records))
    labels = [s.replace("x", "×") for s in scales]
    fig = go.Figure()
    for m in modes_in(records):
        ys = [next((r.get(field) for r in records if r["mode"] == m and r["scale"] == s), None) for s in scales]
        hover = f"{MODE_SHORT[m]}<br>%{{x}}: %{{y:,.1f}}<extra></extra>"
        if len(scales) > 1:
            fig.add_trace(go.Scatter(x=labels, y=ys, name=MODE_SHORT[m], mode="lines+markers",
                                     line=dict(color=MODE_COLOURS[m], width=2), marker=dict(size=9),
                                     hovertemplate=hover))
        else:
            fig.add_trace(go.Bar(x=labels, y=ys, name=MODE_SHORT[m], marker_color=MODE_COLOURS[m],
                                 marker_line_color=TOKENS["panel"], marker_line_width=2, hovertemplate=hover))
    return _layout(fig, y_title, "generator scale", log=log)


def convergence_chart(trace: Sequence[Dict[str, Any]]) -> go.Figure:
    """Per round: the n-th best score found (tau_n) and the best score any unexplored follow-up could reach."""
    rounds = [str(t.get("round")) for t in trace]
    fig = go.Figure([
        go.Scatter(x=rounds, y=[t.get("tau_n") for t in trace], name="n-th best score found", mode="lines+markers",
                   line=dict(color=SERIES[0], width=2), marker=dict(size=9)),
        go.Scatter(x=rounds, y=[t.get("max_frontier_bound") for t in trace], name="best a follow-up could still reach",
                   mode="lines+markers", line=dict(color=SERIES[1], width=2, dash="dot"), marker=dict(size=9))])
    stop = next((i for i, t in enumerate(trace)
                 if (t.get("max_frontier_bound") or 0) <= (t.get("tau_n") or 0)), None)
    if stop is not None:
        fig.add_annotation(x=rounds[stop], y=trace[stop].get("tau_n"), text="stop: nothing left can win",
                           showarrow=True, arrowhead=0, ay=-40, font=dict(color=TOKENS["ink"], size=13))
    return _layout(fig, "score", "round")


def sensitivity_chart(rows: Sequence[Dict[str, Any]], field: str, y_title: str, pct: bool) -> go.Figure:
    thetas = sorted({r["theta"] for r in rows})
    fig = go.Figure()
    for i, n in enumerate(sorted({r["n"] for r in rows})):
        ys = [next((r.get(field) for r in rows if r["n"] == n and r["theta"] == t), None) for t in thetas]
        fig.add_trace(go.Scatter(x=[str(t) for t in thetas], y=ys, name=f"n = {n}", mode="lines+markers",
                                 line=dict(color=SERIES[i], width=2), marker=dict(size=9),
                                 hovertemplate=f"n = {n}, θ = %{{x}}: %{{y:,.3g}}<extra></extra>"))
    return _layout(fig, y_title, "θ (stop following a path below this affinity)", y_range=[0, 1.05] if pct else None)


def grouped_bars(categories: Sequence[str], series: Dict[str, Sequence[Optional[float]]], y_title: str,
                 x_title: str = "", pct: bool = False) -> go.Figure:
    """Categories on x, a fixed-order colour per series (e.g. retrieval modes x {nDCG@10, MRR})."""
    fig = go.Figure()
    for i, (name, vals) in enumerate(series.items()):
        fig.add_trace(go.Bar(x=list(categories), y=[v or 0 for v in vals], name=name, marker_color=SERIES[i],
                             marker_line_color=TOKENS["panel"], marker_line_width=2,
                             text=[(f"{v:.2f}" if pct else f"{v:,.0f}") if v is not None else "—" for v in vals],
                             textposition="outside", hovertemplate=f"{name}<br>%{{x}}: %{{text}}<extra></extra>"))
    return _layout(fig, y_title, x_title, y_range=[0, 1.12] if pct else None)
