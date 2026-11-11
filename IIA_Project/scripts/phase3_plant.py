"""CONTEXT-ADDED (R1): plant the phase-3 scenarios into the generated tables (called by phase3_generate.py).

Scenarios reuse EXISTING vehicles, FIRs, certificates and captures and only add or edit rows of the NEW
tables, so phase 1-2 behaviour is untouched. Each planted row is recorded in w.planted so the sanity pass
never deletes it, and every scenario lists its evidence rows as [source, table, pk] for the tests and the
evaluation. Kinds: ALIBI, QUICK_CLAIM, TRANSFER_BEFORE_LOSS, PERSON_RING, CLONE, HARD_NEGATIVE.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

from scripts.phase3_common import (REF_TODAY, STORY_PLATES, address_variant, canon, epoch_ist, ist_date,
                                   iso_ts, name_variant, new_mobile, ocr_fold, parse_dmy, pincode)
from scripts.phase3_generate import World, cert_test, claim_row, covering_policy

Ev = list[Any]


def add(w: World, sid: str, kind: str, plates: list[str], evidence: list[Ev], explanation: str,
        indicators: list[str], persons: list[Ev] | None = None) -> None:
    chassis = sorted({r["chassis_no"] for r in w.t["VEHICLE_IDENTITY"]
                      if r["registration_id"] in {int(w.vehicles[p]["registration_id"]) for p in plates if p in w.vehicles}})
    w.reserved.update(plates)
    w.scenarios.append({"id": sid, "kind": kind, "plates": plates, "chassis": chassis, "persons": persons or [],
                        "expected_indicators": indicators, "evidence": evidence, "explanation": explanation})


def fir_day(i: dict[str, str]) -> date:
    return ist_date(int(i["reported_date"]))


def free_thefts(w: World, status: str = "OPEN") -> list[dict[str, str]]:
    return [i for i in w.incidents if i["incident_type"] == "THEFT" and i["case_status"] == status
            and canon(i["vehicle_number"]) not in STORY_PLATES | w.reserved]


def complainant(w: World, incident_id: int) -> Ev:
    pid = next(l["person_id"] for l in w.t["INCIDENT_PERSONS"] if l["incident_id"] == incident_id and l["role"] == "COMPLAINANT")
    return ["THEFT", "PERSONS", pid]


def captures_of(w: World, plate: str) -> list[dict[str, str]]:
    return sorted((c for c in w.captures if ocr_fold(c["plate_id"]) == plate), key=lambda c: c["captured_at"])


def new_challan(w: World, capture: dict[str, str], violation: str, fine: int, status: str, minutes: int) -> int:
    w.t["CHALLANS"] = [c for c in w.t["CHALLANS"] if c["capture_id"] != int(capture["capture_id"])]
    cid = w.next_id("CHALLANS", "challan_id")
    at = (datetime.fromisoformat(capture["captured_at"]) + timedelta(minutes=minutes)).isoformat()
    w.t["CHALLANS"].append({"challan_id": cid, "capture_id": int(capture["capture_id"]), "violation": violation,
                            "fine_inr": fine, "challan_status": status, "issued_at": at})
    w.planted.add(("CHALLANS", cid))
    return cid


def plant_alibi(w: World) -> None:
    n = 0
    for i in free_thefts(w):
        plate, fir = canon(i["vehicle_number"]), fir_day(i)
        cert = next((c for c in w.certs if canon(c["regn_number"]) == plate
                     and fir < date.fromisoformat(c["valid_upto"]) - timedelta(days=182) <= REF_TODAY), None)
        if cert is None or n == 3:
            continue
        n += 1  # a six-month certificate, so the issuing test falls inside the stolen interval
        w.t["CERT_TESTS"] = [t for t in w.t["CERT_TESTS"] if t["cert_no"] != cert["cert_no"]]
        test = cert_test(cert, 1 + w.rng.randrange(8), date.fromisoformat(cert["valid_upto"]) - timedelta(days=182), "PASS", w.rng)
        test["test_id"] = w.next_id("CERT_TESTS", "test_id")
        w.t["CERT_TESTS"].append(test)
        w.planted.add(("CERT_TESTS", test["test_id"]))
        ev: list[Ev] = [["THEFT", "CRIME_RECORDS", int(i["incident_id"])], ["PUC", "POLLUTION_CERT", cert["cert_no"]],
                        ["PUC", "CERT_TESTS", test["test_id"]]]
        codes, seen = ["ALIBI_PUC"], [c for c in captures_of(w, plate) if c["captured_at"][:10] > fir.isoformat()]
        if seen:  # the owner paid a camera challan for a car they had reported stolen
            cid = new_challan(w, seen[0], "RED_LIGHT_JUMP", 1000, "PAID", 12)
            ev += [["CAM", "PLATE_CAPTURES", int(seen[0]["capture_id"])], ["CAM", "CHALLANS", cid]]
            codes += ["ALIBI_CAMERA", "ALIBI_CHALLAN"]
        add(w, f"ALIBI-{n}", "ALIBI", [plate], ev, f"FIR {i['fir_no']} (open) says the car was stolen on {fir}, yet it "
            f"passed a PUC test on {test['test_ts'][:10]}" + (" and a challan was issued and paid" if seen else "")
            + " inside the stolen interval: a PUC test needs the car physically at the centre.", codes,
            [complainant(w, int(i["incident_id"]))])


def plant_quick_claims(w: World) -> None:
    n = 0
    for i in free_thefts(w):  # FIR-backed: loss within 15 days of cover, insurer told before the police
        plate, fir = canon(i["vehicle_number"]), fir_day(i)
        p = covering_policy(w, plate, fir)
        if p is None or not 2 <= (fir - parse_dmy(p["policy_start"])).days <= 16 or n == 2:
            continue
        n += 1
        loss = min(fir - timedelta(days=2), parse_dmy(p["policy_start"]) + timedelta(days=15))
        cid = put_claim(w, p, loss, fir - timedelta(days=1), "INVESTIGATING")
        add(w, f"QUICK_CLAIM-{n}", "QUICK_CLAIM", [plate],
            [["INS", "POLICY_RECORDS", int(p["policy_id"])], ["INS", "CLAIMS", cid], ["THEFT", "CRIME_RECORDS", int(i["incident_id"])]],
            f"Theft loss on {loss}, {(loss - parse_dmy(p['policy_start'])).days} days after the policy started; the insurer "
            f"was told on {fir - timedelta(days=1)}, a day before FIR {i['fir_no']} was filed.", ["QUICK_CLAIM"])
    theft_plates = {canon(i["vehicle_number"]) for i in w.incidents}
    pool = [p for p in w.policies if p["is_active"] == "1" and date(2026, 3, 1) <= parse_dmy(p["policy_start"]) <= date(2026, 8, 10)
            and canon(p["vehicle_reg"]) not in theft_plates | STORY_PLATES | w.reserved
            and sum(canon(q["vehicle_reg"]) == canon(p["vehicle_reg"]) for q in w.policies) == 1]
    for p in w.rng.sample(pool, 3 - n):  # no FIR at all: the claim stands on the insurer's word alone
        n += 1
        loss = parse_dmy(p["policy_start"]) + timedelta(days=w.rng.randrange(5, 13))
        cid = put_claim(w, p, loss, loss + timedelta(days=1), "INVESTIGATING")
        add(w, f"QUICK_CLAIM-{n}", "QUICK_CLAIM", [canon(p["vehicle_reg"])],
            [["INS", "POLICY_RECORDS", int(p["policy_id"])], ["INS", "CLAIMS", cid]],
            f"Theft claimed for a loss on {loss}, {(loss - parse_dmy(p['policy_start'])).days} days into a new policy, "
            "and the police have no FIR for this vehicle.", ["QUICK_CLAIM"])


def put_claim(w: World, p: dict[str, str], loss: date, intimation: date, status: str) -> int:
    w.t["CLAIMS"] = [c for c in w.t["CLAIMS"] if not (c["policy_id"] == int(p["policy_id"]) and c["loss_type"] == "THEFT")]
    row = claim_row(p, loss, intimation, "THEFT", w.rng.uniform(450000, 1400000), status, w.rng)
    row["claim_id"] = w.next_id("CLAIMS", "claim_id")
    w.t["CLAIMS"].append(row)
    w.planted.add(("CLAIMS", row["claim_id"]))
    return row["claim_id"]


def transfer(w: World, plate: str, day: date) -> int:
    v = w.vehicles[plate]
    taken = {int(w.vehicles[p]["owner_id"]) for p in w.reserved | STORY_PLATES if p in w.vehicles}
    prev = w.rng.choice([o for o in sorted(w.owners) if o not in taken and o != int(v["owner_id"])])
    tid = w.next_id("OWNERSHIP_HISTORY", "transfer_id")
    w.t["OWNERSHIP_HISTORY"].append({"transfer_id": tid, "registration_id": int(v["registration_id"]), "prev_owner_id": prev,
                                     "new_owner_id": int(v["owner_id"]), "transfer_date": day.isoformat(), "transfer_type": "SALE"})
    w.planted.add(("OWNERSHIP_HISTORY", tid))
    return tid


def plant_transfers(w: World) -> None:
    n = 0
    for i in free_thefts(w):
        plate, fir = canon(i["vehicle_number"]), fir_day(i)
        v = w.vehicles[plate]
        p = covering_policy(w, plate, fir - timedelta(days=1))
        if p is None or date.fromisoformat(v["registered_on"]) > fir - timedelta(days=400) or n == 3:
            continue
        n += 1
        day = fir - timedelta(days=w.rng.randrange(8, 26))
        tid = transfer(w, plate, day)
        claim = next((c for c in w.t["CLAIMS"] if c["policy_id"] == int(p["policy_id"]) and c["loss_type"] == "THEFT"), None)
        cid = claim["claim_id"] if claim else put_claim(w, p, fir - timedelta(days=1), fir + timedelta(days=2), "OPEN")
        w.planted.add(("CLAIMS", cid))
        add(w, f"TRANSFER-{n}", "TRANSFER_BEFORE_LOSS", [plate],
            [["REG", "OWNERSHIP_HISTORY", tid], ["THEFT", "CRIME_RECORDS", int(i["incident_id"])], ["INS", "CLAIMS", cid]],
            f"Sold to the current owner on {day}, {(fir - day).days} days before FIR {i['fir_no']} and the theft claim.",
            ["TRANSFER_BEFORE_LOSS"], [["REG", "OWNERS", int(v["owner_id"])], complainant(w, int(i["incident_id"]))])
    old = [i for i in free_thefts(w) if date.fromisoformat(w.vehicles[canon(i["vehicle_number"])]["registered_on"])
           < fir_day(i) - timedelta(days=500)]
    for k, i in enumerate(old[:1], 1):  # hard negative: a sale long before the loss is ordinary
        plate, fir = canon(i["vehicle_number"]), fir_day(i)
        tid = transfer(w, plate, fir - timedelta(days=w.rng.randrange(200, 380)))
        add(w, f"HN-TRANSFER-{k}", "HARD_NEGATIVE", [plate],
            [["REG", "OWNERSHIP_HISTORY", tid], ["THEFT", "CRIME_RECORDS", int(i["incident_id"])]],
            "A sale more than 200 days before the theft: TRANSFER_BEFORE_LOSS must not fire.", [])


def plant_rings(w: World) -> None:
    theft_plates = {canon(i["vehicle_number"]) for i in w.incidents}
    solo = [canon(p["vehicle_reg"]) for p in w.policies
            if sum(canon(q["vehicle_reg"]) == canon(p["vehicle_reg"]) for q in w.policies) == 1]
    clean = [p for p in sorted(set(solo)) if p not in theft_plates | STORY_PLATES | w.reserved]
    picks = w.rng.sample(clean, 4)
    for n, (hub, insured) in enumerate(zip(picks[:2], picks[2:]), 1):
        owner_id = int(w.vehicles[hub]["owner_id"])
        o, mobile = w.owners[owner_id], next(c["mobile"] for c in w.t["OWNER_CONTACTS"] if c["owner_id"] == owner_id)
        policy = next(p for p in w.policies if canon(p["vehicle_reg"]) == insured)
        party = next(r for r in w.t["POLICY_PARTIES"] if r["policy_id"] == int(policy["policy_id"]))
        holder = next(h for h in w.t["POLICY_HOLDERS"] if h["holder_id"] == party["holder_id"])
        holder.update(holder_name=name_variant(o["full_name"], "initial" if n == 1 else "swap"), mobile=mobile,
                      address=address_variant(o["address_line"], o["city"], w.rng), pincode=pincode(o["city"]))
        pid = max(p["person_id"] for p in w.t["PERSONS"]) + 1
        w.t["PERSONS"].append({"person_id": pid, "name": name_variant(o["full_name"], "kumar" if n == 1 else "upper"),
                               "mobile": mobile, "address": address_variant(o["address_line"], o["city"], w.rng)})
        firs = free_thefts(w)[n * 3: n * 3 + (2 if n == 1 else 1)]
        ev: list[Ev] = [["REG", "OWNER_CONTACTS", owner_id], ["INS", "POLICY_PARTIES", int(policy["policy_id"])]]
        for i in firs:
            w.t["INCIDENT_PERSONS"].append({"incident_id": int(i["incident_id"]), "person_id": pid, "role": "ACCUSED"})
            ev.append(["THEFT", "INCIDENT_PERSONS", [int(i["incident_id"]), pid, "ACCUSED"]])
        add(w, f"RING-{n}", "PERSON_RING", [hub, insured] + [canon(i["vehicle_number"]) for i in firs], ev,
            f"Mobile {mobile} belongs to {o['full_name']} (REG owner of {hub}), to policy holder '{holder['holder_name']}' "
            f"of {insured}, and to the accused in {len(firs)} FIR(s): one person behind several vehicles.", ["PERSON_RING"],
            [["REG", "OWNERS", owner_id], ["INS", "POLICY_HOLDERS", holder["holder_id"]], ["THEFT", "PERSONS", pid]])


def km(a: dict[str, str], b: dict[str, str]) -> float:
    la, lb, dl = math.radians(float(a["lat"])), math.radians(float(b["lat"])), math.radians(float(b["lon"]) - float(a["lon"]))
    return 6371 * math.acos(min(1.0, math.sin(la) * math.sin(lb) + math.cos(la) * math.cos(lb) * math.cos(dl)))


def plant_clones(w: World) -> None:
    cams, n = {c["camera_id"]: c for c in w.cameras}, 0
    for plate, v in sorted(w.vehicles.items()):
        caps = captures_of(w, plate)
        for a, b in zip(caps, caps[1:]):
            minutes = (datetime.fromisoformat(b["captured_at"]) - datetime.fromisoformat(a["captured_at"])).total_seconds() / 60
            dist = km(cams[a["camera_id"]], cams[b["camera_id"]])
            if a["camera_id"] == b["camera_id"] or minutes > 120 or dist / max(minutes, 1) * 60 < 150 \
                    or v["make"] in (a["observed_make"], b["observed_make"]) and v["model"] in (a["observed_model"], b["observed_model"]):
                continue
            n += 1  # the real owner gets a speeding challan from a road his car was never on
            cid = new_challan(w, b, "OVER_SPEEDING", 2000, "PENDING", 3)
            add(w, f"CLONE-{n}", "CLONE", [plate],
                [["REG", "VEHICLE_REGISTRATION", int(v["registration_id"])], ["CAM", "PLATE_CAPTURES", int(a["capture_id"])],
                 ["CAM", "PLATE_CAPTURES", int(b["capture_id"])], ["CAM", "CHALLANS", cid]],
                f"Seen at {cams[a['camera_id']]['location_name']} and {cams[b['camera_id']]['location_name']} {dist:.0f} km apart "
                f"within {minutes:.0f} min, as a {b['observed_make']} {b['observed_model']} although REG says {v['make']} {v['model']}.",
                ["CLONE"])
            break


def plant_hard_negatives(w: World) -> None:
    theft = {canon(i["vehicle_number"]): i for i in w.incidents}
    n = 0
    for i in free_thefts(w):
        plate = canon(i["vehicle_number"])
        near = next((f"{plate[:-1]}{(int(plate[-1]) + d) % 10}" for d in (1, 9)
                     if f"{plate[:-1]}{(int(plate[-1]) + d) % 10}" in w.vehicles), "")
        if near and near not in theft and near not in w.reserved | STORY_PLATES and n < 2:
            n += 1
            add(w, f"HN-PLATE-{n}", "HARD_NEGATIVE", [near, plate],
                [["REG", "VEHICLE_REGISTRATION", int(w.vehicles[near]["registration_id"])], ["THEFT", "CRIME_RECORDS", int(i["incident_id"])]],
                f"{near} is one character from stolen {plate} but a different car (own chassis, own owner): its context must "
                "not pick up that FIR.", [])
    by_name: dict[str, list[int]] = {}
    for oid, o in sorted(w.owners.items()):
        by_name.setdefault(o["full_name"], []).append(oid)
    for k, (name, ids) in enumerate(sorted((n, i) for n, i in by_name.items() if len(i) > 1)[:1], 1):
        add(w, f"HN-NAME-{k}", "HARD_NEGATIVE", [], [["REG", "OWNER_CONTACTS", i] for i in ids],
            f"Two REG owners are both called {name}, with different mobiles and towns: they are two people.", [],
            [["REG", "OWNERS", i] for i in ids])
    o_id = sorted(o for o in w.owners if w.owners[o]["city"] not in ("Delhi", "New Delhi", "Gurugram"))[7]
    pid = max(p["person_id"] for p in w.t["PERSONS"]) + 1
    i = free_thefts(w, "CLOSED")[0]
    w.t["PERSONS"].append({"person_id": pid, "name": w.owners[o_id]["full_name"], "mobile": new_mobile(w.rng, w.mobiles),
                           "address": f"{w.rng.randrange(1, 300)}, Shastri Nagar, Delhi"})
    w.t["INCIDENT_PERSONS"].append({"incident_id": int(i["incident_id"]), "person_id": pid, "role": "WITNESS"})
    add(w, "HN-NAME-2", "HARD_NEGATIVE", [], [["THEFT", "INCIDENT_PERSONS", [int(i["incident_id"]), pid, "WITNESS"]]],
        f"A Delhi witness shares the exact name of REG owner {w.owners[o_id]['full_name']} from {w.owners[o_id]['city']}, "
        "with another mobile and address: a name alone must not merge them.", [],
        [["REG", "OWNERS", o_id], ["THEFT", "PERSONS", pid]])


def plant_all(w: World) -> None:
    plant_alibi(w), plant_quick_claims(w), plant_transfers(w), plant_rings(w), plant_clones(w), plant_hard_negatives(w)


def add_noise(w: World) -> None:
    """A few chassis numbers missing or mistyped, as clerks do; exact chassis joins must miss them."""
    rng, broken = w.rng, []
    theft_plates = {canon(i["vehicle_number"]) for i in w.incidents}
    plate_of = {int(p["policy_id"]): canon(p["vehicle_reg"]) for p in w.policies}
    for party in w.t["POLICY_PARTIES"]:
        if plate_of[party["policy_id"]] in w.reserved | STORY_PLATES | theft_plates or rng.random() > 0.025:
            continue
        c = party["chassis_no"]
        party["chassis_no"] = None if rng.random() < 0.5 else c[:4] + c[5] + c[4] + c[6:] if c[4] != c[5] else c[:4] + "X" + c[5:]
        broken.append(["INS", "POLICY_PARTIES", party["policy_id"]])
    sp = next(r for r in w.t["STOLEN_PROPERTY"] if r["incident_id"] == int(free_thefts(w, "CLOSED")[-1]["incident_id"]))
    sp["chassis_no"] = sp["chassis_no"][:-2] + sp["chassis_no"][-1] + sp["chassis_no"][-2]
    broken.append(["THEFT", "STOLEN_PROPERTY", sp["incident_id"]])
    add(w, "HN-CHASSIS-1", "HARD_NEGATIVE", [], broken,
        f"{len(broken)} chassis numbers are missing (NULL) or mistyped outside REG: the exact chassis bridge must not "
        "link them; the plate bridge still can.", [])


def sanity_pass(w: World) -> None:
    """Remove accidental presence events inside stolen intervals of vehicles no ALIBI scenario owns."""
    alibi = {p for s in w.scenarios if s["kind"] in ("ALIBI", "CLONE") for p in s["plates"]}
    prop = {r["incident_id"]: r for r in w.t["STOLEN_PROPERTY"]}
    for i in w.incidents:
        plate = canon(i["vehicle_number"])
        if i["stolen_flag"] != "Y" or plate in alibi:
            continue
        start, sp = int(i["reported_date"]), prop[int(i["incident_id"])]
        certs = {c["cert_no"] for c in w.certs if canon(c["regn_number"]) == plate}
        for t in (t for t in w.t["CERT_TESTS"] if t["cert_no"] in certs):
            ts = epoch_ist(date.fromisoformat(t["test_ts"][:10]), int(t["test_ts"][11:13]))
            if start <= ts <= (sp["recovered_on"] or 10**12):
                if sp["recovered_on"] and ts - start > 2 * 86400:  # recovered before the test instead
                    sp["recovered_on"] = epoch_ist(date.fromisoformat(t["test_ts"][:10]) - timedelta(days=1))
                else:
                    t["test_ts"] = iso_ts(ist_date(start) - timedelta(days=30), int(t["test_ts"][11:13]))
        caps = {int(c["capture_id"]) for c in captures_of(w, plate)
                if start <= epoch_ist(date.fromisoformat(c["captured_at"][:10])) <= (sp["recovered_on"] or 10**12)}
        w.t["CHALLANS"] = [c for c in w.t["CHALLANS"] if c["capture_id"] not in caps or ("CHALLANS", c["challan_id"]) in w.planted]
