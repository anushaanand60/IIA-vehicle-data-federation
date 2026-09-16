"""
generate_theft.py — synthesizes THEFT (Police Crime Records, SQLite) data.

Reads source_master.csv and:
  - picks ~30 plates as STOLEN, case still OPEN -> triggers decision rule 2
  - picks ~10 plates as STOLEN then RECOVERED, case CLOSED -> tests recovery logic
  - everyone else simply has NO row here at all (most vehicles are never
    involved in any crime record — this is the normal/expected case)

Formats the plate as THEFT's own broken convention: lower-case with spaces,
e.g. 'dl 01 ab 0001'. Dates are stored as raw Unix epoch INTEGERS (not a date
type at all), and booleans are stored as text 'Y'/'N' rather than 0/1.
"""

import csv
import random
from datetime import date, datetime, timedelta

SEED = 2023519
random.seed(SEED)

TODAY = date(2026, 9, 4)

N_STOLEN_OPEN = 30
N_STOLEN_RECOVERED = 10

POLICE_STATIONS = ["Connaught Place PS", "Karol Bagh PS", "Sector 14 PS", "MG Road PS", "Civil Lines PS"]


def lowercase_spaced_plate(plate: str) -> str:
    """'DL01AB0001' -> 'dl 01 ab 0001' — THEFT's own convention."""
    return f"{plate[0:2]} {plate[2:4]} {plate[4:6]} {plate[6:10]}".lower()


def to_epoch(d: date) -> int:
    return int(datetime(d.year, d.month, d.day).timestamp())


def read_master():
    with open("source_master.csv") as f:
        return list(csv.DictReader(f))


def main():
    master = read_master()
    random.Random(f"{SEED}-THEFT").shuffle(master)  # its own order, not INS's or CAM's (see ins.py)

    stolen_open = master[:N_STOLEN_OPEN]
    stolen_recovered = master[N_STOLEN_OPEN:N_STOLEN_OPEN + N_STOLEN_RECOVERED]

    rows = []
    incident_id = 1

    for r in stolen_open:
        plate = r["true_plate"]
        reported = TODAY - timedelta(days=random.randint(1, 90))
        rows.append({
            "incident_id": incident_id,
            "vehicle_number": lowercase_spaced_plate(plate),
            "fir_no": f"FIR{incident_id:05d}/2026",
            "reported_date": to_epoch(reported),
            "incident_type": "THEFT",
            "stolen_flag": "Y",
            "recovered_flag": "N",
            "case_status": "OPEN",
            "police_station": random.choice(POLICE_STATIONS),
        })
        incident_id += 1

    for r in stolen_recovered:
        plate = r["true_plate"]
        reported = TODAY - timedelta(days=random.randint(100, 400))
        rows.append({
            "incident_id": incident_id,
            "vehicle_number": lowercase_spaced_plate(plate),
            "fir_no": f"FIR{incident_id:05d}/2025",
            "reported_date": to_epoch(reported),
            "incident_type": "THEFT",
            "stolen_flag": "Y",
            "recovered_flag": "Y",
            "case_status": "CLOSED",
            "police_station": random.choice(POLICE_STATIONS),
        })
        incident_id += 1

    with open("theft_crime_records.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    print(f"THEFT: {len(rows)} incident rows written "
          f"({N_STOLEN_OPEN} OPEN, {N_STOLEN_RECOVERED} CLOSED/recovered). "
          f"The remaining ~{len(master) - len(rows)} vehicles have no crime record at all.")


if __name__ == "__main__":
    main()