"""
Transformation functions registered by name for the GAV Mapping Registry.
Each is a pure function that can be referenced by the mediator when converting
source data to the global virtual schema.
"""

import re
from datetime import datetime, date, timezone
from typing import Any, Optional, Callable, Dict

MAKE_CORRECTIONS = {
    "hyundia": "Hyundai",
    "maruti": "Maruti Suzuki",
    "maruti suzuki": "Maruti Suzuki",
    "tata": "Tata",
    "toyota": "Toyota",
    "mahindra": "Mahindra",
    "kia": "Kia",
    "honda": "Honda"
}

def norm_plate(val: Any) -> Optional[str]:
    """Uppercase, strip non-alphanumerics (spaces, hyphens, etc.)."""
    if val is None:
        return None
    s = str(val).strip()
    return re.sub(r"[^A-Za-z0-9]", "", s).upper()

def parse_ddmmyyyy(val: Any) -> Optional[str]:
    """Parse 'DD/MM/YYYY' text into ISO 'YYYY-MM-DD'."""
    if not val:
        return None
    s = str(val).strip()
    try:
        dt = datetime.strptime(s, "%d/%m/%Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        try:
            dt = datetime.strptime(s, "%Y-%m-%d")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return s

def epoch_to_date(val: Any) -> Optional[str]:
    """Convert integer Unix epoch seconds into ISO 'YYYY-MM-DD'."""
    if val is None:
        return None
    try:
        sec = int(val)
        dt = datetime.fromtimestamp(sec, timezone.utc)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError, OSError):
        return None

def yn_to_bool(val: Any) -> Optional[bool]:
    """Convert 'Y' / 'N' to Python boolean."""
    if val is None:
        return None
    s = str(val).strip().upper()
    if s in ("Y", "YES", "TRUE", "1"):
        return True
    if s in ("N", "NO", "FALSE", "0"):
        return False
    return None

def tinyint_to_bool(val: Any) -> Optional[bool]:
    """Convert tinyint (1/0) or similar to boolean."""
    if val is None:
        return None
    try:
        return int(val) == 1
    except (ValueError, TypeError):
        return None

def status_map(val: Any) -> Optional[str]:
    """Normalize status tokens to canonical uppercase representations."""
    if val is None:
        return None
    s = str(val).strip().upper()
    mapping = {
        "ACT": "ACTIVE",
        "ACTIVE": "ACTIVE",
        "SUSP": "SUSPENDED",
        "SUSPENDED": "SUSPENDED",
        "CANC": "CANCELLED",
        "CANCELLED": "CANCELLED",
        "OPEN": "OPEN",
        "CLOSED": "CLOSED"
    }
    return mapping.get(s, s)

def title_case(val: Any) -> Optional[str]:
    """Convert string to Title Case."""
    if val is None:
        return None
    return str(val).strip().title()

def fix_make(val: Any) -> Optional[str]:
    """Correct known misspellings (e.g. 'Hyundia' -> 'Hyundai')."""
    if val is None:
        return None
    s = str(val).strip().lower()
    return MAKE_CORRECTIONS.get(s, str(val).strip().title())

TRANSFORM_REGISTRY: Dict[str, Callable[[Any], Any]] = {
    "norm_plate": norm_plate,
    "parse_ddmmyyyy": parse_ddmmyyyy,
    "epoch_to_date": epoch_to_date,
    "yn_to_bool": yn_to_bool,
    "tinyint_to_bool": tinyint_to_bool,
    "status_map": status_map,
    "title_case": title_case,
    "fix_make": fix_make,
    "none": lambda x: x
}

def get_transform(name: Optional[str]) -> Callable[[Any], Any]:
    if not name:
        return lambda x: x
    return TRANSFORM_REGISTRY.get(name, lambda x: x)
