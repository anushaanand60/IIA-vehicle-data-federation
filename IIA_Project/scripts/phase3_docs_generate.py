"""CONTEXT-ADDED (R3: unstructured corpus) Generate the DOCS source's documents and the evaluation gold.

    python scripts/phase3_docs_generate.py                 # templates + local LLM (if Ollama answers), seed 20261111
    python scripts/phase3_docs_generate.py --no-llm --load # template-only, then load sources/docs/docs.db

Reads the structured root CSVs (the phase-3 tables when present, else the phase 1-2 ones only) and writes
  docs_documents.csv        DOCUMENTS rows for the DOCS source (doc_id, doc_type, title, body, issuer, issued_on)
  data/phase3_gold.json     per document: rows it was written from, plates it is about (graded), planted conflicts
Reproducible: one seeded RNG drives every choice; LLM rewrites are seeded and cached (data/phase3_docs_llm_cache.json),
and a rewrite is kept only if every identifier, date and number of the template survives (else the template stays),
so the gold stays exact whatever the model writes.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from phase3_docs_templates import Doc, Writer, canon, ddmmyyyy_to_iso, pk  # noqa: E402
import phase3_docs_writers as W  # noqa: E402
from phase3_docs_extras import (STORY, distractors, llm_rewrite, puc_test_notices, same_entity_links,  # noqa: E402
                                story_docs)

SEED = 20261111
OUT_CSV, GOLD = ROOT / "docs_documents.csv", ROOT / "data" / "phase3_gold.json"
# planting probabilities, tuned so ~5 % of all documents carry a planted text-vs-database conflict
P_RECOVERY, P_INCIDENT_DATE, P_NEWS_COLOUR, P_LOSS_DATE, P_POLICY_UNTIL, P_ACC_COLOUR = .30, .10, .10, .15, .10, .07


def load(name: str) -> List[Dict[str, Any]]:
    path = ROOT / f"{name}.csv"
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.DictReader(f))


def build_vehicles(t: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Dict[str, Any]]:
    owners = {r["owner_id"]: r for r in t["reg_owners"]}
    ident = {r["registration_id"]: r for r in t["reg_vehicle_identity"]}
    veh: Dict[str, Dict[str, Any]] = {}
    for r in t["reg_vehicle_registration"]:
        o = owners.get(r["owner_id"], {})
        veh[canon(r["registration_no"])] = {
            "plate": canon(r["registration_no"]), "reg_id": r["registration_id"], "owner_id": r["owner_id"],
            "owner_name": o.get("full_name"), "address": o.get("address_line"), "city": o.get("city"),
            "make": r["make"] or None, "model": r["model"], "colour": r["colour"], "reg_status": r["reg_status"],
            "registered_on": r["registered_on"] or None, "chassis": ident.get(r["registration_id"], {}).get("chassis_no")}
    for c in t["cam_plate_captures"]:  # plates only a camera knows (MH12IJ7788): described by what was observed
        p = canon(c["plate_id"])
        if p not in veh and re.fullmatch(r"[A-Z]{2}\d{2}[A-Z]{1,3}\d{4}", p):
            veh[p] = {"plate": p, "reg_id": None, "owner_id": None, "owner_name": None, "address": None, "city": None,
                      "make": c["observed_make"], "model": c["observed_model"], "colour": c["observed_colour"],
                      "reg_status": None, "registered_on": c["captured_at"][:10], "chassis": None}
    return veh


def generate(seed: int = SEED) -> List[Doc]:
    rng = random.Random(seed)
    w = Writer(rng)
    names = ["reg_owners", "reg_vehicle_registration", "reg_vehicle_identity", "reg_ownership_history",
             "ins_insurers", "ins_policy_records", "ins_policy_holders", "ins_policy_parties", "ins_claims",
             "theft_crime_records", "theft_stations", "theft_stolen_property", "theft_persons", "theft_incident_persons",
             "cam_cameras", "cam_plate_captures", "cam_challans", "puc_records", "puc_test_centres", "puc_analysers", "puc_cert_tests"]
    t = {n: load(n) for n in names}
    veh = build_vehicles(t)
    owners = {r["owner_id"]: r for r in t["reg_owners"]}
    stations = {r["station_name"]: r for r in t["theft_stations"]}
    cams = {r["camera_id"]: r for r in t["cam_cameras"]}
    insurers = {r["insurer_id"]: r["insurer_name"] for r in t["ins_insurers"]}
    persons = {r["person_id"]: r for r in t["theft_persons"]}
    by_inc: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for ip in t["theft_incident_persons"]:
        if ip["person_id"] in persons:
            by_inc[ip["incident_id"]].append({**persons[ip["person_id"]], "role": ip["role"]})
    sp = {r["incident_id"]: r for r in t["theft_stolen_property"]}
    caps: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for c in t["cam_plate_captures"]:
        caps[canon(c["plate_id"])].append({**c, "location_name": cams.get(c["camera_id"], {}).get("location_name")})
    docs: List[Doc] = []

    def vehicle(plate: str) -> Dict[str, Any]:
        p = canon(plate)
        return veh.get(p) or {"plate": p, "reg_id": None, "owner_id": None, "owner_name": None, "make": None,
                              "model": "car", "colour": None, "registered_on": None, "chassis": None}

    thefts = []
    for crime in t["theft_crime_records"]:
        v = vehicle(crime["vehicle_number"])
        is_theft = crime["incident_type"] == "THEFT"
        open_ = str(crime["case_status"]).upper() == "OPEN"
        r = rng.random()
        plant = ("recovery" if is_theft and open_ and r < P_RECOVERY else
                 "incident_date" if is_theft and r > 1 - P_INCIDENT_DATE else None)
        docs.append(W.fir_narrative(w, v, crime, sp.get(crime["incident_id"]), by_inc.get(crime["incident_id"], []),
                                    stations, plant))
        if not is_theft:
            continue
        thefts.append((v, crime))
        wits = [p for p in by_inc.get(crime["incident_id"], []) if p["role"] == "WITNESS"]
        if not wits:  # no THEFT.PERSONS yet: an unnamed-in-DB bystander (no PERSONS link)
            wits = [{"person_id": None, "name": rng.choice(["Suresh Kr. Yadav", "Anil Mehta", "Farhan Qureshi",
                                                           "Pooja Bhatia", "Gurpreet Singh"]), "address": None}]
        for p in wits[:2]:
            docs.append(W.witness_statement(w, v, crime, p))
        if rng.random() < 0.85:
            docs.append(W.theft_news(w, v, crime, "colour" if rng.random() < P_NEWS_COLOUR else None))
    rng.shuffle(thefts)
    for i in range(0, len(thefts) - 2, 3):
        docs.append(W.roundup_news(w, thefts[i:i + 3], rng.choice(["Delhi", "Gurugram", "the NCR"])))

    policies = {}
    for p in t["ins_policy_records"]:
        policies[p["policy_id"]] = {**p, "insurer_name": insurers.get(p["insurer_id"]),
                                    "_until_iso": ddmmyyyy_to_iso(p["policy_until"]),
                                    "_start_iso": ddmmyyyy_to_iso(p["policy_start"])}
    holders = {r["holder_id"]: r for r in t["ins_policy_holders"]}
    party = {r["policy_id"]: r for r in t["ins_policy_parties"]}
    if t["ins_claims"]:
        for claim in rng.sample(t["ins_claims"], min(350, len(t["ins_claims"]))):
            pol = policies.get(claim["policy_id"])
            if pol:
                claim = {**claim, "_loss_iso": ddmmyyyy_to_iso(claim["loss_date"])}
                h = holders.get(party.get(pol["policy_id"], {}).get("holder_id"))
                docs.append(W.surveyor_report(w, vehicle(pol["vehicle_reg"]), pol, claim, h,
                                              "loss_date" if rng.random() < P_LOSS_DATE else None))
    story_pols = [p for p in policies.values() if canon(p["vehicle_reg"]) in STORY]
    pool = [p for p in policies.values() if p not in story_pols]
    for pol in story_pols + rng.sample(pool, min(200, len(pool))):
        h = holders.get(party.get(pol["policy_id"], {}).get("holder_id"))
        docs.append(W.surveyor_report(w, vehicle(pol["vehicle_reg"]), pol, None, h,
                                      "policy_until" if rng.random() < P_POLICY_UNTIL else None))

    captured = [p for p in caps if p in veh]
    others = [p for p in veh if p not in caps]
    picks = [s for s in STORY if s in veh] + rng.sample(captured, min(170, len(captured))) + rng.sample(others, min(110, len(others)))
    for p in dict.fromkeys(picks):
        cap = rng.choice(caps[p]) if caps.get(p) else None
        docs.append(W.accident_report(w, veh[p], cap, "colour" if rng.random() < P_ACC_COLOUR else None))

    for v in veh.values():
        if v.get("reg_status") in ("SUSPENDED", "CANCELLED"):
            docs.append(W.rto_notice(w, v, v["reg_status"]))
    by_reg = {v["reg_id"]: v for v in veh.values() if v["reg_id"]}
    hist = [h for h in t["reg_ownership_history"] if h["registration_id"] in by_reg]
    for h in rng.sample(hist, min(200, len(hist))):
        docs.append(W.rto_notice(w, by_reg[h["registration_id"]], "transfer", h, owners))
    active = [v for v in veh.values() if v.get("reg_status") == "ACTIVE"]
    for v in rng.sample(active, min(150 if not hist else 60, len(active))):
        docs.append(W.rto_notice(w, v, "generic"))
    certs = t["puc_records"]
    picked = [c for c in certs if canon(c["regn_number"]) in STORY] + rng.sample(certs, min(120, len(certs)))
    for c in {c["cert_no"]: c for c in picked}.values():
        docs.append(W.puc_notice(w, vehicle(c["regn_number"]), c))
    docs += puc_test_notices(w, t, vehicle)
    cap_by_id = {c["capture_id"]: c for cs in caps.values() for c in cs}
    for ch in t["cam_challans"]:
        cap = cap_by_id.get(ch["capture_id"])
        if cap:
            docs.append(W.challan_notice(w, vehicle(cap["plate_id"]), ch, cap))
    for cam in cams.values():
        for _ in range(3):
            docs.append(W.camera_news(w, cam))
    docs += story_docs(w, veh, caps) + distractors(w, rng, veh, [v for v, _ in thefts])
    alias = same_entity_links(t)
    for d in docs:
        if not d.distractor_for:
            d.links += [b for a in list(d.links) for b in alias.get(a, [])]
    return docs


def write(docs: List[Doc], seed: int) -> Dict[str, Any]:
    rng = random.Random(seed + 2)
    rng.shuffle(docs)  # doc ids must not reveal the type or the generating order
    gold: Dict[str, Any] = {"docs": {}, "by_plate": defaultdict(dict)}
    with OUT_CSV.open("w", newline="", encoding="utf-8") as f:
        out = csv.writer(f)
        out.writerow(["doc_id", "doc_type", "title", "body", "issuer", "issued_on"])
        for i, d in enumerate(docs, 1):
            d.doc_id = f"D{i:05d}"
            out.writerow([d.doc_id, d.doc_type, d.title[:160], d.body, d.issuer[:60], d.issued_on])
            gold["docs"][d.doc_id] = {"doc_type": d.doc_type, "plates": d.plates, "links": [list(l) for l in dict.fromkeys(d.links)],
                                      "conflicts": d.conflicts, "distractor_for": d.distractor_for, "llm": d.llm}
            for p, g in d.plates.items():
                gold["by_plate"][p][d.doc_id] = g
    stats = {"docs": len(docs), "by_type": dict(Counter(d.doc_type for d in docs)), "llm_written": sum(d.llm for d in docs),
             "with_conflict": sum(bool(d.conflicts) for d in docs), "conflicts_by_attr": dict(Counter(
                 c["attr"] for d in docs for c in d.conflicts)), "distractors": sum(bool(d.distractor_for) for d in docs)}
    payload = {"_doc": "CONTEXT-ADDED (R3): gold for document retrieval/linking/conflicts; evaluation only, never "
                       "served by DOCS. links = [source, table, pk]; plates grade 2 = subject, 1 = mentioned.",
               "seed": seed, "stats": stats, **gold}
    GOLD.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    return stats


def load_db(db_path: Path = ROOT / "sources" / "docs" / "docs.db") -> int:
    ddl = re.sub(r"--[^\n]*", "", (ROOT / "sources" / "docs" / "schema.sql").read_text(encoding="utf-8"))
    with OUT_CSV.open(newline="", encoding="utf-8") as f:
        rows = [tuple(r[c] for c in ("doc_id", "doc_type", "title", "body", "issuer", "issued_on")) for r in csv.DictReader(f)]
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as con:
        con.execute("DROP TABLE IF EXISTS DOCUMENTS")
        for stmt in [s for s in ddl.split(";") if s.strip()]:
            con.execute(stmt)
        con.executemany("INSERT INTO DOCUMENTS VALUES (?,?,?,?,?,?)", rows)
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--no-llm", action="store_true", help="template-only (also the automatic fallback)")
    ap.add_argument("--llm-docs", type=int, default=80, help="how many documents the local model rewrites")
    ap.add_argument("--load", action="store_true", help="also (re)load sources/docs/docs.db")
    args = ap.parse_args()
    phase3 = (ROOT / "reg_vehicle_identity.csv").exists()
    print(f"  structured input: {'phase-3 tables' if phase3 else 'phase 1-2 CSVs only (re-run after the phase-3 generator)'}")
    docs = generate(args.seed)
    kept = 0 if args.no_llm else llm_rewrite(docs, args.llm_docs, args.seed)
    stats = write(docs, args.seed)
    print(f"  {stats['docs']} documents -> {OUT_CSV.name}; LLM-written {kept}; conflicts planted in "
          f"{stats['with_conflict']} ({stats['conflicts_by_attr']}); {stats['by_type']}")
    if args.load:
        print(f"  loaded {load_db()} rows into sources/docs/docs.db")


if __name__ == "__main__":
    main()
