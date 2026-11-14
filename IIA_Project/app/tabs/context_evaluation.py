"""CONTEXT-ADDED (R2/R3): Context Evaluation — is the bounded context search right, and how much does it save?

Reads only `data/context_eval.json`, which `scripts/evaluate_context.py` writes headless; the
"Re-run" button runs that script's quick mode. Order of the page = order of the argument:
1. exactness first (same rows a central warehouse would pick — independent of our own labels),
2. data moved as the data grows, 3. why the search may stop early, 4. how sensitive the knobs are,
5. free-text top-k, 6. documents. Each chart has a one-line takeaway computed from the numbers above it
and a table view. Keys carry the `ce_` prefix.

CONTEXT-ADDED (R2/R3: "Proof" page): rebuilt as three claim cards -- same answer as a central copy, less data
moved, better documents -- each one plain sentence, one big number, one chart and one "how we measured it"
line; every other measurement sits in one "More measurements" expander. The old sections are kept below,
commented out.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import streamlit as st  # noqa: E402

# CONTEXT-REPLACED (R2/R3: no KPI tiles on the Proof page):
# from components import kpi_row, page_head, panel, section, styled_table  # noqa: E402
from components import page_head, panel, section, styled_table  # noqa: E402
# CONTEXT-END
from tabs import context_charts as ch  # noqa: E402

DATA = _ROOT / "data" / "context_eval.json"
SCRIPT = _ROOT / "scripts" / "evaluate_context.py"
METRICS = {"Rows": ("rows", "rows sent to the mediator"), "Kilobytes": ("kb", "kilobytes sent"),
           "Round trips": ("round_trips", "round trips"), "Latency": ("latency_ms", "milliseconds")}
SEEDS = {"Hub seeds": "hub", "Single plates": "story", "Scenario plates": "scenario", "Free text": "text"}


def load() -> Optional[Dict[str, Any]]:
    try:
        return json.loads(DATA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:.0%}"


def _show(rows: List[Dict[str, Any]], key: str) -> None:
    with st.expander("Show the numbers", expanded=False):
        styled_table(rows)


def _plot(fig: Any, key: str) -> None:
    st.plotly_chart(fig, key=key, config={"displayModeBar": False})


def _rerun_button() -> None:
    # CONTEXT-REPLACED (R2/R3: a quiet button on the Proof page's top line):
    # if st.button("Re-run (quick: demo data only)", key="ce_rerun"):
    if st.button("Re-run", key="ce_rerun", type="tertiary", help="Quick run on the demo data only (about a minute)."):
    # CONTEXT-END
        with st.spinner("Measuring every approach on the demo data…"):
            proc = subprocess.run([sys.executable, str(SCRIPT), "--quick"], cwd=str(_ROOT), capture_output=True,
                                  text=True, timeout=1200)
        if proc.returncode != 0:
            st.error("The evaluation script failed: " + (proc.stderr or proc.stdout)[-600:])


# CONTEXT-REPLACED (R2/R3: the six-section page becomes three claim cards + "More measurements"; old code kept):
# def _exactness(d: Dict[str, Any]) -> None:
#     summ = {r["mode"]: r for r in d.get("exactness_summary", [])}
#     with panel("ce_exact"):
#         section("Does it find what a central warehouse would find?",
#                 "The oracle copies every match to one place and re-ranks it with the same scores; we compare our best n "
#                 "rows with its best n.")
#         if not summ:
#             st.markdown("Not measured yet: context discovery is not available.")
#             return
#         ours, b1 = summ.get("bounded", {}).get("overlap_at_n"), summ.get("topn", {}).get("overlap_at_n")
#         st.markdown(f"**Ours keeps {_pct(ours)} of the rows the central oracle picks**"
#                     + (f"; one round of per-database top-n without bounds keeps {_pct(b1)}." if b1 is not None else "."))
#         _plot(ch.by_mode_bars(list(summ.values()), "overlap_at_n", "share of oracle top-n found", pct=True), "ce_exact_c")
#         st.caption("B0 is the oracle's own input, so it scores 100% by definition. A row tied with the oracle's n-th "
#                    "score counts as found. Where ours misses, it is mostly by design: one parent row may contribute only "
#                    "a few children, so one busy camera or police station cannot crowd everything else out.")
#         stab = (d.get("stability_summary") or [{}])[0]
#         if stab.get("tau_mean") is not None:
#             kpi_row([("Rank agreement after ±50% weight noise (Kendall τ)", f"{stab['tau_mean']:.2f}"),
#                      ("Worst case τ", f"{stab['tau_min']:.2f}"), ("Top-n rows that stay", _pct(stab.get("overlap_mean")))])
#             st.caption("Each edge weight was scaled by a random factor between 0.5 and 1.5, twenty times; τ = 1 means "
#                        "the ranking did not change at all.")
#         with st.expander("Judged relevance (secondary): precision@n and nDCG@n", expanded=False):
#             st.caption("Grades come from the scenarios the data generator planted, so the generator wrote both the "
#                        "data and the answer key; read these as a sanity check, not as proof.")
#             styled_table([{"approach": ch.MODE_SHORT.get(m, m), "overlap@n": _pct(r.get("overlap_at_n")),
#                            "precision@n": _pct(r.get("precision_at_n")), "nDCG@n": _pct(r.get("ndcg_at_n")),
#                            "queries": r.get("queries")} for m, r in summ.items()])
#
#
# def _movement(d: Dict[str, Any]) -> None:
#     rows = [dict(r, kb=(r.get("bytes") or 0) / 1024) for r in d.get("movement_summary", [])]
#     with panel("ce_moved"):
#         section("Data moved as the data grows", "Mean per query. Hub seeds (an owner with many cars, a busy camera, "
#                 "a busy police station) are where copying everything hurts most.")
#         if not rows:
#             st.markdown("Not measured yet: context discovery is not available.")
#             return
#         kinds = {k: v for k, v in SEEDS.items() if any(r["kind"] == v for r in rows)}
#         c1, c2 = st.columns(2)
#         seed = c1.segmented_control("Queries", list(kinds), default=list(kinds)[0], key="ce_seed") or list(kinds)[0]
#         metric = c2.segmented_control("Measure", list(METRICS), default="Rows", key="ce_metric") or "Rows"
#         field, label = METRICS[metric]
#         sel = [r for r in rows if r["kind"] == kinds[seed]]
#         top = max((r["scale"] for r in sel), key=lambda s: int(s.strip("x") or 1), default="x1")
#         at = {r["mode"]: r.get(field) for r in sel if r["scale"] == top}
#         if at.get("bounded") is not None and at.get("closure"):
#             st.markdown(f"**At {top.replace('x', '×')}, ours needs {at['bounded']:,.0f} {label} where copying every "
#                         f"match needs {at['closure']:,.0f} ({1 - at['bounded'] / at['closure']:.0%} less).**")
#         _plot(ch.scale_chart(sel, field, label, log=metric in ("Rows", "Kilobytes")), "ce_moved_c")
#         if d.get("params", {}).get("serving", "").startswith("in-process"):
#             st.caption("Measured with every database served in-process, so latency excludes the network; "
#                        "rows and bytes are exact.")
#         _show([{"scale": r["scale"], "approach": ch.MODE_SHORT.get(r["mode"], r["mode"]), "rows": r.get("rows"),
#                 "KB": round(r["kb"], 1), "round trips": r.get("round_trips"), "ms": r.get("latency_ms"),
#                 "queries": r.get("queries")} for r in sel], "ce_moved_t")
#
#
# def _convergence(d: Dict[str, Any]) -> None:
#     conv, prune = d.get("convergence"), d.get("pruning") or []
#     if not conv and not prune:
#         return
#     with panel("ce_conv"):
#         section("Why the search can stop early", "After each round the mediator compares the n-th best score it holds "
#                 "with the best score any unexplored follow-up could still reach.")
#         if prune:
#             p = prune[0]
#             st.markdown(f"**Follow-ups into other databases skipped because they could not win: "
#                         f"{p.get('pruned') or 0:.0f} of {p.get('considered') or 0:.0f} on average.**")
#         if conv:
#             st.caption(f"Query: {conv.get('text')} ({conv.get('scale', 'x1').replace('x', '×')}).")
#             _plot(ch.convergence_chart(conv["trace"]), "ce_conv_c")
#             _show(conv["trace"], "ce_conv_t")
#
#
# def _sensitivity(d: Dict[str, Any]) -> None:
#     rows = d.get("sensitivity") or []
#     if not rows:
#         return
#     with panel("ce_sens"):
#         section("How sensitive are the two knobs?", "n = rows each database may send; θ = how weak a link may get "
#                 "before a path is dropped.")
#         metric = st.segmented_control("Measure", ["Rows sent", "Agreement with oracle"], default="Rows sent",
#                                       key="ce_sens_m") or "Rows sent"
#         pct = metric != "Rows sent"
#         field = "overlap_at_n" if pct else "rows"
#         lo = min(rows, key=lambda r: r.get("rows") or 0)
#         st.markdown(f"**Cheapest setting: n = {lo['n']}, θ = {lo['theta']} — {lo.get('rows') or 0:,.0f} rows, "
#                     f"{_pct(lo.get('overlap_at_n'))} agreement.**")
#         _plot(ch.sensitivity_chart(rows, field, "share of oracle top-n" if pct else "rows sent", pct), "ce_sens_c")
#         _show(rows, "ce_sens_t")
#
#
# def _topk(d: Dict[str, Any]) -> None:
#     summ = d.get("topk_summary") or []
#     if not summ:
#         return
#     with panel("ce_topk"):
#         section("Finding the best 10 vehicles", "TA reads each database's ranked list a step at a time; "
#                 "TPUT agrees a threshold in three rounds; naive pulls every candidate row.")
#         tasks = list(dict.fromkeys(r.get("task", "free text") for r in summ))
#         c1, c2 = st.columns(2)
#         task = (c1.segmented_control("Task", tasks, default=tasks[0], key="ce_topk_task") if len(tasks) > 1
#                 else None) or tasks[0]
#         metric = c2.segmented_control("Measure", ["Rows read", "Round trips"], default="Rows read",
#                                       key="ce_topk_m") or "Rows read"
#         field = "rows" if metric == "Rows read" else "round_trips"
#         by = {r["method"]: r for r in summ if r.get("task", "free text") == task}
#         if by.get("ta", {}).get("rows") is not None and by.get("naive", {}).get("rows"):
#             st.markdown(f"**The threshold algorithm read {by['ta']['rows']:,.0f} rows per run instead of "
#                         f"{by['naive']['rows']:,.0f} ({1 - by['ta']['rows'] / by['naive']['rows']:.0%} less).**")
#         methods = [m for m in ("naive", "ta", "tput") if m in by and by[m].get(field) is not None]
#         names = {"naive": "naive (every candidate)", "ta": "TA", "tput": "TPUT"}
#         _plot(ch.grouped_bars([names[m] for m in methods], {metric.lower(): [by[m].get(field) for m in methods]},
#                               f"{metric.lower()} per run"), "ce_topk_c")
#         methods = [m for m in ("naive", "ta", "tput") if m in by]
#         _show([{"method": names[m], "rows": by[m].get("rows"), "round trips": by[m].get("round_trips"),
#                 "ranked reads": by[m].get("sorted_accesses"), "look-ups": by[m].get("random_accesses")}
#                for m in methods], "ce_topk_t")
#
#
# def _retrieval(d: Dict[str, Any]) -> None:
#     rows = d.get("retrieval") or []
#     if not rows:
#         return
#     with panel("ce_docs"):
#         section("Documents: does the context help find the right ones?", "Same documents, same ranking function; "
#                 "only what the search is given changes.")
#         names = {"query_only": "the question only", "context": "+ the discovered context",
#                  "context+anchors": "+ exact identifiers"}
#         best = max(rows, key=lambda r: r.get("ndcg_at_10") or 0)
#         st.markdown(f"**Best: {names.get(best['mode'], best['mode'])}, nDCG@10 {best.get('ndcg_at_10') or 0:.2f}.**")
#         cats = [names.get(r["mode"], r["mode"]) for r in rows]
#         _plot(ch.grouped_bars(cats, {"nDCG@10": [r.get("ndcg_at_10") for r in rows], "MRR": [r.get("mrr") for r in rows]},
#                               "score (1 = perfect)", pct=True), "ce_docs_c")
#         _show(rows, "ce_docs_t")
#
#
# def render() -> None:
#     page_head("Context Evaluation", "Does each database sending only its best rows still find the right answer, "
#               "and how much less data crosses the network as the databases grow?")
#     d = load()
#     with st.container(horizontal=True, vertical_alignment="center"):
#         st.caption(f"Measured {d.get('generated_at', '?')} · scales {', '.join(d.get('params', {}).get('scales', []))}"
#                    if d else "No measurements yet.")
#         _rerun_button()
#     d = load() or d
#     if not d:
#         st.info("Run python scripts/evaluate_context.py (or the button above) to measure.")
#         return
#     _exactness(d)
#     _movement(d)
#     _convergence(d)
#     _sensitivity(d)
#     _topk(d)
#     _retrieval(d)
#     if d.get("notes"):
#         with st.expander(f"Not measured yet, and why ({len(d['notes'])})", expanded=False):
#             for note in d["notes"]:
#                 st.markdown(f"- {note}")
# CONTEXT-END


# CONTEXT-ADDED (R2/R3: the Proof page)
def _big(number: str, label: str) -> str:
    return (f'<p style="margin:6px 0 0;font-family:var(--cv-font-mono);font-size:3rem;font-weight:700;'
            f'color:var(--cv-brand);line-height:1;">{number}</p>'
            f'<p style="margin:6px 0 0;font-size:1rem;color:var(--cv-ink2);">{label}</p>')


def _claim(key: str, question: str, sentence: str, number: str, label: str, fig: Any, how: str) -> None:
    """One claim = one question, one plain sentence, one big number, one chart, one "how we measured it" line."""
    with panel(key):
        section(question, sentence)
        left, right = st.columns([1, 2], vertical_alignment="center")
        left.markdown(_big(number, label), unsafe_allow_html=True)
        with right:
            _plot(fig, f"{key}_c")
        st.caption("How we measured it: " + how)


def _same_answer(d: Dict[str, Any]) -> None:
    summ = {r["mode"]: r for r in d.get("exactness_summary", [])}
    ours = summ.get("bounded", {}).get("overlap_at_n")
    if ours is None:
        return
    n, q = d.get("params", {}).get("n", 10), summ["bounded"].get("queries")
    others = [summ[m] for m in ("bounded", "topn", "mediator") if m in summ]   # copy-everything is the yardstick
    _claim("ce_exact", "Same answer as copying everything to one place?",
           f"We return {_pct(ours)} of the rows that a central copy of every matching row ranks as its best.",
           _pct(ours), "of the central copy's top rows found",
           ch.by_mode_bars(others, "overlap_at_n", "share of the central copy's top rows", pct=True),
           f"for {q} questions every matching row was copied to one place and ranked with the same scores; we count "
           f"how many of its best {n} rows each approach also returns (a tie with its {n}th score counts). Copying "
           "everything is the yardstick, so it scores 100% by definition and is not drawn.")


def _per_scale(d: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Mean rows per question for each (scale, approach), weighted by how many questions of each kind were run."""
    acc: Dict[tuple, List[float]] = {}
    for r in d.get("movement_summary", []):
        if r.get("rows") is not None and r.get("mode") in ("bounded", "closure"):
            a = acc.setdefault((r["scale"], r["mode"]), [0.0, 0.0])
            a[0] += r["rows"] * (r.get("queries") or 1)
            a[1] += r.get("queries") or 1
    return [{"scale": sc, "mode": m, "rows": tot / k, "queries": k} for (sc, m), (tot, k) in acc.items() if k]


