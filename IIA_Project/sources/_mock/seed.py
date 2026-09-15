"""Deterministic mock data: ~40 vehicles over the design PDF's schemas (§3), one SQLite file per agency.

The six story vehicles (CLAUDE.md §9, design PDF §4) are written by hand; the rest is seeded filler.
PUC is the fifth agency for rehearsing the add-a-source demo (design PDF §8.3, UC6).
"""
from __future__ import annotations

import random
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SOURCES = ("REG", "INS", "THEFT", "CAM", "PUC")
STORY_PLATES = ("DL01AB1234", "DL05CD9876", "DL09KL3321", "HR26EF4455", "UP16GH1122", "MH12IJ7788")
FILLER_VEHICLES = 34
MISREAD_RATE = 0.05  # design PDF §3.4: about 5% of captures misread
OCR_SWAPS = {"0": "O", "O": "0", "1": "I", "I": "1"}  # mirrors CAM's registry variant_map
IST = timezone(timedelta(hours=5, minutes=30))

MAKES = [("Maruti Suzuki", ["Baleno", "Dzire", "Brezza", "Ertiga"]), ("Hyundai", ["i20", "Verna", "Venue", "Creta"]),
         ("Tata", ["Punch", "Nexon", "Harrier"]), ("Mahindra", ["XUV700", "Scorpio", "Thar"]),
         ("Honda", ["Amaze", "City"]), ("Toyota", ["Innova", "Glanza"]), ("Kia", ["Seltos", "Sonet"])]
COLOURS = ["White", "Silver", "Grey", "Black", "Red", "Blue"]
FUELS = ["PETROL", "DIESEL", "CNG", "EV"]
FIRST = ["Ananya", "Kabir", "Ishaan", "Diya", "Arjun", "Meera", "Aditya", "Sara", "Karan", "Nisha", "Rahul", "Tanvi"]
LAST = ["Iyer", "Reddy", "Patel", "Kapoor", "Nair", "Joshi", "Malhotra", "Chopra", "Bose", "Rao", "Khan", "Das"]
STREETS = ["MG Road", "Nehru Place", "Civil Lines", "Sector 15", "Model Town", "Park Street", "Station Road"]
CITIES = {"DL": "New Delhi", "HR": "Gurugram", "UP": "Noida", "MH": "Pune", "KA": "Bengaluru", "RJ": "Jaipur"}
INSURERS = ["ICICI Lombard", "HDFC ERGO", "Bajaj Allianz", "Tata AIG", "New India Assurance", "Digit"]
POLICE_STATIONS = ["Lajpat Nagar, Delhi", "Sector 20, Noida", "DLF Phase 3, Gurugram", "Shivajinagar, Pune",
                   "Koramangala, Bengaluru", "Malviya Nagar, Jaipur"]
CAMERAS = [  # camera_id, location_name, lat, lon, state
    ("CAM-DL01", "Ring Road, Lajpat Nagar, Delhi", 28.5677, 77.2433, "DL"), ("CAM-DL02", "ITO Crossing, Delhi", 28.6289, 77.241, "DL"),
    ("CAM-DL03", "Mathura Road, Delhi", 28.5805, 77.2526, "DL"), ("CAM-HR01", "Sohna Road, Gurugram", 28.411, 77.0425, "HR"),
    ("CAM-HR02", "NH-48 Toll Plaza, Gurugram", 28.4764, 77.0635, "HR"), ("CAM-HR03", "Kundli Border, Sonipat", 28.8736, 77.108, "HR"),
    ("CAM-UP01", "Sector 18, Noida", 28.57, 77.3219, "UP"), ("CAM-UP02", "DND Flyway, Noida", 28.561, 77.293, "UP"),
    ("CAM-MH01", "Mumbai-Pune Expressway, Khalapur", 18.8263, 73.2811, "MH"), ("CAM-MH02", "Western Express Highway, Andheri", 19.1197, 72.8468, "MH"),
    ("CAM-KA01", "Silk Board Junction, Bengaluru", 12.9174, 77.6225, "KA"), ("CAM-RJ01", "Tonk Road, Jaipur", 26.8545, 75.8034, "RJ"),
]
CAMERAS_BY_STATE = {state: [c[0] for c in CAMERAS if c[4] == state] for state in CITIES}
LETTERS = "ABCDEFGHJKLMNPRSTUVWXYZ"  # series letters avoid I and O


