"""Executor (CLAUDE.md §7 steps 7-8, design PDF §8): selection, plates, metadata-driven SQL, resilience."""
from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from mediator.contract import FederationResponse, Registry, RenderSpec
from mediator.executor import build_sql, classify_http, execute, plate_variants, render_plate, select_sources
from mediator.registry_loader import normalize_registry
from sources._mock.run_all import running
from sources.wrapper_template import guard_sql

ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "sources" / "_mock" / "mock_mappings.json"
MOCK_UC6 = ROOT / "sources" / "_mock" / "mock_mappings_uc6.json"
SOURCE_IDS = ["REG", "INS", "THEFT", "CAM"]
INSURANCE_ONLY = ["insurer_name", "policy_type", "insurance_start", "insurance_expiry"]
MOCK_PORTS = {"REG": 18001, "INS": 18002, "THEFT": 18003, "CAM": 18004}


def mock_entries(path: Path = MOCK) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["sources"]


def registry_on(ports: dict[str, int] | None = None, path: Path = MOCK, **overrides: dict) -> Registry:
    raw = json.loads(path.read_text(encoding="utf-8"))
    for e in raw["sources"]:
        if ports:
            e["base_url"] = f"http://127.0.0.1:{ports[e['source_id']]}"
        e.update(overrides.get(e["source_id"], {}))
    return normalize_registry(raw)


def one_source(source_id: str, base_url: str, **fields) -> Registry:
    entry = next(e for e in mock_entries() if e["source_id"] == source_id)
    entry.update(base_url=base_url, **fields)
    return normalize_registry([entry])


def ids(specs) -> list[str]:
    return [s.source_id for s in specs]


MOCK_REGISTRY = registry_on()


# ================================ unit: no network ================================

# --- source selection (design PDF §8.1 step 2) ----------------------------------------

def test_full_profile_request_selects_every_source():
    selected, skipped = select_sources(MOCK_REGISTRY, None)
    assert ids(selected) == SOURCE_IDS and skipped == {}


def test_insurance_only_request_asks_the_insurer_and_the_registration_authority():
    full, _ = select_sources(MOCK_REGISTRY, None)
    insurance, skipped = select_sources(MOCK_REGISTRY, INSURANCE_ONLY)
    assert ids(insurance) == ["REG", "INS"] and len(insurance) < len(full)  # UC1: "plan trace shows 2 sources"
    assert set(skipped) == {"THEFT", "CAM"}
    assert all("covers none" in reason for reason in skipped.values())


def test_derived_insurance_status_selects_the_source_of_its_input():
    selected, _ = select_sources(MOCK_REGISTRY, ["insurance_status"])
    assert ids(selected) == ["REG", "INS"]


def test_identity_authority_joins_any_narrow_request():
    selected, _ = select_sources(MOCK_REGISTRY, ["stolen_status"])
    assert ids(selected) == ["REG", "THEFT"]


def test_without_an_identity_authority_only_covering_sources_are_asked():
    selected, _ = select_sources(registry_on(REG={"identity_authority": False}), INSURANCE_ONLY)
    assert ids(selected) == ["INS"]


def test_requesting_only_the_plate_asks_every_source_whether_it_knows_it():
    selected, _ = select_sources(MOCK_REGISTRY, ["plate_number"])
    assert ids(selected) == SOURCE_IDS


def test_unknown_requested_attribute_is_a_caller_error():
    with pytest.raises(ValueError, match="insurance_expiry_date"):
        select_sources(MOCK_REGISTRY, ["insurance_expiry_date"])


# --- plates -------------------------------------------------------------------------

@pytest.mark.parametrize("source_id, expected", [
    ("REG", "DL01AB1234"), ("INS", "DL-01-AB-1234"), ("THEFT", "dl 01 ab 1234"), ("CAM", "DL01AB1234")])
def test_canonical_plate_is_rendered_in_each_sources_own_spelling(source_id, expected):
    assert render_plate("DL01AB1234", MOCK_REGISTRY.by_id(source_id).key_predicate.render) == expected


def test_render_copes_with_plates_shorter_or_longer_than_the_groups():
    spec = RenderSpec(groups=[2, 2, 2, 4], separator="-")
    assert render_plate("KA011234", spec) == "KA-01-12-34"
    assert render_plate("DL01AB12345678", spec) == "DL-01-AB-1234-5678"