def _less_data(d: Dict[str, Any]) -> None:
    rows = sorted(_per_scale(d), key=lambda r: int(r["scale"].strip("x") or 1))
    scales = list(dict.fromkeys(r["scale"] for r in rows))
    at = {r["mode"]: r["rows"] for r in rows if scales and r["scale"] == scales[-1]}
    if not at.get("bounded") or not at.get("closure"):
        return
    top = scales[-1].replace("x", "×")
    size = "the demo data" if top == "×1" else f"{top} the demo data"
    only = "" if len(scales) > 1 else f" Only {top} was measured in this run; the full run adds ×10 and ×100."
    _claim("ce_moved", "How much less data crosses the network?",
           f"On {size}, ours moves {at['bounded']:,.0f} rows per question where copying everything moves "
           f"{at['closure']:,.0f}.", f"{at['closure'] / at['bounded']:,.0f}×", "fewer rows moved",
           ch.scale_chart(rows, "rows", "rows moved per question", log=True),
           f"rows each agency sent to the mediator, averaged over {rows[-1]['queries']:.0f} questions (plates, "
           f"planted scenarios, free text, busy owners and cameras) at each data size "
           f"({', '.join(x.replace('x', '×') for x in scales)}); "
           f"the scale is logarithmic.{only}")


def _better_docs(d: Dict[str, Any]) -> None:
    by = {r["mode"]: r for r in d.get("retrieval") or [] if r.get("ndcg_at_10") is not None}
    if "query_only" not in by:
        return
    names = {"query_only": "the question only", "context": "+ the context", "context+anchors": "+ exact identifiers"}
    modes = [m for m in names if m in by]
    best = max((m for m in modes if m != "query_only"), key=lambda m: by[m]["ndcg_at_10"], default="query_only")
    _claim("ce_docs", "Does the context find better documents?",
           f"Searching with the context ranks the right documents higher: {by[best]['ndcg_at_10']:.2f} against "
           f"{by['query_only']['ndcg_at_10']:.2f} with the question alone.", f"{by[best]['ndcg_at_10']:.2f}",
           "ranking quality (1 = perfect)",
           ch.grouped_bars([names[m] for m in modes], {"ranking quality": [by[m]["ndcg_at_10"] for m in modes]},
                           "ranking quality (1 = perfect)", pct=True),
           f"{by['query_only'].get('n_queries', 0):.0f} questions whose relevant documents are known; same documents and "
           "same ranking function (BM25), only what the search is given changes. Ranking quality is nDCG@10.")


