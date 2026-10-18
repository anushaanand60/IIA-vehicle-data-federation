"""Test scenarios page (Data quality group): one-click stress tests of the matcher and the cleanser.

Every scenario lives in `mediator/scenarios.py` (`SCENARIOS`, `run_scenario`): it builds its own
input, runs the real pipeline live against the federation, compares the outcome with what it
expected, and restores any data it changed. This page only lists them by rubric, runs them, and
draws the `ScenarioResult` (expected vs actual, PASS/FAIL, steps, data tables, elapsed time, whether
data was restored). Results are kept in `st.session_state["sc2_results"]` so they survive reruns.
The backend is imported lazily: if it is missing the page says so instead of crashing.
Widget keys carry `sc2_` (`sc_` belongs to Citizen Check).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import altair as alt  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from components import chip, chip_strip, group_label, kpi_row, page_head, panel, section, stepper, styled_table  # noqa: E402
from theme import TOKENS  # noqa: E402
from components import how_to  # noqa: E402  (phase-2 simplified GUI)

STATE = "sc2_results"
# The backend tags each scenario "1", "2" or "1+2"; the page groups by rubric in this order.
RUBRICS = {"1": "Rubric 1: approximate plate matching", "2": "Rubric 2: cleansing camera records",
           "1+2": "Rubrics 1 and 2 together"}


def backend() -> Any:
    """`mediator.scenarios`, or None. Imported per render so a module that arrives later is picked up."""
    try:
        from mediator import scenarios
        return scenarios
    except Exception as exc:  # any import failure is "not available yet", never a crash
        st.session_state["sc2_import_error"] = f"{type(exc).__name__}: {exc}"
        return None


def field(obj: Any, name: str, default: Any = None) -> Any:
    """ScenarioResult / Step may be a dataclass, a pydantic model or a dict."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def run_one(mod: Any, key: str) -> Dict[str, Any]:
    t0 = time.perf_counter()
    try:
        res = mod.run_scenario(key)
    except Exception as exc:  # a scenario that raises is a FAIL with its reason, not a broken page
        return {"key": key, "title": key, "passed": False, "expected": "the scenario to run",
                "actual": f"it raised {type(exc).__name__}: {exc}", "steps": [], "data": {},
                "elapsed_ms": round((time.perf_counter() - t0) * 1000), "restored": None}
    return {k: field(res, k) for k in ("key", "title", "rubric", "expected", "actual", "passed", "steps",
                                        "data", "elapsed_ms", "restored")}


def _chip(res: Optional[Dict[str, Any]]) -> str:
    if res is None:
        return chip("NOT RUN", TOKENS["neutral"])
    if res["passed"] is None:  # the backend's "cannot run on this deployment"; its data says why
        return chip("SKIPPED", TOKENS["warn"])
    return chip("PASS", TOKENS["ok"]) if res["passed"] else chip("FAIL", TOKENS["bad"])


def rubric_name(tag: Any) -> str:
    return RUBRICS.get(str(tag), str(tag or "Other"))


def _steps(steps: List[Any]) -> List[Dict[str, Any]]:
    out = []
    for s in steps or []:
        ok = field(s, "ok")
        word = {True: "ok", False: "failed", None: "info"}.get(ok, "info")
        out.append({"title": f"[{word}] {field(s, 'title', '')}", "detail": field(s, "detail", ""), "done": ok is True})
    return out


def _data(key: str, data: Any) -> None:
    if not data:
        return
    if not isinstance(data, dict):
        st.code(json.dumps(data, indent=1, default=str), language="json")
        return
    scalars = [(k, v) for k, v in data.items() if isinstance(v, (int, float, str, bool)) or v is None]
    short = [(k, v) for k, v in scalars if len(_fmt(v)) <= 12]  # long text in a KPI tile wrapped letter by letter
    if short:
        kpi_row([(k.replace("_", " "), _fmt(v)) for k, v in short])
    if len(short) < len(scalars):
        styled_table([{"name": k.replace("_", " "), "value": _fmt(v)} for k, v in scalars if (k, v) not in short])
    for k, v in data.items():
        if isinstance(v, list) and v and all(isinstance(r, dict) for r in v):
            group_label(k.replace("_", " "))
            styled_table(v, max_rows=40)
        elif isinstance(v, list) and v and all(isinstance(x, (int, float)) for x in v):
            _histogram(key, k, v)
        elif isinstance(v, dict) and v:
            group_label(k.replace("_", " "))
            flat = {kk: vv for kk, vv in v.items() if not (isinstance(vv, list) and vv and all(isinstance(r, dict) for r in vv))}
            styled_table([{"name": kk, "value": _fmt(vv) if not isinstance(vv, (dict, list)) else json.dumps(vv, default=str)}
                          for kk, vv in flat.items()])
            for kk, vv in v.items():
                if kk not in flat:  # a nested list of rows (e.g. the match's candidates) reads as its own table
                    group_label(f"{k.replace('_', ' ')}: {kk.replace('_', ' ')}")
                    styled_table(vv, max_rows=20)


