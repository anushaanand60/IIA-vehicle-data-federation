"""CONTEXT-ADDED (R2): test doubles for the phase-3 query / top-k / analytics tests (workstream C).

`FakeClient` has SourceClient's shape (query / post / stats) but runs the SQL on in-memory SQLite databases
built from the contract schemas (docs/phase3/CONTRACT.md). Every statement first goes through the real wrapper
guard (sources.wrapper_template.guard_sql), so a test also proves the generated SQL would be accepted by the
wrappers. No ports are bound.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from mediator.context_types import SourceStats
from sources.wrapper_template import QueryRejected, guard_sql

SCHEMAS: Dict[str, List[str]] = {
    "REG": [
        "CREATE TABLE OWNERS(owner_id INTEGER PRIMARY KEY, full_name TEXT, address_line TEXT, city TEXT)",
        "CREATE TABLE VEHICLE_REGISTRATION(registration_id INTEGER PRIMARY KEY, registration_no TEXT, owner_id INTEGER,"
        " make TEXT, model TEXT, colour TEXT, fuel_type TEXT, registered_on TEXT, reg_status TEXT, rto_code TEXT)",
        "CREATE TABLE VEHICLE_IDENTITY(registration_id INTEGER PRIMARY KEY, chassis_no TEXT, engine_no TEXT, mfg_year INTEGER)",
        "CREATE TABLE OWNERSHIP_HISTORY(transfer_id INTEGER PRIMARY KEY, registration_id INTEGER, prev_owner_id INTEGER,"
        " new_owner_id INTEGER, transfer_date TEXT, transfer_type TEXT)",
        "CREATE TABLE OWNER_CONTACTS(owner_id INTEGER PRIMARY KEY, mobile TEXT, pincode TEXT)",
    ],
    "INS": [
        "CREATE TABLE INSURERS(insurer_id INTEGER PRIMARY KEY, insurer_name TEXT)",
        "CREATE TABLE POLICY_RECORDS(policy_id INTEGER PRIMARY KEY, vehicle_reg TEXT, insurer_id INTEGER, policy_type TEXT,"
        " policy_start TEXT, policy_until TEXT, is_active INTEGER, premium_inr REAL)",
        "CREATE TABLE POLICY_HOLDERS(holder_id INTEGER PRIMARY KEY, holder_name TEXT, mobile TEXT, address TEXT, pincode TEXT)",
        "CREATE TABLE POLICY_PARTIES(policy_id INTEGER PRIMARY KEY, holder_id INTEGER, chassis_no TEXT)",
        "CREATE TABLE CLAIMS(claim_id INTEGER PRIMARY KEY, policy_id INTEGER, loss_date TEXT, intimation_date TEXT,"
        " loss_type TEXT, amount_claimed REAL, claim_status TEXT, surveyor_name TEXT)",
    ],
    "THEFT": [
        "CREATE TABLE CRIME_RECORDS(incident_id INTEGER PRIMARY KEY, vehicle_number TEXT, fir_no TEXT, reported_date INTEGER,"
        " incident_type TEXT, stolen_flag TEXT, recovered_flag TEXT, case_status TEXT, police_station TEXT)",
        "CREATE TABLE STATIONS(station_name TEXT PRIMARY KEY, district TEXT, zone TEXT)",
        "CREATE TABLE STOLEN_PROPERTY(incident_id INTEGER PRIMARY KEY, chassis_no TEXT, engine_no TEXT, recovered_on INTEGER)",
        "CREATE TABLE PERSONS(person_id INTEGER PRIMARY KEY, name TEXT, mobile TEXT, address TEXT)",
        "CREATE TABLE INCIDENT_PERSONS(incident_id INTEGER, person_id INTEGER, role TEXT, PRIMARY KEY(incident_id, person_id, role))",
    ],
    "CAM": [
        "CREATE TABLE CAMERAS(camera_id TEXT PRIMARY KEY, location_name TEXT, lat REAL, lon REAL)",
        "CREATE TABLE PLATE_CAPTURES(capture_id INTEGER PRIMARY KEY, plate_id TEXT, camera_id TEXT, captured_at TEXT,"
        " observed_make TEXT, observed_model TEXT, observed_colour TEXT, ocr_confidence REAL)",
        "CREATE TABLE ZONES(zone_id INTEGER PRIMARY KEY, zone_name TEXT, city TEXT)",
        "CREATE TABLE CAMERA_ZONES(camera_id TEXT PRIMARY KEY, zone_id INTEGER, road TEXT)",
        "CREATE TABLE CHALLANS(challan_id INTEGER PRIMARY KEY, capture_id INTEGER, violation TEXT, fine_inr INTEGER,"
        " challan_status TEXT, issued_at TEXT)",
    ],
    "PUC": [
        "CREATE TABLE POLLUTION_CERT(cert_no TEXT PRIMARY KEY, regn_number TEXT, valid_upto TEXT, tested_at TEXT, emission_norm TEXT)",
        "CREATE TABLE TEST_CENTRES(centre_id INTEGER PRIMARY KEY, centre_name TEXT, city TEXT, lat REAL, lon REAL)",
        "CREATE TABLE ANALYSERS(analyser_id INTEGER PRIMARY KEY, centre_id INTEGER, calibrated_on TEXT)",
        "CREATE TABLE CERT_TESTS(test_id INTEGER PRIMARY KEY, cert_no TEXT, analyser_id INTEGER, test_ts TEXT,"
        " co_pct REAL, hc_ppm INTEGER, result TEXT)",
    ],
}
REGISTRY = [  # the mapping-registry rows top-k reads, frozen so tests do not depend on meta.db
    ("REG", "VEHICLE_REGISTRATION", "make", "vehicle_make"), ("REG", "VEHICLE_REGISTRATION", "model", "vehicle_model"),
    ("REG", "VEHICLE_REGISTRATION", "colour", "vehicle_colour"), ("REG", "OWNERS", "full_name", "owner_name"),
    ("REG", "VEHICLE_REGISTRATION", "reg_status", "registration_status"), ("INS", "POLICY_RECORDS", "is_active", "is_active"),
    ("THEFT", "CRIME_RECORDS", "stolen_flag", "stolen_flag"), ("THEFT", "CRIME_RECORDS", "recovered_flag", "recovered_flag"),
    ("THEFT", "CRIME_RECORDS", "case_status", "case_status"), ("CAM", "CAMERAS", "location_name", "last_seen_location"),
    ("CAM", "PLATE_CAPTURES", "ocr_confidence", "ocr_confidence"),
]


class FakeClient:
    def __init__(self, data: Dict[str, Dict[str, List[Dict[str, Any]]]], down: Optional[set] = None) -> None:
        self.dbs: Dict[str, sqlite3.Connection] = {}
        self.down = down or set()
        self.calls: List[tuple] = []
        self._stats: Dict[str, SourceStats] = {}
        for sid, ddl in SCHEMAS.items():
            con = sqlite3.connect(":memory:")
            con.row_factory = sqlite3.Row
            for stmt in ddl:
                con.execute(stmt)
            for table, rows in data.get(sid, {}).items():
                for r in rows:
                    cols = ", ".join(r)
                    con.execute(f"INSERT INTO {table}({cols}) VALUES ({', '.join('?' * len(r))})", list(r.values()))
            self.dbs[sid] = con

    def stat(self, sid: str) -> SourceStats:
        return self._stats.setdefault(sid, SourceStats(source_id=sid))

    def stats(self) -> List[SourceStats]:
        return list(self._stats.values())

    def post(self, sid: str, path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if path == "/query":
            return {"rows": self.query(sid, payload["sql"])}
        return None

    def query(self, sid: str, sql: str) -> List[Dict[str, Any]]:
        self.calls.append((sid, sql))
        st = self.stat(sid)
        st.round_trips += 1
        if sid in self.down or sid not in self.dbs:
            st.status, st.error = "DOWN", "fake: source down"
            return []
        whitelist = [t for (t,) in self.dbs[sid].execute("SELECT name FROM sqlite_master WHERE type='table'")]
        try:
            guarded = guard_sql(sql, whitelist)
        except QueryRejected as exc:   # surfacing this as a test failure is the point: our SQL must pass the guard
            raise AssertionError(f"guard rejected SQL for {sid}: {exc}\n{sql}") from exc
        rows = [dict(r) for r in self.dbs[sid].execute(guarded).fetchall()]
        st.rows_shipped += len(rows)
        st.sql_statements += 1
        return rows


def use_frozen_registry(monkeypatch: Any) -> None:
    from mediator import topk_sql
    monkeypatch.setattr(topk_sql, "registry_rows", lambda: list(REGISTRY))