def test_ocr_variants_cover_digits_read_as_letters():
    kp = MOCK_REGISTRY.by_id("CAM").key_predicate
    variants = plate_variants("DL01AB1234", kp.variant_map, kp.max_variants)
    assert variants[0] == "DL01AB1234" and "DLOIAB1234" in variants
    assert len(variants) == len(set(variants)) == 8  # three substitutable characters: 2 ** 3


def test_ocr_variants_cover_letters_read_as_digits():
    kp = MOCK_REGISTRY.by_id("CAM").key_predicate
    assert "MH121J7788" in plate_variants("MH12IJ7788", kp.variant_map, kp.max_variants)


def test_ocr_variants_are_breadth_first_by_substitution_count_and_capped():
    assert plate_variants("1111", {"1": ["I", "L"]}, max_variants=5) == ["1111", "I111", "L111", "1I11", "1L11"]


# --- SQL built purely from the registry (design PDF §8.1 step 3) --------------------------

def test_no_join_when_no_requested_column_needs_one():
    assert build_sql(MOCK_REGISTRY.by_id("INS"), "DL05CD9876", ["insurance_expiry"]) == (
        "SELECT vehicle_reg, policy_until FROM POLICY_RECORDS WHERE vehicle_reg = 'DL-05-CD-9876'")


def test_join_is_added_and_columns_qualified_when_a_requested_column_needs_it():
    assert build_sql(MOCK_REGISTRY.by_id("INS"), "DL05CD9876", ["insurer_name"]) == (
        "SELECT POLICY_RECORDS.vehicle_reg, INSURERS.insurer_name FROM POLICY_RECORDS "
        "LEFT JOIN INSURERS ON POLICY_RECORDS.insurer_id = INSURERS.insurer_id "
        "WHERE POLICY_RECORDS.vehicle_reg = 'DL-05-CD-9876'")


def test_full_request_on_the_registration_authority_joins_owners():
    assert build_sql(MOCK_REGISTRY.by_id("REG"), "DL01AB1234", None) == (
        "SELECT VEHICLE_REGISTRATION.registration_no, OWNERS.full_name, VEHICLE_REGISTRATION.make, "
        "VEHICLE_REGISTRATION.model, VEHICLE_REGISTRATION.colour, VEHICLE_REGISTRATION.registered_on, "
        "VEHICLE_REGISTRATION.reg_status FROM VEHICLE_REGISTRATION "
        "LEFT JOIN OWNERS ON VEHICLE_REGISTRATION.owner_id = OWNERS.owner_id "
        "WHERE VEHICLE_REGISTRATION.registration_no = 'DL01AB1234'")


def test_identity_check_projects_only_the_key_column():
    assert build_sql(MOCK_REGISTRY.by_id("REG"), "DL01AB1234", ["insurance_expiry"]) == (
        "SELECT registration_no FROM VEHICLE_REGISTRATION WHERE registration_no = 'DL01AB1234'")


def test_order_by_is_pushed_down_for_an_epoch_column():
    assert build_sql(MOCK_REGISTRY.by_id("THEFT"), "HR26EF4455", None).endswith(
        "WHERE vehicle_number = 'hr 26 ef 4455' ORDER BY reported_date DESC")


def test_text_dates_are_never_ordered_in_sql():
    assert "ORDER BY" not in build_sql(MOCK_REGISTRY.by_id("INS"), "DL05CD9876", None)


def test_ocr_predicate_join_and_qualified_order_by_together():
    sql = build_sql(MOCK_REGISTRY.by_id("CAM"), "DL01AB1234", None)
    assert "LEFT JOIN CAMERAS ON PLATE_CAPTURES.camera_id = CAMERAS.camera_id" in sql
    assert "WHERE PLATE_CAPTURES.plate_id IN ('DL01AB1234', " in sql and "'DLOIAB1234'" in sql
    assert sql.endswith(" ORDER BY PLATE_CAPTURES.captured_at DESC")