def ins_plate(p: str) -> str:
    return f"{p[:2]}-{p[2:4]}-{p[4:6]}-{p[6:]}"


def theft_plate(p: str) -> str:
    return f"{p[:2]} {p[2:4]} {p[4:6]} {p[6:]}".lower()


def puc_plate(p: str) -> str:
    return f"{p[:2]} {p[2:4]} {p[4:6]} {p[6:]}"


def dmy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def epoch(local: datetime) -> int:
    return int(local.replace(tzinfo=IST).timestamp())


def stamp(local: datetime) -> str:
    return local.strftime("%Y-%m-%d %H:%M:%S+05:30")  # how PostgreSQL prints a TIMESTAMPTZ in IST


def build_all(target_dir: str | Path = HERE) -> dict[str, Path]:
    """(Re)build every mock database from schema_<source>.sql plus generated rows."""
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)
    data, paths = generate(), {}
    for source_id in SOURCES:
        path = paths[source_id] = target / f"{source_id.lower()}.db"
        path.unlink(missing_ok=True)
        with closing(sqlite3.connect(path)) as con:
            con.executescript((HERE / f"schema_{source_id.lower()}.sql").read_text(encoding="utf-8"))
            for table, rows in data[source_id].items():
                con.executemany(f"INSERT INTO {table} VALUES ({', '.join('?' * len(rows[0]))})", rows)
            con.commit()
    return paths


def generate(seed: int = 2026) -> dict[str, dict[str, list[tuple[Any, ...]]]]:
    data = {"REG": {"OWNERS": [], "VEHICLE_REGISTRATION": []},
            "INS": {"INSURERS": list(enumerate(INSURERS, 1)), "POLICY_RECORDS": []},
            "THEFT": {"CRIME_RECORDS": []},
            "CAM": {"CAMERAS": [c[:4] for c in CAMERAS], "PLATE_CAPTURES": []},
            "PUC": {"POLLUTION_CERT": []}}
    _story(data)
    _filler(data, random.Random(seed))
    return data


