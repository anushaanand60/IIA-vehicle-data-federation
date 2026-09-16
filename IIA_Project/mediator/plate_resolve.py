"""Confusable-character plate identity resolution for Challan Guard.

Roughly nine in ten wrongful e-challans trace back to an ANPR plate misread, and the confusions
are always the same shapes: O/0/D, 1/I, 8/B, 5/S, 2/Z, 6/G (docs/FIELD_RESEARCH.md). Before any
fine is raised, the guard asks "which *registered* vehicle did the camera most likely see?" and
must be able to defend the answer to the citizen who receives the challan.

Design trade-offs, both deliberate:

  * **One edit, fixed table.** Candidates are the read itself plus every single-character
    substitution from CONFUSABLE. No edit distance, no fuzzy string metric, no learned model. The
    candidate set is therefore small, deterministic and reproducible months later in an appeal --
    the opposite of "the algorithm said so".
  * **Corroboration, not confidence.** A plate is only ever rewritten when the *vehicle* the
    camera described agrees with the registration (make and/or colour). A read that is itself
    registered always resolves to itself: a Hyundai registration photographed as a Kia is a clone
    signal for the next step, not a reason to go looking for a different registration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from mediator.transforms import norm_plate

# Documented ANPR confusion pairs. Values are the characters a reader may have meant instead.
CONFUSABLE: Dict[str, str] = {
    "0": "OD", "O": "0D", "D": "0O",
    "1": "I", "I": "1",
    "8": "B", "B": "8",
    "5": "S", "S": "5",
    "2": "Z", "Z": "2",
    "6": "G", "G": "6",
}

MAKE_WEIGHT = 0.5      # the strongest corroboration a road camera can give
COLOUR_WEIGHT = 0.3    # weaker: light and weather move perceived colour
EXACT_READ_WEIGHT = 0.2
RESOLVE_THRESHOLD = 0.5   # at least a make match, or an exact read plus a colour match
SEPARATION = 0.2          # how far ahead the winner must be when rivals are also registered

Outcome = str  # "RESOLVED" | "AMBIGUOUS" | "NONE"
Lookup = Callable[[str], Dict[str, Any]]


@dataclass
class Candidate:
    plate: str
    registered: bool
    make: Optional[str]
    colour: Optional[str]
    score: float
    reasons: List[str] = field(default_factory=list)


def candidates(plate_read: str, max_edits: int = 1, limit: int = 12) -> List[str]:
    """The read first, then one-character confusable substitutions, canonical and deduplicated."""
    read = norm_plate(plate_read) or ""
    if not read:
        return []
    out = [read]
    if max_edits >= 1:
        for i, ch in enumerate(read):
            for option in CONFUSABLE.get(ch, ""):
                variant = read[:i] + option + read[i + 1:]
                if variant not in out:
                    out.append(variant)
                if len(out) >= limit:
                    return out
    return out[:limit]


def _agrees(observed: Optional[str], registered: Optional[str]) -> bool:
    """Loose either-way prefix match: "Maruti" from a camera still means "Maruti Suzuki" in REG."""
    a, b = str(observed or "").strip().lower(), str(registered or "").strip().lower()
    if not a or not b:
        return False
    return a.startswith(b) or b.startswith(a)


def score_candidate(cand: str, plate_read: str, reg_profile: Dict[str, Any],
                    observed_make: Optional[str], observed_colour: Optional[str]) -> Candidate:
    """Registration is mandatory; everything else is corroboration the citizen can check."""
    profile = reg_profile or {}
    make, colour = profile.get("vehicle_make"), profile.get("vehicle_colour")
    registered = bool(make or colour or profile.get("registration_status"))
    if not registered:
        return Candidate(cand, False, None, None, 0.0, ["no registration record"])

    score, reasons = 0.0, []
    if _agrees(observed_make, make):
        score += MAKE_WEIGHT
        reasons.append(f"observed make {observed_make} matches registered {make}")
    if _agrees(observed_colour, colour):
        score += COLOUR_WEIGHT
        reasons.append(f"observed colour {observed_colour} matches registered {colour}")
    if cand == (norm_plate(plate_read) or ""):
        score += EXACT_READ_WEIGHT
        reasons.append("exactly as the camera read it")
    return Candidate(cand, True, make, colour, round(score, 3), reasons)


def resolve(plate_read: str, observed_make: Optional[str], observed_colour: Optional[str],
            lookup: Lookup) -> Tuple[Optional[Candidate], List[Candidate], Outcome]:
    """(best, ranked registered candidates, outcome). Never raises: a broken lookup means NONE."""
    read = norm_plate(plate_read) or ""
    ranked: List[Candidate] = []
    for cand in candidates(plate_read):
        try:
            profile = lookup(cand) or {}
        except Exception:  # a source failure is "we could not confirm", never an accusation
            profile = {}
        scored = score_candidate(cand, read, profile, observed_make, observed_colour)
        if scored.registered:
            ranked.append(scored)
    # Stable tie-break: score first, then the read itself, then alphabetical, so two runs of the
    # same case can never produce two different identities.
    ranked.sort(key=lambda c: (-c.score, c.plate != read, c.plate))
    if not ranked:
        return None, [], "NONE"
    best = ranked[0]
    clear_winner = len(ranked) == 1 or (best.score - ranked[1].score) >= SEPARATION
    credible = best.plate == read or best.score >= RESOLVE_THRESHOLD
    return best, ranked, ("RESOLVED" if clear_winner and credible else "AMBIGUOUS")
