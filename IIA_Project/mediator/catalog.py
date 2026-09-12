"""
Catalog & Mapping Registry for the Federated Mediator.
Stored in mediator/meta.db (SQLite).
"""

import sqlite3
import os
import json
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

META_DB_PATH = os.path.join(os.path.dirname(__file__), "meta.db")

DEFAULT_SOURCES = [
    {
        "source_id": "REG",
        "display_name": "Regional Transport Office (REG)",
        "dbms": "PostgreSQL",
        "base_url": "http://127.0.0.1:8001",
        "identifier_attr": "registration_no",
        "authority": "OFFICIAL",
        "trust_score": 0.95,
        "covers": [
            "plate_number", "owner_name", "vehicle_make", "vehicle_model",
            "vehicle_colour", "registration_date", "registration_status"
        ],
        "timeout_ms": 1500
    },
    {
        "source_id": "INS",
        "display_name": "Insurance Provider (INS)",
        "dbms": "MySQL",
        "base_url": "http://127.0.0.1:8002",
        "identifier_attr": "vehicle_reg",
        "authority": "OFFICIAL",
        "trust_score": 0.90,
        "covers": [
            "plate_number", "insurer_name", "policy_type",
            "insurance_start", "insurance_expiry", "insurance_status"
        ],
        "timeout_ms": 1500
    },
    {
        "source_id": "THEFT",
        "display_name": "Police Crime Records (THEFT)",
        "dbms": "SQLite",
        "base_url": "http://127.0.0.1:8003",
        "identifier_attr": "vehicle_number",
        "authority": "OFFICIAL",
        "trust_score": 0.90,
        "covers": [
            "plate_number", "stolen_status", "last_incident_date", "case_status"
        ],
        "timeout_ms": 1500
    },
    {
        "source_id": "CAM",
        "display_name": "Road Camera Network (CAM)",
        "dbms": "PostgreSQL",
        "base_url": "http://127.0.0.1:8004",
        "identifier_attr": "plate_id",
        "authority": "OBSERVATIONAL",
        "trust_score": 0.60,
        "covers": [
            "plate_number", "last_seen_location", "last_seen_time",
            "observed_make", "observed_model", "observed_colour"
        ],
        "timeout_ms": 1500
    }
]


def init_meta_db():
    conn = sqlite3.connect(META_DB_PATH)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS SOURCE_CATALOG (
        source_id TEXT PRIMARY KEY,
        display_name TEXT,
        dbms TEXT,
        base_url TEXT,
        identifier_attr TEXT,
        authority TEXT,
        trust_score REAL,
        covers TEXT, -- JSON array
        timeout_ms INTEGER,
        last_health TEXT,
        last_seen_at TEXT
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS MAPPING_REGISTRY (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id TEXT,
        source_table TEXT,
        source_attr TEXT,
        global_attr TEXT,
        transform_fn TEXT,
        aggregate TEXT,
        join_path TEXT,
        match_score REAL,
        validated_by TEXT,
        validated_at TEXT,
        UNIQUE(source_id, source_table, source_attr, global_attr)
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS REPORT_LOG (
        report_id INTEGER PRIMARY KEY AUTOINCREMENT,
        plate TEXT,
        decision TEXT,
        confidence TEXT,
        reasons TEXT, -- JSON array
        evidence_json TEXT, -- JSON object
        sources_used TEXT, -- JSON array
        generated_at TEXT
    );
    """)

    # Seed default sources if empty
    cur.execute("SELECT COUNT(*) FROM SOURCE_CATALOG;")
    if cur.fetchone()[0] == 0:
        for s in DEFAULT_SOURCES:
            cur.execute("""
            INSERT INTO SOURCE_CATALOG 
            (source_id, display_name, dbms, base_url, identifier_attr, authority, trust_score, covers, timeout_ms, last_health, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                s["source_id"], s["display_name"], s["dbms"], s["base_url"], s["identifier_attr"],
                s["authority"], s["trust_score"], json.dumps(s["covers"]), s["timeout_ms"], "OK", datetime.now(timezone.utc).isoformat()
            ))


    conn.commit()
    conn.close()

def get_source_catalog() -> Dict[str, Dict[str, Any]]:
    init_meta_db()
    conn = sqlite3.connect(META_DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM SOURCE_CATALOG;")
    rows = cur.fetchall()
    catalog = {}
    for r in rows:
        d = dict(r)
        d["covers"] = json.loads(d["covers"]) if d["covers"] else []
        catalog[d["source_id"]] = d
    conn.close()
    return catalog

def get_mappings_for_source(source_id: str) -> List[Dict[str, Any]]:
    init_meta_db()
    conn = sqlite3.connect(META_DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM MAPPING_REGISTRY WHERE source_id = ?;", (source_id,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def get_all_mappings() -> List[Dict[str, Any]]:
    init_meta_db()
    conn = sqlite3.connect(META_DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM MAPPING_REGISTRY;")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows

def save_mapping(source_id: str, source_table: str, source_attr: str, global_attr: str,
                 transform_fn: str = "none", aggregate: Optional[str] = None,
                 join_path: Optional[str] = None, match_score: float = 1.0, validated_by: str = "human_expert") -> None:
    init_meta_db()
    conn = sqlite3.connect(META_DB_PATH)
    cur = conn.cursor()
    now_ts = datetime.now(timezone.utc).isoformat()
    cur.execute("""
    INSERT INTO MAPPING_REGISTRY 
    (source_id, source_table, source_attr, global_attr, transform_fn, aggregate, join_path, match_score, validated_by, validated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(source_id, source_table, source_attr, global_attr) DO UPDATE SET
        transform_fn = excluded.transform_fn,
        aggregate = excluded.aggregate,
        join_path = excluded.join_path,
        match_score = excluded.match_score,
        validated_by = excluded.validated_by,
        validated_at = excluded.validated_at;
    """, (source_id, source_table, source_attr, global_attr, transform_fn, aggregate, join_path, match_score, validated_by, now_ts))
    conn.commit()
    conn.close()

def register_source(source_id: str, display_name: str, dbms: str, base_url: str,
                    identifier_attr: str, authority: str, trust_score: float, covers: List[str], timeout_ms: int = 1500) -> None:
    init_meta_db()
    conn = sqlite3.connect(META_DB_PATH)
    cur = conn.cursor()
    now_ts = datetime.now(timezone.utc).isoformat()
    cur.execute("""
    INSERT INTO SOURCE_CATALOG 
    (source_id, display_name, dbms, base_url, identifier_attr, authority, trust_score, covers, timeout_ms, last_health, last_seen_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(source_id) DO UPDATE SET
        display_name = excluded.display_name,
        dbms = excluded.dbms,
        base_url = excluded.base_url,
        identifier_attr = excluded.identifier_attr,
        authority = excluded.authority,
        trust_score = excluded.trust_score,
        covers = excluded.covers,
        timeout_ms = excluded.timeout_ms,
        last_seen_at = excluded.last_seen_at;
    """, (source_id, display_name, dbms, base_url, identifier_attr, authority, trust_score, json.dumps(covers), timeout_ms, "OK", now_ts))
    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_meta_db()
    cat = get_source_catalog()
    print(f"Catalog initialized with {len(cat)} sources: {list(cat.keys())}")
    maps = get_all_mappings()
    print(f"Mappings initialized with {len(maps)} rules.")
