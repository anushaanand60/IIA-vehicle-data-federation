"""CONTEXT-ADDED (R2: free-text queries): turn a typed question into a ParsedQuery of typed predicates.

    parse("list all stolen white Creta Gurgaon")
      -> stolen_status = True, vehicle_colour = White, vehicle_model = Creta, vehicle_make = Hyundai,
         location ~ Gurugram

Rules first (plates, makes/models, colours, statuses, places, relative dates, owner names), every one
explainable from mediator/query_vocab.py. The local LLM (JSON mode) is asked only about words the rules left
unexplained, and nothing it says is trusted blindly: each proposed predicate must name an allowed attribute,
an allowed operator and an allowed value, or it is dropped with a note. It never writes SQL. With
use_llm=False, or with Ollama off, the rules-only parse is returned. Results are cached per query string.
"""
from __future__ import annotations

import calendar
import json
import re
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from mediator import llm
from mediator import query_vocab as V
from mediator.context_types import ParsedQuery, Predicate
from mediator.similarity import jaro_winkler

PLATE_TEXT = re.compile(r"\b([A-Za-z]{2})[\s-]*(\d{1,2})[\s-]*([A-Za-z]{0,3})[\s-]*(\d{4})\b")
BH_TEXT = re.compile(r"\b(\d{2})[\s-]*BH[\s-]*(\d{4})[\s-]*([A-Za-z]{1,2})\b", re.I)
SEEN_CUE = re.compile(r"\b(?:seen|spotted|captured|sighted|caught on camera|passing)\b[\w\s]{0,12}$")
NAME_CUE = re.compile(r"\b(?:owned by|owner(?:\s+is|\s+named)?|belonging to|registered to|in the name of)\s+"
                      r"(?:(?:mr|mrs|ms|shri|smt|dr)\.?\s+)?([A-Za-z][A-Za-z'.-]+(?:\s+[A-Za-z][A-Za-z'.-]+){0,2})", re.I)
FUZZY_MIN = 0.92            # Jaro-Winkler: catches "cretta", "hyundaii"; below this, ordinary words start matching
LLM_CONFIDENCE_CAP = 0.8    # a model-proposed predicate is never as certain as a rule hit
SYNONYM_CAP = 0.5           # a value the text does not spell ("rouge" -> Red) counts at half weight
NAMED_ATTRS = ("vehicle_make", "vehicle_model", "location", "last_seen_location", "indicator")
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_cache: Dict[Tuple[str, bool, str], ParsedQuery] = {}


def canonical_plate(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(text)).upper()


def _blank(rest: str, start: int, end: int) -> str:
    return rest[:start] + " " * (end - start) + rest[end:]


def _month_window(y: int, m: int) -> Tuple[str, str]:
    return date(y, m, 1).isoformat(), date(y, m, calendar.monthrange(y, m)[1]).isoformat()


def _time_window(rest: str, today: date) -> Tuple[Optional[Tuple[str, str]], str, Optional[str]]:
    """Relative and absolute dates -> inclusive ISO window. Returns (window, rest with the phrase blanked, note)."""
    first = today.replace(day=1)
    prev = first - timedelta(days=1)
    rules: List[Tuple[str, Any]] = [
        (r"\blast\s+month\b|\bprevious\s+month\b", lambda m: _month_window(prev.year, prev.month)),
        (r"\bthis\s+month\b", lambda m: (first.isoformat(), today.isoformat())),
        (r"\b(?:last|past)\s+(\d{1,3})\s+(day|week|month)s?\b", lambda m: (
            (today - timedelta(days=int(m.group(1)) * {"day": 1, "week": 7, "month": 30}[m.group(2)])).isoformat(),
            today.isoformat())),
        (r"\b(?:last|past)\s+week\b", lambda m: ((today - timedelta(days=7)).isoformat(), today.isoformat())),
        (r"\byesterday\b", lambda m: ((today - timedelta(days=1)).isoformat(),) * 2),
        (r"\btoday\b", lambda m: (today.isoformat(),) * 2),
        (r"\bthis\s+year\b", lambda m: (date(today.year, 1, 1).isoformat(), today.isoformat())),
        (r"\blast\s+year\b", lambda m: (date(today.year - 1, 1, 1).isoformat(), date(today.year - 1, 12, 31).isoformat())),
        (r"\b(?:since|after|from)\s+(\d{4}-\d{2}-\d{2})\b", lambda m: (m.group(1), today.isoformat())),
        (r"\b(?:in\s+)?(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{4})\b",
         lambda m: _month_window(int(m.group(2)), MONTHS[m.group(1)])),
    ]
    for pattern, make in rules:
        m = re.search(pattern, rest)
        if m:
            window = make(m)
            return window, _blank(rest, m.start(), m.end()), f"time window {window[0]}..{window[1]} from '{m.group(0).strip()}' (rule, today={today})"
    return None, rest, None