def test_same_column_name_from_two_tables_is_aliased_rather_than_lost():
    extra = [{"source_table": "PLATE_CAPTURES", "source_attr": "camera_id", "global_attr": "last_seen_location"},
             {"source_table": "CAMERAS", "source_attr": "camera_id", "global_attr": "last_seen_location"}]
    cam = registry_on(CAM={"attribute_map": mock_entries()[3]["attribute_map"] + extra}).by_id("CAM")
    sql = build_sql(cam, "DL01AB1234", ["last_seen_location"])
    assert "PLATE_CAPTURES.camera_id, CAMERAS.camera_id AS CAMERAS__camera_id" in sql


@pytest.mark.parametrize("source_id", SOURCE_IDS)
def test_generated_sql_passes_the_wrapper_guard(source_id):
    spec = MOCK_REGISTRY.by_id(source_id)
    guard_sql(build_sql(spec, "UP16GH1122", None), [spec.table, *(j.table for j in spec.joins)])


def test_quotes_in_registry_data_cannot_break_out_of_the_literal():
    spec = registry_on(THEFT={"key_predicate": {"column": "vehicle_number",
                                                "render": {"groups": [2], "separator": "'"}}}).by_id("THEFT")
    assert "= 'DL''01AB1234'" in build_sql(spec, "DL01AB1234", None)


def test_template_may_use_the_rendered_plate_directly():
    spec = registry_on(THEFT={"query_template": "SELECT * FROM CRIME_RECORDS WHERE vehicle_number = '{plate}'"}).by_id("THEFT")
    assert build_sql(spec, "DL01AB1234", None) == "SELECT * FROM CRIME_RECORDS WHERE vehicle_number = 'dl 01 ab 1234'"


def test_template_with_an_unknown_hole_is_a_value_error():
    spec = registry_on(THEFT={"query_template": "SELECT {columns} FROM {table} WHERE {predicate} AND {tenant}"}).by_id("THEFT")
    with pytest.raises(ValueError, match="tenant"):
        build_sql(spec, "DL01AB1234", None)


def test_template_without_a_joins_hole_cannot_serve_joined_columns():
    spec = registry_on(REG={"query_template": "SELECT {columns} FROM {table} WHERE {predicate}"}).by_id("REG")
    with pytest.raises(ValueError, match="joins"):
        build_sql(spec, "DL01AB1234", None)


# --- classification and invariants ------------------------------------------------------

@pytest.mark.parametrize("code, status", [
    (503, "DOWN"), (504, "TIMEOUT"), (400, "ERROR"), (404, "ERROR"), (422, "ERROR"), (500, "ERROR")])
def test_non_200_wrapper_responses_are_classified(code, status):
    assert classify_http(code) == status


def test_executor_contains_no_source_specific_names():
    code = (ROOT / "mediator" / "executor.py").read_text(encoding="utf-8")
    schema_names = (r"policy_until|vehicle_reg|policy_records|crime_records|plate_captures|registration_no|"
                    r"vehicle_number|plate_id|insurers|cameras|owners|full_name|captured_at|reported_date|"
                    r"insurance_status|puc_expiry")
    assert re.findall(schema_names, code, re.I) == []
    assert re.findall(r"\b(REG|INS|THEFT|CAM|PUC)\b", code) == []


# ============================ integration: real wrappers ============================

@pytest.fixture(scope="module")
def live_registry(tmp_path_factory) -> Registry:
    with running(ports=MOCK_PORTS, db_dir=tmp_path_factory.mktemp("mock_db")):
        yield registry_on(MOCK_PORTS)


@pytest.mark.parametrize("raw", ["HR26EF4455", "HR-26-EF-4455", "hr 26 ef 4455", "  Hr.26/eF 4455 "])
def test_any_spelling_of_the_plate_finds_rows_in_all_four_sources(live_registry, raw):
    resp = execute(live_registry, raw)
    assert resp.trace.plate_raw == raw and resp.trace.plate_normalized == "HR26EF4455"
    assert {r.source_id: (r.status, r.row_count > 0) for r in resp.results} == {s: ("OK", True) for s in SOURCE_IDS}


