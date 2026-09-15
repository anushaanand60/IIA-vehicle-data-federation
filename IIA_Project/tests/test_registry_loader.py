"""registry_loader: absorbs the registry as the team writes it, JSON (CLAUDE.md §5.2) or meta.db (design PDF §7.1)."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from mediator.contract import GLOBAL_ATTRIBUTES, JoinSpec, Registry
from mediator.registry_loader import (
    DEFAULT_TEMPLATE,
    META_DB,
    REAL_REGISTRY,
    RegistryError,
    default_registry_path,
    load_registry,
    normalize_registry,
)

ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "sources" / "_mock" / "mock_mappings.json"
MOCK_UC6 = ROOT / "sources" / "_mock" / "mock_mappings_uc6.json"
TEAMMATE = ROOT / "tests" / "fixtures" / "registry_teammate_shape.json"


def _mock_raw() -> dict:
    return json.loads(MOCK.read_text(encoding="utf-8"))


def make_meta_db(path: Path, *, global_schema: bool = False) -> Path:
    """A meta.db laid out exactly as design PDF §7.1 describes SOURCE_CATALOG and MAPPING_REGISTRY."""
    with closing(sqlite3.connect(path)) as con:
        con.executescript("""
            CREATE TABLE SOURCE_CATALOG (source_id TEXT PRIMARY KEY, display_name TEXT, dbms TEXT, base_url TEXT,
                identifier_attr TEXT, authority TEXT, trust_score REAL, covers TEXT, timeout_ms INTEGER,
                last_health TEXT, last_seen_at TEXT);
            CREATE TABLE MAPPING_REGISTRY (source_id TEXT, source_table TEXT, source_attr TEXT, global_attr TEXT,
                transform_fn TEXT, aggregate TEXT, join_path TEXT, match_score REAL, validated_by TEXT, validated_at TEXT);
        """)
        con.executemany("INSERT INTO SOURCE_CATALOG VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
            ("REG", "Regional Transport Office", "postgresql", "http://192.168.43.11:8001", "registration_no",
             "OFFICIAL", 0.95, '["plate_number", "owner_name", "vehicle_make"]', 1500, "OK", "2026-09-12T10:00:00"),
            ("CAM", "Road Camera Network", "postgresql", "http://192.168.43.14:8004/", "plate_id",
             "OBSERVATIONAL", 0.60, "plate_number, last_seen_location, last_seen_time", None, None, None),
        ])
        con.executemany("INSERT INTO MAPPING_REGISTRY VALUES (?,?,?,?,?,?,?,?,?,?)", [
            ("REG", "VEHICLE_REGISTRATION", "registration_no", "plate_number", "norm_plate", None, None, 1.0, "P1", "2026-09-08"),
            ("REG", "OWNERS", "full_name", "owner_name", None, None, "VEHICLE_REGISTRATION.owner_id=OWNERS.owner_id", 0.84, "P1", "2026-09-08"),
            ("REG", "VEHICLE_REGISTRATION", "make", "vehicle_make", "fix_make", None, None, 0.93, "P1", "2026-09-08"),
            ("CAM", "PLATE_CAPTURES", "plate_id", "plate_number", "norm_plate", None, None, 0.86, "P4", "2026-09-08"),
            ("CAM", "CAMERAS", "location_name", "last_seen_location", None, None, "PLATE_CAPTURES.camera_id=CAMERAS.camera_id", 0.72, "P4", "2026-09-08"),
            ("CAM", "PLATE_CAPTURES", "captured_at", "last_seen_time", None, "latest_by:captured_at", None, 0.79, "P4", "2026-09-08"),
        ])
        if global_schema:
            con.execute("CREATE TABLE GLOBAL_SCHEMA (global_attr TEXT, derived_from TEXT)")
            con.executemany("INSERT INTO GLOBAL_SCHEMA VALUES (?, ?)", [("puc_expiry", None), ("insurance_status", "insurance_expiry")])
        con.commit()
    return path


# --- the files in this repository ----------------------------------------------

def test_mock_registry_loads_all_four_sources():
    reg = load_registry(MOCK)
    assert [s.source_id for s in reg.sources] == ["REG", "INS", "THEFT", "CAM"]
    assert reg.by_id("CAM").key_predicate.match == "in_variants"
    assert reg.by_id("REG").identity_authority is True
    assert [j.table for j in reg.by_id("REG").joins] == ["OWNERS"]


def test_mock_registry_covers_every_attribute_including_derived_ones():
    uncovered = [a for a, srcs in load_registry(MOCK).coverage_matrix().items() if not srcs]
    assert uncovered == []


def test_mock_registry_notes_are_kept_in_extra_not_dropped():
    assert "lexically" in load_registry(MOCK).by_id("INS").extra["notes"]


def test_uc6_registry_adds_a_fifth_source_and_its_attribute_as_data_only():
    reg = load_registry(MOCK_UC6)
    assert [s.source_id for s in reg.sources] == ["REG", "INS", "THEFT", "CAM", "PUC"]
    assert "puc_expiry" in reg.vocabulary
    assert json.loads(MOCK_UC6.read_text(encoding="utf-8"))["sources"][:4] == _mock_raw()["sources"]  # no drift


def test_real_team_registry_loads():
    real = [p for p in (META_DB, REAL_REGISTRY) if p.exists()]
    if not real:
        pytest.skip("neither mediator/meta.db nor mediator/mappings.json exists yet (INTEGRATION_REPORT B1)")
    assert isinstance(load_registry(real[0]), Registry)


# --- design PDF §7.1: meta.db ---------------------------------------------------------

@pytest.fixture()
def meta(tmp_path) -> Registry:
    return load_registry(make_meta_db(tmp_path / "meta.db"))


def test_meta_db_sources_load_in_catalog_order(meta):
    assert [s.source_id for s in meta.sources] == ["REG", "CAM"]


def test_catalog_authority_spellings_are_folded(meta):
    assert (meta.by_id("REG").authority, meta.by_id("CAM").authority) == ("authoritative", "observational")


def test_catalog_column_names_are_translated(meta):
    reg = meta.by_id("REG")
    assert reg.trust == pytest.approx(0.95)
    assert reg.key_predicate.column == "registration_no"
    assert reg.table == "VEHICLE_REGISTRATION"
    assert reg.timeout_ms == 1500 and meta.by_id("CAM").timeout_ms == 3000
    assert meta.by_id("CAM").base_url == "http://192.168.43.14:8004"


def test_covers_stored_as_json_text_or_comma_text_both_parse(meta):
    assert meta.by_id("REG").covers == ["plate_number", "owner_name", "vehicle_make"]
    assert meta.by_id("CAM").covers == ["plate_number", "last_seen_location", "last_seen_time"]


def test_mapping_columns_are_translated(meta):
    make = meta.by_id("REG").attribute_map[2]
    assert (make.source_attr, make.transform, make.score) == ("make", "fix_make", pytest.approx(0.93))


def test_join_path_becomes_a_declared_join(meta):
    assert meta.by_id("REG").joins == [
        JoinSpec(table="OWNERS", left="VEHICLE_REGISTRATION.owner_id", right="OWNERS.owner_id")]
    assert [j.table for j in meta.by_id("CAM").joins] == ["CAMERAS"]


def test_latest_by_becomes_an_aggregate_that_is_not_pushed_down(meta):
    agg = meta.by_id("CAM").aggregate
    assert (agg.strategy, agg.global_attr, agg.pushdown) == ("latest", "last_seen_time", False)


def test_catalog_runtime_columns_are_kept_in_extra(meta):
    assert meta.by_id("REG").extra["last_health"] == "OK"


def test_global_schema_table_extends_the_vocabulary(tmp_path):
    reg = load_registry(make_meta_db(tmp_path / "meta.db", global_schema=True))
    assert "puc_expiry" in reg.vocabulary and reg.derived["insurance_status"] == ["insurance_expiry"]


def test_meta_db_without_the_catalog_tables_fails_loudly(tmp_path):
    path = tmp_path / "meta.db"
    with closing(sqlite3.connect(path)) as con:
        con.execute("CREATE TABLE REPORT_LOG (report_id INTEGER)")
    with pytest.raises(RegistryError, match="SOURCE_CATALOG"):
        load_registry(path)


def test_mapping_for_a_source_missing_from_the_catalog_fails_naming_it(tmp_path):
    path = make_meta_db(tmp_path / "meta.db")
    with closing(sqlite3.connect(path)) as con:
        con.execute("INSERT INTO MAPPING_REGISTRY (source_id, source_table, source_attr, global_attr) "
                    "VALUES ('GHOST', 'T', 'c', 'plate_number')")
        con.commit()
    with pytest.raises(RegistryError, match="GHOST"):
        load_registry(path)


def test_missing_meta_db_is_not_silently_created(tmp_path):
    with pytest.raises(RegistryError, match="not found"):
        load_registry(tmp_path / "meta.db")
    assert not (tmp_path / "meta.db").exists()


# --- JSON divergences absorbed (INTEGRATION_REPORT §3) -----------------------------------

@pytest.fixture(scope="module")
def teammate() -> Registry:
    return load_registry(TEAMMATE)


def test_dict_keyed_sources_take_their_id_from_the_key_uppercased(teammate):
    assert [s.source_id for s in teammate.sources] == ["REG", "CAM"]


def test_percentage_trust_and_scores_are_rescaled(teammate):
    reg = teammate.by_id("REG")
    assert reg.trust == pytest.approx(0.95)
    assert reg.attribute_map[1].score == pytest.approx(0.91)


def test_authority_spellings_are_folded(teammate):
    assert teammate.by_id("REG").authority == "authoritative"
    assert teammate.by_id("CAM").authority == "observational"


def test_url_alias_gets_scheme_and_loses_trailing_slash(teammate):
    assert teammate.by_id("REG").base_url == "http://127.0.0.1:8001"


def test_name_alias_becomes_display_name(teammate):
    assert teammate.by_id("REG").display_name == "RTO"


def test_bare_string_key_predicate_becomes_exact_match_on_that_column(teammate):
    kp = teammate.by_id("REG").key_predicate
    assert (kp.column, kp.match, kp.render.groups) == ("vehicle_reg_no", "exact", [])


def test_missing_fields_get_documented_defaults(teammate):
    reg = teammate.by_id("REG")
    assert reg.timeout_ms == 3000
    assert reg.query_template == DEFAULT_TEMPLATE
    assert reg.aggregate.strategy == "all"


def test_explicit_timeout_is_respected(teammate):
    assert teammate.by_id("CAM").timeout_ms == 900


def test_table_is_derived_from_the_key_columns_mapping(teammate):
    assert teammate.by_id("REG").table == "vehicle_registration"


def test_covers_is_derived_from_attribute_map_when_absent(teammate):
    assert teammate.by_id("REG").covers == ["plate_number", "owner_name"]


def test_column_aliases_are_accepted_and_unmatched_columns_dropped(teammate):
    attrs = [m.source_attr for m in teammate.by_id("REG").attribute_map]
    assert attrs == ["vehicle_reg_no", "owner_full_name"]  # reg_id had global_attr null


def test_unknown_per_source_keys_are_preserved_in_extra(teammate):
    assert teammate.by_id("REG").extra == {"matched_at": "2026-09-10T11:02:00"}


@pytest.mark.parametrize("shape", ["bare_list", "sources_list", "id_keyed_top_level"])
def test_all_three_container_shapes_are_accepted(shape):
    entries = _mock_raw()["sources"]
    raw = {
        "bare_list": entries,
        "sources_list": {"sources": entries},
        "id_keyed_top_level": {"global_schema": {"attributes": []}, **{e["source_id"]: e for e in entries}},
    }[shape]
    assert [s.source_id for s in normalize_registry(raw).sources] == ["REG", "INS", "THEFT", "CAM"]


def test_json_global_schema_extends_the_vocabulary():
    raw = _mock_raw() | {"global_schema": {"attributes": ["puc_expiry"], "derived": {"is_insured": ["insurance_expiry"]}}}
    reg = normalize_registry(raw)
    assert {"puc_expiry", "is_insured", "insurance_status"} <= set(reg.vocabulary)
    assert set(GLOBAL_ATTRIBUTES) <= set(reg.global_attributes)  # the agreed names can never be dropped


# --- failing loudly ------------------------------------------------------------

def test_unknown_global_attribute_fails_naming_the_source_and_attribute():
    raw = _mock_raw()
    raw["sources"][1]["attribute_map"][4]["global_attr"] = "policy_end"
    with pytest.raises(RegistryError, match=r"INS.*policy_end"):
        normalize_registry(raw)


def test_missing_key_predicate_fails_naming_the_source():
    raw = _mock_raw()
    del raw["sources"][2]["key_predicate"]
    with pytest.raises(RegistryError, match="THEFT"):
        normalize_registry(raw)


def test_file_that_is_not_json_fails_loudly(tmp_path):
    bad = tmp_path / "mappings.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(RegistryError, match="not valid JSON"):
        load_registry(bad)


def test_missing_file_fails_loudly(tmp_path):
    with pytest.raises(RegistryError, match="not found"):
        load_registry(tmp_path / "nope.json")


def test_empty_registry_fails_loudly():
    with pytest.raises(RegistryError):
        normalize_registry({"sources": []})


# --- which registry the mediator reads -----------------------------------------

def test_env_var_overrides_registry_path(monkeypatch, tmp_path):
    monkeypatch.setenv("IIA_REGISTRY", str(tmp_path / "x.json"))
    assert default_registry_path() == tmp_path / "x.json"


def test_falls_back_from_meta_db_to_mappings_json_to_the_mock(monkeypatch):
    monkeypatch.delenv("IIA_REGISTRY", raising=False)
    expected = next((p for p in (META_DB, REAL_REGISTRY) if p.exists()), MOCK)
    assert default_registry_path() == expected