class _Builder:
    def __init__(self, text: str) -> None:
        self.q = ParsedQuery(text=text)
        self.explained: set = set()      # leftover words an accepted LLM predicate accounts for

    def add(self, attr: str, op: str, value: Any, conf: float, note: str) -> None:
        if any(p.attr == attr and p.value == value for p in self.q.predicates):
            return
        self.q.predicates.append(Predicate(attr=attr, op=op, value=value, confidence=round(conf, 3)))
        self.q.notes.append(f"{attr} {op} {value!r}: {note}")


def _rules(text: str, today: date) -> Tuple[_Builder, str]:
    b = _Builder(text)
    rest = text.lower()
    for m in list(PLATE_TEXT.finditer(text)) + list(BH_TEXT.finditer(text)):
        plate = canonical_plate(m.group(0))
        if (m.re is BH_TEXT or plate[:2] in _state_codes()) and plate not in b.q.plates:
            b.q.plates.append(plate)
            b.q.notes.append(f"plate {plate} from '{m.group(0)}' (rule: Indian plate pattern, canonicalised)")
            rest = _blank(rest, m.start(), m.end())
    for m in NAME_CUE.finditer(text):
        words = [w for w in m.group(1).split() if w.lower() not in V.STOPWORDS]
        if words:
            b.add("owner_name", "~", " ".join(words).title(), 1.0, f"rule: name after '{m.group(0).split()[0]}'")
            rest = _blank(rest, m.start(), m.end())
    for pattern, code in V.INDICATOR_PHRASES:
        for m in re.finditer(pattern, rest):
            b.add("indicator", "=", code, 1.0, f"rule: relational phrase '{m.group(0).strip()}'")
            if code in V.INDICATOR_IMPLIES:
                a, op, v = V.INDICATOR_IMPLIES[code]
                b.add(a, op, v, 1.0, f"implied by {code}")
            span = re.sub(r"[a-z]+", lambda w: " " * len(w.group(0)) if w.group(0) in V.INDICATOR_WORDS else w.group(0), m.group(0))
            rest = rest[:m.start()] + span + rest[m.end():]
    window, rest, note = _time_window(rest, today)
    if window and note:
        b.q.time_window = window
        b.q.notes.append(note)
    for pattern, attr, op, value in V.STATUS_PHRASES:
        for m in re.finditer(pattern, rest):
            b.add(attr, op, value, 1.0, f"rule: status phrase '{m.group(0).strip()}'")
            rest = _blank(rest, m.start(), m.end())
    for place, aliases in V.PLACES.items():
        for alias in sorted(aliases, key=len, reverse=True):
            for m in re.finditer(r"\b" + re.escape(alias) + r"\b", rest):
                attr = "last_seen_location" if SEEN_CUE.search(text.lower()[:m.start()]) else "location"
                b.add(attr, "~", place, 1.0, f"rule: place alias '{alias}'" + (" after a sighting verb" if attr != "location" else ""))
                rest = _blank(rest, m.start(), m.end())
    rest = _vehicle_words(b, rest, text.lower())
    rest = re.sub(r"\b(?:seen|spotted|captured|sighted|in|at|near)\b", " ", rest)
    return b, rest