def _fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.3f}" if abs(v) < 10 else f"{v:,.1f}"
    return "—" if v is None else str(v)


def _histogram(key: str, name: str, values: List[float]) -> None:
    group_label(f"{name.replace('_', ' ')}: distribution of {len(values)} values")
    chart = alt.Chart(pd.DataFrame({"value": values})).mark_bar(color=TOKENS["brand"]).encode(
        x=alt.X("value:Q", bin=alt.Bin(maxbins=30), title=name.replace("_", " "),
                axis=alt.Axis(labelFontSize=14, titleFontSize=15)),
        y=alt.Y("count():Q", title="reads", axis=alt.Axis(labelFontSize=14, titleFontSize=15)))
    st.altair_chart(chart.properties(height=260).configure_axis(labelFontSize=15, titleFontSize=15,
                    labelColor=TOKENS["ink"], titleColor=TOKENS["ink"]), key=f"sc2_hist_{key}_{name}",
                    theme=None, width="stretch")  # theme=None: Streamlit's theme shrank the axis text


def restore_state(res: Dict[str, Any]) -> str:
    """The backend's `restored` is False both for "changed nothing" and "failed to undo". Every change it
    makes registers an undo that runs as a "Restore: ..." step, so those steps tell the two apart."""
    changed = any(str(field(s, "title", "")).startswith("Restore") for s in res.get("steps") or [])
    if res.get("restored"):
        return "data restored"
    return "data NOT restored" if changed else "changed no data"


# PHASE2-HIDDEN (simplified GUI, 19 Oct 2026): a result drawn open: chips, expected/actual table, step list, data expander; previously called from _scenario.
def _result(key: str, res: Dict[str, Any]) -> None:
    state = restore_state(res)
    chip_strip([_chip(res), chip(f"{float(res.get('elapsed_ms') or 0):,.0f} ms", TOKENS["info"]),
                chip(state, TOKENS["bad"] if state == "data NOT restored" else TOKENS["ok"])])
    styled_table([{"Expected": res.get("expected"), "Actual": res.get("actual")}])
    if res.get("steps"):
        group_label("What the scenario did, step by step")
        stepper(_steps(res["steps"]))
    if res.get("data"):
        # Collapsed by default: sixteen open result sets made a 16 000 px page nobody could scan; the
        # batch run stays open because its accuracy and latency figures are the result.
        with st.expander("Data the scenario recorded (tables and numbers)", expanded=key == "batch_stress"):
            _data(key, res.get("data"))


# PHASE2-HIDDEN (simplified GUI, 19 Oct 2026): one scenario as heading + chips + 'What it does / Why it matters' prose + open result; previously called from render() (now _render_before_simplification).
def _scenario(mod: Any, key: str, spec: Dict[str, Any], results: Dict[str, Any]) -> None:
    res = results.get(key)
    head, button = st.columns([5, 1])
    with head:
        group_label(spec.get("title", key))
        chip_strip([_chip(res), chip(rubric_name(spec.get("rubric")), TOKENS["neutral"])])
        st.markdown(f"**What it does:** {spec.get('what', '—')}  \n**Why it matters:** {spec.get('why', '—')}")
    if button.button("Run", key=f"sc2_run_{key}", width="stretch"):
        with st.spinner(f"Running {spec.get('title', key)} live…"):
            results[key] = run_one(mod, key)
        res = results[key]
    if res is not None:
        _result(key, res)
    st.markdown("<hr style='border:none;border-top:1px solid var(--cv-line);margin:18px 0;'>", unsafe_allow_html=True)


