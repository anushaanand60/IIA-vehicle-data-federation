"""Two more ways a failed or unasked source must not become a finding (Task 3.1)."""
import pytest

from mediator.integrator import integrate_results
from tests.test_decisions import CAM_ROW, CORE, INS_ROW, REG_ROW, executed


@pytest.fixture(scope="module", autouse=True)
def validated_mappings() -> None:
    from tests.fixtures import inject_test_mappings
    inject_test_mappings()


@pytest.mark.parametrize("failed", ["INS", "THEFT"])
def test_a_source_that_errored_is_undetermined_not_a_finding(failed):
    answers = {"REG": ("OK", [REG_ROW]), "INS": ("OK", [INS_ROW]), "THEFT": ("OK", []), "CAM": ("OK", [CAM_ROW])}
    answers[failed] = ("ERROR", [])
    profile = integrate_results(executed(**answers), "DL01AB1234", CORE)
    assert (profile["decision"], profile["confidence"]) == ("UNDETERMINED", "LOW")
    assert failed in " ".join(profile["reasons"])


def test_camera_question_that_never_asked_registration_does_not_conclude_unregistered():
    profile = integrate_results(executed(THEFT=("OK", []), CAM=("OK", [CAM_ROW])), "DL01AB1234", ["THEFT", "CAM"])
    assert "UNREGISTERED" not in profile["decision"]
    reasons = " ".join(profile["reasons"])
    assert "not checked in this query: REG, INS" in reasons
    assert "registered vehicle specifications" not in reasons  # nothing registered was compared


def test_an_errored_source_is_never_derived_as_absence():
    profile = integrate_results(executed(REG=("OK", [REG_ROW]), INS=("ERROR", []), THEFT=("ERROR", []), CAM=("OK", [CAM_ROW])),
                                "DL01AB1234", CORE)
    assert profile["insurance_status"] == "UNKNOWN"
    assert profile["stolen_status"] == "UNKNOWN"