def test_clean_vehicle_joins_run_on_the_source_and_ocr_misreads_are_found(live_registry):
    resp = execute(live_registry, "DL01AB1234")
    assert all(r.status == "OK" for r in resp.results)
    assert resp.by_source("REG").rows[0]["full_name"] == "Aarav Sharma"  # through the OWNERS join
    cam = resp.by_source("CAM").rows
    assert "DLOIAB1234" in {row["plate_id"] for row in cam} and all(row["location_name"] for row in cam)
    assert resp.by_source("THEFT").row_count == 0


def test_expired_policy_arrives_raw_with_source_names_and_formats(live_registry):
    ins = execute(live_registry, "DL05CD9876").by_source("INS")
    assert ins.status == "OK" and "12/07/2026" in {row["policy_until"] for row in ins.rows}
    assert all(row["vehicle_reg"] == "DL-05-CD-9876" and row["insurer_name"] == "HDFC ERGO" for row in ins.rows)
    assert not any("insurance_expiry" in row for row in ins.rows)  # no renaming to global names


def test_missing_policy_is_ok_with_zero_rows_not_a_failure(live_registry):
    ins = execute(live_registry, "DL09KL3321").by_source("INS")
    assert (ins.status, ins.row_count, ins.error) == ("OK", 0, None)


def test_stolen_vehicle_latest_incident_comes_first(live_registry):
    first = execute(live_registry, "HR26EF4455").by_source("THEFT").rows[0]
    assert (first["stolen_flag"], first["recovered_flag"], first["case_status"]) == ("Y", "N", "OPEN")


def test_cloned_plate_conflict_is_passed_through_unresolved(live_registry):
    resp = execute(live_registry, "UP16GH1122")
    assert resp.by_source("REG").rows[0]["model"] == "Creta"
    assert {row["observed_model"] for row in resp.by_source("CAM").rows} == {"VENUE"}


def test_unregistered_vehicle_is_absent_from_reg_but_seen_by_camera(live_registry):
    resp = execute(live_registry, "MH12IJ7788")
    assert (resp.by_source("REG").status, resp.by_source("REG").row_count) == ("OK", 0)
    assert {"MH12IJ7788", "MH121J7788"} <= {row["plate_id"] for row in resp.by_source("CAM").rows}


def test_uc1_insurance_question_asks_two_sources(live_registry):
    resp = execute(live_registry, "DL05CD9876", INSURANCE_ONLY)
    assert [r.source_id for r in resp.results] == ["REG", "INS"] == resp.trace.sources_selected
    assert set(resp.trace.sources_skipped) == {"THEFT", "CAM"}
    assert resp.by_source("REG").rows == [{"registration_no": "DL05CD9876"}]  # existence confirmed, nothing more
    assert set(resp.by_source("INS").rows[0]) == {"vehicle_reg", "insurer_name", "policy_type", "policy_start", "policy_until"}


def test_derived_attribute_fetches_only_its_inputs(live_registry):
    resp = execute(live_registry, "DL05CD9876", ["insurance_status"])
    assert resp.trace.requested_attrs == ["insurance_status"]
    assert {tuple(row) for row in resp.by_source("INS").rows} == {("vehicle_reg", "policy_until")}


def test_full_request_trace_lists_derived_attributes_too(live_registry):
    assert "insurance_status" in execute(live_registry, "DL01AB1234").trace.requested_attrs


def test_sql_sent_is_recorded_per_source(live_registry):
    for r in execute(live_registry, "DL01AB1234").results:
        assert r.sql_sent == build_sql(live_registry.by_id(r.source_id), "DL01AB1234", None)
        assert r.fetched_at is not None


def test_mediator_overhead_is_small_next_to_the_source_calls(live_registry):
    execute(live_registry, "DL01AB1234")  # warm imports and the OS resolver
    resp = execute(live_registry, "DL01AB1234")
    overhead = resp.trace.total_elapsed_ms - max(r.elapsed_ms for r in resp.results)
    assert overhead < 150, f"{overhead} ms spent outside the source calls"


def test_guard_rejection_becomes_error_status_not_an_exception(live_registry):
    broken = registry_on(MOCK_PORTS, THEFT={"query_template": "SELECT {columns} FROM {table} JOIN officers ON 1=1 WHERE {predicate}"})
    theft = execute(broken, "DL01AB1234").by_source("THEFT")
    assert theft.status == "ERROR" and "whitelist" in theft.error and theft.rows == []


