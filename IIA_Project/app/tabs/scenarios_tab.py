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
    if scalars:
        kpi_row([(k.replace("_", " "), _fmt(v)) for k, v in scalars])
    for k, v in data.items():
        if isinstance(v, list) and v and all(isinstance(r, dict) for r in v):
            group_label(k.replace("_", " "))
            styled_table(v, max_rows=40)
        elif isinstance(v, list) and v and all(isinstance(x, (int, float)) for x in v):
            _histogram(key, k, v)
        elif isinstance(v, dict) and v:
            group_label(k.replace("_", " "))
            styled_table([{"name": kk, "value": _fmt(vv) if not isinstance(vv, (dict, list)) else json.dumps(vv, default=str)}
                          for kk, vv in v.items()])


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


def _result(key: str, res: Dict[str, Any]) -> None:
    restored = {True: "data restored", False: "data NOT restored", None: "changed no data"}.get(res.get("restored"), "—")
    chip_strip([_chip(res), chip(f"{float(res.get('elapsed_ms') or 0):,.0f} ms", TOKENS["info"]),
                chip(restored, TOKENS["ok"] if res.get("restored") is not False else TOKENS["bad"])])
    styled_table([{"Expected": res.get("expected"), "Actual": res.get("actual")}])
    if res.get("steps"):
        group_label("What the scenario did, step by step")
        stepper(_steps(res["steps"]))
    _data(key, res.get("data"))


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


def render() -> None:
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
