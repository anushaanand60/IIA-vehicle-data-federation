"""Mock sources (CLAUDE.md §7 step 5) mirroring the team design PDF §3: tables, heterogeneity, stories."""
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pytest

from sources._mock.seed import STORY_PLATES, build_all

ROOT = Path(__file__).resolve().parents[1]
MOCK_DIR = ROOT / "sources" / "_mock"
CANONICAL = r"[A-Z]{2}\d{2}[A-Z]{2}\d{4}"


@pytest.fixture(scope="module")
def dbs(tmp_path_factory) -> dict[str, Path]:
    return build_all(tmp_path_factory.mktemp("mock"))


def rows(dbs, source_id: str, sql: str) -> list[tuple]:
    with closing(sqlite3.connect(dbs[source_id])) as con:
        return con.execute(sql).fetchall()


def column(dbs, source_id: str, sql: str) -> list:
    return [r[0] for r in rows(dbs, source_id, sql)]


def columns_of(dbs, source_id: str, table: str) -> list[str]:
    return [r[1] for r in rows(dbs, source_id, f"PRAGMA table_info({table})")]


# --- shape: the design PDF's tables, one database per agency ---------------------

def test_builds_the_four_sources_plus_the_uc6_pollution_authority(dbs):
    assert set(dbs) == {"REG", "INS", "THEFT", "CAM", "PUC"} and all(p.exists() for p in dbs.values())


@pytest.mark.parametrize("source_id, table, expected", [
    ("REG", "OWNERS", ["owner_id", "full_name", "address_line", "city"]),
    ("REG", "VEHICLE_REGISTRATION", ["registration_id", "registration_no", "owner_id", "make", "model", "colour",
                                     "fuel_type", "registered_on", "reg_status", "rto_code"]),
    ("INS", "INSURERS", ["insurer_id", "insurer_name"]),
    ("INS", "POLICY_RECORDS", ["policy_id", "vehicle_reg", "insurer_id", "policy_type", "policy_start", "policy_until",
                               "is_active", "premium_inr"]),
    ("THEFT", "CRIME_RECORDS", ["incident_id", "vehicle_number", "fir_no", "reported_date", "incident_type",
                                "stolen_flag", "recovered_flag", "case_status", "police_station"]),
    ("CAM", "CAMERAS", ["camera_id", "location_name", "lat", "lon"]),
    ("CAM", "PLATE_CAPTURES", ["capture_id", "plate_id", "camera_id", "captured_at", "observed_make", "observed_model",
                               "observed_colour", "ocr_confidence"]),
    ("PUC", "POLLUTION_CERT", ["cert_no", "regn_number", "valid_upto"]),
])
def test_tables_and_columns_match_the_design_pdf(dbs, source_id, table, expected):
    assert columns_of(dbs, source_id, table) == expected


def test_story_plates_are_the_six_from_the_brief():
    assert STORY_PLATES == ("DL01AB1234", "DL05CD9876", "DL09KL3321", "HR26EF4455", "UP16GH1122", "MH12IJ7788")


def test_about_forty_vehicles_with_one_registration_row_each(dbs):
    reg = column(dbs, "REG", "SELECT registration_no FROM VEHICLE_REGISTRATION")
    assert len(reg) == len(set(reg)) == 39  # MH12IJ7788 is the 40th: seen by camera, never registered


def test_owners_are_normalised_into_their_own_table(dbs):
    assert column(dbs, "REG", "SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM VEHICLE_REGISTRATION GROUP BY owner_id)")[0] > 1
    assert column(dbs, "REG", "SELECT o.full_name FROM VEHICLE_REGISTRATION v JOIN OWNERS o ON v.owner_id = o.owner_id "
                              "WHERE v.registration_no = 'DL01AB1234'") == ["Aarav Sharma"]


@pytest.mark.parametrize("registry_file", ["mock_mappings.json", "mock_mappings_uc6.json"])
def test_every_mock_registry_mapping_and_join_names_a_real_mock_column(dbs, registry_file):
    for source in json.loads((MOCK_DIR / registry_file).read_text(encoding="utf-8"))["sources"]:
        named = [(m["source_table"], m["source_attr"]) for m in source["attribute_map"]]
        for join in source.get("joins", []):
            named += [tuple(join["left"].split(".")), tuple(join["right"].split("."))]
        for table, col in named:
            assert col in columns_of(dbs, source["source_id"], table), (source["source_id"], table, col)


