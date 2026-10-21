"""Open-world plate likelihood: how sure can a match be when the car might be one no source holds?

PHASE3-ADDED (C3: matching re-evaluated) -- the whole module is new in phase 3.

Why: the learned matcher (plate_match, logreg + Platt) was trained only on reads of plates that are in
the registry, so its probability answers "which registered plate is this?" and never "is it a car we
do not hold at all?". It scored `0L01AB1234` as DL01AB1234 at p=1.000 and `XX01AB1234` (no such
state) at 0.999. This module asks the missing question with a noisy-channel model:

    hypothesis c   the camera read plate c (held by some source)        prior pi_c (1, or less, below)
    hypothesis u   the camera read a valid plate u that no source holds  prior RHO per plate

    L(read | plate)   probability that OCR turns `plate` into `read`: per character, keep it, substitute
                      r for x with EPS * sub(r | x), delete it with INDEL, or insert one with INDEL / 36.
                      Summed over all alignments (the forward algorithm).
    p(c | read)       pi_c L(read | c) / ( sum_c' pi_c' L(read | c') + RHO * sum_u L(read | u) )

The sum over every unknown valid plate u is exact, not sampled: the Indian plate grammar (state code,
2 district digits, 1-3 series letters, 4 digits; or the Bharat series) is a sequence of character
slots, so the forward algorithm runs over slot *sets* instead of single characters. Known plates are
then subtracted from that sum, so a plate is never counted as both known and unknown.

Parameters (measured or stated; none tuned on the test split):
    CHANNELS  substitutions per character (EPS) and insertions/deletions per character (INDEL) by the
              OCR's own confidence, measured on the rendered benchmark reads (data/match_benchmark.csv,
              degradation != observed): EPS = differing characters / characters, over reads of the right
              length; INDEL = share of reads of the wrong length / 10 characters. A 0.95-confidence read
              is far cleaner than a 0.3 one, so one global error rate would mis-trust both.
    sub(r|x)  the training split's substitution counts (data/confusion_costs.json), smoothed over all pairs
              at once (see _sub_table); documented confusables the split never showed get a floor.
    RHO       0.05, an assumption: for every 20 plates the sources hold near a read, one real plate they
              do not (out-of-state, unregistered). scripts/evaluate_open_world.py reports 0.01 .. 0.2.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from mediator.lexicons import BH_SERIES, INDIAN_STATE_CODES
from mediator.similarity import ocr_confusion_cost

ROOT = Path(__file__).resolve().parent.parent
COSTS_PATH = ROOT / "data" / "confusion_costs.json"

RHO = 0.05
ALPHA = 0.5
CONFUSABLE_PSEUDO = 5.0  # a documented confusable never seen in training: about as likely as 5/S (16 counts)
# (lowest confidence, EPS, INDEL), one row per confidence bucket of the rendered benchmark reads
CHANNELS = ((0.9, 0.0251, 0.0065), (0.8, 0.0522, 0.0141), (0.6, 0.0606, 0.0197),
            (0.4, 0.0975, 0.0466), (0.0, 0.3000, 0.0748))
UNKNOWN_CONFIDENCE = (0.062, 0.026)  # all 2,153 rendered reads: for a read whose confidence is not known
LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
DIGITS = "0123456789"
ALPHABET = LETTERS + DIGITS
STATE_CODES = tuple(sorted(INDIAN_STATE_CODES))
PLATE_RE = re.compile(r"^[A-Z]{2}\d{2}[A-Z]{1,3}\d{4}$")
STATE_SPAN = 2  # the first two characters of a civilian plate are the state / UT code


def is_valid(plate: str) -> bool:
    """A real Indian plate: official state code + district + series + number, or a Bharat-series plate."""
    p = str(plate or "").upper()
    return bool(PLATE_RE.match(p) and p[:2] in INDIAN_STATE_CODES) or bool(BH_SERIES.fullmatch(p))


# ------------------------------------------------------------------ the channel: P(read char | true char)

@lru_cache(maxsize=1)
def _sub_table() -> Dict[Tuple[str, str], float]:
    """(true, read) -> q: how much likelier this substitution is than the average one (q averages 1).

    Smoothed over all pairs at once, not per true character. Per-character normalisation made a true 0
    (whose counted errors are nearly all 0->O) twenty times LESS likely to be read as D than a 9 is, only
    because 0 has more counted errors; an unseen pair now costs the same whatever the true character.
    Pairs the hand table documents as ANPR confusables (similarity.ocr_confusion_cost) but the training
    split never showed get CONFUSABLE_PSEUDO pseudo-counts instead of ALPHA, as learned_confusion_cost
    also falls back to the hand table.
    """
    counts: Dict[Tuple[str, str], int] = {}
    if COSTS_PATH.exists():
        for p in json.loads(COSTS_PATH.read_text(encoding="utf-8")).get("pairs", []):
            n = int(p.get("count", 0))
            counts[(p["a"], p["b"])] = counts[(p["b"], p["a"])] = n
    weights = {(x, r): counts.get((x, r), 0) + (CONFUSABLE_PSEUDO if ocr_confusion_cost(x, r) < 1.0 else ALPHA)
               for x in ALPHABET for r in ALPHABET if r != x}
    mean = sum(weights.values()) / len(weights)
    return {k: v / mean for k, v in weights.items()}


@lru_cache(maxsize=1)
def _error_share() -> Dict[str, float]:
    """x -> sum over r of q(x, r) / 35: how error-prone x is relative to the average character."""
    table, others = _sub_table(), len(ALPHABET) - 1
    return {x: sum(table[(x, r)] for r in ALPHABET if r != x) / others for x in ALPHABET}


@dataclass(frozen=True)
class Channel:
    eps: float    # substitutions per character, on average over characters
    indel: float  # insertions or deletions per character

    def emit(self, read_char: str, true_char: str) -> float:
        """P(OCR outputs read_char | the plate shows true_char), given the character was not dropped."""
        others = len(ALPHABET) - 1
        if read_char == true_char:  # an error-prone character (0, often read O) is kept less often
            return max(0.5, 1.0 - self.indel - self.eps * _error_share().get(true_char, 1.0))
        return self.eps * _sub_table().get((true_char, read_char), ALPHA) / others


def channel(confidence: Optional[float] = None) -> Channel:
    """The measured OCR channel for a read of this confidence."""
    if confidence is None:
        return Channel(*UNKNOWN_CONFIDENCE)
    for low, eps, indel in CHANNELS:
        if float(confidence) >= low:
            return Channel(eps, indel)
    return Channel(*CHANNELS[-1][1:])


# ------------------------------------------------------------------ forward algorithm over slot sets

Slot = Sequence[str]  # the characters this position of the plate may hold


def _step(prev: List[float], read: str, chars: Slot, ch: Channel) -> List[float]:
    """One plate slot: from P(read[:j] | prefix) to P(read[:j] | prefix + one char from `chars`)."""
    out = [0.0] * (len(read) + 1)
    dele, ins = ch.indel * len(chars), ch.indel / len(ALPHABET)
    for j in range(len(read) + 1):
        v = prev[j] * dele                                                 # the character was dropped
        if j:
            v += prev[j - 1] * sum(ch.emit(read[j - 1], x) for x in chars)  # read, possibly misread
            v += out[j - 1] * ins                                          # a spurious one inserted
        out[j] = v
    return out


def _start(read: str, ch: Channel) -> List[float]:
    out = [1.0] + [0.0] * len(read)
    for j in range(1, len(read) + 1):  # leading insertions
        out[j] = out[j - 1] * ch.indel / len(ALPHABET)
    return out


def likelihood(read: str, plate: str, ch: Optional[Channel] = None) -> float:
    """L(read | plate): every alignment of one known plate, summed."""
    read, plate, ch = str(read or "").upper(), str(plate or "").upper(), ch or channel()
    f = _start(read, ch)
    for c in plate:
        f = _step(f, read, c, ch)
    return f[-1]


# The plate grammar: after the state code, 2 district digits, 1-3 series letters and 4 digits. The Bharat
# series (22BH1234AA) has no state code and is its own template.
CIVILIAN = [[DIGITS, DIGITS] + [LETTERS] * k + [DIGITS] * 4 for k in (1, 2, 3)]
BHARAT = [[DIGITS, DIGITS, "B", "H"] + [DIGITS] * 4 + [LETTERS] * k for k in (1, 2)]


def all_valid_mass(read: str, ch: Optional[Channel] = None) -> float:
    """sum over EVERY valid plate u of L(read | u): how well 'some real plate' explains this read."""
    read, ch = str(read or "").upper(), ch or channel()
    if not read:
        return 0.0
    start = _start(read, ch)
    after_state = [0.0] * (len(read) + 1)
    for code in STATE_CODES:  # the state code is one joint slot of 2 characters
        f = start
        for c in code:
            f = _step(f, read, c, ch)
        after_state = [a + b for a, b in zip(after_state, f)]
    total = 0.0
    for first, templates in ((after_state, CIVILIAN), (start, BHARAT)):
        for template in templates:
            f = first
            for slot in template:
                f = _step(f, read, slot, ch)
            total += f[-1]
    return total


# ------------------------------------------------------------------ explanation of one match

@dataclass
class Correction:
    kind: str          # "swap" (one character read as another), "missing" or "extra"
    position: int      # 1-based position in the candidate plate
    read: str          # what OCR produced ("" for missing)
    plate: str         # what the plate holds ("" for extra)
    in_state_code: bool

    def words(self) -> str:
        where = f"position {self.position}" + (" (state code)" if self.in_state_code else "")
        if self.kind == "swap":
            return f"{self.read}->{self.plate} at {where}"
        if self.kind == "missing":
            return f"{self.plate} missing at {where}"
        return f"extra {self.read} before {where}"


def corrections(read: str, plate: str, ch: Optional[Channel] = None) -> List[Correction]:
    """The single most likely alignment (Viterbi): which characters the match had to change."""
    read, plate, ch = str(read or "").upper(), str(plate or "").upper(), ch or channel()
    n, m = len(read), len(plate)
    ins = ch.indel / len(ALPHABET)
    best = [[0.0] * (n + 1) for _ in range(m + 1)]
    back: List[List[Optional[str]]] = [[None] * (n + 1) for _ in range(m + 1)]
    best[0][0] = 1.0
    for i in range(m + 1):
        for j in range(n + 1):
            if i == 0 and j == 0:
                continue
            options = []
            if i and j:
                options.append((best[i - 1][j - 1] * ch.emit(read[j - 1], plate[i - 1]), "diag"))
            if i:
                options.append((best[i - 1][j] * ch.indel, "del"))
            if j:
                options.append((best[i][j - 1] * ins, "ins"))
            best[i][j], back[i][j] = max(options)
    out: List[Correction] = []
    i, j = m, n
    while i or j:
        step = back[i][j]
        if step == "diag":
            if read[j - 1] != plate[i - 1]:
                out.append(Correction("swap", i, read[j - 1], plate[i - 1], i <= STATE_SPAN))
            i, j = i - 1, j - 1
        elif step == "del":
            out.append(Correction("missing", i, "", plate[i - 1], i <= STATE_SPAN))
            i -= 1
        else:
            out.append(Correction("extra", i + 1, read[j - 1], "", i + 1 <= STATE_SPAN))
            j -= 1
    return list(reversed(out))


# ------------------------------------------------------------------ the posterior

@dataclass
class OpenWorld:
    probabilities: Dict[str, float]   # candidate plate -> p(c | read)
    unknown: float                    # p(the read is of a plate no source holds)
    likelihoods: Dict[str, float]


def posterior(read: str, candidates: Iterable[str], priors: Optional[Mapping[str, float]] = None,
              rho: Optional[float] = None, confidence: Optional[float] = None) -> OpenWorld:
    """p(candidate | read) for each known candidate, against every valid plate no source holds.

    `priors` weights a candidate by how strongly the sources say it exists (1.0 = held by an
    authoritative source; federated_match gives a camera-only plate less). Known plates outside
    `candidates` are far from the read (blocking dropped them), so their share is negligible.
    """
    read = str(read or "").upper()
    cands = list(dict.fromkeys(str(c).upper() for c in candidates))
    priors, rho = priors or {}, RHO if rho is None else rho  # RHO read now, so an evaluation can vary it
    ch = channel(confidence)
    like = {c: likelihood(read, c, ch) for c in cands}
    known_valid = sum(v for c, v in like.items() if is_valid(c))
    unknown_mass = max(0.0, all_valid_mass(read, ch) - known_valid)
    weights = {c: float(priors.get(c, 1.0)) * like[c] for c in cands}
    z = sum(weights.values()) + rho * unknown_mass
    if z <= 0.0:
        return OpenWorld({c: 0.0 for c in cands}, 1.0, like)
    return OpenWorld({c: w / z for c, w in weights.items()}, rho * unknown_mass / z, like)