# PHASE2-HIDDEN (simplified GUI, 19 Oct 2026): the whole previous page: the summary panel and one panel per rubric with every scenario open; previously called from app/app.py as scenarios_tab.render.
def _render_before_simplification() -> None:
    page_head("Test scenarios", "Stress tests an examiner would try, one click each: clean and damaged images, "
              "unregistered and look-alike plates, inserted, modified and deleted records, a source going down.")
    mod = backend()
    if mod is None or not hasattr(mod, "SCENARIOS") or not hasattr(mod, "run_scenario"):
        with panel("sc2_missing"):
            section("Scenarios are not available yet",
                    "What this shows: the scenario runner (mediator/scenarios.py) could not be loaded on this laptop.")
            st.warning(f"Not available yet: {st.session_state.get('sc2_import_error', 'SCENARIOS or run_scenario missing')}. "
                       "The other Data quality pages work without it.")
        return
    results: Dict[str, Any] = st.session_state.setdefault(STATE, {})
    catalogue: Dict[str, Dict[str, Any]] = dict(mod.SCENARIOS)
    with panel("sc2_summary"):
        section("Run everything", "What this shows: every scenario runs the real pipeline live against the "
                "agency wrappers and checks its own result. Every scenario that changes data restores it afterwards.")
        done = [results[k] for k in catalogue if k in results]
        kpi_row([("Scenarios", len(catalogue)), ("Run", len(done)), ("Passed", sum(1 for r in done if r["passed"])),
                 ("Failed", sum(1 for r in done if r["passed"] is False)),
                 ("Skipped", sum(1 for r in done if r["passed"] is None)),
                 ("Total time", f"{sum(r.get('elapsed_ms') or 0 for r in done) / 1000:.1f} s")])
        c1, c2 = st.columns([1, 1])
        if c1.button("Run all scenarios", key="sc2_run_all", type="primary", width="stretch"):
            bar = st.progress(0.0, text="Starting…")
            for i, key in enumerate(catalogue, 1):
                bar.progress((i - 1) / len(catalogue), text=f"Running {catalogue[key].get('title', key)} ({i} of {len(catalogue)})")
                results[key] = run_one(mod, key)
            bar.progress(1.0, text="All scenarios finished.")
            st.rerun()  # the KPI row above was drawn before the run; redraw it with the results
        if c2.button("Clear results", key="sc2_clear", width="stretch"):
            results.clear()
    rubrics: Dict[str, List[str]] = {}
    order = list(RUBRICS)
    for key, spec in sorted(catalogue.items(), key=lambda kv: order.index(str(kv[1].get("rubric")))
                            if str(kv[1].get("rubric")) in order else len(order)):
        rubrics.setdefault(rubric_name(spec.get("rubric")), []).append(key)
    for i, (rubric, keys) in enumerate(rubrics.items()):
        with panel(f"sc2_rubric_{i}"):
            passed = sum(1 for k in keys if results.get(k, {}).get("passed"))
            section(rubric, f"What this shows: {len(keys)} scenario(s) for this rubric; {passed} passed in this session.")
            for key in keys:
                _scenario(mod, key, catalogue[key], results)


# ------------------------------------------------------------------ phase-2 simplified page

# Group names shown on the page (the backend's "1" / "2" / "1+2" tags; the old RUBRICS names stay above).
GROUPS = {"1": "Plate matching", "2": "Cleansing camera records", "1+2": "Matching and cleansing together"}
# "title — what it checks": one short sentence per scenario; the backend's own `why` is the fallback.
CHECKS = {
    "clean_image": "a clean photo must end in an ACCEPT of the right car.",
    "ocr_zero_o": "the commonest OCR error (0 read as O) must cost almost nothing.",
    "ocr_b8": "camera make and colour turn a REVIEW into a safe ACCEPT.",
    "two_errors_rotated": "a read with two OCR errors must still find the right car.",
    "unreadable_image": "a bad image gives REVIEW or REJECT, never a confident wrong ACCEPT.",
    "unregistered_plate": "a plate nobody registered must be REJECTed.",
    "ambiguous_twins": "two equally good registrations must go to a person (REVIEW).",
    "context_breaks_tie": "camera make and colour pick the right twin.",
    "reverse_search": "a registration finds its exact and misread camera captures.",
    "examiner_insert_dirty": "an inserted dirty capture is detected, fixed and undone live.",
    "examiner_modify_conflict": "a colour that contradicts the registration is flagged, not overwritten.",
    "examiner_delete": "a deleted capture disappears from the next check.",
    "misread_plate_cleansed": "a misread plate is corrected only on the matcher's ACCEPT.",
    "source_down_refusal": "with the insurance source down the system refuses to decide.",
    "batch_stress": "ACCEPT precision >= 0.98 and p95 latency < 200 ms on many reads.",
    "rdr_teach_refusal": "a rule that would break a cornerstone case is refused.",
}


def group_name(tag: Any) -> str:
    return GROUPS.get(str(tag), str(tag or "Other"))


