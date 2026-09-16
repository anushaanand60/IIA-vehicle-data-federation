"""mediator/planner.py takes sources, derived attributes and the identity check from metadata.

The design's first innovation claim is "adding a source means register + map, zero engine code",
so the planner must not name a single source id in its own body.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def catalog(tmp_path, monkeypatch):
    """A private meta.db seeded with the default catalog, so each test may change it freely."""
    import mediator.catalog as catalog_module
    monkeypatch.setattr(catalog_module, "META_DB_PATH", str(tmp_path / "meta.db"))
    catalog_module.init_meta_db()
    return catalog_module


def planned(attrs) -> set:
    from mediator.planner import plan_query
    return set(plan_query("DL-01-AB-1234", attrs)["sources"])


def test_planner_code_names_no_source():
    code = (ROOT / "mediator" / "planner.py").read_text(encoding="utf-8")
    assert re.findall(r"[\"'](REG|INS|THEFT|CAM|PUC)[\"']", code) == []


def test_uc1_asks_the_insurer_and_the_identity_authority(catalog):
    assert planned(["insurance_status", "insurance_expiry"]) == {"INS", "REG"}


def test_uc3_theft_and_sighting_question_asks_only_theft_and_camera(catalog):
    assert planned(["stolen_status", "last_seen_location"]) == {"THEFT", "CAM"}


def test_identity_check_comes_from_the_catalog_not_the_code(catalog):
    catalog.set_identity_authority("REG", False)
    assert planned(["insurance_status"]) == {"INS"}


def test_derived_attribute_follows_its_inputs_even_if_no_source_lists_it(catalog):
    ins = catalog.get_source_catalog()["INS"]
    covers = [a for a in ins["covers"] if a != "insurance_status"]
    catalog.register_source("INS", ins["display_name"], ins["dbms"], ins["base_url"], ins["identifier_attr"],
                            ins["authority"], ins["trust_score"], covers, ins["timeout_ms"])
    assert "INS" in planned(["insurance_status"])


def test_a_newly_registered_source_is_planned_with_no_code_change(catalog):
    catalog.register_source("PUC", "Pollution-Certificate Authority", "SQLite", "http://127.0.0.1:8005",
                            "regn_number", "OFFICIAL", 0.85, ["plate_number", "puc_expiry"])
    assert planned(["puc_expiry"]) == {"PUC"}
    assert "PUC" in planned(None)


def test_identity_authority_survives_an_older_meta_db(tmp_path, monkeypatch):
    """A meta.db created before the column existed is migrated, not rebuilt."""
    import sqlite3

    import mediator.catalog as catalog_module
    db = tmp_path / "old.db"
    monkeypatch.setattr(catalog_module, "META_DB_PATH", str(db))
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE SOURCE_CATALOG (source_id TEXT PRIMARY KEY, display_name TEXT, dbms TEXT,
                   base_url TEXT, identifier_attr TEXT, authority TEXT, trust_score REAL, covers TEXT,
                   timeout_ms INTEGER, last_health TEXT, last_seen_at TEXT);""")
    con.execute("INSERT INTO SOURCE_CATALOG VALUES ('REG','RTO','PostgreSQL','http://x','registration_no',"
                "'OFFICIAL',0.95,'[\"plate_number\"]',1500,'OK','now');")
    con.commit()
    con.close()

    cat = catalog_module.get_source_catalog()
    assert cat["REG"]["identity_authority"] == 1
