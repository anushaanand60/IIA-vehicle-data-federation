"""CONTEXT-ADDED (R2: proof and cost): the case-file renderers of app/tabs/context_receipt.py.

Pure helpers are checked directly; the three render functions are smoke-tested with AppTest on a hand-built
`res` (no sources). "Pursue" is replaced by a fake inside the app script, so no port is needed.
"""
from __future__ import annotations

from streamlit.testing.v1 import AppTest

# imported as app.tabs.* (the module adds app/ to sys.path itself): importing `tabs` first would let a later
# `import app` find app/app.py instead of the package and break other page tests in the same session
from app.tabs import context_receipt as cr
from mediator.context_types import ContextResult, ContextRow, ParsedQuery, SourceStats


def _ctx() -> ContextResult:
    rows = [ContextRow("INS", "POLICY_RECORDS", 562, {}, 0.8, ["POLICY_RECORDS#562"], 0, affinity=1.0),
            ContextRow("REG", "OWNERS", 602, {}, 0.24, ["VEHICLE_REGISTRATION#602", "OWNERS#602"], 1,
                       affinity=0.8, edge_weights=[0.8])]
    stats = [SourceStats("REG", 42, 13231, 6, bound=0.0), SourceStats("INS", 73, 38000, 7, bound=0.11)]
    lead = {"target": "CAM", "attr": "plate", "value": "UP16IJ0376", "bound": 0.19, "kind": "plate", "round": 1,
            "from": ("REG", "VEHICLE_REGISTRATION", "376"), "weight": 0.48, "confidence": 1.0, "alike": 19}
    person = {**lead, "target": "INS", "attr": "person", "kind": "pk", "value": "POLICY_HOLDERS#9", "bound": 0.18,
              "confidence": 0.93, "alike": 0, "from": ("REG", "OWNERS", "602")}
    return ContextResult(query=ParsedQuery(text="DL05CD9876", plates=["DL05CD9876"]), rows=rows, stats=stats,
                         baseline_rows=17157, pruned_leads=[person, lead],
                         trace=[{"round": 1, "tau_n": 0.24, "max_frontier_bound": 0.98, "max_source_bound": 0.11,
                                 "expanded": 193, "pruned": 5990},
                                {"round": 2, "tau_n": 0.24, "max_frontier_bound": 0.12, "max_source_bound": 0.11,
                                 "expanded": 0, "pruned": 22},
                                {"round": 2, "tau_n": 0.24, "final": True, "stopped": "bound"}])


def test_headline_and_bill_count_every_agency():
    b = cr.bill({"context": _ctx(), "docs": None, "plate": "DL05CD9876"})
    assert b["rows"] == 115 and b["agencies"] == 2 and b["considered"] == 6205 and b["pruned"] == 6012
    assert cr.headline(b) == ("This answer cost 115 rows (50 KB) from 2 agencies; copying everything would have "
                              "moved 17,157 (99% less).")


def test_leads_are_grouped_and_worded_plainly():
    person, lead = _ctx().pruned_leads
    text = cr.lead_text(lead, 0.24)
    assert text.startswith("Camera sightings of plate UP16IJ0376, and 19 look-alike readings (found through")
    assert cr.lead_text(person, 0.24).startswith("Insurance records of a person matched to this case (93% sure) (found")
    assert "round limit" in cr.lead_text({**lead, "bound": 0.3}, 0.24)
    assert "Registration (RTO): Vehicle registration #376" in text and text.endswith("below the cut-off 0.24")
    assert cr.pursued_note({"rows": [], "above": [], "cutoff": 0.24, "bound": 0.19}).startswith(
        "Followed anyway: 0 more records, none scored above the cut-off 0.24")
    assert cr.pursued_note({"rows": [], "complete": False}).endswith("that agency did not answer.")


def test_stop_chart_has_both_lines_per_round():
    fig = cr.stop_chart(_ctx().trace)
    assert [list(t.x) for t in fig.data] == [[1, 2], [1, 2]] and list(fig.data[1].y) == [0.98, 0.12]
    assert cr.stop_chart([]) is None


def _page() -> None:
    import streamlit as st
    from app.tabs import context_receipt as cr
    from mediator import context_explain as X
    from tests.test_context_receipt import _ctx
    X.pursue = lambda lead, ctx, **k: {"rows": [], "above": [], "cutoff": 0.24, "bound": lead["bound"]}
    if "res" not in st.session_state:
        st.session_state.res = {"context": _ctx(), "docs": None, "plate": "DL05CD9876", "indicators": []}
    res = st.session_state.res
    cr.render_robustness(res)
    cr.render_why(res)
    cr.render_receipt(res)
    cr.render_receipt({"context": None})       # nothing, and no error
    cr.render_why({"context": "not a result"})  # a broken input becomes one quiet caption


def _text(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)


def test_renderers_show_proof_leads_and_receipt():
    at = AppTest.from_function(_page, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    text = _text(at)
    assert "Clear verdict uses all 2 agencies" in text
    assert "Admitted because its score 0.80" in text and "Leads not followed" in text
    assert "This answer cost 115 rows (50 KB) from 2 agencies" in text
    assert "How the records were found could not be shown" in text
    at.selectbox(key="cr_why_pick").set_value(1).run()
    assert "Owner #602 (weight 0.80)" in _text(at)
    assert _text(at).index("UP16IJ0376") < _text(at).index("matched to this case")   # best bound first
    at.button(key="cr_pursue_0").click().run()
    assert "Followed anyway: 0 more records" in _text(at)
