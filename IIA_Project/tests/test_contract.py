"""Contract tests: the shapes every module agrees on (CLAUDE.md §5, design PDF §5 and §7)."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from mediator.contract import (
    DERIVED_ATTRIBUTES,
    GLOBAL_ATTRIBUTES,
    FederationResponse,
    JoinSpec,
    KeyPredicate,
    PlanTrace,
    Registry,
    SourceResult,
    SourceSpec,
    canonical_plate,
)


def spec_dict(**overrides) -> dict:
    base = {
        "source_id": "INS",
        "display_name": "Insurance provider",
        "dbms": "mysql",
        "base_url": "http://127.0.0.1:8002",
        "authority": "authoritative",
        "trust": 0.9,
        "timeout_ms": 3000,
        "covers": ["plate_number", "insurance_expiry"],
        "key_predicate": {"column": "vehicle_reg"},
        "query_template": "SELECT {columns} FROM {table}{joins} WHERE {predicate}{order_by}",
        "table": "POLICY_RECORDS",
        "attribute_map": [
            {"source_table": "POLICY_RECORDS", "source_attr": "vehicle_reg", "global_attr": "plate_number", "score": 1.0},
            {"source_table": "POLICY_RECORDS", "source_attr": "policy_until", "global_attr": "insurance_expiry", "score": 0.9},
        ],
    }
    base.update(overrides)
    return base


def owners_join_dict(**overrides) -> dict:
    """The design PDF's REG: owner_name lives in OWNERS, one join away from the plate."""
    entry = spec_dict(
        source_id="REG", table="VEHICLE_REGISTRATION", covers=["plate_number", "owner_name"],
        key_predicate={"column": "registration_no"},
        joins=[{"table": "OWNERS", "left": "VEHICLE_REGISTRATION.owner_id", "right": "OWNERS.owner_id"}],
        attribute_map=[
            {"source_table": "VEHICLE_REGISTRATION", "source_attr": "registration_no", "global_attr": "plate_number"},
            {"source_table": "OWNERS", "source_attr": "full_name", "global_attr": "owner_name"},
        ])
    entry.update(overrides)
    return entry


def puc_dict() -> dict:
    """UC6: a fifth agency bringing an attribute nobody had before."""
    return spec_dict(
        source_id="PUC", table="POLLUTION_CERT", covers=["plate_number", "puc_expiry"],
        key_predicate={"column": "regn_number"},
        attribute_map=[
            {"source_table": "POLLUTION_CERT", "source_attr": "regn_number", "global_attr": "plate_number"},
            {"source_table": "POLLUTION_CERT", "source_attr": "valid_upto", "global_attr": "puc_expiry"},
        ])


# --- global vocabulary -------------------------------------------------------

def test_global_attributes_are_the_nineteen_agreed_names():
    assert len(GLOBAL_ATTRIBUTES) == 19
    assert "plate_number" in GLOBAL_ATTRIBUTES
    assert "observed_colour" in GLOBAL_ATTRIBUTES


def test_insurance_status_is_derived_from_the_expiry_never_stored():
    assert DERIVED_ATTRIBUTES == {"insurance_status": ("insurance_expiry",)}
    assert "insurance_status" not in GLOBAL_ATTRIBUTES


@pytest.mark.parametrize("raw", ["DL01AB1234", "DL-01-AB-1234", "dl 01 ab 1234", "  Dl.01/ab_1234 "])
def test_canonical_plate_strips_to_uppercase_alphanumerics(raw):
    assert canonical_plate(raw) == "DL01AB1234"


def test_canonical_plate_does_not_undo_ocr_corruption():
    # Un-corrupting is the source's error model (registry data), not the canonical form.
    assert canonical_plate("DLOIAB1234") == "DLOIAB1234"


@pytest.mark.parametrize("raw", ["", "   ", "--//--"])
def test_canonical_plate_rejects_input_with_no_alphanumerics(raw):
    with pytest.raises(ValueError):
        canonical_plate(raw)


# --- SourceResult status rules (§5.3) ---------------------------------------

def _result(**kw) -> SourceResult:
    base = dict(source_id="INS", status="OK", rows=[], row_count=0, sql_sent="SELECT 1",
                fetched_at=datetime.now(timezone.utc), elapsed_ms=12, error=None)
    base.update(kw)
    return SourceResult(**base)


def test_ok_with_zero_rows_is_valid_because_absence_is_data():
    assert _result().status == "OK"


def test_row_count_must_equal_number_of_rows():
    with pytest.raises(ValidationError):
        _result(rows=[{"a": 1}], row_count=2)


@pytest.mark.parametrize("status", ["TIMEOUT", "DOWN", "ERROR"])
def test_failed_status_requires_an_error_message(status):
    with pytest.raises(ValidationError):
        _result(status=status, error=None)


@pytest.mark.parametrize("status", ["TIMEOUT", "DOWN", "ERROR"])
def test_failed_status_cannot_carry_rows(status):
    with pytest.raises(ValidationError):
        _result(status=status, rows=[{"a": 1}], row_count=1, error="boom")


def test_ok_status_cannot_carry_an_error():
    with pytest.raises(ValidationError):
        _result(error="should not be here")


def test_unknown_status_is_rejected():
    with pytest.raises(ValidationError):
        _result(status="MAYBE")


# --- SourceSpec (registry entry) --------------------------------------------

def test_valid_spec_is_accepted_with_defaults():
    spec = SourceSpec(**spec_dict())
    assert spec.key_predicate.match == "exact"
    assert (spec.aggregate.strategy, spec.aggregate.pushdown) == ("all", False)
    assert (spec.joins, spec.identity_authority) == ([], False)


