"""CONTEXT-ADDED (R1): shared helpers for the phase-3 generator (scripts/phase3_generate.py, phase3_plant.py).

Everything here is deterministic given the caller's random.Random, so re-running the generator writes
byte-identical CSVs. Each agency keeps its own formats on purpose: REG ISO dates, INS DD/MM/YYYY text,
THEFT epoch seconds (IST midnight, like the existing reported_date), CAM and PUC ISO timestamps.
"""
from __future__ import annotations

import csv
import re
import zlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from random import Random
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REF_TODAY = date(2026, 9, 4)  # mediator.decide.REFERENCE_TODAY: nothing is generated after this day
IST = timezone(timedelta(hours=5, minutes=30))
STORY_PLATES = {"DL01AB1234", "DL05CD9876", "DL09KL3321", "HR26EF4455", "UP16GH1122", "MH12IJ7788",
                "KA05MN9999", "DL03SC5566"}  # phase 1-2 demo vehicles (+ the scrapped one): never planted on

# New tables per source, in load order (parents first), with their columns in schema.sql order.
NEW_TABLES: dict[str, list[tuple[str, list[str]]]] = {
    "REG": [("VEHICLE_IDENTITY", ["registration_id", "chassis_no", "engine_no", "mfg_year"]),
            ("OWNERSHIP_HISTORY", ["transfer_id", "registration_id", "prev_owner_id", "new_owner_id",
                                   "transfer_date", "transfer_type"]),
            ("OWNER_CONTACTS", ["owner_id", "mobile", "pincode"])],
    "INS": [("POLICY_HOLDERS", ["holder_id", "holder_name", "mobile", "address", "pincode"]),
            ("POLICY_PARTIES", ["policy_id", "holder_id", "chassis_no"]),
            ("CLAIMS", ["claim_id", "policy_id", "loss_date", "intimation_date", "loss_type",
                        "amount_claimed", "claim_status", "surveyor_name"])],
    "THEFT": [("STATIONS", ["station_name", "district", "zone"]),
              ("STOLEN_PROPERTY", ["incident_id", "chassis_no", "engine_no", "recovered_on"]),
              ("PERSONS", ["person_id", "name", "mobile", "address"]),
              ("INCIDENT_PERSONS", ["incident_id", "person_id", "role"])],
    "CAM": [("ZONES", ["zone_id", "zone_name", "city"]),
            ("CAMERA_ZONES", ["camera_id", "zone_id", "road"]),
            ("CHALLANS", ["challan_id", "capture_id", "violation", "fine_inr", "challan_status", "issued_at"])],
    "PUC": [("TEST_CENTRES", ["centre_id", "centre_name", "city", "lat", "lon"]),
            ("ANALYSERS", ["analyser_id", "centre_id", "calibrated_on"]),
            ("CERT_TESTS", ["test_id", "cert_no", "analyser_id", "test_ts", "co_pct", "hc_ppm", "result"])],
}


def csv_name(source_id: str, table: str) -> str:
    return f"{source_id.lower()}_{table.lower()}.csv"


def read_csv(name: str) -> list[dict[str, str]]:
    with (ROOT / name).open(newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: "" if row.get(c) is None else row[c] for c in columns})


