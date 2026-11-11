"""CONTEXT-ADDED (R2): tiny contract-shaped source databases + an in-process client for the context tests.

Five sources (REG, INS, THEFT, CAM, PUC) with the phase-3 table shapes from docs/phase3/CONTRACT.md, filled by a
seeded generator: shared owners (fan-out), chassis bridges, a noisy camera read, a person ring sharing one
mobile across three agencies, and a same-name-different-person hard negative. No ports are bound: wrappers
run through FastAPI's TestClient.
"""
from __future__ import annotations

import json
import random
from typing import Any, Callable, Dict, List, Optional

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from mediator.context_types import SourceStats
from sources.wrapper_template import create_app

DDL: Dict[str, List[str]] = {
    "REG": ["CREATE TABLE OWNERS (owner_id INTEGER PRIMARY KEY, full_name VARCHAR(80), address_line VARCHAR(160), city VARCHAR(40))",
            "CREATE TABLE VEHICLE_REGISTRATION (registration_id INTEGER PRIMARY KEY, registration_no VARCHAR(16), owner_id INTEGER, make VARCHAR(30), model VARCHAR(30), colour VARCHAR(20), fuel_type VARCHAR(10), registered_on DATE, reg_status VARCHAR(12), rto_code VARCHAR(6))",
            "CREATE TABLE VEHICLE_IDENTITY (registration_id INTEGER PRIMARY KEY, chassis_no VARCHAR(17), engine_no VARCHAR(14), mfg_year INTEGER)",
            "CREATE TABLE OWNERSHIP_HISTORY (transfer_id INTEGER PRIMARY KEY, registration_id INTEGER, prev_owner_id INTEGER, new_owner_id INTEGER, transfer_date DATE, transfer_type VARCHAR(12))",
            "CREATE TABLE OWNER_CONTACTS (owner_id INTEGER PRIMARY KEY, mobile VARCHAR(10), pincode CHAR(6))"],
    "INS": ["CREATE TABLE INSURERS (insurer_id INTEGER PRIMARY KEY, insurer_name VARCHAR(60))",
            "CREATE TABLE POLICY_RECORDS (policy_id INTEGER PRIMARY KEY, vehicle_reg VARCHAR(16), insurer_id INTEGER, policy_type VARCHAR(20), policy_start VARCHAR(10), policy_until VARCHAR(10), is_active INTEGER, premium_inr DECIMAL(10,2))",
            "CREATE TABLE POLICY_HOLDERS (holder_id INTEGER PRIMARY KEY, holder_name VARCHAR(80), mobile VARCHAR(10), address VARCHAR(160), pincode CHAR(6))",
            "CREATE TABLE POLICY_PARTIES (policy_id INTEGER PRIMARY KEY, holder_id INTEGER, chassis_no VARCHAR(17))",
            "CREATE TABLE CLAIMS (claim_id INTEGER PRIMARY KEY, policy_id INTEGER, loss_date VARCHAR(10), intimation_date VARCHAR(10), loss_type VARCHAR(12), amount_claimed DECIMAL(10,2), claim_status VARCHAR(16), surveyor_name VARCHAR(60))"],
    "THEFT": ["CREATE TABLE CRIME_RECORDS (incident_id INTEGER PRIMARY KEY, vehicle_number VARCHAR(20), fir_no VARCHAR(20), reported_date INTEGER, incident_type VARCHAR(12), stolen_flag CHAR(1), recovered_flag CHAR(1), case_status VARCHAR(10), police_station VARCHAR(60))",
              "CREATE TABLE STATIONS (station_name VARCHAR(60) PRIMARY KEY, district VARCHAR(40), zone VARCHAR(20))",
              "CREATE TABLE STOLEN_PROPERTY (incident_id INTEGER PRIMARY KEY, chassis_no VARCHAR(17), engine_no VARCHAR(14), recovered_on INTEGER)",
              "CREATE TABLE PERSONS (person_id INTEGER PRIMARY KEY, name VARCHAR(80), mobile VARCHAR(10), address VARCHAR(160))",
              "CREATE TABLE INCIDENT_PERSONS (incident_id INTEGER, person_id INTEGER, role VARCHAR(12), PRIMARY KEY (incident_id, person_id, role))"],
    "CAM": ["CREATE TABLE CAMERAS (camera_id VARCHAR(8) PRIMARY KEY, location_name VARCHAR(60), lat DECIMAL(9,6), lon DECIMAL(9,6))",
            "CREATE TABLE PLATE_CAPTURES (capture_id INTEGER PRIMARY KEY, plate_id VARCHAR(16), camera_id VARCHAR(8), captured_at VARCHAR(19), observed_make VARCHAR(30), observed_model VARCHAR(30), observed_colour VARCHAR(20), ocr_confidence DECIMAL(4,3))",
            "CREATE TABLE ZONES (zone_id INTEGER PRIMARY KEY, zone_name VARCHAR(40), city VARCHAR(40))",
            "CREATE TABLE CAMERA_ZONES (camera_id VARCHAR(8) PRIMARY KEY, zone_id INTEGER, road VARCHAR(60))",
            "CREATE TABLE CHALLANS (challan_id INTEGER PRIMARY KEY, capture_id INTEGER, violation VARCHAR(40), fine_inr INTEGER, challan_status VARCHAR(10), issued_at VARCHAR(19))"],
    "PUC": ["CREATE TABLE POLLUTION_CERT (cert_no VARCHAR(30) PRIMARY KEY, regn_number VARCHAR(16), valid_upto DATE, tested_at VARCHAR(60), emission_norm VARCHAR(10))",
            "CREATE TABLE TEST_CENTRES (centre_id INTEGER PRIMARY KEY, centre_name VARCHAR(60), city VARCHAR(40), lat DECIMAL(9,6), lon DECIMAL(9,6))",
            "CREATE TABLE ANALYSERS (analyser_id INTEGER PRIMARY KEY, centre_id INTEGER, calibrated_on DATE)",
            "CREATE TABLE CERT_TESTS (test_id INTEGER PRIMARY KEY, cert_no VARCHAR(30), analyser_id INTEGER, test_ts VARCHAR(19), co_pct DECIMAL(5,2), hc_ppm INTEGER, result VARCHAR(4))"],
}
TABLES = {sid: [stmt.split()[2] for stmt in ddl] for sid, ddl in DDL.items()}
STORY = "DL05CD9876"
MAKES = [("Hyundai", "Creta"), ("Maruti", "Swift"), ("Tata", "Nexon"), ("Honda", "City")]