def _story(data: dict) -> None:
    owners, vehicles = data["REG"]["OWNERS"], data["REG"]["VEHICLE_REGISTRATION"]
    for plate, owner, address, city, make, model, colour, fuel, registered in [
            ("DL01AB1234", "Aarav Sharma", "14 Lajpat Nagar II", "New Delhi", "Maruti Suzuki", "Swift", "Red", "PETROL", "2019-03-14"),
            ("DL05CD9876", "Priya Mehta", "B-7 Vasant Kunj", "New Delhi", "Honda", "City", "Silver", "PETROL", "2018-07-12"),
            ("DL09KL3321", "Rohan Verma", "221 Mayur Vihar Phase 1", "New Delhi", "Tata", "Nexon", "Blue", "DIESEL", "2021-11-02"),
            ("HR26EF4455", "Neha Gupta", "U-12 DLF Phase 3", "Gurugram", "Toyota", "Fortuner", "White", "DIESEL", "2022-01-20"),
            ("UP16GH1122", "Vikram Singh", "C-45 Sector 50", "Noida", "Hyundai", "Creta", "White", "PETROL", "2020-05-09")]:
        owners.append((len(owners) + 1, owner, address, city))  # MH12IJ7788 is deliberately never registered
        vehicles.append((len(vehicles) + 1, plate, len(owners), make, model, colour, fuel, registered, "ACTIVE", plate[:4]))

    policies = data["INS"]["POLICY_RECORDS"]
    for plate, insurer_id, policy_type, start, until, active, premium in [
            ("DL01AB1234", 1, "COMPREHENSIVE", "14/03/2025", "13/03/2026", 0, 8450.00),
            ("DL01AB1234", 1, "COMPREHENSIVE", "14/03/2026", "13/03/2027", 1, 8920.00),
            # Renewed a week early, then lapsed: "19/07/2025" sorts after "12/07/2026" as text.
            ("DL05CD9876", 2, "COMPREHENSIVE", "20/07/2024", "19/07/2025", 0, 11200.00),
            ("DL05CD9876", 2, "THIRD_PARTY", "13/07/2025", "12/07/2026", 0, 3100.00),
            ("HR26EF4455", 3, "COMPREHENSIVE", "20/01/2026", "19/01/2027", 1, 24500.00),
            ("UP16GH1122", 5, "COMPREHENSIVE", "09/05/2026", "08/05/2027", 1, 13400.00)]:
        policies.append((len(policies) + 1, ins_plate(plate), insurer_id, policy_type, start, until, active, premium))

    data["THEFT"]["CRIME_RECORDS"].append((1, "hr 26 ef 4455", "FIR-1893/2026", epoch(datetime(2026, 8, 28, 22, 40)),
                                           "THEFT", "Y", "N", "OPEN", "DLF Phase 3, Gurugram"))

    captures = data["CAM"]["PLATE_CAPTURES"]
    for plate_id, camera, seen, make, model, colour, confidence in [
            ("DLOIAB1234", "CAM-DL01", datetime(2026, 8, 30, 21, 14, 5), "MARUTI SUZUKI", "SWIFT", "red", 0.612),
            ("DL01AB1234", "CAM-HR02", datetime(2026, 9, 2, 8, 3, 51), "MARUTI SUZUKI", "SWIFT", "red", 0.954),
            ("DL05CD9876", "CAM-DL02", datetime(2026, 9, 5, 18, 22, 10), "HONDA", "CITY", "silver", 0.931),
            ("DLO9KL3321", "CAM-DL03", datetime(2026, 9, 6, 7, 45, 33), "TATA", "NEXON", "blue", 0.688),
            ("HR26EF4455", "CAM-HR01", datetime(2026, 9, 7, 23, 10, 2), "TOYOTA", "FORTUNER", "white", 0.902),
            ("HR26EF4455", "CAM-HR03", datetime(2026, 9, 8, 2, 31, 47), "TOYOTA", "FORTUNER", "white", 0.604),
            # Registered as a white Creta; the cameras keep seeing a silver Venue: possible cloned plate.
            ("UP16GH1122", "CAM-UP01", datetime(2026, 9, 4, 13, 55, 20), "HYUNDAI", "VENUE", "silver", 0.917),
            ("UPI6GH1122", "CAM-UP02", datetime(2026, 9, 9, 9, 12, 44), "HYUNDAI", "VENUE", "silver", 0.745),
            ("MH12IJ7788", "CAM-MH01", datetime(2026, 9, 3, 16, 40, 9), "MAHINDRA", "SCORPIO", "black", 0.889),
            ("MH121J7788", "CAM-MH02", datetime(2026, 9, 10, 19, 5, 31), "MAHINDRA", "SCORPIO", "black", 0.662)]:
        captures.append((len(captures) + 1, plate_id, camera, stamp(seen), make, model, colour, confidence))

    certs = data["PUC"]["POLLUTION_CERT"]
    for plate, valid in [("DL01AB1234", "2027-02-28"), ("DL05CD9876", "2026-06-30"),
                         ("HR26EF4455", "2026-12-15"), ("UP16GH1122", "2027-01-10")]:
        certs.append((f"PUC-{len(certs) + 1:05d}", puc_plate(plate), valid))


