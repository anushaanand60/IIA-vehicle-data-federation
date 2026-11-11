"""CONTEXT-ADDED (R3: unstructured corpus) scenario/story documents, hard negatives, gold expansion and the
optional local-LLM rewrite used by scripts/phase3_docs_generate.py (split out to keep each module small).
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
from phase3_docs_templates import Doc, Writer, canon, pk  # noqa: E402
import phase3_docs_writers as W  # noqa: E402
from mediator.doc_conflicts import text_dates  # noqa: E402
from sources.docs.textindex import tokenize  # noqa: E402

LLM_CACHE = ROOT / "data" / "phase3_docs_llm_cache.json"
OLLAMA, MODEL = "http://127.0.0.1:11434", "qwen2.5:3b"
STORY = ["DL01AB1234", "DL05CD9876", "DL09KL3321", "HR26EF4455", "UP16GH1122", "MH12IJ7788", "KA05MN9999"]


def puc_test_notices(w: Writer, t: Dict[str, List[Dict[str, Any]]], vehicle: Any) -> List[Doc]:
    """PUC centre notices for the planted scenarios (an emission test needs the car physically present)."""
    scen = ROOT / "data" / "phase3_scenarios.json"
    if not scen.exists() or not t["puc_cert_tests"]:
        return []
    try:
        scenarios = json.loads(scen.read_text(encoding="utf-8"))
    except ValueError:
        return []
    wanted = {str(e[2]) for s in scenarios if isinstance(s, dict) for e in s.get("evidence", [])
              if isinstance(e, list) and len(e) == 3 and e[:2] == ["PUC", "CERT_TESTS"]}
    certs = {c["cert_no"]: c for c in t["puc_records"]}
    centres = {c["centre_id"]: c for c in t["puc_test_centres"]}
    analysers = {a["analyser_id"]: a for a in t["puc_analysers"]}
    out = []
    for test in t["puc_cert_tests"]:
        cert = certs.get(test["cert_no"])
        if not cert or test["test_id"] not in wanted:
            continue
        centre = centres.get(analysers.get(test["analyser_id"], {}).get("centre_id"), {})
        v = vehicle(cert["regn_number"])
        d = Doc("RTO_NOTICE", f"PUC test record: {centre.get('centre_name', 'test centre')}", "",
                centre.get("centre_name") or "PUC centre", test["test_ts"][:10], plates={canon(v["plate"]): 2},
                links=[("PUC", "CERT_TESTS", pk(test["test_id"])), ("PUC", "POLLUTION_CERT", cert["cert_no"])])
        if centre:
            d.links.append(("PUC", "TEST_CENTRES", pk(centre["centre_id"])))
        d.body = (f"{centre.get('centre_name', 'The centre')}, {centre.get('city', '')}: emission test of vehicle "
                  f"{w.plate(v['plate'], 0.05)} ({W.vehicle_phrase(w, v)}) carried out on {w.date(test['test_ts'][:10])} at "
                  f"{test['test_ts'][11:16]} hrs, result {test.get('result')}. Certificate {cert['cert_no']} issued; "
                  f"the vehicle was physically present at the centre for the test.")
        out.append(d)
    return out


def story_docs(w: Writer, veh: Dict[str, Dict[str, Any]], caps: Dict[str, List[Dict[str, Any]]]) -> List[Doc]:
    out = []
    clones = ["UP16GH1122"]
    scen = ROOT / "data" / "phase3_scenarios.json"
    if scen.exists():
        try:
            clones += [canon(p) for s in json.loads(scen.read_text(encoding="utf-8"))
                       if isinstance(s, dict) and s.get("kind") == "CLONE" for p in s.get("plates", [])]
        except ValueError:
            pass
    for plate in dict.fromkeys(clones):  # possible clone: the text describes the car the camera SAW
        v = veh.get(plate)
        seen = [c for c in caps.get(plate, []) if v and str(c["observed_make"]).lower() != str(v.get("make")).lower()]
        if not v or not v.get("reg_id") or not seen:
            continue
        c = seen[0]
        d = Doc("NEWS", f"Suspected cloned number plate spotted at {c['location_name']}", "", "NCR Express",
                c["captured_at"][:10], plates={plate: 2}, links=[("REG", "VEHICLE_REGISTRATION", pk(v["reg_id"])),
                                                                 ("REG", "OWNERS", pk(v["owner_id"]))])
        d.links += [("CAM", "PLATE_CAPTURES", pk(x["capture_id"])) for x in seen[:2]]
        where = " and ".join(dict.fromkeys(x["location_name"] for x in seen[:2]))
        d.body = (f"Traffic police suspect a cloned plate after a {c['observed_colour'].lower()} {c['observed_make']} "
                  f"{c['observed_model']} carrying the number {plate[:2]}-{plate[2:4]}-{plate[4:-4]}-{plate[-4:]} was "
                  f"flagged on {w.date(c['captured_at'][:10])} at {where}. Records show the number belongs to a "
                  f"{v['colour'].lower()} {v['make']} {v['model']} owned by {v['owner_name']} in {v['city']}.")
        db = ["REG", "VEHICLE_REGISTRATION", pk(v["reg_id"])]
        d.conflicts.append({"attr": "make", "db": db, "db_value": v["make"], "text_value": c["observed_make"]})
        if str(c["observed_colour"]).lower() != str(v["colour"]).lower():
            d.conflicts.append({"attr": "colour", "db": db, "db_value": v["colour"], "text_value": c["observed_colour"]})
        out.append(d)
    if "MH12IJ7788" in veh:
        c = (caps.get("MH12IJ7788") or [{}])[0]
        out.append(Doc("NEWS", "Camera flags car with no registration record", (
            f"An ANPR camera at {c.get('location_name', 'Old Delhi Signal')} read the number MH 12 IJ 7788 on a grey "
            "Tata Nexon. The transport department said the number is not found in its registration records."),
            "Delhi Daily", "2026-09-05", plates={"MH12IJ7788": 2},
            links=[("CAM", "PLATE_CAPTURES", pk(c["capture_id"]))] if c else []))
    return out


def distractors(w: Writer, rng: random.Random, veh: Dict[str, Dict[str, Any]], theft_veh: List[Dict[str, Any]]) -> List[Doc]:
    """Hard negatives: plates one character off a target, and same-name owners with a different car."""
    out = []
    targets = [p for p in STORY if p in veh] + [v["plate"] for v in theft_veh]
    for target in targets:
        near = [p for p in veh if p != target and len(p) == len(target) and sum(a != b for a, b in zip(p, target)) == 1]
        if near:
            p = rng.choice(near)
            out.append(W.lookalike_news(w, target, p, veh[p], None))
        else:
            i = rng.randrange(len(target) - 4, len(target))
            fake = target[:i] + rng.choice([d for d in "0123456789" if d != target[i]]) + target[i + 1:]
            if fake not in veh:
                out.append(W.lookalike_news(w, target, fake, None, None))
    for target in [p for p in STORY if p in veh and veh[p].get("owner_name")] + [v["plate"] for v in theft_veh[:18] if v.get("owner_name")]:
        fake = f"{rng.choice(['HR', 'UP', 'MH'])}{rng.randint(10, 99)}{rng.choice(['XY', 'QZ', 'KM'])}{rng.randint(1000, 9999)}"
        out.append(W.lookalike_news(w, target, fake, None, veh[target]["owner_name"]))
    return out


def _identifiers(text: str) -> set:
    """Tokens carrying a digit (plates, FIR/policy numbers, times, amounts), dates removed (compared as dates)."""
    for _, s, e in sorted(text_dates(text), key=lambda x: -x[1]):
        text = text[:s] + " " + text[e:]
    return {t for t in tokenize(text) if any(c.isdigit() for c in t)}


def faithful(original: str, rewrite: str, keep: List[str]) -> bool:
    """A rewrite is kept only if it states exactly the same dates and identifiers (none lost, none invented) and
    the strings the template marked as essential; re-wording "12/07/2026" as "12th July 2026" is fine."""
    same_dates = {d for d, _, _ in text_dates(original)} == {d for d, _, _ in text_dates(rewrite)}
    return same_dates and _identifiers(original) == _identifiers(rewrite) and all(k in rewrite for k in keep)


def llm_rewrite(docs: List[Doc], n: int, seed: int) -> int:
    """Rewrite ~n template documents with the local model for natural variety. Returns how many were kept."""
    try:
        import httpx
        httpx.get(f"{OLLAMA}/api/tags", timeout=2).raise_for_status()
    except Exception:
        print("  Ollama not answering: template-only corpus")
        return 0
    cache = json.loads(LLM_CACHE.read_text(encoding="utf-8")) if LLM_CACHE.exists() else {}
    rng = random.Random(seed + 1)
    pool = [d for d in docs if not d.conflicts and d.doc_type in ("FIR_NARRATIVE", "WITNESS_STATEMENT", "NEWS", "ACCIDENT_REPORT")]
    rng.shuffle(pool)
    kept = 0
    for i, d in enumerate(pool[: int(n * 1.6)]):
        if kept >= n:
            break
        must = sorted({m for m in re.findall(r"[A-Za-z0-9./-]*\d[A-Za-z0-9./-]*", d.body)} | set(d.keep),
                      key=lambda s: (len(s), s))   # total order: the prompt (and its cache key) must not depend on hashing
        prompt = (f"Rewrite this {d.doc_type.replace('_', ' ').lower()} in natural Indian English as a different "
                  f"writer would (60-120 words, one paragraph, no heading). Copy these strings exactly as written: "
                  f"{'; '.join(must)}. Do not add any new names, numbers or dates.\n\nTEXT:\n{d.body}")
        key = hashlib.sha256(f"{MODEL}|{seed + i}|0.7|{prompt}".encode()).hexdigest()
        if key not in cache:
            try:
                r = httpx.post(f"{OLLAMA}/api/generate", timeout=90, json={
                    "model": MODEL, "prompt": prompt, "stream": False, "options": {"temperature": 0.7, "seed": seed + i, "num_predict": 260}})
                cache[key] = r.json().get("response", "") if r.status_code == 200 else ""
            except Exception:
                cache[key] = ""
            LLM_CACHE.write_text(json.dumps(cache, indent=0, ensure_ascii=False), encoding="utf-8")
        text = " ".join(str(cache[key]).split())
        if text and faithful(d.body, text, d.keep) and 150 < len(text) < 1400:  # gold still exact
            d.body, d.llm = text, True
            kept += 1
    return kept


def same_entity_links(t: Dict[str, List[Dict[str, Any]]]) -> Dict[tuple, List[tuple]]:
    """Gold expansion: rows that are the same real-world thing in another agency's records.
    A chassis printed in a text names every row holding that chassis; an owner named in a text is also the
    INS holder / THEFT person of the same vehicle when the name is identical."""
    alias: Dict[tuple, List[tuple]] = defaultdict(list)
    by_chassis: Dict[str, List[tuple]] = defaultdict(list)
    for r in t["reg_vehicle_identity"]:
        by_chassis[r["chassis_no"]].append(("REG", "VEHICLE_IDENTITY", pk(r["registration_id"])))
    for r in t["ins_policy_parties"]:
        by_chassis[r["chassis_no"]].append(("INS", "POLICY_PARTIES", pk(r["policy_id"])))
    for r in t["theft_stolen_property"]:
        by_chassis[r["chassis_no"]].append(("THEFT", "STOLEN_PROPERTY", pk(r["incident_id"])))
    for rows in by_chassis.values():
        for a in rows:
            alias[a] += [b for b in rows if b != a]
    owners = {r["owner_id"]: r["full_name"] for r in t["reg_owners"]}
    reg = {canon(r["registration_no"]): r["owner_id"] for r in t["reg_vehicle_registration"]}
    holders = {r["holder_id"]: r["holder_name"] for r in t["ins_policy_holders"]}
    for p in t["ins_policy_parties"]:
        pol = next((x for x in t["ins_policy_records"] if x["policy_id"] == p["policy_id"]), None)
        oid = reg.get(canon(pol["vehicle_reg"])) if pol else None
        if oid and holders.get(p["holder_id"]) == owners.get(oid):
            alias[("REG", "OWNERS", pk(oid))].append(("INS", "POLICY_HOLDERS", pk(p["holder_id"])))
            alias[("INS", "POLICY_HOLDERS", pk(p["holder_id"]))].append(("REG", "OWNERS", pk(oid)))
    persons = {r["person_id"]: r["name"] for r in t["theft_persons"]}
    crimes = {r["incident_id"]: canon(r["vehicle_number"]) for r in t["theft_crime_records"]}
    for ip in t["theft_incident_persons"]:
        oid = reg.get(crimes.get(ip["incident_id"], ""))
        if oid and ip["role"] == "COMPLAINANT" and persons.get(ip["person_id"]) == owners.get(oid):
            alias[("REG", "OWNERS", pk(oid))].append(("THEFT", "PERSONS", pk(ip["person_id"])))
            alias[("THEFT", "PERSONS", pk(ip["person_id"]))].append(("REG", "OWNERS", pk(oid)))
    return alias
