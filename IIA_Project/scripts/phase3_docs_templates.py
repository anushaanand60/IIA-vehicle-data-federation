"""CONTEXT-ADDED (R3: unstructured corpus): the document writers used by scripts/phase3_docs_generate.py.

Each writer turns structured rows into one document AND records, for evaluation only, which rows the text was
written from (gold links), which plates it is about (graded relevance) and any conflict planted on purpose.
Noise is deliberate: plates in four spellings and OCR-corrupted, dates in six styles ("12th July 2026"), Hinglish
phrases, spelling variants (Gurgaon/Gurugram, "Kr." for Kumar), partial plates in witness statements.
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

IST = timezone(timedelta(hours=5, minutes=30))
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December"]
OCR_SWAPS = {"0": "O", "O": "0", "8": "B", "B": "8", "2": "Z", "5": "S", "1": "I", "6": "G", "I": "1"}
RTO_CITY = {"DL": "New Delhi", "HR": "Gurugram", "UP": "Noida", "MH": "Pune", "KA": "Bengaluru"}
STATION_CITY = {"MG Road PS": "Gurugram", "Sector 14 PS": "Gurugram", "Sector 29 PS": "Gurugram",
                "Connaught Place PS": "New Delhi", "Karol Bagh PS": "New Delhi", "Civil Lines PS": "Delhi",
                "Lajpat Nagar PS": "New Delhi"}
COLOURS = ["White", "Silver", "Grey", "Black", "Red", "Blue", "Brown", "Maroon"]
HINGLISH = {"theft": ["Complainant ne bataya ki gaadi ghar ke bahar parked thi.", "Subah dekha to gaadi gayab thi.",
                      "Aas-paas ke logon se poochh-taachh ki gayi.", "CCTV footage ki jaanch jaari hai."],
            "accident": ["Driver ne bataya ki brake time pe nahi laga.", "Mauke par koi chot nahi aayi.",
                         "Dono parties ne aapas mein samjhauta kar liya."],
            "survey": ["Gaadi ki condition theek-thaak hai.", "Insured ne original RC dikhayi."]}
Link = Tuple[str, str, Any]


@dataclass
class Doc:
    doc_type: str
    title: str
    body: str
    issuer: str
    issued_on: str
    links: List[Link] = field(default_factory=list)
    plates: Dict[str, int] = field(default_factory=dict)      # canonical plate -> grade (2 subject, 1 mentioned)
    conflicts: List[Dict[str, Any]] = field(default_factory=list)
    distractor_for: Optional[str] = None
    keep: List[str] = field(default_factory=list)              # strings an LLM rewrite must preserve verbatim
    llm: bool = False
    doc_id: str = ""


def canon(p: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(p or "")).upper()


def pk(v: Any) -> Any:
    s = str(v)
    return int(s) if s.isdigit() else s


def epoch_to_iso(v: Any) -> Optional[str]:
    try:
        return datetime.fromtimestamp(int(float(v)), tz=IST).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


def ddmmyyyy_to_iso(v: Any) -> Optional[str]:
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", str(v or ""))
    return f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else None


def shift(iso: str, days: int) -> str:
    return (date.fromisoformat(iso) + timedelta(days=days)).isoformat()


class Writer:
    """Shared noise helpers; the generator subclasses nothing, it just calls these."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng

    def plate(self, plate: str, ocr_p: float = 0.18) -> str:
        p = canon(plate)
        if len(p) < 8:
            return p
        if self.rng.random() < ocr_p:  # one or two OCR confusions, never in the state code
            chars = list(p)
            spots = [i for i in range(2, len(chars)) if chars[i] in OCR_SWAPS]
            for i in self.rng.sample(spots, k=min(len(spots), self.rng.choice([1, 1, 2]))):
                chars[i] = OCR_SWAPS[chars[i]]
            return "".join(chars)
        st, rto, series, num = p[:2], p[2:4], p[4:-4], p[-4:]
        return self.rng.choice([p, f"{st}-{rto}-{series}-{num}", f"{st} {rto} {series} {num}",
                                f"{st}{rto}{series}{num}".lower(), f"{st}-{rto}{series}-{num}"])

    def date(self, iso: str) -> str:
        d = date.fromisoformat(iso)
        r = self.rng.random()
        if r < 0.30:
            suf = "th" if 11 <= d.day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(d.day % 10, "th")
            return f"{d.day}{suf} {MONTHS[d.month - 1]} {d.year}"
        if r < 0.50:
            return d.strftime("%d/%m/%Y")
        if r < 0.65:
            return d.strftime("%d-%m-%Y")
        if r < 0.80:
            return f"{d.day} {MONTHS[d.month - 1][:3]} {d.year}"
        if r < 0.90:
            return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"
        return iso

    def name(self, full: str) -> str:
        s = str(full or "")
        if self.rng.random() < 0.35:
            s = re.sub(r"\bKumar\b", "Kr.", s)
            s = re.sub(r"\bMohammed\b|\bMohammad\b", "Mohd.", s)
        return s

    def city(self, city: str) -> str:
        c = str(city or "")
        if c == "Gurugram" and self.rng.random() < 0.45:
            return "Gurgaon"
        return c

    def hinglish(self, p: float = 0.35, kind: str = "theft") -> str:
        return (" " + self.rng.choice(HINGLISH[kind])) if self.rng.random() < p else ""

    def time(self) -> str:
        h = self.rng.choice([1, 2, 3, 5, 6, 7, 9, 10, 11, 14, 17, 19, 21, 22, 23])
        return f"{h:02d}:{self.rng.choice(['00', '15', '30', '45'])} hrs"


def place_of_station(station: str) -> str:
    return re.sub(r"\s*P\.?S\.?$", "", str(station or "")).strip()


def vehicle_phrase(w: Writer, v: Dict[str, Any], colour: Optional[str] = None, model: Optional[str] = None) -> str:
    return f"{(colour or v.get('colour') or '').lower()} {v.get('make') or ''} {model or v.get('model') or ''}".strip()


def colour_conflict(w: Writer, v: Dict[str, Any]) -> Optional[str]:
    others = [c for c in COLOURS if c.lower() != str(v.get("colour") or "").lower()]
    return w.rng.choice(others) if others else None


def recovered_conflict(doc: Doc, crime: Dict[str, Any], when: str) -> None:
    doc.conflicts.append({"attr": "recovery", "db": ["THEFT", "CRIME_RECORDS", pk(crime["incident_id"])],
                          "db_value": crime.get("case_status"), "text_value": f"recovered on {when}"})