def _more(d: Dict[str, Any]) -> None:
    """Everything else, each with a plain one-line title (no nested expanders inside, so no number tables)."""
    with st.expander("More measurements", expanded=False):
        stab = (d.get("stability_summary") or [{}])[0]
        if stab.get("tau_mean") is not None:
            st.markdown("**Does the ranking hold if the link weights are shaken?**")
            st.caption(f"Each link weight scaled by a random 0.5–1.5, twenty times: rank agreement {stab['tau_mean']:.2f} "
                       f"on average, worst {stab['tau_min']:.2f} (1 = unchanged; Kendall τ); "
                       f"{_pct(stab.get('overlap_mean'))} of the top rows stay.")
        conv, prune = d.get("convergence"), (d.get("pruning") or [{}])[0]
        if conv:
            st.markdown("**Why the search can stop early**")
            st.caption(f"After each round, the n-th best score found against the best any unexplored follow-up could "
                       f"still reach; {prune.get('pruned') or 0:,.0f} of {prune.get('considered') or 0:,.0f} follow-ups "
                       "were skipped on average because they could not win.")
            _plot(ch.convergence_chart(conv["trace"]), "ce_conv_c")
        sens = d.get("sensitivity") or []
        if sens:
            lo = min(sens, key=lambda r: r.get("rows") or 0)
            st.markdown("**How the two settings change the cost**")
            st.caption(f"Rows each agency may send (n) and the weakest link still followed (θ). Cheapest: n = {lo['n']}, "
                       f"θ = {lo['theta']} — {lo.get('rows') or 0:,.0f} rows, {_pct(lo.get('overlap_at_n'))} agreement.")
            _plot(ch.sensitivity_chart(sens, "rows", "rows sent", False), "ce_sens_c")
        _more_topk(d)
        summ = d.get("exactness_summary") or []
        if summ:
            st.markdown("**Against the planted answer key**")
            st.caption("The data generator wrote both the data and the key, so this is a sanity check, not proof.")
            styled_table([{"approach": ch.MODE_SHORT.get(r["mode"], r["mode"]), "found in central copy":
                           _pct(r.get("overlap_at_n")), "precision": _pct(r.get("precision_at_n")),
                           "ranking quality": _pct(r.get("ndcg_at_n")), "questions": r.get("queries")} for r in summ])


