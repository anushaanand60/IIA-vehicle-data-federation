"""
inject_demo_fixtures.py

Appends the 5 demo cases into the generated CSV files so that the loader 
can blindly load the CSVs without knowing about demo data.
Also generates the mediator_test_cases.csv file.
"""

import csv
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEMO_PLATES = {
    "DL01AB1234": {
        "story": "Clean / CLEAR",
        "expected_decision": "CLEAR",
        "expected_confidence": "HIGH",
        "reg": {
            "owner_name": "Ramesh Sharma",
            "address": "12 Barakhamba Road",
            "city": "New Delhi",
            "make": "Hyundai",
            "model": "Creta",
            "colour": "White",
            "fuel_type": "PETROL",
            "registered_on": "2023-01-15",
            "reg_status": "ACTIVE",
            "rto_code": "DL01"
        },
        "ins": {
            "reg": "DL-01-AB-1234",
            "insurer_id": 1,
            "policy_type": "COMPREHENSIVE",
            "policy_start": "15/01/2026",
            "policy_until": "14/01/2027",
            "is_active": 1,
            "premium_inr": 12500.00
        },
        "theft": None,
        "cam": {
            "camera_id": "CAM001",
            "captured_at": "2026-09-04T08:30:00",
            "observed_make": "Hyundai",
            "observed_model": "Creta",
            "observed_colour": "White",
            "ocr_confidence": 0.98
        },
        "puc": {
            "cert_no": "PUC-2026-001",
            "valid_upto": "2027-01-10",
            "tested_at": "South Delhi Center",
            "emission_norm": "BS-VI"
        }
    },
    "DL05CD9876": {
        "story": "Expired policy -> UNINSURED",
        "expected_decision": "UNINSURED — REPORT",
        "expected_confidence": "HIGH",
        "reg": {
            "owner_name": "Priya Singh",
            "address": "45 Mall Road",
            "city": "Delhi",
            "make": "Maruti Suzuki",
            "model": "Swift",
            "colour": "Silver",
            "fuel_type": "PETROL",
            "registered_on": "2021-05-10",
            "reg_status": "ACTIVE",
            "rto_code": "DL05"
        },
        "ins": {
            "reg": "DL-05-CD-9876",
            "insurer_id": 4,
            "policy_type": "THIRD_PARTY",
            "policy_start": "01/01/2025",
            "policy_until": "10/06/2026",
            "is_active": 0,
            "premium_inr": 4500.00
        },
        "theft": None,
        "cam": {
            "camera_id": "CAM002",
            "captured_at": "2026-09-03T14:15:00",
            "observed_make": "Maruti Suzuki",
            "observed_model": "Swift",
            "observed_colour": "Silver",
            "ocr_confidence": 0.95
        },
        "puc": {
            "cert_no": "PUC-2026-002",
            "valid_upto": "2026-05-15",
            "tested_at": "North Delhi Testing",
            "emission_norm": "BS-IV"
        }
    },
    "HR26EF4455": {
        "story": "Stolen vehicle, open case -> STOLEN",
        "expected_decision": "STOLEN — ALERT POLICE",
        "expected_confidence": "HIGH",
        "reg": {
            "owner_name": "Amit Verma",
            "address": "77 Cyber Hub",
            "city": "Gurugram",
            "make": "Mahindra",
            "model": "XUV700",
            "colour": "Black",
            "fuel_type": "DIESEL",
            "registered_on": "2022-08-20",
            "reg_status": "ACTIVE",
            "rto_code": "HR26"
        },
        "ins": {
            "reg": "HR-26-EF-4455",
            "insurer_id": 2,
            "policy_type": "COMPREHENSIVE",
            "policy_start": "01/01/2026",
            "policy_until": "31/12/2026",
            "is_active": 1,
            "premium_inr": 15000.00
        },
        "theft": {
            "vehicle_number": "hr 26 ef 4455",
            "fir_no": "FIR00999/2026",
            "reported_date": 1786768200, # int(datetime(2026, 8, 15, 10, 0).timestamp()) approx
            "incident_type": "THEFT",
            "stolen_flag": "Y",
            "recovered_flag": "N",
            "case_status": "OPEN",
            "police_station": "Connaught Place PS"
        },
        "cam": {
            "camera_id": "CAM003",
            "captured_at": "2026-09-02T19:45:00",
            "observed_make": "Mahindra",
            "observed_model": "XUV700",
            "observed_colour": "Black",
            "ocr_confidence": 0.94
        },
        "puc": {
            "cert_no": "PUC-2026-003",
            "valid_upto": "2026-11-20",
            "tested_at": "Gurugram Auto Hub",
            "emission_norm": "BS-VI"
        }
    },
    "UP16GH1122": {
        "story": "Make/Model conflict -> SUSPICIOUS - POSSIBLE CLONED PLATE",
        "expected_decision": "SUSPICIOUS — POSSIBLE CLONED PLATE",
        "expected_confidence": "MEDIUM",
        "reg": {
            "owner_name": "Vikram Patel",
            "address": "88 Sector 62",
            "city": "Noida",
            "make": "Hyundai",
            "model": "Creta",
            "colour": "Red",
            "fuel_type": "CNG",
            "registered_on": "2023-11-05",
            "reg_status": "ACTIVE",
            "rto_code": "UP16"
        },
        "ins": {
            "reg": "UP-16-GH-1122",
            "insurer_id": 5,
            "policy_type": "COMPREHENSIVE",
            "policy_start": "20/03/2026",
            "policy_until": "19/03/2027",
            "is_active": 1,
            "premium_inr": 11000.00
        },
        "theft": None,
        "cam": {
            "camera_id": "CAM004",
            "captured_at": "2026-09-04T11:00:00",
            "observed_make": "Kia",
            "observed_model": "Seltos",
            "observed_colour": "Blue",
            "ocr_confidence": 0.96
        },
        "puc": {
            "cert_no": "PUC-2026-004",
            "valid_upto": "2027-02-28",
            "tested_at": "Noida Inspection Point",
            "emission_norm": "BS-VI"
        }
    },
    "MH12IJ7788": {
        "story": "Seen on camera, not registered -> UNREGISTERED / SUSPICIOUS",
        "expected_decision": "UNREGISTERED / SUSPICIOUS",
        "expected_confidence": "MEDIUM",
        "reg": None,
        "ins": None,
        "theft": None,
        "cam": {
            "camera_id": "CAM005",
            "captured_at": "2026-09-04T07:10:00",
            "observed_make": "Tata",
            "observed_model": "Nexon",
            "observed_colour": "Grey",
            "ocr_confidence": 0.92
        },
        "puc": None
    }
}

