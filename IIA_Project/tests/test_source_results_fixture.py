"""tests/fixtures/source_results.json: the handoff artefact for Teammate C must stay true to the contract."""
import json
from pathlib import Path

import pytest

from mediator.contract import FederationResponse

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "source_results.json"
OK, EMPTY = ("OK", True), ("OK", False)

# What each case must show, checked independently of the prose inside the fixture.
EXPECTED = {
    "clean":         ("DL01AB1234", {"REG": OK, "INS": OK, "THEFT": EMPTY, "CAM": OK}),
    "expired":       ("DL05CD9876", {"REG": OK, "INS": OK, "THEFT": EMPTY, "CAM": OK}),
    "no_policy":     ("DL09KL3321", {"REG": OK, "INS": EMPTY, "THEFT": EMPTY, "CAM": OK}),
    "stolen":        ("HR26EF4455", {"REG": OK, "INS": OK, "THEFT": OK, "CAM": OK}),
    "cloned":        ("UP16GH1122", {"REG": OK, "INS": OK, "THEFT": EMPTY, "CAM": OK}),
    "unregistered":  ("MH12IJ7788", {"REG": EMPTY, "INS": EMPTY, "THEFT": EMPTY, "CAM": OK}),
    "insured_query": ("DL05CD9876", {"REG": OK, "INS": OK}),
    "ins_down":      ("DL09KL3321", {"REG": OK, "INS": ("DOWN", False), "THEFT": EMPTY, "CAM": OK}),
}


@pytest.fixture(scope="module")
def cases() -> dict[str, dict]:
    return {case["id"]: case for case in json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]}


def response(cases, case_id: str) -> FederationResponse:
    return FederationResponse.model_validate(cases[case_id]["response"])


def test_fixture_has_the_six_stories_the_uc1_request_and_the_insurer_down_variant(cases):
    assert set(cases) == set(EXPECTED)


@pytest.mark.parametrize("case_id", EXPECTED)
def test_every_case_is_a_valid_federation_response_with_documentation(cases, case_id):
    resp = response(cases, case_id)
    plate, per_source = EXPECTED[case_id]
    assert resp.trace.plate_normalized == plate
    assert {r.source_id: (r.status, r.row_count > 0) for r in resp.results} == per_source
    assert cases[case_id]["story"] and cases[case_id]["look_at"]


def test_expired_case_carries_the_raw_ddmmyyyy_expiry(cases):
    assert "12/07/2026" in {row["policy_until"] for row in response(cases, "expired").by_source("INS").rows}


def test_stolen_case_puts_the_open_theft_first(cases):
    first = response(cases, "stolen").by_source("THEFT").rows[0]
    assert (first["stolen_flag"], first["recovered_flag"]) == ("Y", "N")


def test_cloned_case_shows_the_model_conflict_unresolved(cases):
    resp = response(cases, "cloned")
    assert resp.by_source("REG").rows[0]["model"] == "Creta"
    assert {row["observed_model"] for row in resp.by_source("CAM").rows} == {"VENUE"}


def test_uc1_case_confirms_registration_and_fetches_only_the_expiry(cases):
    resp = response(cases, "insured_query")
    assert resp.trace.requested_attrs == ["insurance_status"]
    assert set(resp.trace.sources_skipped) == {"THEFT", "CAM"}
    assert resp.by_source("REG").rows == [{"registration_no": "DL05CD9876"}]
    assert {tuple(row) for row in resp.by_source("INS").rows} == {("vehicle_reg", "policy_until")}


def test_insurer_down_differs_from_no_policy_only_in_status(cases):
    no_policy, down = response(cases, "no_policy").by_source("INS"), response(cases, "ins_down").by_source("INS")
    assert no_policy.rows == down.rows == []
    assert (no_policy.status, no_policy.error) == ("OK", None)
    assert down.status == "DOWN" and down.error