def _vehicle_words(b: _Builder, rest: str, low: str) -> str:
    for word in re.findall(r"[a-z0-9]+", rest):
        exact = word in V.MODELS or word in V.MAKES or word in V.COLOURS
        hit, sim = (word, 1.0) if exact else _fuzzy(word)
        if not hit:
            continue
        if hit in V.MODELS and not (hit in V.AMBIGUOUS_MODELS and V.AMBIGUOUS_MODELS[hit] not in low):
            make, model = V.MODELS[hit]
            b.add("vehicle_model", "=", model, sim, f"rule: model word '{word}'" + ("" if exact else f" (fuzzy {sim:.2f})"))
            b.add("vehicle_make", "=", make, sim, f"implied by model {model}")
        elif hit in V.MAKES:
            b.add("vehicle_make", "=", V.MAKES[hit], sim, f"rule: make word '{word}'" + ("" if exact else f" (fuzzy {sim:.2f})"))
        elif hit in V.COLOURS:
            b.add("vehicle_colour", "=", V.COLOURS[hit], sim, f"rule: colour word '{word}'" + ("" if exact else f" (fuzzy {sim:.2f})"))
        else:
            continue
        rest = re.sub(r"\b" + re.escape(word) + r"\b", " ", rest, count=1)
    return rest


def _fuzzy(word: str) -> Tuple[Optional[str], float]:
    if len(word) < 4 or word in V.STOPWORDS:
        return None, 0.0
    best, score = None, 0.0
    for key in list(V.MODELS) + list(V.MAKES) + list(V.COLOURS):
        s = jaro_winkler(word, key)
        if s > score:
            best, score = key, s
    return (best, score) if score >= FUZZY_MIN else (None, 0.0)


def _state_codes() -> frozenset:
    try:
        from mediator.lexicons import INDIAN_STATE_CODES
        return INDIAN_STATE_CODES
    except Exception:  # lexicons pulls in the registry; plate detection must not depend on it loading
        return frozenset({"DL", "HR", "UP", "MH", "KA", "RJ", "TN", "GJ", "PB", "WB"})


