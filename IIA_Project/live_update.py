"""
Live Source Mutator (Demonstrating Freshness & Virtual Integration).
Allows you to insert or update rows directly in the autonomous source databases
during a live evaluation to prove that data is queried live without ETL or caching.
"""

import sqlite3
import os
import argparse
from datetime import datetime, timezone

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SOURCES_DIR = os.path.join(ROOT_DIR, "sources")

REG_DB = os.path.join(SOURCES_DIR, "reg", "reg.db")
INS_DB = os.path.join(SOURCES_DIR, "ins", "ins.db")
THEFT_DB = os.path.join(SOURCES_DIR, "theft", "theft.db")
CAM_DB = os.path.join(SOURCES_DIR, "cam", "cam.db")

def hyphenate(plate: str) -> str:
    p = plate.replace("-", "").replace(" ", "").upper()
    if len(p) == 10:
        return f"{p[0:2]}-{p[2:4]}-{p[4:6]}-{p[6:10]}"
    return plate

def renew_insurance(plate: str, new_expiry_ddmmyyyy: str = "31/12/2027"):
    """Update or insert an active renewal policy in INS."""
    hyphen_plate = hyphenate(plate)
    conn = sqlite3.connect(INS_DB)
    cur = conn.cursor()
    # Check if vehicle exists in INS
    cur.execute("SELECT policy_id FROM POLICY_RECORDS WHERE UPPER(REPLACE(REPLACE(vehicle_reg, '-', ''), ' ', '')) = ?", (plate.replace("-", "").replace(" ", "").upper(),))
    row = cur.fetchone()
    if row:
        cur.execute("""
        UPDATE POLICY_RECORDS 
        SET policy_until = ?, is_active = 1 
        WHERE policy_id = ?
        """, (new_expiry_ddmmyyyy, row[0]))
        print(f"✅ [INS] Updated policy for {plate} -> new expiry: {new_expiry_ddmmyyyy}, is_active=1")
    else:
        cur.execute("SELECT MAX(policy_id) FROM POLICY_RECORDS")
        new_id = (cur.fetchone()[0] or 3000) + 1
        cur.execute("""
        INSERT INTO POLICY_RECORDS (policy_id, vehicle_reg, insurer_id, policy_type, policy_start, policy_until, is_active, premium_inr)
        VALUES (?, ?, 1, 'COMPREHENSIVE', '01/01/2026', ?, 1, 9500.00)
        """, (new_id, hyphen_plate, new_expiry_ddmmyyyy))
        print(f"✅ [INS] Inserted brand-new active policy for {plate} -> expiry: {new_expiry_ddmmyyyy}")
    conn.commit()
    conn.close()

def expire_insurance(plate: str, old_expiry_ddmmyyyy: str = "10/01/2026"):
    """Expire an existing policy in INS."""
    conn = sqlite3.connect(INS_DB)
    cur = conn.cursor()
    p = plate.replace("-", "").replace(" ", "").upper()
    cur.execute("""
    UPDATE POLICY_RECORDS 
    SET policy_until = ?, is_active = 0 
    WHERE UPPER(REPLACE(REPLACE(vehicle_reg, '-', ''), ' ', '')) = ?
    """, (old_expiry_ddmmyyyy, p))
    conn.commit()
    conn.close()
    print(f"✅ [INS] Policy expired for {plate} -> expiry set to {old_expiry_ddmmyyyy}, is_active=0")

def report_stolen(plate: str, fir_no: str = "FIR-LIVE-99/2026", station: str = "Connaught Place PS"):
    """Insert a real-time theft record in THEFT."""
    p_spaced = f"{plate[0:2]} {plate[2:4]} {plate[4:6]} {plate[6:10]}".lower() if len(plate) == 10 else plate.lower()
    conn = sqlite3.connect(THEFT_DB)
    cur = conn.cursor()
    cur.execute("SELECT MAX(incident_id) FROM CRIME_RECORDS")
    new_id = (cur.fetchone()[0] or 500) + 1
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    cur.execute("""
    INSERT INTO CRIME_RECORDS (incident_id, vehicle_number, fir_no, reported_date, incident_type, stolen_flag, recovered_flag, case_status, police_station)
    VALUES (?, ?, ?, ?, 'THEFT', 'Y', 'N', 'OPEN', ?)
    """, (new_id, p_spaced, fir_no, now_epoch, station))
    conn.commit()
    conn.close()
    print(f"🚨 [THEFT] Stolen report logged for {plate} ({fir_no}, status OPEN)")

def register_vehicle(plate: str, owner_name: str, make: str, model: str, colour: str):
    """Insert a brand-new vehicle registration in REG."""
    p = plate.replace("-", "").replace(" ", "").upper()
    conn = sqlite3.connect(REG_DB)
    cur = conn.cursor()
    cur.execute("SELECT MAX(owner_id) FROM OWNERS")
    new_oid = (cur.fetchone()[0] or 1000) + 1
    cur.execute("INSERT INTO OWNERS VALUES (?, ?, 'Live Demo Address', 'Delhi')", (new_oid, owner_name))

    cur.execute("SELECT MAX(registration_id) FROM VEHICLE_REGISTRATION")
    new_rid = (cur.fetchone()[0] or 1000) + 1
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cur.execute("""
    INSERT INTO VEHICLE_REGISTRATION VALUES (?, ?, ?, ?, ?, ?, 'PETROL', ?, 'ACTIVE', 'DL01')
    """, (new_rid, p, new_oid, make, model, colour, today_iso))
    conn.commit()
    conn.close()
    print(f"📋 [REG] Registered new vehicle {plate}: {owner_name} ({make} {model}, {colour})")

def add_camera_sighting(plate: str, location_name: str, make: str, model: str, colour: str):
    """Insert a new camera sighting in CAM."""
    p = plate.replace("-", "").replace(" ", "").upper()
    conn = sqlite3.connect(CAM_DB)
    cur = conn.cursor()
    cur.execute("SELECT MAX(capture_id) FROM PLATE_CAPTURES")
    new_cid = (cur.fetchone()[0] or 5000) + 1
    now_iso = datetime.now(timezone.utc).isoformat()
    cur.execute("""
    INSERT INTO PLATE_CAPTURES VALUES (?, ?, 'CAM001', ?, ?, ?, ?, 0.98)
    """, (new_cid, p, now_iso, make, model, colour))
    conn.commit()
    conn.close()
    print(f"📸 [CAM] Camera sighting recorded for {plate} at {location_name} ({make} {model}, {colour})")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Live Source Mutator for IIA Demo")
    parser.add_argument("--action", choices=["renew", "expire", "stolen", "register", "sighting"], required=True)
    parser.add_argument("--plate", required=True, help="Target vehicle plate")
    parser.add_argument("--expiry", default="31/12/2027", help="Expiry date for renew/expire (DD/MM/YYYY)")
    parser.add_argument("--owner", default="Live Demo Owner", help="Owner name for register")
    parser.add_argument("--make", default="Hyundai", help="Make for register/sighting")
    parser.add_argument("--model", default="Creta", help="Model for register/sighting")
    parser.add_argument("--colour", default="White", help="Colour for register/sighting")

    args = parser.parse_args()

    if args.action == "renew":
        renew_insurance(args.plate, args.expiry)
    elif args.action == "expire":
        expire_insurance(args.plate, args.expiry)
    elif args.action == "stolen":
        report_stolen(args.plate)
    elif args.action == "register":
        register_vehicle(args.plate, args.owner, args.make, args.model, args.colour)
    elif args.action == "sighting":
        add_camera_sighting(args.plate, "Ring Road Junction", args.make, args.model, args.colour)