def canon(plate: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", plate.upper())


_TO_DIGIT = str.maketrans("OIZSBGQD", "01258600")
_TO_LETTER = str.maketrans("012586", "OIZSBG")


def ocr_fold(plate: str) -> str:
    """Undo the usual OCR swaps by position (SS DD LL NNNN), so a CAM read finds its REG plate."""
    p = canon(plate)
    if len(p) != 10:
        return p
    return (p[:2].translate(_TO_LETTER) + p[2:4].translate(_TO_DIGIT)
            + p[4:6].translate(_TO_LETTER) + p[6:].translate(_TO_DIGIT))


def dmy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def parse_dmy(text: str) -> date:
    return datetime.strptime(text, "%d/%m/%Y").date()


def epoch_ist(d: date, hour: int = 0) -> int:
    return int(datetime(d.year, d.month, d.day, hour, tzinfo=IST).timestamp())


def ist_date(epoch: int) -> date:
    return datetime.fromtimestamp(epoch, IST).date()


def iso_ts(d: date, hour: int, minute: int = 0) -> str:
    return datetime(d.year, d.month, d.day, hour, minute).isoformat()


# ------------------------------------------------------------------ people

FIRST = ["Rahul", "Anil", "Sunil", "Rajesh", "Manoj", "Deepak", "Sanjay", "Vijay", "Pooja", "Neha", "Kavita",
         "Rakesh", "Mohit", "Arjun", "Imran", "Salim", "Gurpreet", "Harpreet", "Lakshmi", "Meena", "Sachin",
         "Naveen", "Prakash", "Farhan", "Ritu", "Anjali", "Vikas", "Ajay", "Suresh", "Dinesh"]
LAST = ["Sharma", "Verma", "Gupta", "Yadav", "Singh", "Chauhan", "Malik", "Khan", "Rawat", "Tomar", "Bansal",
        "Aggarwal", "Saini", "Kapoor", "Mehta", "Joshi", "Nair", "Reddy", "Shetty", "Kumar", "Tiwari", "Dahiya"]
STREETS = ["Main Road", "Gali No. 4", "Ring Road", "Mall Road", "Station Road", "Sector 15", "Sector 22",
           "Phase 2", "Block C", "Old Market", "MG Road", "Nehru Nagar", "Shastri Nagar", "Laxmi Nagar"]
PIN_BY_CITY = {"new delhi": "110001", "delhi": "110092", "gurugram": "122001", "gurgaon": "122001",
               "noida": "201301", "mumbai": "400001", "pune": "411001", "bengaluru": "560001",
               "bangalore": "560001", "chennai": "600001", "kolkata": "700001", "hyderabad": "500001",
               "jaipur": "302001", "lucknow": "226001", "agra": "282001", "faridabad": "121001"}


def pincode(city: str) -> str:
    """Known metros get their real head-office PIN; other towns a stable pseudo PIN (crc32, not hash())."""
    known = PIN_BY_CITY.get(city.strip().lower())
    if known:
        return known
    h = zlib.crc32(city.strip().lower().encode())
    return f"{1 + h % 8}{(h // 8) % 100000:05d}"


def new_mobile(rng: Random, used: set[str]) -> str:
    while True:
        m = rng.choice("6789") + f"{rng.randrange(10**9):09d}"
        if m not in used:
            used.add(m)
            return m


def name_variant(name: str, style: str) -> str:
    """How another agency's clerk writes the same person: initials, Kr., swapped order, caps, a typo."""
    parts = name.split()
    if len(parts) < 2 or style == "same":
        return name
    if style == "initial":
        return f"{parts[0][0]}. {' '.join(parts[1:])}"
    if style == "kumar":
        return " ".join("Kr." if p == "Kumar" else p for p in parts) if "Kumar" in parts \
            else f"{parts[0]} Kumar {' '.join(parts[1:])}"
    if style == "swap":
        return f"{parts[-1]} {' '.join(parts[:-1])}"
    if style == "upper":
        return name.upper()
    if style == "typo":  # drop one inner letter of the surname, e.g. Sharma -> Shrma
        last = parts[-1]
        cut = 1 + len(last) // 2 if len(last) > 3 else 1
        return " ".join(parts[:-1] + [last[:cut] + last[cut + 1:]])
    raise ValueError(style)


def pick_style(rng: Random) -> str:
    return rng.choices(["same", "initial", "kumar", "swap", "upper", "typo"], [55, 10, 8, 8, 9, 10])[0]


_ABBREV = [("H.No.", "House No."), ("Road", "Rd"), ("Street", "St."), ("Nagar", "Ngr"), ("Sector", "Sec"),
           ("Chowk", "Chk")]


def address_variant(address: str, city: str, rng: Random) -> str:
    text = re.sub(r"\s*\n\s*", ", ", address.strip())
    for long, short in _ABBREV:
        if long in text and rng.random() < 0.6:
            text = text.replace(long, short)
    return f"{text}, {city}" if city else text
