"""CONTEXT-ADDED (R3: text-vs-database conflicts) flag where a retrieved document contradicts a linked row.

Only rows the document is linked to are compared (doc_linking first), so a news item about another car cannot
raise a conflict. Checks, each one explainable in a sentence:
  * recovery       - text says the vehicle was "recovered"/"traced" (not negated) while the FIR row is OPEN;
  * dates          - a date in the text is classified by its nearest cue word (stolen/reported -> incident_date,
                     recovered -> recovered_on, loss -> loss_date, valid till -> policy_until) and compared with the
                     linked row's time column; the column and its encoding come from data/context_graph.json;
  * colour / make / model - inside a vehicle phrase ("blue Kia Seltos") that differs from the registration row
                     of the plate the document is about;
  * plate          - the document prints a linked chassis number next to a different plate.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from mediator.context_types import Conflict, ContextRow, Mention
from mediator.similarity import ocr_edit_similarity
from sources.docs.textindex import canon, plate_spans

IST = timezone(timedelta(hours=5, minutes=30))
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
DATE_RE = re.compile(
    r"(?P<d1>\d{1,2})(?:st|nd|rd|th)?\s+(?P<m1>[A-Za-z]{3,9})\.?,?\s+(?P<y1>\d{4})"
    r"|(?P<m2>[A-Za-z]{3,9})\s+(?P<d2>\d{1,2}),?\s+(?P<y2>\d{4})"
    r"|(?P<d3>\d{1,2})[/-](?P<m3>\d{1,2})[/-](?P<y3>\d{4})"
    r"|(?P<y4>\d{4})-(?P<m4>\d{2})-(?P<d4>\d{2})")
# cue word -> (date class, (source, table) whose graph time column it is compared with, tolerance in days)
CUES: List[Tuple[str, str, Tuple[str, str], int]] = [
    (r"recovered|traced", "recovered_on", ("THEFT", "STOLEN_PROPERTY"), 2),
    (r"date of loss|loss on|loss", "loss_date", ("INS", "CLAIMS"), 1),
    (r"valid till|valid up ?to|till|expir\w*", "policy_until", ("INS", "POLICY_RECORDS"), 1),
    (r"valid from|w\.e\.f\.?|issued", "ignore", ("", ""), 0),
    (r"stolen|theft|missing|chori|night of|reported|registered|lodged", "incident_date", ("THEFT", "CRIME_RECORDS"), 3),
]
COLOURS = {"white", "silver", "grey", "gray", "black", "red", "blue", "brown", "maroon", "beige", "green", "yellow", "orange"}
MODELS = {"creta", "venue", "i20", "verna", "swift", "baleno", "dzire", "brezza", "city", "amaze", "nexon", "punch",
          "harrier", "tiago", "seltos", "sonet", "carens", "innova", "fortuner", "glanza", "xuv700", "scorpio", "thar"}
MAKES = ["maruti suzuki", "maruti", "hyundai", "tata", "mahindra", "kia", "honda", "toyota", "renault", "skoda",
         "volkswagen", "nissan", "ford", "mg"]
VEHICLE_PHRASE = re.compile(rf"\b(\w+)\s+({'|'.join(MAKES)})\s+(\w+)", re.I)   # "<colour> <make> <model>"
NEGATION = re.compile(r"(not|yet to be|never|no|nahi)\W+(\w+\W+){0,2}$", re.I)


def to_date(value: Any, fmt: str) -> Optional[date]:
    try:
        if fmt == "epoch":
            return datetime.fromtimestamp(int(float(value)), tz=IST).date()
        if fmt == "ddmmyyyy":
            d, m, y = str(value).split("/")
            return date(int(y), int(m), int(d))
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError, OSError):
        return None


def text_dates(body: str) -> List[Tuple[date, int, int]]:
    out = []
    for m in DATE_RE.finditer(body):
        g = m.groupdict()
        try:
            if g["y1"] or g["y2"]:
                mon = MONTHS.get((g["m1"] or g["m2"]).lower()[:3])
                if not mon:
                    continue
                out.append((date(int(g["y1"] or g["y2"]), mon, int(g["d1"] or g["d2"])), m.start(), m.end()))
            elif g["y3"]:
                out.append((date(int(g["y3"]), int(g["m3"]), int(g["d3"])), m.start(), m.end()))
            else:
                out.append((date(int(g["y4"]), int(g["m4"]), int(g["d4"])), m.start(), m.end()))
        except ValueError:
            continue
    return out


def date_class(body: str, start: int, end: int) -> Optional[Tuple[str, Tuple[str, str], int]]:
    """Nearest cue word: before the date first ("recovered on X"), else after it ("On X ... was stolen").

    Windows are fixed character spans, not sentences: abbreviations such as "P.S." and "H.No." break naive
    sentence splitting. Trade-off: a cue from a neighbouring clause can occasionally be picked up."""
    s0 = max(start - 100, body.rfind(". ", 0, start) + 1, 0)
    before, after = body[s0:start].lower(), body[end: end + 160].lower()
    best = None
    for pattern, cls, src, tol in CUES:
        for m in re.finditer(pattern, before):
            dist = len(before) - m.end()
            if best is None or dist < best[0]:
                best = (dist, cls, src, tol)
    if best is None:
        for pattern, cls, src, tol in CUES:
            m = re.search(pattern, after)
            if m and (best is None or m.start() < best[0]):
                best = (m.start(), cls, src, tol)
    return None if best is None or best[1] == "ignore" else best[1:]


def detect(doc_id: str, body: str, mentions: Sequence[Mention], linked: Sequence[ContextRow],
           graph: Mapping[str, Any]) -> List[Conflict]:
    out: List[Conflict] = []
    low = body.lower()
    for row in [r for r in linked if (r.source_id, r.table) == ("THEFT", "CRIME_RECORDS")]:
        if str(row.row.get("case_status", "")).upper() == "OPEN":
            for m in re.finditer(r"\b(recovered|traced)\b", low):
                if not NEGATION.search(low[max(0, m.start() - 30): m.start()]):
                    out.append(Conflict("recovery", "OPEN", "THEFT", body[m.start(): m.end() + 20].strip(), doc_id,
                                        (m.start(), m.end()), f"text says the vehicle was {m.group(1)}; FIR {row.pk} is OPEN"))
                    break
    tables = graph.get("sources", {})
    for d, s, e in text_dates(body):
        cls = date_class(body, s, e)
        if not cls:
            continue
        attr, (sid, table), tol = cls
        time = tables.get(sid, {}).get("tables", {}).get(table, {}).get("time") or {}
        for row in [r for r in linked if (r.source_id, r.table) == (sid, table)]:
            db = to_date(row.row.get(time.get("col")), time.get("fmt", "iso_date")) if time else None
            if db and abs((db - d).days) > tol:
                out.append(Conflict(attr, db.isoformat(), sid, d.isoformat(), doc_id, (s, e),
                                    f"text date {body[s:e]!r} vs {table}.{time['col']} = {db.isoformat()}"))
    out += _vehicle_conflicts(doc_id, body, mentions, linked)
    seen, unique = set(), []
    for c in out:
        if (c.attr, str(c.text_value)) not in seen:
            seen.add((c.attr, str(c.text_value)))
            unique.append(c)
    return unique


def _vehicle_conflicts(doc_id: str, body: str, mentions: Sequence[Mention], linked: Sequence[ContextRow]) -> List[Conflict]:
    out: List[Conflict] = []
    # the registration's descriptive columns, as named in docs/phase3/CONTRACT.md (REG.VEHICLE_REGISTRATION)
    regs = [r for r in linked if (r.source_id, r.table) == ("REG", "VEHICLE_REGISTRATION")]
    spans = plate_spans(body)
    for reg in regs:
        plate, make = canon(reg.row.get("registration_no")), str(reg.row.get("make") or "").lower()
        colour, model = str(reg.row.get("colour") or "").lower(), str(reg.row.get("model") or "").lower()
        if not make:
            continue
        for m in VEHICLE_PHRASE.finditer(body):
            near = min(spans, key=lambda sp: abs(sp[0] - m.end()), default=None)
            if near and ocr_edit_similarity(near[2], plate) < 0.92 and len(spans) > 1:
                continue   # this phrase describes another plate in a multi-vehicle text
            said_colour, said_make, said_model = m.group(1).lower(), m.group(2).lower(), m.group(3).lower()
            if said_colour not in COLOURS and said_model not in MODELS:
                continue   # "the Hyundai service centre": not a description of a car
            note = f"text: {m.group(0)!r}; REG registration: {colour} {make} {model}"
            if said_colour in COLOURS and colour and {said_colour, colour} != {"grey", "gray"} and said_colour != colour:
                out.append(Conflict("colour", reg.row.get("colour"), "REG", said_colour.title(), doc_id,
                                    (m.start(1), m.end(1)), note))
            if not (make.startswith(said_make) or said_make.startswith(make)):
                out.append(Conflict("make", reg.row.get("make"), "REG", m.group(2), doc_id, (m.start(2), m.end(2)), note))
            elif said_model in MODELS and model and said_model != model:
                out.append(Conflict("model", reg.row.get("model"), "REG", m.group(3), doc_id, (m.start(3), m.end(3)), note))
    chassis = [m for m in mentions if m.attr == "chassis"]
    plates = {canon(r.row.get("registration_no")) for r in regs}
    for m in chassis:
        for s, e, tok in spans:
            if abs(s - m.end) < 80 and plates and max(ocr_edit_similarity(tok, p) for p in plates) < 0.92:
                out.append(Conflict("plate", sorted(plates)[0], "REG", body[s:e], doc_id, (s, e),
                                    f"chassis {m.text} printed with plate {body[s:e]}"))
    return out