def _leftover(rest: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9']+", rest) if w not in V.STOPWORDS and len(w) > 1]


def _llm_prompt(text: str, leftover: List[str], known: List[Predicate]) -> str:
    schema = {a: (V.allowed_values(a) if a != "owner_name" else "a person's name copied from the text")
              for a in V.ENUM_ATTRS + V.BOOL_ATTRS + V.FREE_ATTRS}
    ops = {a: list(o) for a, o in V.OPS.items()}
    return ("You map words of a vehicle-search question to filters. Use ONLY these attributes and values:\n"
            f"{json.dumps(schema)}\nAllowed operators per attribute: {json.dumps(ops)}\n"
            f"Question: {text!r}\nAlready understood: {json.dumps([(p.attr, p.op, p.value) for p in known], default=str)}\n"
            f"Explain only these leftover words: {json.dumps(leftover)}\n"
            'Answer JSON {"predicates": [{"word": <the leftover word>, "attr": ..., "op": ..., "value": ..., '
            '"confidence": 0..1}]}; use an empty list if the words are not a filter. Never guess a make or model '
            "from a body type; never invent values.")


def _apply_llm(b: _Builder, leftover: List[str]) -> None:
    out = llm.generate_json(_llm_prompt(b.q.text, leftover, b.q.predicates))
    if out is None:
        b.q.notes.append(f"LLM unavailable; left as keywords: {leftover}")
        return
    b.q.parser = "rules+llm"
    rule_attrs = {p.attr for p in b.q.predicates}
    proposals = out.get("predicates") if isinstance(out.get("predicates"), list) else []
    kept: List[Tuple[str, str, Any, float, str]] = []
    for raw in proposals:
        verdict = validate(raw, b.q, leftover, rule_attrs)
        if isinstance(verdict, str):
            b.q.notes.append(f"LLM proposal {raw!r} dropped: {verdict}")
        else:
            kept.append(verdict)
    for attr in {k[0] for k in kept}:
        values = {k[2] for k in kept if k[0] == attr}
        if len(values) > 1:   # the model contradicts itself: trust neither guess
            b.q.notes.append(f"LLM proposals for {attr} dropped: conflicting values {sorted(map(str, values))}")
            kept = [k for k in kept if k[0] != attr]
    for attr, op, value, conf, how in kept:
        b.add(attr, op, value, conf, f"LLM, {how}")
        b.explained.update(w for w in leftover if f"'{w}'" in how)
        make = next((mk for mk, md in V.MODELS.values() if md == value), None) if attr == "vehicle_model" else None
        if make and "vehicle_make" not in rule_attrs:
            b.add("vehicle_make", "=", make, conf, f"implied by LLM model {value}")


def _grounded(word: str, attr: str, value: Any) -> bool:
    """Does the cited word spell the value (or one of its aliases), give or take a typo?"""
    names = [str(value).lower()] + (V.place_aliases(str(value)) if "location" in attr else []) +         [k for k, v in list(V.MAKES.items()) + list(V.COLOURS.items()) if v == value]
    return any(jaro_winkler(word, n) >= 0.85 or n in word for n in names)


def validate(raw: Any, q: ParsedQuery, leftover: Optional[List[str]] = None,
             rule_attrs: Optional[set] = None) -> Any:
    """(attr, op, value, confidence, how) if the proposal passes every check, else the reason it does not.

    Checks, in order: shape; attribute and operator in the allow-list; value in the vocabulary; the cited word is
    one the rules left over; proper names (make, model, place, owner) must be spelled in the text, never
    inferred ("SUV" is not a Toyota); descriptive values (colour, status) may be synonyms ("rouge" -> Red) but
    then count at half weight; rules win over the model.
    """
    if not isinstance(raw, dict):
        return "not an object"
    attr, op = str(raw.get("attr", "")), str(raw.get("op", "="))
    if attr not in V.OPS:
        return f"attribute {attr!r} is not in the schema registry allow-list"
    if op not in V.OPS[attr]:
        return f"operator {op!r} not allowed for {attr}"
    value = V.canonical_value(attr, raw.get("value"))
    if value is None:
        return f"value {raw.get('value')!r} not allowed for {attr}"
    if attr in (rule_attrs if rule_attrs is not None else {p.attr for p in q.predicates}):
        return f"{attr} already set by a rule (rules win)"
    if attr == "owner_name":
        if not all(w.lower() in q.text.lower() for w in str(value).split()):
            return "name does not occur in the question (hallucination guard)"
        return attr, op, value, min(_conf(raw), LLM_CONFIDENCE_CAP), "name copied from the text"
    word = str(raw.get("word", "")).lower().strip()
    if leftover is not None and word not in leftover:
        return f"cites {word!r}, which is not a word the rules left unexplained"
    if _grounded(word, attr, value):
        return attr, op, value, min(_conf(raw), LLM_CONFIDENCE_CAP), f"'{word}' spells it (capped at {LLM_CONFIDENCE_CAP})"
    if attr in NAMED_ATTRS:
        return f"{attr} is a name and {word!r} does not spell {value!r} (names are never inferred)"
    return attr, op, value, min(_conf(raw), SYNONYM_CAP), f"synonym '{word}' (half weight)"


def _conf(raw: Dict[str, Any]) -> float:
    try:
        return max(0.0, float(raw.get("confidence", 0.6)))
    except (TypeError, ValueError):
        return 0.6


def parse(text: str, use_llm: bool = True, today: Optional[date] = None) -> ParsedQuery:
    """Free text -> ParsedQuery. Never raises; an empty question gives an empty query."""
    today = today or date.today()
    key = (text.strip(), use_llm, today.isoformat())
    if key in _cache:
        return _cache[key]
    b, rest = _rules(text or "", today)
    leftover = _leftover(rest)
    if leftover and use_llm:
        _apply_llm(b, leftover)
    b.q.keywords = [w for w in leftover if w not in b.explained]
    if not b.q.predicates and not b.q.plates:
        b.q.notes.append("no filter recognised; the question is kept as keywords")
    _cache[key] = b.q
    return b.q