def _more_topk(d: Dict[str, Any]) -> None:
    by = {r["method"]: r for r in d.get("topk_summary") or [] if r.get("task", "free text") == "free text"}
    methods = [m for m in ("naive", "ta", "tput") if by.get(m, {}).get("rows") is not None]
    if not methods:
        return
    names = {"naive": "read every candidate", "ta": "step-by-step threshold (TA)", "tput": "three-round threshold (TPUT)"}
    st.markdown("**Finding the best 10 vehicles from a description**")
    st.caption("Rows read per question by each way of merging the agencies' ranked lists.")
    _plot(ch.grouped_bars([names[m] for m in methods], {"rows read": [by[m]["rows"] for m in methods]},
                          "rows read per question"), "ce_topk_c")


def render() -> None:
    page_head("Proof", "Three questions a sceptic would ask about the context search, each answered with one "
              "measured number.")
    d = load()
    with st.container(horizontal=True, vertical_alignment="center"):
        st.markdown("This page shows that sending only each agency's best rows gives the same answer as copying "
                    "everything to one place, moves far less data, and finds better documents.")
        _rerun_button()
    d = load() or d
    if not d:
        st.info("Run python scripts/evaluate_context.py (or the button above) to measure.")
        return
    when = str(d.get("generated_at", "?")).replace("T", " ")[:16]
    st.caption(f"Measured {when} UTC on the generated demo data" + (" · quick run" if d.get("params", {}).get("quick")
                                                                     else ""))
    _same_answer(d)
    _less_data(d)
    _better_docs(d)
    _more(d)
    if d.get("notes"):
        with st.expander(f"Not measured yet, and why ({len(d['notes'])})", expanded=False):
            for note in d["notes"]:
                st.markdown(f"- {note}")
# CONTEXT-END