def test_columns_only_one_agency_has_are_left_unmapped(dbs):
    sources = json.loads((MOCK_DIR / "mock_mappings.json").read_text(encoding="utf-8"))["sources"]
    mapped = {m["source_attr"] for s in sources for m in s["attribute_map"]}
    assert not mapped & {"fuel_type", "rto_code", "premium_inr", "police_station", "ocr_confidence"}


def test_build_is_deterministic(tmp_path):
    first, second = build_all(tmp_path / "a"), build_all(tmp_path / "b")
    for source_id in first:
        with closing(sqlite3.connect(first[source_id])) as a, closing(sqlite3.connect(second[source_id])) as b:
            assert list(a.iterdump()) == list(b.iterdump())


# --- heterogeneity preserved (design PDF §3.5) ----------------------------------------

@pytest.mark.parametrize("source_id, sql, pattern", [
    ("REG", "SELECT registration_no FROM VEHICLE_REGISTRATION", CANONICAL),
    ("INS", "SELECT vehicle_reg FROM POLICY_RECORDS", r"[A-Z]{2}-\d{2}-[A-Z]{2}-\d{4}"),
    ("THEFT", "SELECT vehicle_number FROM CRIME_RECORDS", r"[a-z]{2} \d{2} [a-z]{2} \d{4}"),
    ("PUC", "SELECT regn_number FROM POLLUTION_CERT", r"[A-Z]{2} \d{2} [A-Z]{2} \d{4}"),
])
def test_each_authoritative_source_spells_plates_its_own_way(dbs, source_id, sql, pattern):
    values = column(dbs, source_id, sql)
    assert values and all(re.fullmatch(pattern, v) for v in values)


def test_camera_plates_include_o_0_and_i_1_misreads(dbs):
    plates = column(dbs, "CAM", "SELECT plate_id FROM PLATE_CAPTURES")
    assert {"DLOIAB1234", "MH121J7788"} <= set(plates)  # digit read as letter, letter read as digit
    assert sum(not re.fullmatch(CANONICAL, p) for p in plates) >= 4


def test_four_date_encodings(dbs):
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v)
               for v in column(dbs, "REG", "SELECT registered_on FROM VEHICLE_REGISTRATION"))
    assert all(re.fullmatch(r"\d{2}/\d{2}/\d{4}", v)
               for v in column(dbs, "INS", "SELECT policy_until FROM POLICY_RECORDS"))
    assert set(column(dbs, "THEFT", "SELECT DISTINCT typeof(reported_date) FROM CRIME_RECORDS")) == {"integer"}
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\+05:30", v)
               for v in column(dbs, "CAM", "SELECT captured_at FROM PLATE_CAPTURES"))


def test_boolean_and_status_encodings(dbs):
    assert set(column(dbs, "REG", "SELECT DISTINCT reg_status FROM VEHICLE_REGISTRATION")) == {"ACTIVE", "SUSPENDED", "CANCELLED"}
    assert set(column(dbs, "INS", "SELECT DISTINCT is_active FROM POLICY_RECORDS")) == {0, 1}
    assert set(column(dbs, "INS", "SELECT DISTINCT policy_type FROM POLICY_RECORDS")) == {"THIRD_PARTY", "COMPREHENSIVE"}
    assert set(column(dbs, "THEFT", "SELECT DISTINCT stolen_flag FROM CRIME_RECORDS")) == {"Y", "N"}
    assert set(column(dbs, "THEFT", "SELECT DISTINCT recovered_flag FROM CRIME_RECORDS")) == {"Y", "N"}


def test_insurance_and_camera_hold_many_rows_per_vehicle(dbs):
    per_policy = "SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM POLICY_RECORDS GROUP BY vehicle_reg)"
    per_capture = "SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM PLATE_CAPTURES GROUP BY plate_id)"
    assert column(dbs, "INS", per_policy)[0] > 1 and column(dbs, "CAM", per_capture)[0] > 1