def _plate(i: int) -> str:
    return STORY if i == 0 else f"{random.choice(['DL', 'HR', 'UP'])}{i % 9 + 1:02d}{chr(65 + i % 26)}{chr(66 + i % 20)}{1000 + i * 37 % 9000}"


def generate(seed: int = 7, vehicles: int = 14) -> Dict[str, Dict[str, List[Dict[str, Any]]]]:
    """Rows per source/table. Deterministic for a seed."""
    random.seed(seed)
    plates = [_plate(i) for i in range(vehicles)]
    owners = max(3, vehicles // 3)
    names = ["Ravi Kumar", "Sunita Sharma", "Amit Verma", "Neha Gupta", "Arjun Singh", "Pooja Rao", "Vikram Das"]
    d: Dict[str, Dict[str, List[Dict[str, Any]]]] = {s: {t: [] for t in ts} for s, ts in TABLES.items()}
    reg, ins, th, cam, puc = d["REG"], d["INS"], d["THEFT"], d["CAM"], d["PUC"]
    for o in range(1, owners + 1):
        reg["OWNERS"].append({"owner_id": o, "full_name": names[(o - 1) % len(names)], "address_line": f"{o} Ring Road", "city": "Delhi"})
        reg["OWNER_CONTACTS"].append({"owner_id": o, "mobile": f"98{o:08d}" if o > 1 else "9811111111", "pincode": f"1100{o % 9:02d}"})
    reg["OWNERS"].append({"owner_id": owners + 1, "full_name": "Ravi Kumar", "address_line": "77 Lake View", "city": "Pune"})
    reg["OWNER_CONTACTS"].append({"owner_id": owners + 1, "mobile": "9700000077", "pincode": "411001"})  # hard negative
    ins["INSURERS"] += [{"insurer_id": 1, "insurer_name": "Acme General"}, {"insurer_id": 2, "insurer_name": "Bharat Assure"}]
    th["STATIONS"] += [{"station_name": f"PS {z}", "district": z, "zone": "North"} for z in ("Rohini", "Saket", "Dwarka")]
    cam["ZONES"] += [{"zone_id": 1, "zone_name": "Ring", "city": "Delhi"}, {"zone_id": 2, "zone_name": "NH48", "city": "Gurgaon"}]
    for c in range(1, 5):
        cam["CAMERAS"].append({"camera_id": f"C{c}", "location_name": f"Junction {c}", "lat": 28.5 + c / 100, "lon": 77.1})
        cam["CAMERA_ZONES"].append({"camera_id": f"C{c}", "zone_id": 1 + c % 2, "road": f"Road {c}"})
    puc["TEST_CENTRES"].append({"centre_id": 1, "centre_name": "Green PUC", "city": "Delhi", "lat": 28.6, "lon": 77.2})
    puc["ANALYSERS"].append({"analyser_id": 1, "centre_id": 1, "calibrated_on": "2026-01-10"})
    cap = ch = test = claim = inc = 0
    for i, p in enumerate(plates):
        v, make = i + 1, MAKES[i % len(MAKES)]
        chassis = f"MA3CH{v:012d}"
        reg["VEHICLE_REGISTRATION"].append({"registration_id": v, "registration_no": p, "owner_id": 1 + i % owners, "make": make[0], "model": make[1],
                                            "colour": random.choice(["White", "Silver", "Red"]), "fuel_type": "PETROL",
                                            "registered_on": f"202{i % 5}-0{1 + i % 9}-1{i % 9}", "reg_status": "ACTIVE" if i % 5 else "SUSPENDED", "rto_code": p[:4]})
        reg["VEHICLE_IDENTITY"].append({"registration_id": v, "chassis_no": chassis, "engine_no": f"EN{v:08d}", "mfg_year": 2018 + i % 6})
        if i % 3 == 0:
            reg["OWNERSHIP_HISTORY"].append({"transfer_id": v, "registration_id": v, "prev_owner_id": 1 + (i + 1) % owners, "new_owner_id": 1 + i % owners,
                                             "transfer_date": f"2026-0{1 + i % 8}-05", "transfer_type": random.choice(["SALE", "INHERITANCE"])})
        if i % 4 != 2:
            dash = f"{p[:2]}-{p[2:4]}-{p[4:-4]}-{p[-4:]}"
            ins["POLICY_RECORDS"].append({"policy_id": v, "vehicle_reg": dash, "insurer_id": 1 + i % 2, "policy_type": "COMPREHENSIVE",
                                          "policy_start": f"0{1 + i % 9}/01/2026", "policy_until": f"12/07/202{6 + (i % 2)}", "is_active": int(i % 3 != 0), "premium_inr": 9000 + i})
            ins["POLICY_HOLDERS"].append({"holder_id": v, "holder_name": "Ravi Kumaar" if i == 0 else names[(i + 2) % len(names)],
                                          "mobile": "9811111111" if i == 0 else f"97{v:08d}",
                                          "address": "1 Ring Road Delhi" if i == 0 else f"{100 + v} MG Road Delhi", "pincode": "110001" if i == 0 else f"1100{v % 50:02d}"})
            ins["POLICY_PARTIES"].append({"policy_id": v, "holder_id": v, "chassis_no": chassis})
            if i % 3 == 0:
                claim += 1
                ins["CLAIMS"].append({"claim_id": claim, "policy_id": v, "loss_date": f"1{i % 9}/03/2026", "intimation_date": f"2{i % 9}/03/2026",
                                      "loss_type": "THEFT" if i % 2 == 0 else "OWN_DAMAGE", "amount_claimed": 50000 + i, "claim_status": "INVESTIGATING" if i == 0 else "SETTLED", "surveyor_name": "S. Rao"})
        if i % 4 == 0:
            inc += 1
            th["CRIME_RECORDS"].append({"incident_id": inc, "vehicle_number": f"{p[:2]} {p[2:4]} {p[4:-4]} {p[-4:]}".lower(), "fir_no": f"FIR{inc:04d}",
                                        "reported_date": 1772000000 + i * 86400 * 9, "incident_type": "THEFT", "stolen_flag": "Y" if i % 8 == 0 else "N",
                                        "recovered_flag": "N", "case_status": "OPEN" if i % 8 == 0 else "CLOSED", "police_station": f"PS {['Rohini', 'Saket', 'Dwarka'][i % 3]}"})
            th["STOLEN_PROPERTY"].append({"incident_id": inc, "chassis_no": chassis, "engine_no": f"EN{v:08d}", "recovered_on": None})
            th["PERSONS"].append({"person_id": inc, "name": "Ravi Kumar" if i == 0 else names[(i + 4) % len(names)],
                                  "mobile": "9811111111" if i == 0 else f"96{v:08d}", "address": "1 Ring Road Delhi" if i == 0 else f"{200 + v} Park Street"})
            th["INCIDENT_PERSONS"].append({"incident_id": inc, "person_id": inc, "role": "ACCUSED" if i == 0 else "COMPLAINANT"})
        for k in range(3 if i == 0 else 1 + i % 3):
            cap += 1
            read = "DLO5CD9876" if (i == 0 and k == 1) else p
            cam["PLATE_CAPTURES"].append({"capture_id": cap, "plate_id": read, "camera_id": f"C{1 + (i + k) % 4}", "captured_at": f"2026-03-{10 + k:02d} 0{k}:15:00",
                                          "observed_make": make[0], "observed_model": make[1], "observed_colour": "White", "ocr_confidence": 0.9})
            if (i + k) % 3 == 0:
                ch += 1
                cam["CHALLANS"].append({"challan_id": ch, "capture_id": cap, "violation": "SPEEDING", "fine_inr": 2000,
                                        "challan_status": "PENDING" if k == 0 else "PAID", "issued_at": f"2026-03-{10 + k:02d} 09:00:00"})
        cert = f"PUC{v:05d}"
        puc["POLLUTION_CERT"].append({"cert_no": cert, "regn_number": p, "valid_upto": f"2026-1{i % 3}-01", "tested_at": "Green PUC", "emission_norm": "BS-VI"})
        test += 1
        puc["CERT_TESTS"].append({"test_id": test, "cert_no": cert, "analyser_id": 1, "test_ts": f"2026-03-1{i % 9} 10:00:00", "co_pct": 0.2, "hc_ppm": 90, "result": "PASS" if i % 6 else "FAIL"})
    return d


def load(url: str, sid: str, rows: Dict[str, List[Dict[str, Any]]]) -> None:
    eng = create_engine(url)
    with eng.begin() as conn:
        for t in reversed(TABLES[sid]):
            conn.execute(text(f"DROP TABLE IF EXISTS {t}"))
        for stmt in DDL[sid]:
            conn.execute(text(stmt))
        for t, rs in rows.items():
            for r in rs:
                conn.execute(text(f"INSERT INTO {t} ({', '.join(r)}) VALUES ({', '.join(':' + k for k in r)})"), r)
    eng.dispose()


def build(tmp_path: Any, seed: int = 7, vehicles: int = 14) -> Dict[str, str]:
    data, urls = generate(seed, vehicles), {}
    for sid in DDL:
        urls[sid] = f"sqlite:///{(tmp_path / f'{sid.lower()}_{seed}.db').as_posix()}"
        load(urls[sid], sid, data[sid])
    return urls


def write_graph(tmp_path: Any, graph: Dict[str, Any]) -> str:
    p = tmp_path / "context_graph.json"
    p.write_text(json.dumps(graph), encoding="utf-8")
    return str(p)


class InProcessClient:
    """SourceClient look-alike over TestClient: same counting (rows, bytes, round trips), no sockets."""

    def __init__(self, urls: Dict[str, str], down: Optional[set] = None) -> None:
        self.apps = {sid: TestClient(create_app(u, source_id=sid, dbms="sqlite", tables=TABLES.get(sid, [])))
                     for sid, u in urls.items()}
        self.down, self._stats = set(down or ()), {}
        self.log: List[Dict[str, Any]] = []

    def stats(self) -> List[SourceStats]:
        return list(self._stats.values())

    def post(self, sid: str, path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        st = self._stats.setdefault(sid, SourceStats(source_id=sid))
        st.round_trips += 1
        self.log.append({"sid": sid, "path": path, "payload": payload})
        if sid in self.down or sid not in self.apps:
            st.status, st.error = "DOWN", "connection refused"
            return None
        resp = self.apps[sid].post(path, json=payload)
        st.bytes_shipped += len(resp.content)
        if resp.status_code != 200:
            st.status, st.error = "ERROR", resp.text[:300]
            return None
        body = resp.json()
        st.rows_shipped += len(body.get("rows", []) or [])
        st.sql_statements += 1
        return body

    def query(self, sid: str, sql: str) -> List[Dict[str, Any]]:
        body = self.post(sid, "/query", {"sql": sql})
        return list(body.get("rows", [])) if body else []


def make_client(urls: Dict[str, str], **kw: Any) -> Callable[[], InProcessClient]:
    return lambda: InProcessClient(urls, **kw)
