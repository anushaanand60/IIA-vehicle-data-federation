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
        "timeout_ms": 1500,
        # Asked on any question whose attributes are flagged needs_identity_check, so "is this
        # vehicle insured?" still confirms the plate exists at the registration authority (UC1).
        "identity_authority": 1
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
            "observed_make", "observed_model", "observed_colour",
            # Where the camera stands: Challan Guard's impossible-travel check needs it, and
            # declaring it here keeps that check a registry lookup rather than a special case.
            "camera_lat", "camera_lon"
        ],
        "timeout_ms": 1500
    }
]


def _seed_identity_authorities(cur) -> None:
    for s in DEFAULT_SOURCES:
        if s.get("identity_authority"):
            cur.execute("UPDATE SOURCE_CATALOG SET identity_authority = 1 WHERE source_id = ?;", (s["source_id"],))


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
        last_seen_at TEXT,
        identity_authority INTEGER DEFAULT 0
    );
    """)
    # Migration, not a rebuild: a meta.db written before this column existed (every laptop already
    # has one) gains it in place, keeping its base_url edits and health history.
    catalog_columns = [row[1] for row in cur.execute("PRAGMA table_info(SOURCE_CATALOG);").fetchall()]
    if "identity_authority" not in catalog_columns:
        cur.execute("ALTER TABLE SOURCE_CATALOG ADD COLUMN identity_authority INTEGER DEFAULT 0;")
        _seed_identity_authorities(cur)

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
    # Task 2.2 evidence-bundle columns, added in place: every laptop already has a meta.db
    # with rows filed under the old schema, and this migration must not lose them.
    report_log_columns = [row[1] for row in cur.execute("PRAGMA table_info(REPORT_LOG);").fetchall()]
    for extra_col in ("plan_trace_json", "risk_json", "rule_fired", "mediator_commit"):
        if extra_col not in report_log_columns:
            cur.execute(f"ALTER TABLE REPORT_LOG ADD COLUMN {extra_col} TEXT;")

    # Task 2.3. Both tables are mediator-side *policy and audit*, never source data: the watchlist
    # says which plates an operator cares about, the alert log says when the mediator noticed one.
    # Caching a source row here would contradict the freshness thesis; a marker and a timestamp
    # do not.
    cur.execute("""
    CREATE TABLE IF NOT EXISTS WATCHLIST (
        plate TEXT PRIMARY KEY,
        reason TEXT,
        added_by TEXT,
        added_at TEXT
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS ALERT_LOG (
        alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
        plate TEXT,
        reason TEXT,
        seen_at TEXT,      -- the camera timestamp, when a sighting source answered
        location TEXT,     -- the camera location, when a sighting source answered
        decision TEXT,
        ts TEXT            -- when the mediator raised the alert
    );
    """)

    # Task 2.6. The audit trail of every federated query: what was asked, which sources answered
    # and how, and what the mediator decided. Deliberately narrow -- decisions and traces only,
    # never the raw rows a source returned, so this table can never become the warehouse the
    # federation thesis argues against (CLAUDE.md §8 "no caching of source data").
    cur.execute("""
    CREATE TABLE IF NOT EXISTS QUERY_LOG (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT,
        plate TEXT,
        requested_attrs TEXT, -- JSON array
        sources_asked TEXT,   -- JSON array
        statuses TEXT,        -- JSON object: source_id -> status
        decision TEXT,
        confidence TEXT,
        elapsed_ms REAL
    );
    """)

    # Challan Guard (verify-before-fine). A case is the mediator's *own* record of an ANPR
    # allegation and what the federation said about it -- never a copy of a source row. The
    # evidence bundle is a frozen snapshot kept for the dispute audit trail, which is why storing
    # it here does not contradict the "no caching of source data" rule: it is the proof of what a
    # source said at issue time, not a substitute for asking the source again (a dispute always
    # re-queries live).
    cur.execute("""
    CREATE TABLE IF NOT EXISTS CHALLAN_CASES (
        case_id INTEGER PRIMARY KEY AUTOINCREMENT,
        plate_read TEXT NOT NULL,
        plate_resolved TEXT,
        camera_id TEXT,
        location TEXT,
        lat REAL,
        lon REAL,
        captured_at TEXT NOT NULL,
        observed_make TEXT,
        observed_colour TEXT,
        ocr_confidence REAL,
        status TEXT NOT NULL,          -- CANDIDATE | HOLD | ISSUED | REJECTED | DISPUTED | UPHELD | CANCELLED
        verdict TEXT,
        reason TEXT,
        amount_inr INTEGER,
        evidence_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS CHALLAN_EVENTS (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id INTEGER NOT NULL,
        event TEXT NOT NULL,
        actor TEXT NOT NULL,
        at TEXT NOT NULL,
        evidence_json TEXT
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
        _seed_identity_authorities(cur)


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

def set_identity_authority(source_id: str, is_authority: bool = True) -> None:
    """Flag the source the planner asks to confirm a plate exists (see planner, needs_identity_check).

    Kept in the catalog rather than in the planner so the policy is data a demonstrator can change,
    not a branch someone has to edit.
    """
    init_meta_db()
    conn = sqlite3.connect(META_DB_PATH)
    conn.execute("UPDATE SOURCE_CATALOG SET identity_authority = ? WHERE source_id = ?;", (int(is_authority), source_id))
    conn.commit()
    conn.close()

# --------------------------------------------------------------- watchlist & alerts (Task 2.3)
# Plain accessors only: what counts as "watched" and what deserves an alert is policy, and policy
# lives in mediator/watchlist.py. This module just stores rows.

def watchlist_upsert(plate: str, reason: str, added_by: str, added_at: str) -> None:
    """Mark a plate. Re-marking updates the reason rather than duplicating the plate."""
    init_meta_db()
    conn = sqlite3.connect(META_DB_PATH)
    conn.execute("""
    INSERT INTO WATCHLIST (plate, reason, added_by, added_at) VALUES (?, ?, ?, ?)
    ON CONFLICT(plate) DO UPDATE SET
        reason = excluded.reason,
        added_by = excluded.added_by,
        added_at = excluded.added_at;
    """, (plate, reason, added_by, added_at))
    conn.commit()
    conn.close()


def watchlist_delete(plate: str) -> None:
    init_meta_db()
    conn = sqlite3.connect(META_DB_PATH)
    conn.execute("DELETE FROM WATCHLIST WHERE plate = ?;", (plate,))
    conn.commit()
    conn.close()


def watchlist_all() -> List[Dict[str, Any]]:
    init_meta_db()
    conn = sqlite3.connect(META_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM WATCHLIST ORDER BY added_at DESC;").fetchall()]
    conn.close()
    return rows


def watchlist_get(plate: str) -> Optional[Dict[str, Any]]:
    init_meta_db()
    conn = sqlite3.connect(META_DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM WATCHLIST WHERE plate = ?;", (plate,)).fetchone()
    conn.close()
    return dict(row) if row else None


def log_alert(plate: str, reason: str, seen_at: Optional[str], location: Optional[str],
              decision: Optional[str], ts: str) -> int:
    init_meta_db()
    conn = sqlite3.connect(META_DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO ALERT_LOG (plate, reason, seen_at, location, decision, ts)
    VALUES (?, ?, ?, ?, ?, ?);
    """, (plate, reason, seen_at, location, decision, ts))
    alert_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return alert_id


def get_alerts(limit: int = 50) -> List[Dict[str, Any]]:
    """Newest first — an alert log is read from the top."""
    init_meta_db()
    conn = sqlite3.connect(META_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM ALERT_LOG ORDER BY alert_id DESC LIMIT ?;", (int(limit),)).fetchall()]
    conn.close()
    return rows


# ------------------------------------------------------------------- query audit log (Task 2.6)

def log_query(row: Dict[str, Any]) -> int:
    """Append one row to QUERY_LOG. Never raises the caller's transaction: `mediator/core.py`
    wraps the call in try/except so a broken meta.db degrades to "no audit entry", not a failed
    query (the same refuse-don't-crash rule as watchlist/risk annotation)."""
    init_meta_db()
    conn = sqlite3.connect(META_DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO QUERY_LOG
    (ts, plate, requested_attrs, sources_asked, statuses, decision, confidence, elapsed_ms)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?);
    """, (
        row.get("ts") or datetime.now(timezone.utc).isoformat(),
        row.get("plate"),
        json.dumps(row.get("requested_attrs") or []),
        json.dumps(row.get("sources_asked") or []),
        json.dumps(row.get("statuses") or {}),
        row.get("decision"),
        row.get("confidence"),
        row.get("elapsed_ms"),
    ))
    log_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return log_id


def get_query_log(limit: int = 100, plate: Optional[str] = None) -> List[Dict[str, Any]]:
    """Newest first — an audit log is read from the top. `plate` filters to one plate's history."""
    init_meta_db()
    conn = sqlite3.connect(META_DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    if plate:
        cur.execute(
            "SELECT * FROM QUERY_LOG WHERE plate = ? ORDER BY id DESC LIMIT ?;",
            (plate, int(limit)),
        )
    else:
        cur.execute("SELECT * FROM QUERY_LOG ORDER BY id DESC LIMIT ?;", (int(limit),))
    rows = []
    for r in cur.fetchall():
        d = dict(r)
        d["requested_attrs"] = json.loads(d["requested_attrs"]) if d["requested_attrs"] else []
        d["sources_asked"] = json.loads(d["sources_asked"]) if d["sources_asked"] else []
        d["statuses"] = json.loads(d["statuses"]) if d["statuses"] else {}
        rows.append(d)
    conn.close()
    return rows


# --------------------------------------------------------------- challan cases (Challan Guard)
# Plain accessors only, exactly like the watchlist above: what a verdict *means* is policy and
# lives in mediator/challan_guard.py. This module only stores rows.

CHALLAN_COLUMNS = (
    "plate_read", "plate_resolved", "camera_id", "location", "lat", "lon", "captured_at",
    "observed_make", "observed_colour", "ocr_confidence", "status", "verdict", "reason",
    "amount_inr", "evidence_json",
)
# A fine that has actually been raised against this plate. DISPUTED is excluded: a case under
# dispute has not yet been confirmed, so it must not inflate the repeat-offender amount.
CHALLAN_ISSUED_STATUSES = ("ISSUED", "UPHELD")


def _challan_row(row: sqlite3.Row) -> Dict[str, Any]:
    """Decode evidence_json once, at the edge, so no caller has to know it is stored as text."""
    d = dict(row)
    raw = d.pop("evidence_json", None)
    try:
        d["evidence"] = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        d["evidence"] = {}  # a corrupt bundle must not make the whole queue unreadable
    return d


def challan_insert(case: Dict[str, Any]) -> int:
    init_meta_db()
    now_ts = datetime.now(timezone.utc).isoformat()
    values = dict(case)
    if "evidence" in values:
        values["evidence_json"] = json.dumps(values.pop("evidence"))
    values.setdefault("status", "CANDIDATE")
    columns = [c for c in CHALLAN_COLUMNS if c in values]
    conn = sqlite3.connect(META_DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO CHALLAN_CASES (" + ", ".join([*columns, "created_at", "updated_at"]) + ") "
        "VALUES (" + ", ".join(["?"] * (len(columns) + 2)) + ");",
        [values[c] for c in columns] + [now_ts, now_ts],
    )
    case_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return case_id


def challan_get(case_id: int) -> Optional[Dict[str, Any]]:
    init_meta_db()
    conn = sqlite3.connect(META_DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM CHALLAN_CASES WHERE case_id = ?;", (int(case_id),)).fetchone()
    conn.close()
    return _challan_row(row) if row else None


def challan_list(status: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    """Oldest first: a work queue is worked from the top, unlike an audit log."""
    init_meta_db()
    conn = sqlite3.connect(META_DB_PATH)
    conn.row_factory = sqlite3.Row
    if status:
        rows = conn.execute("SELECT * FROM CHALLAN_CASES WHERE status = ? ORDER BY case_id LIMIT ?;",
                            (status, int(limit))).fetchall()
    else:
        rows = conn.execute("SELECT * FROM CHALLAN_CASES ORDER BY case_id LIMIT ?;",
                            (int(limit),)).fetchall()
    conn.close()
    return [_challan_row(r) for r in rows]


def challan_update(case_id: int, **fields: Any) -> None:
    if "evidence" in fields:
        fields["evidence_json"] = json.dumps(fields.pop("evidence"))
    unknown = [k for k in fields if k not in CHALLAN_COLUMNS]
    if unknown:  # fail loudly: a silently ignored field would mean a verdict that never persisted
        raise ValueError(f"CHALLAN_CASES has no column(s): {', '.join(sorted(unknown))}")
    if not fields:
        return
    init_meta_db()
    conn = sqlite3.connect(META_DB_PATH)
    assignments = ", ".join(f"{k} = ?" for k in fields) + ", updated_at = ?"
    conn.execute(f"UPDATE CHALLAN_CASES SET {assignments} WHERE case_id = ?;",
                 [*fields.values(), datetime.now(timezone.utc).isoformat(), int(case_id)])
    conn.commit()
    conn.close()


def challan_event(case_id: int, event: str, actor: str,
                  evidence: Optional[Dict[str, Any]] = None) -> int:
    init_meta_db()
    conn = sqlite3.connect(META_DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO CHALLAN_EVENTS (case_id, event, actor, at, evidence_json) "
                "VALUES (?, ?, ?, ?, ?);",
                (int(case_id), event, actor, datetime.now(timezone.utc).isoformat(),
                 json.dumps(evidence) if evidence is not None else None))
    event_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return event_id


def challan_events(case_id: int) -> List[Dict[str, Any]]:
    init_meta_db()
    conn = sqlite3.connect(META_DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM CHALLAN_EVENTS WHERE case_id = ? ORDER BY event_id;",
                        (int(case_id),)).fetchall()
    conn.close()
    return [_challan_row(r) for r in rows]


def challan_prior_issued(plate: str, exclude_case_id: Optional[int] = None) -> int:
    """How many fines this plate already carries — the repeat-offender test for MV Act §196."""
    init_meta_db()
    conn = sqlite3.connect(META_DB_PATH)
    placeholders = ", ".join(["?"] * len(CHALLAN_ISSUED_STATUSES))
    sql = (f"SELECT COUNT(*) FROM CHALLAN_CASES WHERE status IN ({placeholders}) "
           f"AND COALESCE(plate_resolved, plate_read) = ?")
    params: List[Any] = [*CHALLAN_ISSUED_STATUSES, plate]
    if exclude_case_id is not None:
        sql += " AND case_id != ?"
        params.append(int(exclude_case_id))
    count = int(conn.execute(sql + ";", params).fetchone()[0])
    conn.close()
    return count


if __name__ == "__main__":
    init_meta_db()
    cat = get_source_catalog()
    print(f"Catalog initialized with {len(cat)} sources: {list(cat.keys())}")
    maps = get_all_mappings()
    print(f"Mappings initialized with {len(maps)} rules.")
