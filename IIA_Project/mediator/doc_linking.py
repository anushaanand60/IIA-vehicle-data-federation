"""CONTEXT-ADDED (R3: tie retrieved text back to database rows) EROCS-style entity-document linking.

The context (rows from REG/INS/THEFT/CAM/PUC) is the dictionary; a document is scanned for mentions of its values:
  * anchors (plate, chassis, FIR no., policy no., ...) - matched approximately: identifier-shaped spans in the text
    are compared with the OCR-aware edit similarity (0<->O cheap, other substitutions a full edit);
  * descriptors (names, station, place, make, model, colour, insurer) - matched on normalised tokens
    (Gurgaon == Gurugram, Kr. == Kumar); person names also token-by-token fuzzy (edit similarity >= 0.85).
A row is linked when the summed weight of the mentions of ITS values reaches SUPPORT, and only if the document is
coherently about the context (an anchor hit, or several descriptor kinds including a specific one) - the EROCS idea
that an entity is identified by several of its terms co-occurring, not by one common word.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Tuple

from datetime import date

from mediator.context_types import Mention
from mediator.doc_conflicts import text_dates
from mediator.similarity import edit_similarity, ocr_edit_similarity
from sources.docs.textindex import canon, is_identifier, plate_spans, tokenize_spans

RowKey = Tuple[str, str, Any]
# How much one mention says about the row carrying the value. Unique identifiers almost decide alone; a plate
# (0.45) needs one more matching fact of the same row, because every source row about the car carries the plate.
ATTR_WEIGHT = {"chassis": 0.9, "fir_no": 0.9, "policy_no": 0.9, "claim_no": 0.9, "cert_no": 0.9, "plate": 0.45,
               "person": 0.6, "station": 0.5, "place": 0.5, "insurer": 0.5, "address": 0.4, "model": 0.25,
               "city": 0.2, "make": 0.15, "colour": 0.1, "date": 0.5}
OBSERVATIONAL = {"CAM"}   # a camera EVENT row (capture, challan) needs its date in the text, not just the car
DATE_TOLERANCE = 1        # days; "stolen on the 12th" is often reported on the 13th
SPECIFIC = {"person", "station", "place", "address", "insurer"}   # descriptors that can single out one case
SUPPORT = 0.5           # minimum summed mention weight for a row link
ANCHOR_SIM = 0.92       # same OCR-tolerant threshold as the DOCS index (one non-OCR substitution is refused)
NAME_SIM = 0.85
COHERENCE = 0.9        # without an anchor: summed best weight per descriptor kind, incl. one SPECIFIC kind
SNIPPET_RADIUS = 140


@dataclass
class CtxValue:
    value: str                    # as the source stores it (after anchor formatting, e.g. POL-000562)
    attr: str                     # plate, chassis, fir_no, ..., person, station, place, make, model, colour
    kind: str                     # "anchor" | "descriptor" | "date" (ISO value from the row's time column)
    weight: float = 0.0           # query weight: row score x trust x gamma^hops x rarity (see doc_retrieval)
    rows: List[RowKey] = field(default_factory=list)


def _anchor_mentions(body: str, anchors: Sequence[CtxValue]) -> List[Mention]:
    spans = [(s, e, tok) for s, e, tok in plate_spans(body)]
    taken = {(s, e) for s, e, _ in spans}
    spans += [(s, e, tok) for tok, s, e in tokenize_spans(body) if is_identifier(tok) and (s, e) not in taken]
    out = []
    for s, e, tok in spans:
        best, best_sim = None, 0.0
        for a in anchors:
            target = canon(a.value)
            if abs(len(target) - len(tok)) > 2:
                continue
            sim = 1.0 if target == tok else ocr_edit_similarity(tok, target)
            if sim > best_sim:
                best, best_sim = a, sim
        if best is not None and best_sim >= ANCHOR_SIM:
            out.append(Mention(text=body[s:e], kind="anchor", attr=best.attr, start=s, end=e, matched_value=best.value,
                               similarity=round(best_sim, 3), weight=round(ATTR_WEIGHT.get(best.attr, 0.5) * best_sim, 3)))
    return out


def _descriptor_mentions(body: str, descriptors: Sequence[CtxValue]) -> List[Mention]:
    toks = [(t, s, e) for t, s, e in tokenize_spans(body)]
    words = [t for t, _, _ in toks]
    out: List[Mention] = []
    used: set = set()
    for d in sorted(descriptors, key=lambda d: -len(d.value)):   # longest phrase first: "Sector 29 Crossing" before "Sector"
        want = [t for t, _, _ in tokenize_spans(d.value)]
        n = len(want)
        if not n:
            continue
        for i in range(len(words) - n + 1):
            if any(j in used for j in range(i, i + n)):
                continue
            if d.attr == "person":
                sims = [1.0 if a == b else edit_similarity(a, b) for a, b in zip(words[i:i + n], want)]
                sim = min(sims)
                if sim < NAME_SIM or (n == 1):   # a lone first name is not a person mention
                    continue
            elif words[i:i + n] == want:
                sim = 1.0
            else:
                continue
            s, e = toks[i][1], toks[i + n - 1][2]
            used.update(range(i, i + n))
            out.append(Mention(text=body[s:e], kind="descriptor", attr=d.attr, start=s, end=e, matched_value=d.value,
                               similarity=round(sim, 3), weight=round(ATTR_WEIGHT.get(d.attr, 0.15) * sim, 3)))
    return out


def _date_mentions(body: str, dates: Sequence[CtxValue]) -> List[Mention]:
    out = []
    for d, s, e in text_dates(body):
        for v in dates:
            if abs((date.fromisoformat(v.value) - d).days) <= DATE_TOLERANCE:
                out.append(Mention(text=body[s:e], kind="descriptor", attr="date", start=s, end=e, matched_value=v.value,
                                   similarity=1.0, weight=ATTR_WEIGHT["date"]))
    return out


def find_mentions(body: str, values: Sequence[CtxValue]) -> List[Mention]:
    anchors = [v for v in values if v.kind == "anchor"]
    descriptors = [v for v in values if v.kind == "descriptor"]
    found = (_anchor_mentions(body, anchors) + _descriptor_mentions(body, descriptors)
             + _date_mentions(body, [v for v in values if v.kind == "date"]))
    return sorted(found, key=lambda m: m.start)


def is_coherent(mentions: Sequence[Mention]) -> bool:
    """Is the text about THIS context? An anchor says so; otherwise several kinds of descriptor must co-occur and
    one must be specific (a person, station, place): "silver Maruti Suzuki Swift in Delhi" fits hundreds of cars."""
    if any(m.kind == "anchor" for m in mentions):
        return True
    best: Dict[str, float] = {}
    for m in mentions:
        best[m.attr] = max(best.get(m.attr, 0.0), m.weight)
    return bool(SPECIFIC & set(best)) and sum(best.values()) >= COHERENCE


def link_rows(mentions: Sequence[Mention], values: Sequence[CtxValue]) -> List[RowKey]:
    """Rows whose own values are mentioned with summed weight >= SUPPORT (each value counted once per row).

    Key rule: a row that HAS an identifier in the context (a plate, FIR no., policy no.) must have one of them
    mentioned - a police station named in a news item does not link every FIR of that station. A camera row
    (observational) also needs its date mentioned: the car being described is not the same as this sighting."""
    if not is_coherent(mentions):
        return []
    by_value: Dict[Tuple[str, str], CtxValue] = {(v.attr, v.value): v for v in values}
    keyed = {row for v in values if v.kind == "anchor" for row in v.rows}
    dated = {row for v in values if v.kind == "date" for row in v.rows}   # event rows (they have a time column)
    best: Dict[Tuple[str, str], float] = {}
    for m in mentions:
        key = (m.attr, m.matched_value)
        best[key] = max(best.get(key, 0.0), m.weight)
    support: Dict[RowKey, float] = defaultdict(float)
    evidence: Dict[RowKey, set] = defaultdict(set)
    for key, w in best.items():
        cv = by_value.get(key)
        for row in cv.rows if cv else []:
            support[row] += w
            evidence[row].add(cv.kind)
    linked = []
    for row, s in sorted(support.items(), key=lambda p: -p[1]):
        if s < SUPPORT - 1e-9 or (row in keyed and "anchor" not in evidence[row]):
            continue
        if row[0] in OBSERVATIONAL and row in dated and "date" not in evidence[row]:
            continue
        linked.append(row)
    return linked


def snippet(body: str, mentions: Sequence[Mention]) -> str:
    """The passage around the strongest mention (anchors first), cut at word boundaries."""
    if not body:
        return ""
    if not mentions:
        return body[: 2 * SNIPPET_RADIUS].rsplit(" ", 1)[0] + ("..." if len(body) > 2 * SNIPPET_RADIUS else "")
    m = max(mentions, key=lambda m: (m.kind == "anchor", m.weight))
    lo, hi = max(0, m.start - SNIPPET_RADIUS), min(len(body), m.end + SNIPPET_RADIUS)
    if lo > 0:
        lo = body.find(" ", lo) + 1 or lo
    if hi < len(body):
        hi = body.rfind(" ", m.end, hi) if body.rfind(" ", m.end, hi) > 0 else hi
    return ("..." if lo > 0 else "") + body[lo:hi].strip() + ("..." if hi < len(body) else "")
