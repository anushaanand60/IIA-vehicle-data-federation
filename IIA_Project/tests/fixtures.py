import sqlite3
import os
import json
from datetime import datetime, timezone

META_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "mediator", "meta.db")

APPROVED_MAPPINGS = [
    # REG
    {"source_id": "REG", "source_table": "VEHICLE_REGISTRATION", "source_attr": "registration_no", "global_attr": "plate_number", "transform_fn": "norm_plate", "aggregate": None, "join_path": "VEHICLE_REGISTRATION.owner_id=OWNERS.owner_id", "match_score": 1.0},
    {"source_id": "REG", "source_table": "OWNERS", "source_attr": "full_name", "global_attr": "owner_name", "transform_fn": "title_case", "aggregate": None, "join_path": "VEHICLE_REGISTRATION.owner_id=OWNERS.owner_id", "match_score": 1.0},
    {"source_id": "REG", "source_table": "VEHICLE_REGISTRATION", "source_attr": "make", "global_attr": "vehicle_make", "transform_fn": "fix_make", "aggregate": None, "join_path": "VEHICLE_REGISTRATION.owner_id=OWNERS.owner_id", "match_score": 1.0},
    {"source_id": "REG", "source_table": "VEHICLE_REGISTRATION", "source_attr": "model", "global_attr": "vehicle_model", "transform_fn": "title_case", "aggregate": None, "join_path": "VEHICLE_REGISTRATION.owner_id=OWNERS.owner_id", "match_score": 1.0},
    {"source_id": "REG", "source_table": "VEHICLE_REGISTRATION", "source_attr": "colour", "global_attr": "vehicle_colour", "transform_fn": "title_case", "aggregate": None, "join_path": "VEHICLE_REGISTRATION.owner_id=OWNERS.owner_id", "match_score": 1.0},
    {"source_id": "REG", "source_table": "VEHICLE_REGISTRATION", "source_attr": "registered_on", "global_attr": "registration_date", "transform_fn": "none", "aggregate": None, "join_path": "VEHICLE_REGISTRATION.owner_id=OWNERS.owner_id", "match_score": 1.0},
    {"source_id": "REG", "source_table": "VEHICLE_REGISTRATION", "source_attr": "reg_status", "global_attr": "registration_status", "transform_fn": "status_map", "aggregate": None, "join_path": "VEHICLE_REGISTRATION.owner_id=OWNERS.owner_id", "match_score": 1.0},
    {"source_id": "REG", "source_table": "VEHICLE_REGISTRATION", "source_attr": "fuel_type", "global_attr": "fuel_type", "transform_fn": "none", "aggregate": None, "join_path": "VEHICLE_REGISTRATION.owner_id=OWNERS.owner_id", "match_score": 1.0},
    {"source_id": "REG", "source_table": "VEHICLE_REGISTRATION", "source_attr": "rto_code", "global_attr": "rto_code", "transform_fn": "none", "aggregate": None, "join_path": "VEHICLE_REGISTRATION.owner_id=OWNERS.owner_id", "match_score": 1.0},

    # INS
    {"source_id": "INS", "source_table": "POLICY_RECORDS", "source_attr": "vehicle_reg", "global_attr": "plate_number", "transform_fn": "norm_plate", "aggregate": None, "join_path": "POLICY_RECORDS.insurer_id=INSURERS.insurer_id", "match_score": 1.0},
    {"source_id": "INS", "source_table": "INSURERS", "source_attr": "insurer_name", "global_attr": "insurer_name", "transform_fn": "title_case", "aggregate": None, "join_path": "POLICY_RECORDS.insurer_id=INSURERS.insurer_id", "match_score": 1.0},
    {"source_id": "INS", "source_table": "POLICY_RECORDS", "source_attr": "policy_type", "global_attr": "policy_type", "transform_fn": "none", "aggregate": None, "join_path": "POLICY_RECORDS.insurer_id=INSURERS.insurer_id", "match_score": 1.0},
    {"source_id": "INS", "source_table": "POLICY_RECORDS", "source_attr": "policy_start", "global_attr": "insurance_start", "transform_fn": "parse_ddmmyyyy", "aggregate": None, "join_path": "POLICY_RECORDS.insurer_id=INSURERS.insurer_id", "match_score": 1.0},
    {"source_id": "INS", "source_table": "POLICY_RECORDS", "source_attr": "policy_until", "global_attr": "insurance_expiry", "transform_fn": "parse_ddmmyyyy", "aggregate": "latest_by:policy_until", "join_path": "POLICY_RECORDS.insurer_id=INSURERS.insurer_id", "match_score": 1.0},
    {"source_id": "INS", "source_table": "POLICY_RECORDS", "source_attr": "is_active", "global_attr": "is_active", "transform_fn": "none", "aggregate": None, "join_path": "POLICY_RECORDS.insurer_id=INSURERS.insurer_id", "match_score": 1.0},
    {"source_id": "INS", "source_table": "POLICY_RECORDS", "source_attr": "premium_inr", "global_attr": "premium_inr", "transform_fn": "none", "aggregate": None, "join_path": "POLICY_RECORDS.insurer_id=INSURERS.insurer_id", "match_score": 1.0},

    # THEFT
    {"source_id": "THEFT", "source_table": "CRIME_RECORDS", "source_attr": "vehicle_number", "global_attr": "plate_number", "transform_fn": "norm_plate", "aggregate": None, "join_path": None, "match_score": 1.0},
    {"source_id": "THEFT", "source_table": "CRIME_RECORDS", "source_attr": "reported_date", "global_attr": "last_incident_date", "transform_fn": "epoch_to_date", "aggregate": "latest_by:reported_date", "join_path": None, "match_score": 1.0},
    {"source_id": "THEFT", "source_table": "CRIME_RECORDS", "source_attr": "case_status", "global_attr": "case_status", "transform_fn": "status_map", "aggregate": None, "join_path": None, "match_score": 1.0},
    {"source_id": "THEFT", "source_table": "CRIME_RECORDS", "source_attr": "stolen_flag", "global_attr": "stolen_flag", "transform_fn": "none", "aggregate": None, "join_path": None, "match_score": 1.0},
    {"source_id": "THEFT", "source_table": "CRIME_RECORDS", "source_attr": "recovered_flag", "global_attr": "recovered_flag", "transform_fn": "none", "aggregate": None, "join_path": None, "match_score": 1.0},
    {"source_id": "THEFT", "source_table": "CRIME_RECORDS", "source_attr": "fir_no", "global_attr": "fir_no", "transform_fn": "none", "aggregate": None, "join_path": None, "match_score": 1.0},
    {"source_id": "THEFT", "source_table": "CRIME_RECORDS", "source_attr": "police_station", "global_attr": "police_station", "transform_fn": "none", "aggregate": None, "join_path": None, "match_score": 1.0},
    {"source_id": "THEFT", "source_table": "CRIME_RECORDS", "source_attr": "incident_type", "global_attr": "incident_type", "transform_fn": "none", "aggregate": None, "join_path": None, "match_score": 1.0},

    # CAM
    {"source_id": "CAM", "source_table": "PLATE_CAPTURES", "source_attr": "plate_id", "global_attr": "plate_number", "transform_fn": "norm_plate", "aggregate": None, "join_path": "PLATE_CAPTURES.camera_id=CAMERAS.camera_id", "match_score": 1.0},
    {"source_id": "CAM", "source_table": "PLATE_CAPTURES", "source_attr": "captured_at", "global_attr": "last_seen_time", "transform_fn": "none", "aggregate": "latest_by:captured_at", "join_path": "PLATE_CAPTURES.camera_id=CAMERAS.camera_id", "match_score": 1.0},
    {"source_id": "CAM", "source_table": "CAMERAS", "source_attr": "location_name", "global_attr": "last_seen_location", "transform_fn": "title_case", "aggregate": None, "join_path": "PLATE_CAPTURES.camera_id=CAMERAS.camera_id", "match_score": 1.0},
    {"source_id": "CAM", "source_table": "PLATE_CAPTURES", "source_attr": "observed_make", "global_attr": "observed_make", "transform_fn": "fix_make", "aggregate": None, "join_path": "PLATE_CAPTURES.camera_id=CAMERAS.camera_id", "match_score": 1.0},
    {"source_id": "CAM", "source_table": "PLATE_CAPTURES", "source_attr": "observed_model", "global_attr": "observed_model", "transform_fn": "title_case", "aggregate": None, "join_path": "PLATE_CAPTURES.camera_id=CAMERAS.camera_id", "match_score": 1.0},
    {"source_id": "CAM", "source_table": "PLATE_CAPTURES", "source_attr": "observed_colour", "global_attr": "observed_colour", "transform_fn": "title_case", "aggregate": None, "join_path": "PLATE_CAPTURES.camera_id=CAMERAS.camera_id", "match_score": 1.0},
    {"source_id": "CAM", "source_table": "PLATE_CAPTURES", "source_attr": "ocr_confidence", "global_attr": "ocr_confidence", "transform_fn": "none", "aggregate": None, "join_path": "PLATE_CAPTURES.camera_id=CAMERAS.camera_id", "match_score": 1.0},

    # PUC
    {"source_id": "PUC", "source_table": "POLLUTION_CERT", "source_attr": "regn_number", "global_attr": "plate_number", "transform_fn": "norm_plate", "aggregate": None, "join_path": None, "match_score": 1.0},
    {"source_id": "PUC", "source_table": "POLLUTION_CERT", "source_attr": "valid_upto", "global_attr": "puc_expiry", "transform_fn": "none", "aggregate": "latest_by:valid_upto", "join_path": None, "match_score": 1.0},
    {"source_id": "PUC", "source_table": "POLLUTION_CERT", "source_attr": "tested_at", "global_attr": "tested_at", "transform_fn": "none", "aggregate": None, "join_path": None, "match_score": 1.0},
    {"source_id": "PUC", "source_table": "POLLUTION_CERT", "source_attr": "emission_norm", "global_attr": "emission_norm", "transform_fn": "none", "aggregate": None, "join_path": None, "match_score": 1.0},
    {"source_id": "PUC", "source_table": "POLLUTION_CERT", "source_attr": "cert_no", "global_attr": "cert_no", "transform_fn": "none", "aggregate": None, "join_path": None, "match_score": 1.0},
]

def inject_test_mappings():
    from mediator.catalog import init_meta_db
    init_meta_db()
    conn = sqlite3.connect(META_DB_PATH)
    cur = conn.cursor()
    now_ts = datetime.now(timezone.utc).isoformat()
    for m in APPROVED_MAPPINGS:
        cur.execute("""
        INSERT OR IGNORE INTO MAPPING_REGISTRY
        (source_id, source_table, source_attr, global_attr, transform_fn, aggregate, join_path, match_score, validated_by, validated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            m["source_id"], m["source_table"], m["source_attr"], m["global_attr"],
            m["transform_fn"], m["aggregate"], m["join_path"], m["match_score"], "human_expert", now_ts
        ))
    conn.commit()
    conn.close()