def test_dirty_rows_for_the_cleaning_stage(dbs):
    assert column(dbs, "REG", "SELECT COUNT(*) FROM VEHICLE_REGISTRATION WHERE make IS NULL")[0] >= 1
    assert column(dbs, "REG", "SELECT COUNT(*) FROM VEHICLE_REGISTRATION WHERE make = 'Hyundia'")[0] >= 1
    duplicates = "SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM POLICY_RECORDS GROUP BY vehicle_reg, policy_start, policy_until)"
    assert column(dbs, "INS", duplicates)[0] > 1


# --- the six story vehicles (CLAUDE.md §9, design PDF §4) -----------------------------------

def test_clean_vehicle_agrees_across_sources(dbs):
    (make, model, colour), = rows(dbs, "REG", "SELECT make, model, colour FROM VEHICLE_REGISTRATION "
                                              "WHERE registration_no = 'DL01AB1234'")
    seen = rows(dbs, "CAM", "SELECT observed_make, observed_model, observed_colour FROM PLATE_CAPTURES "
                            "WHERE plate_id IN ('DL01AB1234', 'DLOIAB1234')")
    assert {tuple(v.lower() for v in r) for r in seen} == {(make.lower(), model.lower(), colour.lower())}
    assert column(dbs, "INS", "SELECT MAX(is_active) FROM POLICY_RECORDS WHERE vehicle_reg = 'DL-01-AB-1234'") == [1]
    assert column(dbs, "THEFT", "SELECT COUNT(*) FROM CRIME_RECORDS WHERE vehicle_number = 'dl 01 ab 1234'") == [0]
    assert column(dbs, "PUC", "SELECT COUNT(*) FROM POLLUTION_CERT WHERE regn_number = 'DL 01 AB 1234'") == [1]


def test_expired_policy_and_the_lexical_sort_trap(dbs):
    until = column(dbs, "INS", "SELECT policy_until FROM POLICY_RECORDS WHERE vehicle_reg = 'DL-05-CD-9876'")
    assert max(until, key=lambda v: datetime.strptime(v, "%d/%m/%Y")) == "12/07/2026"
    assert max(until) != "12/07/2026"  # sorting DD/MM/YYYY text picks the wrong policy: never push ORDER BY down


def test_vehicle_with_no_policy_row(dbs):
    assert column(dbs, "INS", "SELECT COUNT(*) FROM POLICY_RECORDS WHERE vehicle_reg = 'DL-09-KL-3321'") == [0]
    assert column(dbs, "REG", "SELECT COUNT(*) FROM VEHICLE_REGISTRATION WHERE registration_no = 'DL09KL3321'") == [1]


def test_stolen_vehicle_with_open_case_and_a_recovered_one_elsewhere(dbs):
    assert ("THEFT", "Y", "N", "OPEN") in rows(dbs, "THEFT", "SELECT incident_type, stolen_flag, recovered_flag, "
                                                            "case_status FROM CRIME_RECORDS WHERE vehicle_number = 'hr 26 ef 4455'")
    recovered = ("SELECT COUNT(*) FROM CRIME_RECORDS WHERE incident_type = 'THEFT' AND stolen_flag = 'Y' "
                 "AND recovered_flag = 'Y' AND case_status = 'CLOSED'")
    assert column(dbs, "THEFT", recovered)[0] >= 1


def test_possible_cloned_plate(dbs):
    assert rows(dbs, "REG", "SELECT make, model, colour FROM VEHICLE_REGISTRATION "
                            "WHERE registration_no = 'UP16GH1122'") == [("Hyundai", "Creta", "White")]
    assert set(rows(dbs, "CAM", "SELECT observed_make, observed_model, observed_colour FROM PLATE_CAPTURES "
                                "WHERE plate_id IN ('UP16GH1122', 'UPI6GH1122')")) == {("HYUNDAI", "VENUE", "silver")}


def test_seen_by_camera_but_never_registered(dbs):
    assert column(dbs, "REG", "SELECT COUNT(*) FROM VEHICLE_REGISTRATION WHERE registration_no = 'MH12IJ7788'") == [0]
    assert column(dbs, "CAM", "SELECT COUNT(*) FROM PLATE_CAPTURES WHERE plate_id = 'MH12IJ7788'")[0] >= 1
