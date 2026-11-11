"""CONTEXT-ADDED (R1): the phase-3 schemas, generated data and planted scenarios hold together.

Runs against the local SQLite files that scripts/load_source.py builds (skipped if they are not built),
and checks: every source has >= 4 tables; every FK edge in data/context_graph.json resolves (no orphan
child rows); each planted scenario's evidence rows exist; the committed CSVs match the generator.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
GRAPH = json.loads((ROOT / "data" / "context_graph.json").read_text(encoding="utf-8"))
SCENARIOS = json.loads((ROOT / "data" / "phase3_scenarios.json").read_text(encoding="utf-8"))
SOURCES = ("REG", "INS", "THEFT", "CAM", "PUC")
STORY = {"DL01AB1234", "DL05CD9876", "DL09KL3321", "HR26EF4455", "UP16GH1122", "MH12IJ7788", "KA05MN9999"}


def connect(source_id: str) -> sqlite3.Connection:
    path = ROOT / "sources" / source_id.lower() / f"{source_id.lower()}.db"
    if not path.exists():
        pytest.skip(f"{path.name} not built: python scripts/load_source.py {source_id}")
    return sqlite3.connect(path)


def scalar(con: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    return con.execute(sql, params).fetchone()[0]


@pytest.mark.parametrize("source_id", SOURCES)
def test_each_source_has_four_or_five_populated_tables(source_id: str) -> None:
    con = connect(source_id)
    tables = [t for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
    assert 4 <= len(tables) <= 5, tables
    assert set(GRAPH["sources"][source_id]["tables"]) <= set(tables)
    assert all(scalar(con, f"SELECT COUNT(*) FROM {t}") > 0 for t in tables), "an empty table: rerun the generator + loader"


@pytest.mark.parametrize("source_id", SOURCES)
def test_every_context_graph_edge_resolves(source_id: str) -> None:
    con = connect(source_id)
    for e in GRAPH["sources"][source_id]["edges"]:
        orphans = scalar(con, f"SELECT COUNT(*) FROM {e['from']} c WHERE c.{e['col']} IS NOT NULL AND NOT EXISTS "
                              f"(SELECT 1 FROM {e['to']} p WHERE p.{e['to_col']} = c.{e['col']})")
        assert orphans == 0, f"{orphans} orphan row(s) on {e['from']}.{e['col']} -> {e['to']}.{e['to_col']}"


@pytest.mark.parametrize("parent, child, col", [
    ("REG.VEHICLE_REGISTRATION", "VEHICLE_IDENTITY", "registration_id"),
    ("REG.OWNERS", "OWNER_CONTACTS", "owner_id"),
    ("INS.POLICY_RECORDS", "POLICY_PARTIES", "policy_id"),
    ("THEFT.CRIME_RECORDS", "STOLEN_PROPERTY", "incident_id"),
    ("CAM.CAMERAS", "CAMERA_ZONES", "camera_id"),
])
def test_one_to_one_companions_cover_every_parent(parent: str, child: str, col: str) -> None:
    source_id, table = parent.split(".")
    con = connect(source_id)
    assert scalar(con, f"SELECT COUNT(*) FROM {table} p WHERE NOT EXISTS (SELECT 1 FROM {child} c WHERE c.{col} = p.{col})") == 0


def _pk_where(source_id: str, table: str, pk: object) -> tuple[str, tuple]:
    cols = GRAPH["sources"][source_id]["tables"][table]["pk"]
    cols, values = (cols, pk) if isinstance(cols, list) else ([cols], [pk])
    return " AND ".join(f"{c} = ?" for c in cols), tuple(values)


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s["id"] for s in SCENARIOS])
def test_planted_scenario_evidence_rows_exist(scenario: dict) -> None:
    assert scenario["kind"] in {"ALIBI", "QUICK_CLAIM", "TRANSFER_BEFORE_LOSS", "PERSON_RING", "CLONE", "HARD_NEGATIVE"}
    assert scenario["evidence"] and scenario["explanation"]
    for source_id, table, pk in scenario["evidence"] + scenario["persons"]:
        where, params = _pk_where(source_id, table, pk)
        assert scalar(connect(source_id), f"SELECT COUNT(*) FROM {table} WHERE {where}", params) == 1, (source_id, table, pk)


def test_each_scenario_kind_is_planted_and_story_vehicles_are_left_alone() -> None:
    kinds = {s["kind"] for s in SCENARIOS}
    assert kinds == {"ALIBI", "QUICK_CLAIM", "TRANSFER_BEFORE_LOSS", "PERSON_RING", "CLONE", "HARD_NEGATIVE"}
    touched = {p for s in SCENARIOS if s["kind"] != "CLONE" for p in s["plates"]}
    assert not touched & STORY  # UP16GH1122 is only *referenced* as the existing clone story
    ring = next(s for s in SCENARIOS if s["kind"] == "PERSON_RING")
    assert len(set(ring["plates"])) >= 3


def test_crime_stations_and_formats_follow_each_agency() -> None:
    reg, ins, theft = connect("REG"), connect("INS"), connect("THEFT")
    assert all(len(c) == 17 for (c,) in reg.execute("SELECT chassis_no FROM VEHICLE_IDENTITY"))
    assert all(re.fullmatch(r"[6-9]\d{9}", m) for (m,) in reg.execute("SELECT mobile FROM OWNER_CONTACTS"))
    assert all(re.fullmatch(r"\d\d/\d\d/\d{4}", a) and re.fullmatch(r"\d\d/\d\d/\d{4}", b)
               for a, b in ins.execute("SELECT loss_date, intimation_date FROM CLAIMS"))
    assert all(isinstance(r, int) for (r,) in theft.execute("SELECT recovered_on FROM STOLEN_PROPERTY WHERE recovered_on IS NOT NULL"))


def test_committed_csvs_match_a_fresh_generator_run() -> None:
    from scripts.phase3_common import NEW_TABLES, csv_name, read_csv
    from scripts.phase3_generate import generate
    world = generate()
    assert json.loads(json.dumps(world.scenarios)) == SCENARIOS
    for source_id, tables in NEW_TABLES.items():
        for table, _ in tables:
            assert len(read_csv(csv_name(source_id, table))) == len(world.t[table]), table