def _scenario_line(mod: Any, key: str, spec: Dict[str, Any], results: Dict[str, Any]) -> None:
    # PHASE2-REPLACED (review: after a single Run the summary KPIs and the group's 'n of m passed' line were drawn before the click and showed the old count (2 run, page said 1); the status chips took their own line under every scenario, ~35 px x 16):
    # res = results.get(key)
    # head, button = st.columns([6, 1])
    # with head:
    #     st.markdown(f"**{spec.get('title', key)}** — {CHECKS.get(key) or spec.get('why') or spec.get('what', '')}")
    # if button.button("Run", key=f"sc2_run_{key}", width="stretch"):
    #     with st.spinner(f"Running {spec.get('title', key)} live…"):
    #         results[key] = run_one(mod, key)
    #     res = results[key]
    # if res is None:
    #     chip_strip([_chip(None)])
    # else:
    #     state = restore_state(res)
    #     chip_strip([_chip(res), chip(f"{float(res.get('elapsed_ms') or 0):,.0f} ms", TOKENS["info"]),
    #                 chip(state, TOKENS["bad"] if state == "data NOT restored" else TOKENS["ok"])])
    #     with st.expander("Details: expected vs actual, steps, data", expanded=False):
    res = results.get(key)
    head, button = st.columns([6, 1])
    if button.button("Run", key=f"sc2_run_{key}", width="stretch"):
        with st.spinner(f"Running {spec.get('title', key)} live…"):
            results[key] = run_one(mod, key)
        st.rerun()  # the summary KPIs and the group's "passed" line were drawn before this click
    chips = [_chip(res)]
    if res is not None:
        state = restore_state(res)
        chips += [chip(f"{float(res.get('elapsed_ms') or 0):,.0f} ms", TOKENS["info"]),
                  chip(state, TOKENS["bad"] if state == "data NOT restored" else TOKENS["ok"])]
    with head:  # status chips on the same line as "title — what it checks"
        st.markdown(f"{''.join(chips)} &nbsp;**{spec.get('title', key)}** — "
                    f"{CHECKS.get(key) or spec.get('why') or spec.get('what', '')}", unsafe_allow_html=True)
    if res is not None:
        with st.expander("Details: expected vs actual, steps, data", expanded=False):
            styled_table([{"Expected": res.get("expected"), "Actual": res.get("actual")}])
            if res.get("steps"):
                stepper(_steps(res["steps"]))
            _data(key, res.get("data"))
    st.markdown("<hr style='border:none;border-top:1px solid var(--cv-line);margin:12px 0;'>", unsafe_allow_html=True)


def render() -> None:
    page_head("Test scenarios", "One-click stress tests of the matcher and the cleanser; each restores any data it changed.")
    how_to(["Press Run all scenarios (or Run on one line).", "Read the PASS / FAIL chip on each line.",
            "Open Details to see the steps and the data."])
    mod = backend()
    if mod is None or not hasattr(mod, "SCENARIOS") or not hasattr(mod, "run_scenario"):
        with panel("sc2_missing"):
            section("Scenarios are not available yet")
            st.warning(f"Not available yet: {st.session_state.get('sc2_import_error', 'SCENARIOS or run_scenario missing')}.")
        return
    results: Dict[str, Any] = st.session_state.setdefault(STATE, {})
    catalogue: Dict[str, Dict[str, Any]] = dict(mod.SCENARIOS)
    with panel("sc2_summary"):
        c1, c2 = st.columns([1, 1])
        if c1.button("Run all scenarios", key="sc2_run_all", type="primary", width="stretch"):
            bar = st.progress(0.0, text="Starting…")
            for i, key in enumerate(catalogue, 1):
                bar.progress((i - 1) / len(catalogue), text=f"Running {catalogue[key].get('title', key)} ({i} of {len(catalogue)})")
                results[key] = run_one(mod, key)
            bar.progress(1.0, text="All scenarios finished.")
            st.rerun()  # the KPI row below was drawn before the run; redraw it with the results
        if c2.button("Clear results", key="sc2_clear", width="stretch"):
            results.clear()
        done = [results[k] for k in catalogue if k in results]
        kpi_row([("Scenarios", len(catalogue)), ("Run", len(done)), ("Passed", sum(1 for r in done if r["passed"])),
                 ("Failed", sum(1 for r in done if r["passed"] is False)),
                 ("Skipped", sum(1 for r in done if r["passed"] is None)),
                 ("Total time", f"{sum(r.get('elapsed_ms') or 0 for r in done) / 1000:.1f} s")])
    groups: Dict[str, List[str]] = {}
    order = list(GROUPS)
    for key, spec in sorted(catalogue.items(), key=lambda kv: order.index(str(kv[1].get("rubric")))
                            if str(kv[1].get("rubric")) in order else len(order)):
        groups.setdefault(group_name(spec.get("rubric")), []).append(key)
    for i, (name, keys) in enumerate(groups.items()):
        with panel(f"sc2_group_{i}"):
            passed = sum(1 for k in keys if results.get(k, {}).get("passed"))
            section(name, f"{passed} of {len(keys)} passed in this session.")
            for key in keys:
                _scenario_line(mod, key, catalogue[key], results)