@pytest.mark.parametrize("trust", [-0.1, 1.01])
def test_trust_outside_unit_interval_is_rejected(trust):
    with pytest.raises(ValidationError):
        SourceSpec(**spec_dict(trust=trust))


def test_non_positive_timeout_is_rejected():
    with pytest.raises(ValidationError):
        SourceSpec(**spec_dict(timeout_ms=0))


def test_covering_an_attribute_with_no_mapping_is_recorded_not_rejected():
    # THEFT covers stolen_status, which the integrator derives from three of its columns, so no
    # single column maps to it. Such an entry stays in covers[] (it must still drive source
    # selection) and is listed for the coverage report instead of failing validation.
    spec = SourceSpec(**spec_dict(covers=["plate_number", "owner_name"]))
    assert spec.covers_unmapped == ["owner_name"]
    assert "owner_name" in spec.covers


@pytest.mark.parametrize("tpl", ["DELETE FROM x", "  update x set a=1", "WITH x AS (SELECT 1) DELETE FROM y"])
def test_query_template_must_be_a_select(tpl):
    with pytest.raises(ValidationError):
        SourceSpec(**spec_dict(query_template=tpl))


def test_in_variants_match_requires_a_variant_map():
    with pytest.raises(ValidationError):
        KeyPredicate(column="plate_id", match="in_variants", variant_map={})


def test_join_declares_the_table_that_supplies_mapped_columns():
    spec = SourceSpec(**owners_join_dict())
    assert spec.joins == [JoinSpec(table="OWNERS", left="VEHICLE_REGISTRATION.owner_id", right="OWNERS.owner_id")]


def test_mapping_a_column_from_a_table_that_is_never_joined_is_rejected():
    with pytest.raises(ValidationError, match="OWNERS"):
        SourceSpec(**owners_join_dict(joins=[]))


def test_join_sides_must_be_qualified_column_names():
    with pytest.raises(ValidationError):
        JoinSpec(table="OWNERS", left="owner_id", right="OWNERS.owner_id")


def test_order_by_pushdown_is_opt_in():
    spec = SourceSpec(**spec_dict(aggregate={"strategy": "latest", "global_attr": "insurance_expiry"}))
    assert spec.aggregate.pushdown is False


# --- Registry ----------------------------------------------------------------

def test_registry_rejects_duplicate_source_ids():
    with pytest.raises(ValidationError, match="INS"):
        Registry(sources=[spec_dict(), spec_dict()])


def test_unknown_global_attribute_is_rejected_naming_source_and_attribute():
    bad = spec_dict()
    bad["attribute_map"][1]["global_attr"] = "policy_until_date"
    bad["covers"] = ["plate_number", "policy_until_date"]
    with pytest.raises(ValidationError, match=r"INS.*policy_until_date"):
        Registry(sources=[bad])


def test_a_new_attribute_is_rejected_until_the_registry_declares_it():
    with pytest.raises(ValidationError, match="puc_expiry"):
        Registry(sources=[spec_dict(), puc_dict()])


def test_registry_can_extend_the_vocabulary_for_a_new_source():
    reg = Registry(sources=[spec_dict(), puc_dict()], global_attributes=[*GLOBAL_ATTRIBUTES, "puc_expiry"])
    assert "puc_expiry" in reg.vocabulary
    assert reg.coverage_matrix()["puc_expiry"] == ["PUC"]


def test_derived_attribute_must_be_built_from_known_attributes():
    with pytest.raises(ValidationError, match="policy_end"):
        Registry(sources=[spec_dict()], derived={"insurance_status": ["policy_end"]})


def test_derived_attribute_cannot_shadow_a_stored_one():
    with pytest.raises(ValidationError, match="insurance_expiry"):
        Registry(sources=[spec_dict()], derived={"insurance_expiry": ["insurance_start"]})


def test_resolve_expands_derived_attributes_and_rejects_unknown_ones():
    reg = Registry(sources=[spec_dict()])
    assert reg.resolve(["insurance_status", "plate_number", "insurance_expiry"]) == ["insurance_expiry", "plate_number"]
    with pytest.raises(ValueError, match="nope"):
        reg.resolve(["nope"])


def test_coverage_matrix_maps_each_attribute_to_its_sources():
    reg = Registry(sources=[
        spec_dict(),
        spec_dict(source_id="REG", covers=["plate_number"], attribute_map=[spec_dict()["attribute_map"][0]]),
    ])
    matrix = reg.coverage_matrix()
    assert matrix["plate_number"] == ["INS", "REG"]
    assert matrix["insurance_expiry"] == ["INS"]
    assert matrix["owner_name"] == []
    assert matrix["insurance_status"] == ["INS"]  # derived: whoever supplies its inputs
    assert reg.vocabulary[-1] == "insurance_status"


# --- FederationResponse ------------------------------------------------------

def test_federation_response_round_trips_through_json():
    resp = FederationResponse(
        results=[_result()],
        trace=PlanTrace(plate_raw="dl 01 ab 1234", plate_normalized="DL01AB1234",
                        requested_attrs=list(GLOBAL_ATTRIBUTES), sources_selected=["INS"],
                        sources_skipped={"CAM": "covers none of the requested attributes"},
                        total_elapsed_ms=40),
    )
    again = FederationResponse.model_validate_json(resp.model_dump_json())
    assert again == resp
    assert again.by_source("INS").status == "OK"
    assert again.by_source("NOPE") is None