def _filler(data: dict, rng: random.Random) -> None:
    used = set(STORY_PLATES)
    owners, vehicles = data["REG"]["OWNERS"], data["REG"]["VEHICLE_REGISTRATION"]
    for i in range(FILLER_VEHICLES):
        state = rng.choice(sorted(CITIES))
        plate = _fresh_plate(rng, state, used)
        make, models = MAKES[1] if i == 21 else rng.choice(MAKES)
        model, colour = rng.choice(models), rng.choice(COLOURS)
        if i < 30:
            owners.append((len(owners) + 1, f"{rng.choice(FIRST)} {rng.choice(LAST)}",
                           f"{rng.randint(1, 250)} {rng.choice(STREETS)}", CITIES[state]))
        owner_id = len(owners) if i < 30 else 6 + (i - 30)  # the last four share owners: OWNERS is one-to-many
        registered = date(2012, 1, 1) + timedelta(days=rng.randrange(365 * 13))
        status = {0: "SUSPENDED", 1: "CANCELLED", 13: "CANCELLED"}.get(i) or rng.choices(
            ["ACTIVE", "SUSPENDED", "CANCELLED"], weights=[90, 5, 5])[0]
        reg_make = {20: None, 21: "Hyundia"}.get(i, make)  # dirty rows for the cleaning stage (design PDF §4)
        vehicles.append((len(vehicles) + 1, plate, owner_id, reg_make, model, colour, rng.choice(FUELS),
                         registered.isoformat(), status, plate[:4]))
        if i % 10 != 7:  # every tenth filler vehicle is uninsured
            _policies(data, rng, plate, lapsed=i % 6 == 3, duplicate=i == 22)
        if i % 9 == 4:
            _incident(data, rng, plate, "THEFT", stolen="Y", recovered="Y", status="CLOSED")
        if i == 13:
            _incident(data, rng, plate, "SHREDDING", stolen="N", recovered="N", status="CLOSED")
        if i == 30:
            _incident(data, rng, plate, "HIT_AND_RUN", stolen="N", recovered="N", status="OPEN")
        if i != 13 and i % 8 != 5:  # shredded vehicles, and some others, never pass a camera
            _sightings(data, rng, plate, state, make, model, colour)
        if rng.random() < 0.8:
            certs = data["PUC"]["POLLUTION_CERT"]
            valid = date(2026, 1, 1) + timedelta(days=rng.randrange(-200, 500))
            certs.append((f"PUC-{len(certs) + 1:05d}", puc_plate(plate), valid.isoformat()))


def _fresh_plate(rng: random.Random, state: str, used: set[str]) -> str:
    while True:
        plate = f"{state}{rng.randint(1, 99):02d}{rng.choice(LETTERS)}{rng.choice(LETTERS)}{rng.randint(1000, 9999)}"
        if plate not in used:
            used.add(plate)
            return plate


def _policies(data: dict, rng: random.Random, plate: str, lapsed: bool, duplicate: bool) -> None:
    policies = data["INS"]["POLICY_RECORDS"]
    offset = -rng.randrange(30, 400) if lapsed else rng.randrange(20, 300)
    end, insurer_id, years = date(2026, 10, 1) + timedelta(days=offset), rng.randint(1, len(INSURERS)), rng.randint(1, 3)
    for k in range(years):  # consecutive yearly policies, oldest first
        until = end - timedelta(days=365 * (years - 1 - k))
        policies.append((len(policies) + 1, ins_plate(plate), insurer_id, rng.choice(["COMPREHENSIVE", "THIRD_PARTY"]),
                         dmy(until - timedelta(days=364)), dmy(until), int(k == years - 1 and not lapsed),
                         round(rng.uniform(2500, 30000), 2)))
    if duplicate:  # the same policy keyed in twice under a new id
        policies.append((len(policies) + 1, *policies[-1][1:]))


def _incident(data: dict, rng: random.Random, plate: str, kind: str, stolen: str, recovered: str, status: str) -> None:
    records = data["THEFT"]["CRIME_RECORDS"]
    when = datetime(2023, 1, 1) + timedelta(minutes=rng.randrange(60 * 24 * 1300))
    records.append((len(records) + 1, theft_plate(plate), f"FIR-{rng.randint(100, 9999)}/{when.year}", epoch(when),
                    kind, stolen, recovered, status, rng.choice(POLICE_STATIONS)))


def _sightings(data: dict, rng: random.Random, plate: str, state: str, make: str, model: str, colour: str) -> None:
    captures = data["CAM"]["PLATE_CAPTURES"]
    for _ in range(rng.randint(1, 4)):
        seen = datetime(2026, 8, 1) + timedelta(seconds=rng.randrange(40 * 86400))
        misread = rng.random() < MISREAD_RATE
        confidence = round(rng.uniform(0.55, 0.78) if misread else rng.uniform(0.80, 0.99), 3)
        seen_colour = None if rng.random() < 0.08 else colour.lower()  # night-time: colour unreadable
        captures.append((len(captures) + 1, _misread(plate, rng) if misread else plate,
                         rng.choice(CAMERAS_BY_STATE[state]), stamp(seen), make.upper(), model.upper(), seen_colour, confidence))


def _misread(plate: str, rng: random.Random) -> str:
    spots = [k for k, ch in enumerate(plate) if ch in OCR_SWAPS]
    if not spots:
        return plate
    k = rng.choice(spots)
    return plate[:k] + OCR_SWAPS[plate[k]] + plate[k + 1:]