def get_max_id(filename, id_col):
    if not os.path.exists(filename):
        return 1000
    max_id = 0
    with open(filename, newline='', encoding='utf-8', errors='replace') as f:
        reader = csv.DictReader(f)
        for r in reader:
            val = r.get(id_col)
            if val and val.isdigit():
                max_id = max(max_id, int(val))
    return max_id

def inject_reg():
    owners_csv = os.path.join(BASE_DIR, "reg_owners.csv")
    vr_csv = os.path.join(BASE_DIR, "reg_vehicle_registration.csv")
    
    max_owner = get_max_id(owners_csv, "owner_id")
    max_reg = get_max_id(vr_csv, "registration_id")
    
    owners = []
    vrs = []
    for plate, info in DEMO_PLATES.items():
        if info["reg"]:
            r = info["reg"]
            max_owner += 1
            max_reg += 1
            owners.append({"owner_id": max_owner, "full_name": r["owner_name"], "address_line": r["address"], "city": r["city"]})
            vrs.append({
                "registration_id": max_reg, "registration_no": plate, "owner_id": max_owner,
                "make": r["make"], "model": r["model"], "colour": r["colour"], "fuel_type": r["fuel_type"],
                "registered_on": r["registered_on"], "reg_status": r["reg_status"], "rto_code": r["rto_code"]
            })
            
    if owners:
        with open(owners_csv, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=owners[0].keys())
            w.writerows(owners)
    if vrs:
        with open(vr_csv, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=vrs[0].keys())
            w.writerows(vrs)

