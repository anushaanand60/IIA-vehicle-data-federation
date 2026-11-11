"""CONTEXT-ADDED (R1): generate the phase-3 tables from the existing phase 1-2 CSVs, then plant scenarios.

    python scripts/phase3_generate.py            # writes <src>_<table>.csv at repo root + data/phase3_scenarios.json
    python scripts/phase3_generate.py --seed 7   # another deterministic world (tests use the default)

Reads reg_*.csv, ins_*.csv, theft_crime_records.csv, cam_*.csv and puc_records.csv and NEVER writes them:
phase 1-2 ground truth and the existing tests depend on those rows. Only new tables are written, so the
script is idempotent (same seed -> byte-identical files). Then load: python scripts/load_source.py <SRC>.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from random import Random
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.phase3_common import (FIRST, LAST, NEW_TABLES, REF_TODAY, STORY_PLATES, STREETS,  # noqa: E402
                                   address_variant, canon, csv_name, dmy, epoch_ist, ist_date, iso_ts,
                                   name_variant, new_mobile, ocr_fold, parse_dmy, pick_style, pincode,
                                   read_csv, write_csv)

SEED = 20261111
WMI = {"Maruti Suzuki": "MA3", "Hyundai": "MAL", "Hyundia": "MAL", "Tata": "MAT", "Mahindra": "MA1",
       "Kia": "MZB", "Honda": "MAK", "Toyota": "MBJ"}
ENGINE = {"Maruti Suzuki": "K12N", "Hyundai": "G4FG", "Tata": "RVTRN", "Mahindra": "MHWK", "Kia": "G4LA",
          "Honda": "L15Z", "Toyota": "2GDF"}
VIN_YEAR = dict(zip(range(2010, 2026), "ABCDEFGHJKLMNPRS"))
VIN_CHARS = "ABCDEFGHJKLMNPRSTUVWXYZ0123456789"  # no I, O, Q in a VIN
STATIONS = {"Connaught Place PS": ("New Delhi", "NEW DELHI"), "Karol Bagh PS": ("Central Delhi", "CENTRAL"),
            "Civil Lines PS": ("North Delhi", "NORTH"), "Lajpat Nagar PS": ("South East Delhi", "SOUTH-EAST"),
            "Saket PS": ("South Delhi", "SOUTH"), "Vasant Kunj North PS": ("South West Delhi", "SOUTH-WEST"),
            "MG Road PS": ("Gurugram", "GURUGRAM-EAST"), "Sector 14 PS": ("Gurugram", "GURUGRAM-WEST"),
            "DLF Phase 3 PS": ("Gurugram", "GURUGRAM-EAST"), "Sector 20 PS Noida": ("Gautam Buddh Nagar", "NOIDA"),
            "Sector 39 PS Noida": ("Gautam Buddh Nagar", "NOIDA"), "Bandra PS": ("Mumbai Suburban", "MUMBAI-WEST"),
            "Andheri PS": ("Mumbai Suburban", "MUMBAI-WEST"), "Koramangala PS": ("Bengaluru Urban", "BLR-SOUTH-EAST"),
            "Indiranagar PS": ("Bengaluru Urban", "BLR-EAST")}
ZONES = [(1, "Central Delhi", "Delhi"), (2, "Old Delhi", "Delhi"), (3, "NH-48 Corridor", "Gurugram"),
         (4, "Cyber City", "Gurugram"), (5, "IFFCO Chowk - Sector 29", "Gurugram"),
         (6, "Yamuna Expressway", "Agra"), (7, "Jaipur Outer Ring", "Jaipur")]
CAMERA_ZONE = {"CAM001": (3, "NH-48 Delhi-Gurugram Expressway"), "CAM002": (1, "Outer Ring Road"),
               "CAM003": (4, "Cyber City Road"), "CAM004": (5, "Sector 29 Main Road"),
               "CAM005": (2, "Netaji Subhash Marg"), "CAM006": (6, "Yamuna Expressway"),
               "CAM007": (7, "NH-48 Jaipur Bypass")}
VIOLATIONS = {"OVER_SPEEDING": 2000, "RED_LIGHT_JUMP": 1000, "STOP_LINE_VIOLATION": 500, "NO_SEATBELT": 1000,
              "NO_PARKING": 500, "WRONG_SIDE_DRIVING": 5000, "MOBILE_WHILE_DRIVING": 5000}
CAMERA_VIOLATIONS = {"CAM001": ["OVER_SPEEDING"], "CAM006": ["OVER_SPEEDING"], "CAM007": ["OVER_SPEEDING"],
                     "CAM003": ["NO_PARKING", "WRONG_SIDE_DRIVING"]}
CENTRES = {"North Delhi Testing": ("Delhi", 28.704100, 77.102500), "South Delhi Center": ("Delhi", 28.524500, 77.206600),
           "Noida Inspection Point": ("Noida", 28.535500, 77.391000), "Gurugram Auto Hub": ("Gurugram", 28.459500, 77.026600)}
SURVEYORS = ["R.K. Malhotra", "S. Iyer", "Anand Surveyors", "P. Bhatia", "M. Qureshi", "Deshmukh & Co.",
             "V. Raghavan", "N. Sethi"]


@dataclass
class World:
    rng: Random
    owners: dict[int, dict[str, str]]
    vehicles: dict[str, dict[str, str]]          # canonical plate -> VEHICLE_REGISTRATION row
    policies: list[dict[str, str]]
    incidents: list[dict[str, str]]
    captures: list[dict[str, str]]
    cameras: list[dict[str, str]]
    certs: list[dict[str, str]]
    t: dict[str, list[dict[str, Any]]] = field(default_factory=dict)   # new table -> rows
    mobiles: set[str] = field(default_factory=set)
    reserved: set[str] = field(default_factory=set)                  # plates a scenario owns
    planted: set[tuple[str, Any]] = field(default_factory=set)       # (table, pk) rows a scenario relies on
    scenarios: list[dict[str, Any]] = field(default_factory=list)

    def next_id(self, table: str, pk: str) -> int:
        return max((int(r[pk]) for r in self.t[table]), default=0) + 1


def load_world(seed: int) -> World:
    vehicles = {canon(r["registration_no"]): r for r in read_csv("reg_vehicle_registration.csv")}
    return World(rng=Random(seed), owners={int(o["owner_id"]): o for o in read_csv("reg_owners.csv")},
                 vehicles=vehicles, policies=read_csv("ins_policy_records.csv"),
                 incidents=read_csv("theft_crime_records.csv"), captures=read_csv("cam_plate_captures.csv"),
                 cameras=read_csv("cam_cameras.csv"), certs=read_csv("puc_records.csv"))


def gen_reg(w: World) -> None:
    rng, ident, contacts, history = w.rng, [], [], []
    for plate, v in sorted(w.vehicles.items(), key=lambda kv: int(kv[1]["registration_id"])):
        rid, make, reg_on = int(v["registration_id"]), v["make"], date.fromisoformat(v["registered_on"])
        mfg = reg_on.year - (1 if reg_on.month <= 3 and rng.random() < 0.4 else 0)
        serial = (rid * 7919) % 900000 + 100000  # 7919 is prime, so serials never repeat
        vds = "".join(rng.choice(VIN_CHARS) for _ in range(5))
        chassis = (WMI.get(make, "MA6") + vds + rng.choice("0123456789X") + VIN_YEAR.get(mfg, "S")
                   + rng.choice("ABCDEFGHJKLMN") + str(serial))
        engine = ENGINE.get(make, "ENG") + f"{rng.randrange(10**7):07d}"
        ident.append({"registration_id": rid, "chassis_no": chassis, "engine_no": engine, "mfg_year": mfg})
    for oid, o in sorted(w.owners.items()):
        contacts.append({"owner_id": oid, "mobile": new_mobile(rng, w.mobiles), "pincode": pincode(o["city"])})
    stolen = {canon(i["vehicle_number"]) for i in w.incidents}
    owner_ids = sorted(w.owners)
    for plate, v in sorted(w.vehicles.items(), key=lambda kv: int(kv[1]["registration_id"])):
        if plate in STORY_PLATES or plate in stolen or rng.random() > 0.15:
            continue  # a transfer right before a theft is a planted scenario, never random noise
        reg_on, current = date.fromisoformat(v["registered_on"]), int(v["owner_id"])
        span = (date(2026, 8, 31) - reg_on).days - 90
        if span < 30:
            continue
        hops = 2 if rng.random() < 0.2 and span > 400 else 1
        days = sorted(rng.sample(range(90, 90 + span), hops))
        chain = [rng.choice([o for o in owner_ids if o != current]) for _ in range(hops)] + [current]
        for k, d in enumerate(days):
            history.append({"transfer_id": 0, "registration_id": int(v["registration_id"]),
                            "prev_owner_id": chain[k], "new_owner_id": chain[k + 1],
                            "transfer_date": (reg_on + timedelta(days=d)).isoformat(),
                            "transfer_type": rng.choices(["SALE", "INHERITANCE", "AUCTION"], [85, 10, 5])[0]})
    for k, row in enumerate(history, 1):
        row["transfer_id"] = k
    w.t.update(VEHICLE_IDENTITY=ident, OWNER_CONTACTS=contacts, OWNERSHIP_HISTORY=history)


def gen_ins(w: World) -> None:
    rng, holders, parties, claims = w.rng, [], [], []
    chassis = {r["registration_id"]: r["chassis_no"] for r in w.t["VEHICLE_IDENTITY"]}
    mobile = {r["owner_id"]: r["mobile"] for r in w.t["OWNER_CONTACTS"]}
    holder_of: dict[str, int] = {}
    for p in w.policies:
        plate = canon(p["vehicle_reg"])
        v = w.vehicles[plate]
        if plate not in holder_of:  # the insurer's own record of the person, typed by its own clerk
            o = w.owners[int(v["owner_id"])]
            holder_of[plate] = len(holders) + 1
            same_pin = rng.random() < 0.92
            holders.append({"holder_id": holder_of[plate], "holder_name": name_variant(o["full_name"], pick_style(rng)),
                            "mobile": mobile[int(v["owner_id"])] if rng.random() < 0.85 else new_mobile(rng, w.mobiles),
                            "address": address_variant(o["address_line"], o["city"], rng),
                            "pincode": pincode(o["city"]) if same_pin else pincode(o["city"] + "x")})
        parties.append({"policy_id": int(p["policy_id"]), "holder_id": holder_of[plate],
                        "chassis_no": chassis[int(v["registration_id"])]})
    for p in w.policies:  # ordinary damage claims, well after the policy started
        start, until = parse_dmy(p["policy_start"]), min(parse_dmy(p["policy_until"]), REF_TODAY)
        if canon(p["vehicle_reg"]) in STORY_PLATES or (until - start).days < 60 or rng.random() > 0.08:
            continue
        loss = start + timedelta(days=rng.randrange(30, (until - start).days))
        kind = rng.choices(["OWN_DAMAGE", "THIRD_PARTY"], [70, 30])[0]
        age = (REF_TODAY - loss).days
        status = rng.choices(["SETTLED", "REJECTED", "OPEN"], [60, 10, 30])[0] if age > 45 else "OPEN"
        amount = rng.uniform(15000, 150000) if kind == "OWN_DAMAGE" else rng.uniform(50000, 500000)
        claims.append(claim_row(p, loss, loss + timedelta(days=rng.randrange(0, 5)), kind, amount, status, rng))
    for i in w.incidents:  # theft claims follow the FIR: loss a day or two before, intimation after it
        plate = canon(i["vehicle_number"])
        if i["incident_type"] != "THEFT" or plate in STORY_PLATES:
            continue
        fir = ist_date(int(i["reported_date"]))
        loss = fir - timedelta(days=rng.randrange(0, 3))
        policy = covering_policy(w, plate, loss)
        if policy is None or (loss - parse_dmy(policy["policy_start"])).days <= 15:
            continue  # uninsured on the day, or it would look like a planted quick claim
        intimation = min(fir + timedelta(days=rng.randrange(1, 7)), REF_TODAY)
        status = ("SETTLED" if i["recovered_flag"] == "N" else "REJECTED") if i["case_status"] == "CLOSED" \
            else rng.choice(["OPEN", "INVESTIGATING"])
        claims.append(claim_row(policy, loss, intimation, "THEFT", rng.uniform(300000, 1200000), status, rng))
    claims.sort(key=lambda c: (parse_dmy(c["loss_date"]), c["policy_id"]))
    for k, c in enumerate(claims, 1):
        c["claim_id"] = k
    w.t.update(POLICY_HOLDERS=holders, POLICY_PARTIES=parties, CLAIMS=claims)


def claim_row(p: dict[str, str], loss: date, intimation: date, kind: str, amount: float, status: str,
              rng: Random) -> dict[str, Any]:
    return {"claim_id": 0, "policy_id": int(p["policy_id"]), "loss_date": dmy(loss), "intimation_date": dmy(intimation),
            "loss_type": kind, "amount_claimed": f"{amount:.2f}", "claim_status": status,
            "surveyor_name": rng.choice(SURVEYORS)}


def covering_policy(w: World, plate: str, day: date) -> dict[str, str] | None:
    hits = [p for p in w.policies if canon(p["vehicle_reg"]) == plate
            and parse_dmy(p["policy_start"]) <= day <= parse_dmy(p["policy_until"])]
    return hits[-1] if hits else None


def gen_theft(w: World) -> None:
    rng, prop, persons, links = w.rng, [], [], []
    ident = {r["registration_id"]: r for r in w.t["VEHICLE_IDENTITY"]}
    mobile = {r["owner_id"]: r["mobile"] for r in w.t["OWNER_CONTACTS"]}
    for i in w.incidents:
        v = w.vehicles[canon(i["vehicle_number"])]
        vi, reported = ident[int(v["registration_id"])], int(i["reported_date"])
        recovered = None
        if i["recovered_flag"] == "Y":
            day = min(ist_date(reported) + timedelta(days=rng.randrange(3, 46)), REF_TODAY)
            recovered = epoch_ist(day)
        prop.append({"incident_id": int(i["incident_id"]), "chassis_no": vi["chassis_no"],
                     "engine_no": vi["engine_no"], "recovered_on": recovered})
        o = w.owners[int(v["owner_id"])]  # the complainant is the owner, as the police typed them
        persons.append({"person_id": len(persons) + 1, "name": name_variant(o["full_name"], pick_style(rng)),
                        "mobile": mobile[int(v["owner_id"])] if rng.random() < 0.9 else new_mobile(rng, w.mobiles),
                        "address": address_variant(o["address_line"], o["city"], rng)})
        links.append({"incident_id": int(i["incident_id"]), "person_id": len(persons), "role": "COMPLAINANT"})
        for role, p in (("ACCUSED", 0.25 if i["case_status"] == "OPEN" else 0.6), ("WITNESS", 0.2)):
            if rng.random() < p:
                persons.append(stranger(w, len(persons) + 1))
                links.append({"incident_id": int(i["incident_id"]), "person_id": len(persons), "role": role})
    stations = sorted({i["police_station"] for i in w.incidents} | set(STATIONS))
    w.t.update(STATIONS=[{"station_name": s, "district": STATIONS.get(s, ("Unknown", "UNASSIGNED"))[0],
                          "zone": STATIONS.get(s, ("Unknown", "UNASSIGNED"))[1]} for s in stations],
               STOLEN_PROPERTY=prop, PERSONS=persons, INCIDENT_PERSONS=links)


def stranger(w: World, person_id: int) -> dict[str, Any]:
    rng = w.rng
    return {"person_id": person_id, "name": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
            "mobile": new_mobile(rng, w.mobiles),
            "address": f"{rng.randrange(1, 400)}, {rng.choice(STREETS)}, {rng.choice(['Delhi', 'Gurugram', 'Noida', 'Faridabad'])}"}


def gen_cam(w: World) -> None:
    rng, challans = w.rng, []
    for c in w.captures:
        if ocr_fold(c["plate_id"]) in STORY_PLATES or rng.random() > 0.10:
            continue
        violation = rng.choice(CAMERA_VIOLATIONS.get(c["camera_id"], sorted(VIOLATIONS)))
        issued = date.fromisoformat(c["captured_at"][:10])
        at = (datetime.fromisoformat(c["captured_at"]) + timedelta(minutes=rng.randrange(2, 40))).isoformat()
        challans.append({"challan_id": len(challans) + 1, "capture_id": int(c["capture_id"]), "violation": violation,
                         "fine_inr": VIOLATIONS[violation], "issued_at": at,
                         "challan_status": rng.choices(["PENDING", "PAID", "COURT"], [50, 40, 10])[0]
                         if (REF_TODAY - issued).days > 3 else "PENDING"})
    w.t.update(ZONES=[{"zone_id": z, "zone_name": n, "city": c} for z, n, c in ZONES],
               CAMERA_ZONES=[{"camera_id": cam["camera_id"], "zone_id": CAMERA_ZONE.get(cam["camera_id"], (1, ""))[0],
                              "road": CAMERA_ZONE.get(cam["camera_id"], (1, cam["location_name"]))[1]}
                             for cam in w.cameras],
               CHALLANS=challans)


def gen_puc(w: World) -> None:
    rng, tests = w.rng, []
    names = sorted({c["tested_at"] for c in w.certs})
    centres = [{"centre_id": k, "centre_name": n, "city": CENTRES.get(n, ("Delhi", 28.6, 77.2))[0],
                "lat": CENTRES.get(n, ("", 28.6, 77.2))[1], "lon": CENTRES.get(n, ("", 28.6, 77.2))[2]}
               for k, n in enumerate(names, 1)]
    analysers = [{"analyser_id": 2 * (c["centre_id"] - 1) + j, "centre_id": c["centre_id"],
                  "calibrated_on": date(2026, 1 + rng.randrange(6), 1 + rng.randrange(28)).isoformat()}
                 for c in centres for j in (1, 2)]
    centre_of = {c["centre_name"]: c["centre_id"] for c in centres}
    for cert in sorted(w.certs, key=lambda c: c["cert_no"]):
        day = date.fromisoformat(cert["valid_upto"]) - timedelta(days=365)  # one-year certificate by default
        analyser = 2 * (centre_of[cert["tested_at"]] - 1) + rng.choice((1, 2))
        if rng.random() < 0.08:  # failed first, passed after a tune-up a few days later
            tests.append(cert_test(cert, analyser, day - timedelta(days=rng.randrange(1, 6)), "FAIL", rng))
        tests.append(cert_test(cert, analyser, day, "PASS", rng))
    for k, t in enumerate(tests, 1):
        t["test_id"] = k
    w.t.update(TEST_CENTRES=centres, ANALYSERS=analysers, CERT_TESTS=tests)


def cert_test(cert: dict[str, str], analyser: int, day: date, result: str, rng: Random) -> dict[str, Any]:
    co, hc = (rng.uniform(0.05, 0.30), rng.randrange(20, 150)) if result == "PASS" \
        else (rng.uniform(0.45, 1.20), rng.randrange(220, 450))
    return {"test_id": 0, "cert_no": cert["cert_no"], "analyser_id": analyser,
            "test_ts": iso_ts(day, rng.randrange(9, 18), rng.randrange(0, 60)), "co_pct": f"{co:.2f}",
            "hc_ppm": hc, "result": result}


def write_all(w: World) -> dict[str, int]:
    counts = {}
    for source_id, tables in NEW_TABLES.items():
        for table, columns in tables:
            write_csv(ROOT / csv_name(source_id, table), columns, w.t[table])
            counts[f"{source_id}.{table}"] = len(w.t[table])
    (ROOT / "data" / "phase3_scenarios.json").write_text(json.dumps(w.scenarios, indent=1), encoding="utf-8")
    return counts


def generate(seed: int = SEED) -> World:
    from scripts.phase3_plant import add_noise, plant_all, sanity_pass
    w = load_world(seed)
    gen_reg(w), gen_ins(w), gen_theft(w), gen_cam(w), gen_puc(w)
    plant_all(w)       # scenarios first, so the noise below can steer clear of them
    add_noise(w)
    sanity_pass(w)     # no accidental alibis or quick claims outside the planted ones
    return w


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args(argv)
    w = generate(args.seed)
    for name, n in write_all(w).items():
        print(f"  {name:28s}{n:6d} row(s)")
    print(f"  {len(w.scenarios)} planted scenario(s) -> data/phase3_scenarios.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
