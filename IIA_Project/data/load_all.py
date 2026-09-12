import csv
import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT_DIR = BASE_DIR
DATA_DIR = os.path.join(ROOT_DIR, "data")
SOURCES_DIR = os.path.join(ROOT_DIR, "sources")

REG_DB = os.path.join(SOURCES_DIR, "reg", "reg.db")
INS_DB = os.path.join(SOURCES_DIR, "ins", "ins.db")
THEFT_DB = os.path.join(SOURCES_DIR, "theft", "theft.db")
CAM_DB = os.path.join(SOURCES_DIR, "cam", "cam.db")
PUC_DB = os.path.join(SOURCES_DIR, "puc", "puc.db")

def init_schema(db_path, source_name):
    if os.path.exists(db_path):
        os.remove(db_path)
    schema_path = os.path.join(SOURCES_DIR, source_name, "schema.sql")
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.executescript(schema_sql)
    conn.commit()
    return conn, cur

def load_reg():
    conn, cur = init_schema(REG_DB, "reg")
    
    owners_csv = os.path.join(ROOT_DIR, "reg_owners.csv")
    if os.path.exists(owners_csv):
        with open(owners_csv, newline='', encoding='utf-8', errors='replace') as f:
            reader = csv.DictReader(f)
            for r in reader:
                cur.execute(
                    "INSERT INTO OWNERS (owner_id, full_name, address_line, city) VALUES (?, ?, ?, ?)",
                    (int(r["owner_id"]), r["full_name"], r["address_line"], r["city"])
                )
                
    vr_csv = os.path.join(ROOT_DIR, "reg_vehicle_registration.csv")
    if os.path.exists(vr_csv):
        with open(vr_csv, newline='', encoding='utf-8', errors='replace') as f:
            reader = csv.DictReader(f)
            for r in reader:
                cur.execute(
                    """INSERT INTO VEHICLE_REGISTRATION 
                    (registration_id, registration_no, owner_id, make, model, colour, fuel_type, registered_on, reg_status, rto_code)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (int(r["registration_id"]), r["registration_no"], int(r["owner_id"]), r["make"], r["model"],
                     r["colour"], r["fuel_type"], r["registered_on"], r["reg_status"], r["rto_code"])
                )
    conn.commit()
    conn.close()
    print("Loaded REG DB successfully.")

def load_ins():
    conn, cur = init_schema(INS_DB, "ins")
    
    ins_csv = os.path.join(ROOT_DIR, "ins_insurers.csv")
    if os.path.exists(ins_csv):
        with open(ins_csv, newline='', encoding='utf-8', errors='replace') as f:
            reader = csv.reader(f)
            next(reader)
            for row in reader:
                cur.execute("INSERT INTO INSURERS VALUES (?, ?)", (int(row[0]), row[1]))
                
    pol_csv = os.path.join(ROOT_DIR, "ins_policy_records.csv")
    if os.path.exists(pol_csv):
        with open(pol_csv, newline='', encoding='utf-8', errors='replace') as f:
            reader = csv.DictReader(f)
            for r in reader:
                cur.execute("""INSERT INTO POLICY_RECORDS 
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            (int(r["policy_id"]), r["vehicle_reg"], int(r["insurer_id"]), r["policy_type"],
                             r["policy_start"], r["policy_until"], int(r["is_active"]), float(r["premium_inr"])))
    conn.commit()
    conn.close()
    print("Loaded INS DB successfully.")

def load_theft():
    conn, cur = init_schema(THEFT_DB, "theft")
    
    theft_csv = os.path.join(ROOT_DIR, "theft_crime_records.csv")
    if os.path.exists(theft_csv):
        with open(theft_csv, newline='', encoding='utf-8', errors='replace') as f:
            reader = csv.DictReader(f)
            for r in reader:
                cur.execute("""INSERT INTO CRIME_RECORDS VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (int(r["incident_id"]), r["vehicle_number"], r["fir_no"], int(r["reported_date"]),
                             r["incident_type"], r["stolen_flag"], r["recovered_flag"], r["case_status"], r["police_station"]))
    conn.commit()
    conn.close()
    print("Loaded THEFT DB successfully.")

def load_cam():
    conn, cur = init_schema(CAM_DB, "cam")
    
    cam_csv = os.path.join(ROOT_DIR, "cam_cameras.csv")
    if os.path.exists(cam_csv):
        with open(cam_csv, newline='', encoding='utf-8', errors='replace') as f:
            reader = csv.reader(f)
            next(reader)
            for row in reader:
                cur.execute("INSERT INTO CAMERAS VALUES (?, ?, ?, ?)", (row[0], row[1], float(row[2]), float(row[3])))
                
    cap_csv = os.path.join(ROOT_DIR, "cam_plate_captures.csv")
    if os.path.exists(cap_csv):
        with open(cap_csv, newline='', encoding='utf-8', errors='replace') as f:
            reader = csv.DictReader(f)
            for r in reader:
                cur.execute("""INSERT INTO PLATE_CAPTURES VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            (int(r["capture_id"]), r["plate_id"], r["camera_id"], r["captured_at"],
                             r["observed_make"], r["observed_model"], r["observed_colour"], float(r["ocr_confidence"])))
    conn.commit()
    conn.close()
    print("Loaded CAM DB successfully.")

def load_puc():
    conn, cur = init_schema(PUC_DB, "puc")
    
    puc_csv = os.path.join(ROOT_DIR, "puc_records.csv")
    if os.path.exists(puc_csv):
        with open(puc_csv, newline='', encoding='utf-8', errors='replace') as f:
            reader = csv.DictReader(f)
            for r in reader:
                cur.execute("""INSERT INTO POLLUTION_CERT VALUES (?, ?, ?, ?, ?)""",
                            (r["cert_no"], r["regn_number"], r["valid_upto"], r["tested_at"], r["emission_norm"]))
    conn.commit()
    conn.close()
    print("Loaded PUC DB successfully.")


if __name__ == "__main__":
    load_reg()
    load_ins()
    load_theft()
    load_cam()
    load_puc()
