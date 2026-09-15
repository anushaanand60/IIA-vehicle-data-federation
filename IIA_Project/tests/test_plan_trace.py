"""Plan-trace tab (CLAUDE.md §7 step 9), rendered headlessly with Streamlit's AppTest."""
import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.tabs.plan_trace import latency_frame, selection_table
from mediator.contract import FederationResponse

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "source_results.json"
CASES = {c["id"]: c["response"] for c in json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]}
SKIP_REASON = "covers none of the requested attributes (insurance_expiry, insurer_name)"


def response(case_id: str) -> FederationResponse:
    return FederationResponse.model_validate(CASES[case_id])


def insurance_only() -> FederationResponse:
    full = response("expired")
    trace = full.trace.model_copy(update={
        "requested_attrs": ["insurer_name", "insurance_expiry"], "sources_selected": ["INS"],
        "sources_skipped": {sid: SKIP_REASON for sid in ("REG", "THEFT", "CAM")}})
    return full.model_copy(update={"results": [full.by_source("INS")], "trace": trace})


def _page(payload: str) -> None:
    from app.tabs.plan_trace import render
    from mediator.contract import FederationResponse
    render(FederationResponse.model_validate_json(payload))


def run_tab(resp: FederationResponse) -> AppTest:
    at = AppTest.from_function(_page, args=(resp.model_dump_json(),), default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def table_text(at: AppTest) -> str:
    return " ".join(str(v) for v in at.table[0].value.to_numpy().ravel())


# --- pure helpers ---------------------------------------------------------------

def test_selection_table_lists_asked_sources_then_skipped_ones_with_reasons():
    table = selection_table(insurance_only())
    assert list(table["Source"]) == ["INS", "REG", "THEFT", "CAM"]
    assert list(table["Decision"]) == ["Asked", "Skipped", "Skipped", "Skipped"]
    assert table.loc[table["Source"] == "REG", "Detail"].item() == SKIP_REASON


def test_latency_frame_keeps_result_order_and_names_failures_in_words():
    resp = response("ins_down")
    frame = latency_frame(resp)
    assert list(frame["source"]) == ["REG", "INS", "THEFT", "CAM"]
    ins = frame[frame["source"] == "INS"].iloc[0]
    assert ins["status"] == "DOWN" and ins["label"] == f"{resp.by_source('INS').elapsed_ms} ms, DOWN"
    assert frame[frame["source"] == "REG"].iloc[0]["label"] == f"{resp.by_source('REG').elapsed_ms} ms"


# --- rendered tab -----------------------------------------------------------------

@pytest.mark.parametrize("case_id", CASES)
def test_every_fixture_case_renders_without_error(case_id):
    run_tab(response(case_id))


def test_sql_sent_is_shown_verbatim_for_each_asked_source():
    resp = response("stolen")
    assert [c.value for c in run_tab(resp).code] == [r.sql_sent for r in resp.results]


def test_status_is_named_in_words_not_colour_alone():
    text = table_text(run_tab(response("ins_down")))
    assert "OK" in text and "Down" in text


def test_failed_source_shows_its_error_message():
    resp = response("ins_down")
    assert any(resp.by_source("INS").error in e.value for e in run_tab(resp).error)


def test_skipped_sources_and_their_reasons_are_visible():
    text = table_text(run_tab(insurance_only()))
    assert "Skipped" in text and SKIP_REASON in text


def test_headline_metrics_and_latency_chart_are_present():
    resp = response("ins_down")
    at = run_tab(resp)
    metrics = {m.label: m.value for m in at.metric}
    assert metrics == {"Sources asked": "4 of 4", "Answered": "3 of 4", "Total time": f"{resp.trace.total_elapsed_ms} ms"}
    assert at.get("vega_lite_chart")  # Altair charts surface under this element type in AppTest


def test_standalone_demo_offers_every_attribute_the_registry_knows_including_derived_ones():
    tab = Path(__file__).resolve().parents[1] / "app" / "tabs" / "plan_trace.py"
    at = AppTest.from_file(str(tab), default_timeout=30).run()  # no submit, so no source is contacted
    assert not at.exception, [e.value for e in at.exception]
    assert "insurance_status" in at.multiselect[0].options
