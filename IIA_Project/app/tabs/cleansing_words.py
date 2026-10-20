"""Plain words for the Data Cleansing page: what each rule, anomaly, outcome and resolution means.

The rule tree (`data/rdr_rules.json`) keeps its own `because` texts, which cite the cases and the
course material each rule came from; the page shows these shorter sentences instead, keyed by rule
id, so the tree and its cornerstone cases are untouched. `suggest()` reads a capture's open anomalies
and names the resolution an operator would normally pick, with the reason in one line.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

RULE_WORDS: Dict[str, Tuple[str, str]] = {  # rule id -> (if, then)
    "R0": ("nothing below applies", "keep the value as the camera recorded it"),
    "R1": ("a make, model or colour is a spelling variant (Gry)", "standardise it (Grey)"),
    "R2": ("that standardised guess contradicts the registration", "do not guess, send it to a person"),
    "R3": ("a make, model or colour no dictionary knows (Maruthi)", "use the registered value"),
    "R4": ("the camera saw a different make, model or colour than registered, or the plate was in two "
           "places at once (a trip faster than 160 km/h even if the camera clocks are 10 minutes apart)",
           "flag it as clone evidence and never overwrite it"),
    "R5": ("the plate is not registered and the matcher ACCEPTs one registered plate",
           "correct the plate to that registered plate"),
    "R12": ("the matcher's plate is registered with a different colour or model than the camera saw",
            "do not correct, send it to a person"),
    "R6": ("the plate is not registered and the matcher is not sure", "send it to a person"),
    "R7": ("the plate is registered but OCR confidence is below 0.70", "send it to a person"),
    "R8": ("the camera's make or colour agrees with the registration", "trust the read and keep it"),
    "R9": ("the time is readable but in another format", "rewrite it in the camera database's format"),
    "R10": ("the time is unreadable or in the future", "send it to a person"),
    "R11": ("the same plate, camera and moment appear twice", "delete the copy"),
}
EXCEPTION_OF = {"R2": "R1", "R12": "R5", "R8": "R7"}

ANOMALY_WORDS = {
    "PLATE_NOT_REGISTERED": "plate not registered", "PLATE_SHAPE_INVALID": "plate breaks the Indian format",
    "LOW_OCR_CONFIDENCE": "low OCR confidence", "TIMESTAMP_UNPARSEABLE": "time unreadable",
    "TIMESTAMP_FUTURE": "time in the future", "TIMESTAMP_NONSTANDARD": "time in another format",
    "DUPLICATE_CAPTURE": "duplicate record", "IMPOSSIBLE_TRAVEL": "in two places at once",
}
DISPOSITION_WORDS = {"flagged": "clone evidence", "quarantined": "sent to a person",
                     "resolved": "decided by a person", "unhandled": "unhandled"}

RESOLUTION_WORDS: Dict[str, Tuple[str, str]] = {  # decision -> (label, when to pick it)
    "confirm_clone": ("It's a cloned plate", "the camera saw a different car than the one registered. The record "
                      "stays as evidence and the plate goes on the watchlist; no data changes."),
    "accept_candidate": ("Correct the plate", "OCR misread the plate. Pick the registered plate it really is; "
                         "the camera record is rewritten."),
    "delete_record": ("Delete the record", "the record is junk (a copy, or a time that cannot be trusted). It is "
                      "removed from the camera database; Undo puts it back."),
    "dismiss": ("It's fine, keep it", "nothing is really wrong. The issue is closed and the record kept; "
                "write why in the note."),
}


def anomaly_words(code: str) -> str:
    """'MAKE_DISAGREES_REGISTRATION' -> 'make differs from registration'."""
    if code in ANOMALY_WORDS:
        return ANOMALY_WORDS[code]
    field, _, kind = code.partition("_")
    if kind == "DISAGREES_REGISTRATION":
        return f"{field.lower()} differs from registration"
    if kind == "NOT_IN_LEXICON":
        return f"{field.lower()} misspelt"
    return code.replace("_", " ").lower()


def rule_line(rule: Optional[str]) -> str:
    """'R4: if ..., then ...' in plain words."""
    if_, then = RULE_WORDS.get(rule or "", ("", ""))
    return f"if {if_}, then {then}" if if_ else (rule or "")


def rules_table() -> List[Dict[str, str]]:
    return [{"Rule": r + (f" (exception to {EXCEPTION_OF[r]})" if r in EXCEPTION_OF else ""), "If": i, "Then": t}
            for r, (i, t) in RULE_WORDS.items()]


def suggest(open_codes: List[str], candidates: List[Dict[str, Any]]) -> Optional[Tuple[str, str]]:
    """(decision, reason) for what the rules left open, or None when there is nothing to decide."""
    codes = set(open_codes)
    if not codes:
        return None
    if "IMPOSSIBLE_TRAVEL" in codes:
        return "confirm_clone", "the same plate was seen at two places too far apart for the time between them."
    if any(c.endswith("_DISAGREES_REGISTRATION") for c in codes):
        return "confirm_clone", "the camera saw a different car than the one registered to this plate."
    if codes & {"PLATE_NOT_REGISTERED", "PLATE_SHAPE_INVALID"}:
        # only a candidate the matcher did not REJECT is a plausible correction
        plausible = [c for c in candidates if c.get("decision") in ("ACCEPT", "REVIEW")]
        if plausible:
            best = plausible[0]
            if best.get("decision") == "ACCEPT":  # the matcher was sure; R12 held it back on a contradiction
                return "accept_candidate", (f"the matcher's best match is {best['plate']} (p={best['probability']}), "
                                            "but the camera's colour or model contradicts that car's registration. "
                                            f"Correct it only if the photo shows {best['plate']}.")
            return "accept_candidate", (f"the matcher thinks it may be {best['plate']} (p={best['probability']}) "
                                        f"but is not sure enough to correct it alone.")
        if "PLATE_SHAPE_INVALID" not in codes:
            return "dismiss", ("the plate is a valid Indian plate that is simply not in this registry (a new or "
                               "out-of-state car). Keep the record: the federated check reports it as unregistered.")
        if candidates:  # close in characters, but the matcher rejected it: a different car, possibly a doctored plate
            return "dismiss", (f"the read looks like {candidates[0]['plate']}, but the camera saw a different car. "
                               "Keep it as evidence and check the photo.")
        return "delete_record", "the read is not a valid plate and no registered plate is close to it."
    if "DUPLICATE_CAPTURE" in codes:
        return "delete_record", "this record is a copy of another one."
    if codes & {"TIMESTAMP_UNPARSEABLE", "TIMESTAMP_FUTURE"}:
        return "delete_record", "the time cannot be trusted, so the sighting cannot be used."
    if "LOW_OCR_CONFIDENCE" in codes:
        return "dismiss", "the read is a registered plate and nothing contradicts it."
    return "dismiss", "only a small issue is left."