def test_command_line_prints_results_and_trace(live_registry, tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({"sources": [s.model_dump() for s in live_registry.sources]}), encoding="utf-8")
    env = {**os.environ, "IIA_REGISTRY": str(path)}
    human = subprocess.run([sys.executable, "-m", "mediator.executor", "DL05CD9876"],
                           capture_output=True, text=True, cwd=ROOT, env=env, timeout=30)
    assert human.returncode == 0, human.stderr
    assert f"registry : {path}" in human.stdout  # so a demo never silently runs on the wrong registry
    assert "DL05CD9876" in human.stdout and "12/07/2026" in human.stdout
    assert all(source_id in human.stdout for source_id in SOURCE_IDS)
    as_json = subprocess.run([sys.executable, "-m", "mediator.executor", "DL05CD9876", "--attrs", "insurance_status", "--json"],
                             capture_output=True, text=True, cwd=ROOT, env=env, timeout=30)
    assert FederationResponse.model_validate_json(as_json.stdout).trace.sources_selected == ["REG", "INS"]


def test_uc6_fifth_source_is_queried_with_no_engine_change(tmp_path):
    ports = {"REG": 18101, "INS": 18102, "THEFT": 18103, "CAM": 18104, "PUC": 18105}
    with running(source_ids=list(ports), ports=ports, db_dir=tmp_path):
        registry = registry_on(ports, path=MOCK_UC6)
        narrow = execute(registry, "DL01AB1234", ["puc_expiry"])
        full = execute(registry, "DL01AB1234")
    assert [r.source_id for r in narrow.results] == ["REG", "PUC"]
    assert narrow.by_source("PUC").rows == [{"regn_number": "DL 01 AB 1234", "valid_upto": "2027-02-28"}]
    assert [r.source_id for r in full.results] == [*SOURCE_IDS, "PUC"] and all(r.status == "OK" for r in full.results)


# ================================== failure modes ==================================

@pytest.fixture()
def black_hole() -> int:
    """A port that accepts TCP connections and never answers: the slow-source case."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(16)
        yield sock.getsockname()[1]


@pytest.fixture()
def garbage_server() -> int:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html>not json</html>")

        def log_message(self, *_: object) -> None:
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


def test_source_that_never_answers_is_timeout_within_its_budget(black_hole):
    started = time.monotonic()
    r = execute(one_source("REG", f"http://127.0.0.1:{black_hole}", timeout_ms=300), "DL01AB1234").by_source("REG")
    assert r.status == "TIMEOUT" and r.error and r.rows == []
    assert time.monotonic() - started < 1.5


def test_sources_are_called_in_parallel_not_in_sequence(black_hole):
    entries = [e for e in mock_entries() if e["source_id"] in ("REG", "INS")]
    for e in entries:
        e.update(base_url=f"http://127.0.0.1:{black_hole}", timeout_ms=700)
    started = time.monotonic()
    resp = execute(normalize_registry(entries), "DL01AB1234")
    assert [r.status for r in resp.results] == ["TIMEOUT", "TIMEOUT"]
    assert time.monotonic() - started < 1.2  # sequential would be at least 1.4 s


def test_unparseable_wrapper_response_is_error(garbage_server):
    r = execute(one_source("INS", f"http://127.0.0.1:{garbage_server}"), "DL01AB1234").by_source("INS")
    assert r.status == "ERROR" and "json" in r.error.lower()


def test_killing_one_source_degrades_to_down_while_the_others_answer(tmp_path):
    ports = {"REG": 19001, "INS": 19002, "THEFT": 19003, "CAM": 19004}
    with running(ports=ports, db_dir=tmp_path) as procs:
        procs["INS"].kill()
        procs["INS"].wait(timeout=10)
        started = time.monotonic()
        resp = execute(registry_on(ports), "HR26EF4455")
        wall_ms = (time.monotonic() - started) * 1000
    assert {r.source_id: r.status for r in resp.results} == {"REG": "OK", "INS": "DOWN", "THEFT": "OK", "CAM": "OK"}
    assert resp.by_source("INS").error and resp.by_source("INS").rows == []
    assert resp.trace.total_elapsed_ms < 2000 and wall_ms < 2000
