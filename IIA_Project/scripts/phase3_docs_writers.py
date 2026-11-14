"""CONTEXT-ADDED (R3: unstructured corpus): one function per document type.

`v` is a vehicle view the generator assembles from the structured CSVs (plate, reg_id, make, model, colour, owner,
chassis, ...). Every writer returns a templates.Doc whose `links` name the rows the text was written from
(source, table, pk) - the gold the linker is scored against - and whose `conflicts` list anything planted on purpose.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from phase3_docs_templates import (COLOURS, RTO_CITY, STATION_CITY, Doc, Writer, canon, colour_conflict,
                                   epoch_to_iso, pk, place_of_station, recovered_conflict, shift, vehicle_phrase)

V = Dict[str, Any]


def _vehicle_links(v: V, owner: bool = True) -> List[tuple]:
    out = []
    if v.get("reg_id") is not None:
        out.append(("REG", "VEHICLE_REGISTRATION", pk(v["reg_id"])))
        if owner and v.get("owner_id") is not None:
            out.append(("REG", "OWNERS", pk(v["owner_id"])))
    return out


def _city(v: V) -> str:
    return v.get("city") or RTO_CITY.get(canon(v["plate"])[:2], "New Delhi")


def fir_narrative(w: Writer, v: V, crime: Dict[str, Any], sp: Optional[Dict[str, Any]], persons: List[Dict[str, Any]],
                  stations: Dict[str, Dict[str, Any]], plant: Optional[str]) -> Doc:
    rep = epoch_to_iso(crime["reported_date"]) or "2026-01-01"
    station = str(crime.get("police_station") or "")
    place = place_of_station(station)
    city = (stations.get(station) or {}).get("district") or STATION_CITY.get(station, _city(v))
    compl = next((p for p in persons if p.get("role") == "COMPLAINANT"), None)
    who = compl["name"] if compl else (v.get("owner_name") or "the registered owner")
    doc = Doc("FIR_NARRATIVE", f"FIR {crime['fir_no']} - P.S. {place}", "", f"P.S. {place}", rep,
              plates={canon(v["plate"]): 2})
    doc.links.append(("THEFT", "CRIME_RECORDS", pk(crime["incident_id"])))
    doc.links += _vehicle_links(v, owner=(compl is None or compl["name"] == v.get("owner_name")))
    if station in stations:
        doc.links.append(("THEFT", "STATIONS", station))
    ptxt = w.plate(v["plate"])
    if crime.get("incident_type") == "SHREDDING":
        doc.body = (f"Information received at P.S. {place} on {w.date(rep)}: vehicle {vehicle_phrase(w, v)} bearing "
                    f"registration no. {ptxt} was handed over to an authorised scrapping facility and shredded. "
                    f"Entry made vide {crime['fir_no']}. No offence disclosed; record closed.")
        doc.keep = [ptxt, str(crime["fir_no"])]
        return doc
    stolen = shift(rep, -w.rng.choice([0, 0, 1]))
    if plant == "incident_date":
        stolen = shift(rep, -w.rng.randint(9, 30))
        doc.conflicts.append({"attr": "incident_date", "db": ["THEFT", "CRIME_RECORDS", pk(crime["incident_id"])],
                              "db_value": rep, "text_value": stolen})
    chassis = f", chassis no. {sp['chassis_no']}" if sp and sp.get("chassis_no") else ""
    if chassis:
        doc.links += [("THEFT", "STOLEN_PROPERTY", pk(crime["incident_id"]))]
        if v.get("reg_id") is not None and v.get("chassis") == sp["chassis_no"]:
            doc.links.append(("REG", "VEHICLE_IDENTITY", pk(v["reg_id"])))
    spot = w.rng.choice(["the main market", "his residence", "a private parking lot", "the metro station",
                         "the community centre", "the gurudwara"])
    body = (f"Brief facts: On {w.date(stolen)} at about {w.time()} the complainant {w.name(who)}"
            f"{', r/o ' + str(v['address']) if v.get('address') and not compl else ''} reported at P.S. {place}, "
            f"{w.city(city)} that the {vehicle_phrase(w, v)} bearing registration no. {ptxt}{chassis} parked near "
            f"{spot} was stolen.{w.hinglish()} FIR No. {crime['fir_no']} registered u/s 303(2) BNS on {w.date(rep)}.")
    if compl:
        doc.links.append(("THEFT", "PERSONS", pk(compl["person_id"])))
    for p in persons:
        if p.get("role") == "ACCUSED":
            body += f" Accused {w.name(p['name'])} has been named on the basis of CCTV footage."
            doc.links.append(("THEFT", "PERSONS", pk(p["person_id"])))
    status = str(crime.get("case_status") or "").upper()
    if plant == "recovery":
        when = shift(rep, w.rng.randint(4, 30))
        body += f" The vehicle was recovered on {w.date(when)} from an abandoned plot and handed over on superdari."
        recovered_conflict(doc, crime, when)
    elif status == "OPEN":
        body += w.rng.choice([" The vehicle is yet to be recovered; investigation is in progress.",
                              " Vehicle not recovered so far. Gaadi abhi tak nahi mili.", " Investigation continues."])
    else:
        rec = epoch_to_iso(sp.get("recovered_on")) if sp and sp.get("recovered_on") else shift(rep, w.rng.randint(5, 40))
        body += f" The vehicle was recovered on {w.date(rec)} and the case is closed."
    doc.body, doc.keep = body, [ptxt, str(crime["fir_no"])]
    return doc


def witness_statement(w: Writer, v: V, crime: Dict[str, Any], witness: Dict[str, Any]) -> Doc:
    rep = epoch_to_iso(crime["reported_date"]) or "2026-01-01"
    place = place_of_station(crime.get("police_station"))
    doc = Doc("WITNESS_STATEMENT", f"Statement of {witness['name']} in FIR {crime['fir_no']}", "", f"P.S. {place}",
              shift(rep, w.rng.randint(0, 6)), plates={canon(v["plate"]): 2},
              links=[("THEFT", "CRIME_RECORDS", pk(crime["incident_id"])), ("THEFT", "STATIONS", crime.get("police_station"))])
    if witness.get("person_id") is not None:
        doc.links.append(("THEFT", "PERSONS", pk(witness["person_id"])))
    r = w.rng.random()
    if r < 0.5:
        clue = f" I noted the number as {w.plate(v['plate'], ocr_p=0.5)}."
        doc.links += _vehicle_links(v, owner=False)
    elif r < 0.8:
        clue = f" The number plate ended with {canon(v['plate'])[-4:]}, I could not read the rest."
    else:
        clue = " I could not see the number plate in the dark."
    doc.body = (f"Statement recorded u/s 180 BNSS in FIR No. {crime['fir_no']}, P.S. {place}. I, {w.name(witness['name'])}"
                f"{', r/o ' + witness['address'] if witness.get('address') else ''}, state that on the night of "
                f"{w.date(shift(rep, -1))} at about {w.time()} I saw two young men drive away a "
                f"{vehicle_phrase(w, v)} from near {place}.{clue}{w.hinglish(0.4)}")
    return doc


def theft_news(w: Writer, v: V, crime: Dict[str, Any], plant: Optional[str]) -> Doc:
    rep = epoch_to_iso(crime["reported_date"]) or "2026-01-01"
    station = str(crime.get("police_station") or "")
    place, city = place_of_station(station), STATION_CITY.get(station, _city(v))
    colour = v.get("colour")
    doc = Doc("NEWS", "", "", w.rng.choice(["City Times", "Delhi Daily", "NCR Express", "Hindustan Samachar"]),
              shift(rep, w.rng.randint(1, 3)), plates={canon(v["plate"]): 2},
              links=[("THEFT", "CRIME_RECORDS", pk(crime["incident_id"])), ("THEFT", "STATIONS", station)])
    if plant == "colour" and v.get("reg_id") is not None:
        colour = colour_conflict(w, v)
        doc.conflicts.append({"attr": "colour", "db": ["REG", "VEHICLE_REGISTRATION", pk(v["reg_id"])],
                              "db_value": v.get("colour"), "text_value": colour})
    phrase = vehicle_phrase(w, v, colour=colour)
    doc.title = f"{phrase[:1].upper() + phrase[1:]} stolen from {place}, {w.city(city)}"
    plate_bit = ""
    if w.rng.random() < 0.55 or plant == "colour":
        plate_bit = f" (registration {w.plate(v['plate'])})"
        doc.links += _vehicle_links(v, owner=False)
    owner = f" belonging to {w.name(v['owner_name'])}" if v.get("owner_name") and w.rng.random() < 0.6 else ""
    if owner:
        doc.links.append(("REG", "OWNERS", pk(v["owner_id"])))
    doc.body = (f"A {phrase}{plate_bit}{owner} was stolen from near {place} in {w.city(city)} on {w.date(shift(rep, -1))}. "
                f"Police at {station} have registered a case (FIR {crime['fir_no']}) and are scanning CCTV footage "
                f"from nearby ANPR cameras.")
    if str(crime.get("case_status")).upper() == "CLOSED":
        doc.body += " The car was later traced and returned to its owner."
    return doc


def roundup_news(w: Writer, items: List[tuple], city: str) -> Doc:
    """items: (vehicle, crime) pairs; every plate is a secondary mention (grade 1)."""
    doc = Doc("NEWS", f"Vehicle thefts rise in {city} this month", "", "NCR Express",
              epoch_to_iso(items[-1][1]["reported_date"]) or "2026-01-01")
    lines = []
    for v, crime in items:
        lines.append(f"a {vehicle_phrase(w, v)} ({w.plate(v['plate'], ocr_p=0.1)}, FIR {crime['fir_no']}) from "
                     f"{place_of_station(crime['police_station'])}")
        doc.plates[canon(v["plate"])] = 1
        doc.links.append(("THEFT", "CRIME_RECORDS", pk(crime["incident_id"])))
        doc.links += _vehicle_links(v, owner=False)
    doc.body = (f"Police in {w.city(city)} registered several vehicle theft cases in the last few weeks, including "
                + "; ".join(lines) + ". Residents are advised to use steering locks and park in lit areas.")
    return doc


def surveyor_report(w: Writer, v: V, policy: Dict[str, Any], claim: Optional[Dict[str, Any]],
                    holder: Optional[Dict[str, Any]], plant: Optional[str]) -> Doc:
    pol_no = f"POL-{int(policy['policy_id']):06d}"
    ins = policy.get("insurer_name") or "the insurer"
    doc = Doc("SURVEYOR_REPORT", "", "", ins, "2026-01-01", plates={canon(v["plate"]): 2},
              links=[("INS", "POLICY_RECORDS", pk(policy["policy_id"]))] + _vehicle_links(v, owner=False))
    if policy.get("insurer_id"):
        doc.links.append(("INS", "INSURERS", pk(policy["insurer_id"])))
    insured = holder["holder_name"] if holder else v.get("owner_name") or "the insured"
    if holder:
        doc.links.append(("INS", "POLICY_HOLDERS", pk(holder["holder_id"])))
    elif v.get("owner_name"):   # no INS identity: the insured named is REG's owner
        doc.links.append(("REG", "OWNERS", pk(v["owner_id"])))
    if v.get("chassis") and v.get("reg_id") is not None:   # printed below; same_entity_links adds INS/THEFT rows
        doc.links.append(("REG", "VEHICLE_IDENTITY", pk(v["reg_id"])))
    head = (f"Policy No. {pol_no} ({ins}, {str(policy.get('policy_type', '')).replace('_', ' ').lower()}). "
            f"Insured: {w.name(insured)}. Vehicle: {w.plate(v['plate'], ocr_p=0.08)}, {vehicle_phrase(w, v)}"
            + (f", chassis {v['chassis']}" if v.get("chassis") else "") + ".")
    if claim:
        loss = claim.get("_loss_iso") or "2026-01-01"
        text_loss = loss
        if plant == "loss_date":
            text_loss = shift(loss, w.rng.choice([-1, 1]) * w.rng.randint(6, 25))
            doc.conflicts.append({"attr": "loss_date", "db": ["INS", "CLAIMS", pk(claim["claim_id"])],
                                  "db_value": loss, "text_value": text_loss})
        doc.links.append(("INS", "CLAIMS", pk(claim["claim_id"])))
        doc.title, doc.issued_on = f"Surveyor's report, claim CLM-{int(claim['claim_id']):06d}", shift(loss, w.rng.randint(5, 25))
        doc.body = (f"SURVEYOR'S REPORT. Claim ref CLM-{int(claim['claim_id']):06d}. {head} Date of loss: "
                    f"{w.date(text_loss)}. Nature of loss: {str(claim.get('loss_type', '')).replace('_', ' ').lower()}. "
                    f"Surveyor {claim.get('surveyor_name') or 'on panel'} inspected the documents"
                    f"{' and the vehicle' if claim.get('loss_type') != 'THEFT' else ''}. Amount claimed Rs "
                    f"{claim.get('amount_claimed')}. Recommendation: {w.rng.choice(['settle', 'investigate further', 'refer to SIU'])}.")
        return doc
    until = policy.get("_until_iso") or "2026-12-31"
    text_until = until
    if plant == "policy_until":
        text_until = shift(until, w.rng.randint(45, 240))
        doc.conflicts.append({"attr": "policy_until", "db": ["INS", "POLICY_RECORDS", pk(policy["policy_id"])],
                              "db_value": until, "text_value": text_until})
    start = policy.get("_start_iso") or "2025-01-01"
    doc.title, doc.issued_on = f"Pre-inspection report, {pol_no}", start
    doc.body = (f"PRE-INSPECTION REPORT. {head} Cover valid from {w.date(start)} till {w.date(text_until)}. "
                f"Vehicle inspected at {w.city(_city(v))}; odometer and paint condition recorded, no pre-existing "
                f"damage except minor scratches on the rear bumper.{w.hinglish(0.15, 'survey')}")
    return doc


def accident_report(w: Writer, v: V, capture: Optional[Dict[str, Any]], plant: Optional[str]) -> Doc:
    when = (capture or {}).get("captured_at", "")[:10] or v.get("registered_on") or "2026-03-01"
    loc = (capture or {}).get("location_name") or f"{w.rng.choice(['Ring Road', 'Main Market', 'Sector 18'])}, {_city(v)}"
    doc = Doc("ACCIDENT_REPORT", f"Accident report: {loc}", "", "Traffic Police", when,
              plates={canon(v["plate"]): 2}, links=_vehicle_links(v))
    if capture:
        doc.links += [("CAM", "CAMERAS", capture["camera_id"]), ("CAM", "PLATE_CAPTURES", pk(capture["capture_id"]))]
    colour, model = v.get("colour"), v.get("model")
    if plant == "colour" and v.get("reg_id") is not None:
        colour = colour_conflict(w, v)
        doc.conflicts.append({"attr": "colour", "db": ["REG", "VEHICLE_REGISTRATION", pk(v["reg_id"])],
                              "db_value": v.get("colour"), "text_value": colour})
    other = w.rng.choice(["a two-wheeler", "an auto-rickshaw", "a road divider", "a DTC bus", "a parked car"])
    doc.body = (f"ACCIDENT REPORT (GD No. {w.rng.randint(10, 99)}). On {w.date(when)} at {w.time()} a "
                f"{vehicle_phrase(w, v, colour=colour, model=model)} bearing No. {w.plate(v['plate'])} driven by "
                f"{w.name(v.get('owner_name') or 'its owner')} collided with {other} near {loc}."
                f"{' The vehicle was also recorded by the ANPR camera at that junction.' if capture else ''}"
                f"{w.hinglish(kind='accident')} Minor damage; no injuries. Parties settled the matter mutually.")
    if not v.get("owner_name"):
        doc.links = [l for l in doc.links if l[1] != "OWNERS"]
    return doc


def rto_notice(w: Writer, v: V, kind: str, transfer: Optional[Dict[str, Any]] = None,
               owners: Optional[Dict[str, Dict[str, Any]]] = None) -> Doc:
    rto = canon(v["plate"])[:4]
    doc = Doc("RTO_NOTICE", "", "", f"RTO {rto}", v.get("registered_on") or "2026-01-01",
              plates={canon(v["plate"]): 2}, links=_vehicle_links(v))
    if kind == "transfer" and transfer and owners is not None:
        prev, new = owners.get(str(transfer["prev_owner_id"]), {}), owners.get(str(transfer["new_owner_id"]), {})
        doc.links = [("REG", "VEHICLE_REGISTRATION", pk(v["reg_id"])), ("REG", "OWNERSHIP_HISTORY", pk(transfer["transfer_id"]))]
        doc.links += [("REG", "OWNERS", pk(o)) for o in (transfer["prev_owner_id"], transfer["new_owner_id"])]
        doc.issued_on, doc.title = transfer["transfer_date"], f"Transfer of ownership, {canon(v['plate'])}"
        doc.body = (f"Public notice: ownership of {vehicle_phrase(w, v)} No. {w.plate(v['plate'], 0.05)} has been "
                    f"transferred from {w.name(prev.get('full_name', 'previous owner'))} to "
                    f"{w.name(new.get('full_name', 'new owner'))} on {w.date(transfer['transfer_date'])} "
                    f"({str(transfer.get('transfer_type', 'SALE')).lower()}). Objections, if any, within 15 days.")
        return doc
    if kind in ("SUSPENDED", "CANCELLED"):
        doc.issued_on = shift(v.get("registered_on") or "2024-01-01", w.rng.randint(400, 1200))
        doc.title = f"Registration {kind.lower()}: {canon(v['plate'])}"
        why = "pending road tax and fitness" if kind == "SUSPENDED" else "the vehicle reaching end of life"
        doc.body = (f"NOTICE. The registration certificate of vehicle {w.plate(v['plate'], 0.05)} ({vehicle_phrase(w, v)}) "
                    f"registered in the name of {w.name(v.get('owner_name') or '')}"
                    f"{', ' + v['address'] if v.get('address') else ''} stands {kind.lower()} w.e.f. "
                    f"{w.date(doc.issued_on)} on account of {why}. The vehicle shall not ply on public roads.")
        return doc
    reason = w.rng.choice(["duplicate RC issued (original reported lost)", "change of address recorded",
                           "hypothecation endorsement removed after loan closure", "fitness certificate renewed"])
    doc.issued_on = shift(v.get("registered_on") or "2024-01-01", w.rng.randint(60, 900))
    doc.title = f"RTO {rto}: {reason.split(' (')[0]}"
    doc.body = (f"Office of the Registering Authority, {rto}. In respect of {vehicle_phrase(w, v)} No. "
                f"{w.plate(v['plate'])} (owner {w.name(v.get('owner_name') or '')}): {reason} on {w.date(doc.issued_on)}.")
    return doc


def puc_notice(w: Writer, v: V, cert: Dict[str, Any]) -> Doc:
    lapsed = str(cert.get("valid_upto") or "") < "2026-11-11"
    # CONTEXT-REPLACED (R3: an "issued" notice was dated with the certificate's expiry, i.e. in the future; it is
    # issued when the one-year certificate starts. A "lapsed" notice is issued at the lapse, as before):
    # doc = Doc("RTO_NOTICE", f"PUC {'lapsed' if lapsed else 'issued'}: {canon(v['plate'])}", "", "Transport Dept. PUC cell",
    #           str(cert.get("valid_upto") or "2026-01-01"), plates={canon(v["plate"]): 2},
    upto = str(cert.get("valid_upto") or "2026-01-01")
    doc = Doc("RTO_NOTICE", f"PUC {'lapsed' if lapsed else 'issued'}: {canon(v['plate'])}", "", "Transport Dept. PUC cell",
              upto if lapsed else shift(upto, -365), plates={canon(v["plate"]): 2},
    # CONTEXT-END
              links=[("PUC", "POLLUTION_CERT", cert["cert_no"])] + _vehicle_links(v, owner=False))
    doc.body = (f"PUC certificate {cert['cert_no']} for vehicle {w.plate(v['plate'])} ({vehicle_phrase(w, v)}), "
                f"tested at {cert.get('tested_at')}, emission norm {cert.get('emission_norm')}, valid up to "
                f"{w.date(str(cert.get('valid_upto')))}."
                + (" The certificate has lapsed; a challan may be issued if the vehicle is found plying." if lapsed else ""))
    return doc


def camera_news(w: Writer, cam: Dict[str, Any]) -> Doc:
    n = w.rng.randint(40, 900)
    return Doc("NEWS", f"ANPR camera at {cam['location_name']} issues {n} challans", (
        f"Traffic police said the automatic number plate recognition camera at {cam['location_name']} recorded "
        f"{n} violations in a week, mostly over-speeding and red-light jumping. Owners will receive e-challans by SMS."),
        "City Times", f"2026-{w.rng.randint(1, 10):02d}-{w.rng.randint(1, 28):02d}", links=[("CAM", "CAMERAS", cam["camera_id"])])


def lookalike_news(w: Writer, target: str, plate: str, v: Optional[V], name: Optional[str]) -> Doc:
    """Hard negative: a plate one character off `target`, or a same-name person with a different vehicle."""
    make = (v or {}).get("make") or w.rng.choice(["Tata", "Honda", "Kia", "Toyota"])
    model = (v or {}).get("model") or w.rng.choice(["Nexon", "City", "Sonet", "Glanza"])
    colour = (v or {}).get("colour") or w.rng.choice(COLOURS)
    who = name or (v or {}).get("owner_name") or "a local resident"
    doc = Doc("NEWS", f"{colour} {make} {model} towed from no-parking zone", "", "Delhi Daily",
              f"2026-{w.rng.randint(1, 10):02d}-{w.rng.randint(1, 28):02d}", plates={canon(plate): 2},
              distractor_for=target, links=_vehicle_links(v, owner=name is None) if v else [])
    where = w.rng.choice(["Lajpat Nagar", "Sector 62, Noida", "Hinjewadi, Pune", "Cyber City, Gurgaon"])
    doc.body = (f"A {colour.lower()} {make} {model} bearing No. {plate} owned by {who}"
                f"{', a resident of ' + where if name else ''} was towed from a no-parking zone near {where}. "
                f"The owner paid the towing charges and collected the car the same evening.")
    return doc


def challan_notice(w: Writer, v: V, challan: Dict[str, Any], capture: Dict[str, Any]) -> Doc:
    when = str(challan.get("issued_at") or capture.get("captured_at") or "2026-01-01")
    doc = Doc("RTO_NOTICE", f"e-Challan {challan['challan_id']}: {str(challan.get('violation', '')).replace('_', ' ').lower()}",
              "", "Traffic Police e-Challan cell", when[:10], plates={canon(v["plate"]): 2},
              links=[("CAM", "CHALLANS", pk(challan["challan_id"])), ("CAM", "PLATE_CAPTURES", pk(capture["capture_id"])),
                     ("CAM", "CAMERAS", capture["camera_id"])] + _vehicle_links(v, owner=False))
    doc.body = (f"E-challan no. {challan['challan_id']} issued on {w.date(when[:10])} to vehicle {w.plate(v['plate'], 0.1)} "
                f"({vehicle_phrase(w, v)}) for {str(challan.get('violation', '')).replace('_', ' ').lower()} recorded by the "
                f"camera at {capture.get('location_name') or capture['camera_id']} at {when[11:16]} hrs. Fine Rs "
                f"{challan.get('fine_inr')}; status {str(challan.get('challan_status', '')).lower()}.")
    return doc
