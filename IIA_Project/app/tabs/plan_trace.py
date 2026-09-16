"""Plan-trace tab: how the mediator answered. Which sources, why, what SQL, how fast, what failed.

render(response) is the tab body for app/app.py. Standalone demo against whatever sources are up:
    streamlit run app/tabs/plan_trace.py
"""
from __future__ import annotations

import html
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # `streamlit run` puts only this file's folder on sys.path
    sys.path.insert(0, str(ROOT))
_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from mediator.contract import GLOBAL_ATTRIBUTES, FederationResponse  # noqa: E402
from theme import status_class  # noqa: E402

# A status is a state, so it gets a status colour plus an icon and a word: never colour alone.
BADGES = {"OK": ("green", "check_circle", "OK"), "TIMEOUT": ("yellow", "schedule", "Timeout"),
          "ERROR": ("orange", "error", "Error"), "DOWN": ("red", "cloud_off", "Down")}
# One series, one hue. Both steps pass the palette validator against Streamlit's own surfaces.
BAR_COLOUR = {"light": "#2a78d6", "dark": "#3987e5"}
LABEL_INK = {"light": "#52514e", "dark": "#c3c2b7"}  # value labels wear text ink, not the bar colour


def badge(status: str) -> str:
    colour, icon, word = BADGES[status]
    return f":{colour}-badge[:material/{icon}: {word}]"


def selection_table(response: FederationResponse) -> pd.DataFrame:
    asked = [{"Source": r.source_id, "Decision": "Asked", "Status": badge(r.status),
              "Detail": f"{r.row_count} row(s) in {r.elapsed_ms} ms" if r.status == "OK" else r.error}
             for r in response.results]
    skipped = [{"Source": source_id, "Decision": "Skipped", "Status": ":gray-badge[Not asked]", "Detail": reason}
               for source_id, reason in response.trace.sources_skipped.items()]
    return pd.DataFrame(asked + skipped, columns=["Source", "Decision", "Status", "Detail"])


def latency_frame(response: FederationResponse) -> pd.DataFrame:
    return pd.DataFrame(
        [{"source": r.source_id, "elapsed_ms": r.elapsed_ms, "status": r.status, "rows": r.row_count,
          "label": f"{r.elapsed_ms} ms" if r.status == "OK" else f"{r.elapsed_ms} ms, {r.status}"}
         for r in response.results],
        columns=["source", "elapsed_ms", "status", "rows", "label"])


def latency_chart(frame: pd.DataFrame, theme: str) -> alt.LayerChart:
    # Headroom past the longest bar so its value label is never clipped.
    x_max = max(int(frame["elapsed_ms"].max()), 1) * 1.35
    base = alt.Chart(frame).encode(
        y=alt.Y("source:N", sort=list(frame["source"]), title=None),
        x=alt.X("elapsed_ms:Q", title="Milliseconds", scale=alt.Scale(domain=[0, x_max])),
        tooltip=[alt.Tooltip("source:N", title="Source"), alt.Tooltip("status:N", title="Status"),
                 alt.Tooltip("elapsed_ms:Q", title="Latency (ms)"), alt.Tooltip("rows:Q", title="Rows")])
    bars = base.mark_bar(size=18, cornerRadiusEnd=4, color=BAR_COLOUR[theme])
    labels = base.mark_text(align="left", dx=6, color=LABEL_INK[theme]).encode(text="label:N")
    return (bars + labels).properties(height=alt.Step(34))


def render(response: FederationResponse) -> None:
    trace, results = response.trace, response.results
    asked = len(trace.sources_selected)
    everything = set(trace.requested_attrs) >= set(GLOBAL_ATTRIBUTES)  # a registry may add attributes (UC6)
    st.caption(f"Plate **{trace.plate_normalized}**, entered as `{trace.plate_raw}`. Requested: "
               f"{'the full profile' if everything else ', '.join(trace.requested_attrs)}.")

    with st.container(horizontal=True):
        st.metric("Sources asked", f"{asked} of {asked + len(trace.sources_skipped)}", border=True)
        st.metric("Answered", f"{sum(r.status == 'OK' for r in results)} of {asked}", border=True)
        st.metric("Total time", f"{trace.total_elapsed_ms} ms", border=True)

    st.subheader("Source selection")
    st.table(selection_table(response).set_index("Source"))

    st.subheader("Latency per source")
    if results:
        st.altair_chart(latency_chart(latency_frame(response), _theme()))
    else:
        st.caption("No source covers the requested attributes, so none was asked.")

    st.subheader("What each source was sent")
    for r in results:
        with st.container(border=True):
            st.markdown(f"**{r.source_id}** {badge(r.status)} :gray[{r.row_count} row(s), {r.elapsed_ms} ms]")
            if r.sql_sent:
                st.code(r.sql_sent, language="sql", wrap_lines=True)
            if r.error:
                st.error(r.error, icon=":material/error:")
            if r.rows:
                with st.expander(f"Raw rows ({r.row_count})", icon=":material/table_rows:"):
                    st.dataframe(pd.DataFrame(r.rows), hide_index=True)


def _tab_chip(source_id: str, detail: dict) -> str:
    variant = status_class(str(detail.get("status") or ""))
    status_word = str(detail.get("status") or "DOWN").upper()
    elapsed = detail.get("elapsed_ms")
    rows = detail.get("row_count")
    meta = "—" if elapsed is None else f"{elapsed} ms · {rows if rows is not None else '—'} rows"
    return (
        f'<span class="fm-chip fm-chip--{variant}"><span class="fm-dot"></span>'
        f"{html.escape(source_id)} · {html.escape(status_word)}"
        f'<span class="fm-meta">{html.escape(meta)}</span></span>'
    )


