"""Latest-wins in mediator/integrator.py comes from the registry, not from source-specific code.

Each source declares how its own rows are ordered with a mapping whose `aggregate` is
"latest_by:<column>"; the integrator parses that column through the mapping's own transform and
keeps the newest row. A source that declares nothing keeps the order its wrapper returned.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def registry(tmp_path, monkeypatch):
    """A private meta.db: these tests write mappings, and must not touch the demo registry."""
    import mediator.catalog as catalog_module
    monkeypatch.setattr(catalog_module, "META_DB_PATH", str(tmp_path / "meta.db"))
    catalog_module.init_meta_db()
    return catalog_module


def _map(registry, source_id, rows):
    for source_table, source_attr, global_attr, transform_fn, aggregate in rows:
        registry.save_mapping(source_id=source_id, source_table=source_table, source_attr=source_attr,
                              global_attr=global_attr, transform_fn=transform_fn, aggregate=aggregate)


def _integrate(source_id, rows):
    from mediator.integrator import integrate_results
    execution = {"sources_executed": {source_id: {"status": "OK", "rows": rows,
                                                  "fetched_at": "2026-09-16T00:00:00+00:00"}}}
    return integrate_results(execution, "DL01AB1234", [source_id])


INS_MAPPINGS = [
    ("POLICY_RECORDS", "vehicle_reg", "plate_number", "norm_plate", None),
    ("POLICY_RECORDS", "policy_start", "insurance_start", "parse_ddmmyyyy", None),
    ("POLICY_RECORDS", "policy_until", "insurance_expiry", "parse_ddmmyyyy", "latest_by:policy_until"),
]

OLD_POLICY = {"vehicle_reg": "DL-01-AB-1234", "policy_start": "05/09/2022", "policy_until": "04/09/2024"}
NEW_POLICY = {"vehicle_reg": "DL-01-AB-1234", "policy_start": "27/05/2026", "policy_until": "13/11/2026"}


@pytest.mark.parametrize("order", [[OLD_POLICY, NEW_POLICY], [NEW_POLICY, OLD_POLICY]])
def test_insurer_latest_policy_wins_whatever_order_the_rows_arrive_in(registry, order):
    """DD/MM/YYYY does not sort as text - the mapping's parse_ddmmyyyy transform is what orders it."""
    _map(registry, "INS", INS_MAPPINGS)
    profile = _integrate("INS", order)
    assert profile["insurance_expiry"] == "2026-11-13"
    assert profile["insurance_start"] == "2026-05-27"


def test_camera_latest_capture_wins(registry):
    _map(registry, "CAM", [
        ("PLATE_CAPTURES", "plate_id", "plate_number", "norm_plate", None),
        ("PLATE_CAPTURES", "captured_at", "last_seen_time", "none", "latest_by:captured_at"),
        ("PLATE_CAPTURES", "observed_colour", "observed_colour", "title_case", None),
    ])
    profile = _integrate("CAM", [
        {"plate_id": "DL01AB1234", "captured_at": "2026-09-04T08:30:00", "observed_colour": "silver"},
        {"plate_id": "DL01AB1234", "captured_at": "2026-02-11T19:05:00", "observed_colour": "white"},
    ])
    assert profile["last_seen_time"] == "2026-09-04T08:30:00"
    assert profile["observed_colour"] == "Silver"


def test_police_latest_incident_wins_on_epoch_seconds(registry):
    _map(registry, "THEFT", [
        ("CRIME_RECORDS", "vehicle_number", "plate_number", "norm_plate", None),
        ("CRIME_RECORDS", "reported_date", "last_incident_date", "epoch_to_date", "latest_by:reported_date"),
        ("CRIME_RECORDS", "case_status", "case_status", "status_map", None),
        ("CRIME_RECORDS", "stolen_flag", "stolen_flag", "none", None),
        ("CRIME_RECORDS", "recovered_flag", "recovered_flag", "none", None),
    ])
    older = {"vehicle_number": "dl 01 ab 1234", "reported_date": 1739232000,  # 2025-02-11
             "case_status": "CLOSED", "stolen_flag": "Y", "recovered_flag": "Y"}
    newer = {"vehicle_number": "dl 01 ab 1234", "reported_date": 1755216000,  # 2025-08-15
             "case_status": "OPEN", "stolen_flag": "Y", "recovered_flag": "N"}
    profile = _integrate("THEFT", [older, newer])
    assert profile["last_incident_date"] == "2025-08-15"
    assert profile["case_status"] == "OPEN"
    assert profile["stolen_status"] == "STOLEN"  # derived from the newest incident's flags


def test_a_source_that_declares_no_ordering_keeps_the_first_row(registry):
    _map(registry, "REG", [
        ("VEHICLE_REGISTRATION", "registration_no", "plate_number", "norm_plate", None),
        ("VEHICLE_REGISTRATION", "colour", "vehicle_colour", "title_case", None),
    ])
    profile = _integrate("REG", [{"registration_no": "DL01AB1234", "colour": "white"},
                                 {"registration_no": "DL01AB1234", "colour": "black"}])
    assert profile["vehicle_colour"] == "White"


def test_unorderable_values_do_not_raise(registry):
    _map(registry, "INS", INS_MAPPINGS)
    profile = _integrate("INS", [{"vehicle_reg": "DL-01-AB-1234", "policy_until": None},
                                 {"vehicle_reg": "DL-01-AB-1234", "policy_until": "13/11/2026"},
                                 {"vehicle_reg": "DL-01-AB-1234"}])
    assert profile["insurance_expiry"] == "2026-11-13"


def test_integrator_latest_wins_names_no_source():
    code = (ROOT / "mediator" / "integrator.py").read_text(encoding="utf-8")
    assert re.findall(r'==\s*"(REG|INS|THEFT|CAM|PUC)"', code) == []
    assert re.findall(r"==\s*'(REG|INS|THEFT|CAM|PUC)'", code) == []
