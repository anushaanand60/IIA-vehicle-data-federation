"""
generate_puc.py — synthesizes PUC (Pollution Under Control, SQLite) data.

Reads source_master.csv and gives ~70% of vehicles a PUC certificate.
"""

import csv
import random
from datetime import date, timedelta

SEED = 2023519
random.seed(SEED)

def read_master():
    with open("source_master.csv") as f:
        return list(csv.DictReader(f))

def main():
    master = read_master()
    random.shuffle(master)

    puc_records = []
    
    # 70% have PUC
    has_puc = master[:int(len(master)*0.7)]
    
    cert_id = 100
    for r in has_puc:
        plate = r["true_plate"]
        # Some valid, some expired relative to 2026-09-04
        valid_upto = date(2026, 9, 4) + timedelta(days=random.randint(-100, 300))
        center = random.choice(["South Delhi Center", "North Delhi Testing", "Gurugram Auto Hub", "Noida Inspection Point"])
        norm = random.choice(["BS-IV", "BS-VI"])
        puc_records.append({
            "cert_no": f"PUC-2026-{cert_id:03d}",
            "regn_number": plate,
            "valid_upto": valid_upto.isoformat(),
            "tested_at": center,
            "emission_norm": norm
        })
        cert_id += 1

    with open("puc_records.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=puc_records[0].keys())
        w.writeheader()
        w.writerows(puc_records)
        
    print(f"Generated {len(puc_records)} PUC records.")

if __name__ == "__main__":
    main()