def _tab_not_asked_chip(source_id: str) -> str:
    muted = ("border:1px dashed var(--fm-rule);background:var(--fm-paper);"
             "color:var(--fm-ink_muted);")
    return (
        f'<span class="fm-chip" style="{muted}">'
        f'<span class="fm-dot" style="background:var(--fm-ink_muted);"></span>'
        f"{html.escape(source_id)} · NOT ASKED</span>"
    )


def _chip_row(sources_detail: dict) -> None:
    """Status chips per source and per catalogued-but-unasked source.

    Draws its own row (`.fm-chip` classes from `theme.py`) instead of calling
    `components.source_chips`, which renders under the fixed key `fm_chip_row` -- the Investigate
    tab already claims that key in the same page, and two tabs sharing one key in a single script
    run raises a duplicate-element-key error. This tab keeps its own scoped key (`pt_chip_row`).
    """
    try:
        from mediator.catalog import get_source_catalog
        catalogued = list(get_source_catalog().keys())
    except Exception:  # the chip row must never be the thing that breaks this tab
        catalogued = list(sources_detail)
    skipped = [s for s in catalogued if s not in sources_detail]
    if not sources_detail and not skipped:
        st.caption("No source was asked for this query.")
        return
    with st.container(horizontal=True, key="pt_chip_row"):
        for source_id, detail in sources_detail.items():
            st.markdown(_tab_chip(source_id, detail), unsafe_allow_html=True)
        for source_id in skipped:
            st.markdown(_tab_not_asked_chip(source_id), unsafe_allow_html=True)


def render_tab() -> None:
    """Tab 2 body for `app/app.py`.

    `render(response)` above draws a transport-layer `FederationResponse` (the standalone demo,
    `python -m mediator.executor`). The live app's Investigate tab instead writes
    `st.session_state["latest_result"] = {"profile", "plan_trace"}` via `mediator.core.
    run_global_query`, where `plan_trace` is a plain dict (`canonical_plate`, `sources_contacted`,
    `sources_detail`, `sqls`, `total_elapsed_ms`, ...) -- a different shape, so this reads that one
    directly rather than reusing `render()`.
    """
    result = st.session_state.get("latest_result")
    if not result:
        st.info("Execute a query in the Investigate tab to inspect its execution plan trace.")
        return

    trace = result.get("plan_trace") or {}
    sources_detail: dict = trace.get("sources_detail") or {}
    sqls: dict = trace.get("sqls") or {}
    contacted = trace.get("sources_contacted") or []
    answered_ok = sum(1 for detail in sources_detail.values() if detail.get("status") == "OK")

    st.caption(
        f"Plate **{trace.get('canonical_plate', '—')}**, entered as "
        f"`{trace.get('raw_plate', '—')}`. Requested: "
        f"{', '.join(trace.get('requested_attrs') or []) or 'the full profile'}."
    )

    with st.container(horizontal=True, key="pt_metrics"):
        st.metric("Sources asked", len(contacted), border=True)
        st.metric("Answered OK", f"{answered_ok} of {len(contacted)}", border=True)
        st.metric("Total time", f"{trace.get('total_elapsed_ms', 0)} ms", border=True)

    st.subheader("Sources asked and skipped")
    _chip_row(sources_detail)  # `.fm-chip` badges; catalogued-but-unasked sources shown muted

    st.subheader("SQL sent per source")
    if sqls:
        for source_id, sql_text in sqls.items():
            detail = sources_detail.get(source_id, {})
            with st.expander(f"{source_id} — {detail.get('status', '?')}"):
                st.code(sql_text, language="sql")
    else:
        st.caption("No source needed to be asked for this query.")

    st.subheader("Latency per source")
    if sources_detail:
        frame = pd.DataFrame(
            [{"Source": sid, "Latency (ms)": detail.get("elapsed_ms", 0)}
             for sid, detail in sources_detail.items()]
        ).set_index("Source")
        st.bar_chart(frame)
    else:
        st.caption("No source was asked, so there is no latency to show.")


def _theme() -> str:
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:  # no browser session (tests, bare mode): fall back to light
        return "light"


def _demo() -> None:
    from mediator.executor import execute
    from mediator.registry_loader import RegistryError, default_registry_path, load_registry

    st.set_page_config(page_title="Plan trace", page_icon=":material/account_tree:", layout="wide")
    st.title("Plan trace")
    path = default_registry_path()
    try:
        registry = load_registry(path)  # re-read on every rerun, so a newly registered source appears (UC6)
    except RegistryError as exc:
        st.error(f"Registry `{path}` is invalid: {exc}", icon=":material/error:")
        return
    st.caption(f"Registry `{path}`. Every lookup reads the sources live; nothing is cached.")
    with st.form("lookup", border=False):
        plate = st.text_input("Number plate", value="DL05CD9876")
        attrs = st.multiselect("Global attributes", registry.vocabulary, placeholder="All attributes (full profile)")
        submitted = st.form_submit_button("Run federated query", icon=":material/search:", type="primary")
    if submitted:
        try:
            st.session_state.trace_response = execute(registry, plate, attrs or None)
        except ValueError as exc:  # unusable plate or attribute: a caller error, shown not raised
            st.error(str(exc), icon=":material/error:")
    if response := st.session_state.get("trace_response"):
        render(response)


if __name__ == "__main__":
    _demo()