def inject_ins():
    pol_csv = os.path.join(BASE_DIR, "ins_policy_records.csv")
    max_policy = get_max_id(pol_csv, "policy_id")
    
    pols = []
    for plate, info in DEMO_PLATES.items():
        if info["ins"]:
            i = info["ins"]
            max_policy += 1
            pols.append({
                "policy_id": max_policy, "vehicle_reg": i["reg"], "insurer_id": i["insurer_id"],
                "policy_type": i["policy_type"], "policy_start": i["policy_start"], "policy_until": i["policy_until"],
                "is_active": i["is_active"], "premium_inr": i["premium_inr"]
            })
            
    if pols:
        with open(pol_csv, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=pols[0].keys())
            w.writerows(pols)

def inject_theft():
    theft_csv = os.path.join(BASE_DIR, "theft_crime_records.csv")
    max_incident = get_max_id(theft_csv, "incident_id")
    
    crimes = []
    for plate, info in DEMO_PLATES.items():
        if info["theft"]:
            t = info["theft"]
            max_incident += 1
            crimes.append({
                "incident_id": max_incident, "vehicle_number": t["vehicle_number"], "fir_no": t["fir_no"],
                "reported_date": t["reported_date"], "incident_type": t["incident_type"], "stolen_flag": t["stolen_flag"],
                "recovered_flag": t["recovered_flag"], "case_status": t["case_status"], "police_station": t["police_station"]
            })
            
    if crimes:
        with open(theft_csv, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=crimes[0].keys())
            w.writerows(crimes)

def inject_cam():
    cam_csv = os.path.join(BASE_DIR, "cam_plate_captures.csv")
    max_cap = get_max_id(cam_csv, "capture_id")
    
    caps = []
    for plate, info in DEMO_PLATES.items():
        if info["cam"]:
            c = info["cam"]
            max_cap += 1
            caps.append({
                "capture_id": max_cap, "plate_id": plate, "camera_id": c["camera_id"],
                "captured_at": c["captured_at"], "observed_make": c["observed_make"],
                "observed_model": c["observed_model"], "observed_colour": c["observed_colour"],
                "ocr_confidence": c["ocr_confidence"]
            })
            
    if caps:
        with open(cam_csv, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=caps[0].keys())
            w.writerows(caps)

def inject_puc():
    puc_csv = os.path.join(BASE_DIR, "puc_records.csv")
    pucs = []
    for plate, info in DEMO_PLATES.items():
        if info["puc"]:
            p = info["puc"]
            pucs.append({
                "cert_no": p["cert_no"], "regn_number": plate, "valid_upto": p["valid_upto"],
                "tested_at": p["tested_at"], "emission_norm": p["emission_norm"]
            })
            
    if pucs:
        with open(puc_csv, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=pucs[0].keys())
            w.writerows(pucs)

def create_mediator_test_cases():
    test_cases_csv = os.path.join(BASE_DIR, "data", "mediator_test_cases.csv")
    rows = []
    for plate, info in DEMO_PLATES.items():
        rows.append({
            "plate_number": plate,
            "expected_decision": info["expected_decision"],
            "expected_confidence": info["expected_confidence"],
            "story": info["story"]
        })
        
    with open(test_cases_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["plate_number", "expected_decision", "expected_confidence", "story"])
        w.writeheader()
        w.writerows(rows)
    print(f"Created {test_cases_csv}")

if __name__ == "__main__":
    inject_reg()
    inject_ins()
    inject_theft()
    inject_cam()
    inject_puc()
    create_mediator_test_cases()
    print("Demo fixtures injected into CSVs.")
