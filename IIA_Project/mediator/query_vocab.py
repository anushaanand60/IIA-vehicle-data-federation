"""CONTEXT-ADDED (R2: free-text queries): the vocabulary free text is checked against.

One allow-list serves both parsers: the rules look words up here, and anything the local LLM proposes is
accepted only if its attribute, operator and value appear here. Values are the global vocabulary (as the
mediator shows them), never source encodings: "stolen" is `stolen_status = True`, and it is the top-k
module's job to say how THEFT stores that ('Y'). `refresh_vocabulary(client)` widens the lists with the
distinct values the live sources actually hold, so a new model name in REG is understood without a code
change.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

# brand spellings (incl. the misspelling REG really stores) -> canonical make, as transforms.fix_make writes it
MAKES: Dict[str, str] = {
    "hyundai": "Hyundai", "hyundia": "Hyundai", "maruti": "Maruti Suzuki", "suzuki": "Maruti Suzuki",
    "honda": "Honda", "kia": "Kia", "mahindra": "Mahindra", "tata": "Tata", "toyota": "Toyota",
    "renault": "Renault", "skoda": "Skoda", "volkswagen": "Volkswagen", "mg": "MG",
}
# model -> (make, model). Naming the model implies the make (Creta -> Hyundai).
MODELS: Dict[str, Tuple[str, str]] = {
    "creta": ("Hyundai", "Creta"), "i20": ("Hyundai", "i20"), "venue": ("Hyundai", "Venue"),
    "verna": ("Hyundai", "Verna"), "swift": ("Maruti Suzuki", "Swift"), "baleno": ("Maruti Suzuki", "Baleno"),
    "dzire": ("Maruti Suzuki", "Dzire"), "brezza": ("Maruti Suzuki", "Brezza"), "city": ("Honda", "City"),
    "amaze": ("Honda", "Amaze"), "seltos": ("Kia", "Seltos"), "sonet": ("Kia", "Sonet"),
    "xuv700": ("Mahindra", "XUV700"), "scorpio": ("Mahindra", "Scorpio"), "thar": ("Mahindra", "Thar"),
    "nexon": ("Tata", "Nexon"), "punch": ("Tata", "Punch"), "innova": ("Toyota", "Innova"),
    "fortuner": ("Toyota", "Fortuner"),
}
AMBIGUOUS_MODELS = {"city": "honda", "punch": "tata", "venue": "hyundai"}  # also English words: need the make nearby
COLOURS: Dict[str, str] = {c.lower(): c for c in
                           ["White", "Black", "Grey", "Silver", "Red", "Blue", "Brown", "Green", "Yellow", "Orange", "Maroon"]}
COLOURS["gray"] = "Grey"

# canonical place -> aliases as they appear in camera locations, zones, police stations, test centres, cities
PLACES: Dict[str, List[str]] = {
    "Gurugram": ["gurugram", "gurgaon", "ggn", "cyber city", "mg road", "sector 14", "sector 29", "nh8"],
    "Noida": ["noida", "greater noida", "yamuna expressway"],
    "Delhi": ["delhi", "new delhi", "connaught place", "karol bagh", "civil lines", "lajpat nagar", "ring road"],
    "Faridabad": ["faridabad"], "Ghaziabad": ["ghaziabad"], "Agra": ["agra"], "Jaipur": ["jaipur"],
    "Mumbai": ["mumbai", "bombay"], "Pune": ["pune"], "Bengaluru": ["bengaluru", "bangalore"],
    "Chennai": ["chennai", "madras"], "Lucknow": ["lucknow"],
}

# phrase (regex, lower-case text) -> (attr, op, value). Longer, more specific phrases first.
STATUS_PHRASES: List[Tuple[str, str, str, Any]] = [
    (r"\bnot recovered\b|\bstill missing\b", "stolen_status", "=", True),
    (r"\brecovered\b", "recovered_status", "=", True),
    (r"\b(?:expired|lapsed|lapse[sd]?)\s+(?:insurance|policy|policies|cover)\b|"
     r"\b(?:insurance|policy|cover)\s+(?:has\s+)?(?:expired|lapsed)\b|\buninsured\b|"
     r"\b(?:no|without)\s+(?:valid\s+)?(?:insurance|policy|cover)\b|\bexpired\b|\blapsed\b", "insurance_status", "=", "EXPIRED"),
    (r"\b(?:active|valid)\s+(?:insurance|policy|cover)\b|\binsured\b", "insurance_status", "=", "ACTIVE"),
    (r"\bstolen\b|\btheft\b|\bstole\b|\bmissing\b", "stolen_status", "=", True),
    (r"\bcancel+ed\b", "registration_status", "=", "CANCELLED"),
    (r"\bsuspended\b", "registration_status", "=", "SUSPENDED"),
    (r"\bopen\s+(?:case|fir)s?\b|\b(?:case|fir)s?\s+(?:is\s+)?open\b", "case_status", "=", "OPEN"),
    (r"\bclosed\s+(?:case|fir)s?\b|\b(?:case|fir)s?\s+(?:is\s+)?closed\b", "case_status", "=", "CLOSED"),
]
# relations between two agencies' events: these are the analytics indicators, so a question can ask for them
INDICATOR_PHRASES: List[Tuple[str, str]] = [
    (r"\b(?:tested|test|checked)\s+(?:for\s+)?(?:puc|pollution)\b.*?\bafter\s+(?:the\s+)?(?:theft|being\s+stolen)\b|"
     r"\b(?:puc|pollution)\s*(?:test|check|certificate)?s?\s+after\s+(?:the\s+)?theft\b", "ALIBI_PUC"),
    (r"\b(?:challan(?:ed|s)?|fined)\b.*?\bafter\s+(?:the\s+)?(?:theft|being\s+stolen)\b", "ALIBI_CHALLAN"),
    (r"\b(?:seen|spotted|captured|sighted)\b.*?\bafter\s+(?:the\s+)?(?:theft|being\s+stolen)\b", "ALIBI_CAMERA"),
    (r"\bquick\s+claims?\b|\bclaim(?:ed)?\s+soon\s+after\b", "QUICK_CLAIM"),
    (r"\b(?:sold|transferred|transfer)\b.*?\bbefore\s+(?:the\s+)?(?:theft|loss|claim)\b", "TRANSFER_BEFORE_LOSS"),
    (r"\b(?:lapsed|expired)\b.*?\b(?:seen|spotted|captured)\b.*?\bafter\b", "LAPSED_COVER_SEEN"),
    (r"\b(?:ring|shared\s+mobile|same\s+mobile|gang)\b", "PERSON_RING"),
]
INDICATOR_IMPLIES: Dict[str, Tuple[str, str, Any]] = {   # an alibi presupposes a theft
    "ALIBI_PUC": ("stolen_status", "=", True), "ALIBI_CAMERA": ("stolen_status", "=", True),
    "ALIBI_CHALLAN": ("stolen_status", "=", True),
    "LAPSED_COVER_SEEN": ("insurance_status", "=", "EXPIRED"),
}
# inside an indicator phrase only these words are "explained"; a place or a make in the middle stays for the rules
INDICATOR_WORDS: Set[str] = set("""tested test checked for puc pollution certificate check after the theft being stolen
seen spotted captured sighted challan challaned challans fined quick claim claims claimed soon sold transferred transfer before loss
lapsed expired ring shared same mobile gang""".split())
STOPWORDS: Set[str] = set("""a an the all any list show find give me get which what who whose of in on at to from for
by with and or that are is was were be been being cars car vehicles vehicle vehicles' plates plate number numbers
please those these ones one has have had there their its it near around within during since""".split())

# --------------------------------------------------------------------- the allow-list the LLM is held to
ENUM_ATTRS = ("vehicle_make", "vehicle_model", "vehicle_colour", "insurance_status", "registration_status",
              "case_status", "location", "last_seen_location", "indicator")
BOOL_ATTRS = ("stolen_status", "recovered_status")
FREE_ATTRS = ("owner_name",)
OPS: Dict[str, Tuple[str, ...]] = {a: ("=",) for a in ENUM_ATTRS + BOOL_ATTRS}
OPS.update(location=("~", "="), last_seen_location=("~", "="), owner_name=("~", "="))
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z .'-]{2,59}$")


def allowed_values(attr: str) -> Optional[List[Any]]:
    """Every value an attribute may take, or None for a free (validated-by-shape) attribute."""
    table = {
        "vehicle_make": sorted(set(MAKES.values())),
        "vehicle_model": sorted({m for _, m in MODELS.values()}),
        "vehicle_colour": sorted(set(COLOURS.values())),
        "insurance_status": ["EXPIRED", "ACTIVE"],
        "registration_status": ["ACTIVE", "CANCELLED", "SUSPENDED"],
        "case_status": ["OPEN", "CLOSED"],
        "location": sorted(PLACES), "last_seen_location": sorted(PLACES),
        "indicator": [code for _, code in INDICATOR_PHRASES],
    }
    if attr in BOOL_ATTRS:
        return [True]
    return table.get(attr)


def canonical_value(attr: str, value: Any) -> Any:
    """Map a proposed value onto the vocabulary (case-insensitive), or None if it is not allowed."""
    if attr in BOOL_ATTRS:
        return True if str(value).strip().lower() in ("true", "1", "yes", "y") else None
    if attr == "owner_name":
        text = str(value).strip()
        return text.title() if NAME_RE.match(text) else None
    allowed = allowed_values(attr) or []
    low = str(value).strip().lower()
    if attr == "vehicle_make" and low in MAKES:
        return MAKES[low]
    if attr in ("location", "last_seen_location"):
        for place, aliases in PLACES.items():
            if low == place.lower() or low in aliases:
                return place
    return next((v for v in allowed if str(v).lower() == low), None)


def place_aliases(place: str) -> List[str]:
    return PLACES.get(place, [place.lower()])


def refresh_vocabulary(client: Any) -> Dict[str, int]:
    """Widen makes/models/places with the distinct values the live sources hold. Returns counts added."""
    added = {"models": 0, "places": 0}
    for row in client.query("REG", "SELECT DISTINCT make, model FROM VEHICLE_REGISTRATION"):
        make, model = row.get("make"), row.get("model")
        if make and model and str(model).lower() not in MODELS:
            canon = MAKES.get(str(make).lower(), str(make).title())
            MODELS[str(model).lower()] = (canon, str(model))
            added["models"] += 1
    for sid, sql, col in (("CAM", "SELECT DISTINCT city FROM ZONES", "city"),
                          ("THEFT", "SELECT DISTINCT district FROM STATIONS", "district"),
                          ("PUC", "SELECT DISTINCT city FROM TEST_CENTRES", "city")):
        for row in client.query(sid, sql):
            name = str(row.get(col) or "").strip()
            if name and canonical_value("location", name) is None:
                PLACES[name.title()] = [name.lower()]
                added["places"] += 1
    return added
