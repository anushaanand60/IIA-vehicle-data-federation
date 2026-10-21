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
BENCHMARK_PATH = ROOT / "data" / "match_benchmark.csv"  # PHASE3-ADDED (C3 follow-up: indels are learned from it)

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


# PHASE3-ADDED (C3 follow-up: HR26AB05501, the right plate HR26AB0550 plus the plate's right border read as a
# trailing 1, scored 0.010: an extra character was modelled as rare and equally likely to be any of 36 at any
# place, so "an unknown HR26ABO5501 with O read as 0" won 70 to 1. On the training split, 96 of 1,298 rendered
# reads gain a character at the END (71 of them a 1: the border), 18 at the start (17 an I), 85 in the middle;
# 34 lose one in the middle and none at either end.)
WHERE = ("start", "middle", "end")


@lru_cache(maxsize=1)
def _indel_table() -> Dict[str, Dict[str, object]]:
    """Where OCR adds or drops a character and which character it adds, from the TRAINING split only.

    Rates are per opportunity: once per read at the start and at the end, once per gap (insertion) or per
    character (deletion) in the middle. A read one character longer than its plate that starts with the
    plate gained it at the end, one that ends with the plate gained it at the start, any other in the middle;
    likewise for one character shorter. Reads off by more contribute nothing rather than a guessed alignment.
    Add-half smoothed, so an end never seen to lose a character is rare, not impossible.
    """
    import csv  # noqa: PLC0415

    train: set = set()
    if COSTS_PATH.exists():
        train = set(json.loads(COSTS_PATH.read_text(encoding="utf-8")).get("train_plates", []))
    rows: List[Tuple[str, str]] = []
    if train and BENCHMARK_PATH.exists():
        with BENCHMARK_PATH.open(encoding="utf-8") as fh:
            rows = [(r["true_plate"], r["ocr_read"]) for r in csv.DictReader(fh)
                    if r.get("true_plate") in train and r.get("ocr_read") and r.get("degradation") != "observed"]
    if not rows:  # no benchmark on this machine: the phase-3 uniform model, one rate everywhere
        flat = UNKNOWN_CONFIDENCE[1]
        return {"ins": dict.fromkeys(WHERE, flat), "del": dict.fromkeys(WHERE, flat),
                "char": {w: {} for w in WHERE}}
    ins = dict.fromkeys(WHERE, 0)
    dele = dict.fromkeys(WHERE, 0)
    added: Dict[str, Dict[str, int]] = {w: {} for w in WHERE}
    for true, read in rows:
        if len(read) == len(true) + 1:
            w = "end" if read.startswith(true) else "start" if read.endswith(true) else "middle"
            ins[w] += 1
            if w != "middle":
                c = read[-1] if w == "end" else read[0]
                added[w][c] = added[w].get(c, 0) + 1
        elif len(read) == len(true) - 1:
            w = "end" if true.startswith(read) else "start" if true.endswith(read) else "middle"
            dele[w] += 1
    n = len(rows)
    length = sum(len(t) for t, _ in rows) / n
    per = {"start": n, "end": n}
    char: Dict[str, Dict[str, float]] = {}
    for w in WHERE:
        total = sum(added[w].values())
        char[w] = {c: (added[w].get(c, 0) + ALPHA) / (total + ALPHA * len(ALPHABET)) for c in ALPHABET} if total else {}
    return {"ins": {w: (ins[w] + ALPHA) / per.get(w, n * (length - 1)) for w in WHERE},
            "del": {w: (dele[w] + ALPHA) / per.get(w, n * (length - 2)) for w in WHERE},
            "char": char}
# PHASE3-END


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

    # PHASE3-ADDED (C3 follow-up: learned, position-aware insertions and deletions, scaled by how much more or
    # less often a read of this confidence has the wrong length than the average read)
    def ins(self, read_char: str, where: str) -> float:
        """P(OCR adds read_char here); `where` is start, middle or end."""
        t = _indel_table()
        share = t["char"][where].get(read_char, 1.0 / len(ALPHABET))
        return self.indel / UNKNOWN_CONFIDENCE[1] * t["ins"][where] * share

    def dele(self, where: str) -> float:
        """P(OCR drops the plate's character here)."""
        return self.indel / UNKNOWN_CONFIDENCE[1] * _indel_table()["del"][where]
    # PHASE3-END


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


# PHASE3-REPLACED (C3 follow-up: insertions and deletions depend on where they happen and which character is
# added, learned in _indel_table, instead of one uniform rate):
# def _step(prev: List[float], read: str, chars: Slot, ch: Channel) -> List[float]:
#     out = [0.0] * (len(read) + 1)
#     dele, ins = ch.indel * len(chars), ch.indel / len(ALPHABET)
#     for j in range(len(read) + 1):
#         v = prev[j] * dele
#         if j:
#             v += prev[j - 1] * sum(ch.emit(read[j - 1], x) for x in chars)
#             v += out[j - 1] * ins
#         out[j] = v
#     return out
# def _start(read: str, ch: Channel) -> List[float]:
#     out = [1.0] + [0.0] * len(read)
#     for j in range(1, len(read) + 1):
#         out[j] = out[j - 1] * ch.indel / len(ALPHABET)
#     return out
def _step(prev: List[float], read: str, chars: Slot, ch: Channel, first: bool = False,
          last: bool = False) -> List[float]:
    """One plate slot: from P(read[:j] | prefix) to P(read[:j] | prefix + one char from `chars`).
    `first` / `last` mark the plate's first and last slot: a character dropped there, or one added after the
    last, uses the start / end rates."""
    out = [0.0] * (len(read) + 1)
    dele = ch.dele("start" if first else "end" if last else "middle") * len(chars)
    after = "end" if last else "middle"
    for j in range(len(read) + 1):
        v = prev[j] * dele                                                 # the character was dropped
        if j:
            v += prev[j - 1] * sum(ch.emit(read[j - 1], x) for x in chars)  # read, possibly misread
            v += out[j - 1] * ch.ins(read[j - 1], after)                   # a spurious one added after it
        out[j] = v
    return out


def _start(read: str, ch: Channel) -> List[float]:
    out = [1.0] + [0.0] * len(read)
    for j in range(1, len(read) + 1):  # characters added before the plate's first
        out[j] = out[j - 1] * ch.ins(read[j - 1], "start")
    return out


def _run(f: List[float], read: str, slots: Sequence[Slot], ch: Channel, first: bool, last: bool) -> List[float]:
    for k, slot in enumerate(slots):
        f = _step(f, read, slot, ch, first=first and k == 0, last=last and k == len(slots) - 1)
    return f


def likelihood(read: str, plate: str, ch: Optional[Channel] = None) -> float:
    """L(read | plate): every alignment of one known plate, summed."""
    read, plate, ch = str(read or "").upper(), str(plate or "").upper(), ch or channel()
    # PHASE3-REPLACED (C3 follow-up: first and last slot marked):
    # f = _start(read, ch)
    # for c in plate:
    #     f = _step(f, read, c, ch)
    # return f[-1]
    return _run(_start(read, ch), read, list(plate), ch, True, True)[-1]
# PHASE3-END


# The plate grammar: after the state code, 2 district digits, 1-3 series letters and 4 digits. The Bharat
# series (22BH1234AA) has no state code and is its own template.
CIVILIAN = [[DIGITS, DIGITS] + [LETTERS] * k + [DIGITS] * 4 for k in (1, 2, 3)]
BHARAT = [[DIGITS, DIGITS, "B", "H"] + [DIGITS] * 4 + [LETTERS] * k for k in (1, 2)]


# PHASE3-ADDED (C3 follow-up: an unknown plate is as likely as the plates the sources DO hold make its shape.
# All 605 held plates have a 2-letter series, yet every series length had the same weight, so "an unknown
# HR26ABO5501 (3 letters, O read as 0)" competed with the registered HR26AB0550 as an equal.)
SHAPES = ("civilian1", "civilian2", "civilian3", "bharat1", "bharat2")  # series length 1-3; Bharat suffix 1-2
_SHAPE_RE = re.compile(r"^[A-Z]{2}\d{2}([A-Z]{1,3})\d{4}$")
_BH_RE = re.compile(r"^\d{2}BH\d{4}([A-Z]{1,2})$")


def shape_weights(plates: Iterable[str]) -> Dict[str, float]:
    """Shape -> weight (mean 1 over SHAPES), Laplace-smoothed shares of the held plates' shapes.
    With no plates every shape weighs 1, the unweighted grammar."""
    counts = dict.fromkeys(SHAPES, 0)
    for p in plates:
        p = str(p or "").upper()
        civ, bh = _SHAPE_RE.match(p), _BH_RE.match(p)  # BH_RE: the Bharat series, as lexicons.BH_SERIES
        if civ or bh:
            counts[shape_of(p)] += 1
    total = sum(counts.values())
    return {s: (counts[s] + 1) / (total + len(SHAPES)) * len(SHAPES) for s in SHAPES}


def shape_of(plate: str) -> Optional[str]:
    p = str(plate or "").upper()
    civ, bh = _SHAPE_RE.match(p), _BH_RE.match(p)
    return f"civilian{len(civ.group(1))}" if civ else f"bharat{len(bh.group(1))}" if bh else None
# PHASE3-END


def all_valid_mass(read: str, ch: Optional[Channel] = None,
                   weights: Optional[Mapping[str, float]] = None) -> float:  # PHASE3-ADDED (C3 follow-up): weights
    """sum over EVERY valid plate u of L(read | u): how well 'some real plate' explains this read."""
    read, ch = str(read or "").upper(), ch or channel()
    weights = weights or {}  # PHASE3-ADDED (C3 follow-up)
    if not read:
        return 0.0
    start = _start(read, ch)
    after_state = [0.0] * (len(read) + 1)
    for code in STATE_CODES:  # the state code is one joint slot of 2 characters
        # PHASE3-REPLACED (C3 follow-up: the state code holds the plate's first slot):
        # f = start
        # for c in code:
        #     f = _step(f, read, c, ch)
        f = _run(start, read, list(code), ch, True, False)
        after_state = [a + b for a, b in zip(after_state, f)]
    total = 0.0
    # PHASE3-REPLACED (C3 follow-up: a template ends the plate; a Bharat template also begins it):
    # for first, templates in ((after_state, CIVILIAN), (start, BHARAT)):
    #     for template in templates:
    #         f = first
    #         for slot in template:
    #             f = _step(f, read, slot, ch)
    #         total += f[-1]
    # PHASE3-REPLACED (C3 follow-up: each template weighted by how common its shape is among held plates):
    # for f0, templates, begins in ((after_state, CIVILIAN, False), (start, BHARAT, True)):
    #     for template in templates:
    #         total += _run(f0, read, template, ch, begins, True)[-1]
    for f0, templates, begins, kind in ((after_state, CIVILIAN, False, "civilian"), (start, BHARAT, True, "bharat")):
        for k, template in enumerate(templates, start=1):
            total += weights.get(f"{kind}{k}", 1.0) * _run(f0, read, template, ch, begins, True)[-1]
    return total


# ------------------------------------------------------------------ explanation of one match

@dataclass
class Correction:
    kind: str          # "swap" (one character read as another), "missing" or "extra"
    position: int      # 1-based position in the candidate plate
    read: str          # what OCR produced ("" for missing)
    plate: str         # what the plate holds ("" for extra)
    in_state_code: bool
    at_end: bool = False  # PHASE3-ADDED (C3 follow-up): an extra character after the plate's last one
    at_start: bool = False  # PHASE3-ADDED (C3 follow-up): an extra character before the plate's first one

    def words(self) -> str:
        where = f"position {self.position}" + (" (state code)" if self.in_state_code else "")
        if self.kind == "swap":
            return f"{self.read}->{self.plate} at {where}"
        if self.kind == "missing":
            return f"{self.plate} missing at {where}"
        if self.at_end:  # PHASE3-ADDED (C3 follow-up: usually the plate's right border read as 1)
            return f"extra {self.read} at the end"
        if self.at_start:  # PHASE3-ADDED (C3 follow-up: usually the left border read as I)
            return f"extra {self.read} at the start"
        return f"extra {self.read} before {where}"


def corrections(read: str, plate: str, ch: Optional[Channel] = None) -> List[Correction]:
    """The single most likely alignment (Viterbi): which characters the match had to change."""
    read, plate, ch = str(read or "").upper(), str(plate or "").upper(), ch or channel()
    n, m = len(read), len(plate)
    # PHASE3-REPLACED (C3 follow-up: the same position-aware insertion / deletion rates as the forward pass):
    # ins = ch.indel / len(ALPHABET)
    def ins_at(i: int, c: str) -> float:
        return ch.ins(c, "start" if i == 0 else "end" if i == m else "middle")

    def del_at(i: int) -> float:
        return ch.dele("start" if i == 1 else "end" if i == m else "middle")
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
            # PHASE3-REPLACED (C3 follow-up):
            # if i:
            #     options.append((best[i - 1][j] * ch.indel, "del"))
            # if j:
            #     options.append((best[i][j - 1] * ins, "ins"))
            if i:
                options.append((best[i - 1][j] * del_at(i), "del"))
            if j:
                options.append((best[i][j - 1] * ins_at(i, read[j - 1]), "ins"))
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
            # PHASE3-REPLACED (C3 follow-up: an addition after the last character is named as such):
            # out.append(Correction("extra", i + 1, read[j - 1], "", i + 1 <= STATE_SPAN))
            # PHASE3-REPLACED (C3 follow-up: one added before the first character, usually the left border read
            # as I, is outside the plate and is no state-code correction):
            # out.append(Correction("extra", i + 1, read[j - 1], "", i + 1 <= STATE_SPAN, at_end=i == m))
            out.append(Correction("extra", i + 1, read[j - 1], "", 0 < i < STATE_SPAN, at_end=i == m,
                                  at_start=i == 0))
            j -= 1
    return list(reversed(out))


# ------------------------------------------------------------------ the posterior

@dataclass
class OpenWorld:
    probabilities: Dict[str, float]   # candidate plate -> p(c | read)
    unknown: float                    # p(the read is of a plate no source holds)
    likelihoods: Dict[str, float]


def posterior(read: str, candidates: Iterable[str], priors: Optional[Mapping[str, float]] = None,
              rho: Optional[float] = None, confidence: Optional[float] = None,
              shapes: Optional[Mapping[str, float]] = None) -> OpenWorld:  # PHASE3-ADDED (C3 follow-up): shapes
    """p(candidate | read) for each known candidate, against every valid plate no source holds.

    `priors` weights a candidate by how strongly the sources say it exists (1.0 = held by an
    authoritative source; federated_match gives a camera-only plate less). Known plates outside
    `candidates` are far from the read (blocking dropped them), so their share is negligible.
    `shapes` (shape_weights of the held plates) weights the unknown plates by how common their shape is.
    """
    read = str(read or "").upper()
    cands = list(dict.fromkeys(str(c).upper() for c in candidates))
    priors, rho = priors or {}, RHO if rho is None else rho  # RHO read now, so an evaluation can vary it
    ch = channel(confidence)
    like = {c: likelihood(read, c, ch) for c in cands}
    # PHASE3-REPLACED (C3 follow-up: a known plate leaves the weighted sum with its own shape's weight):
    # known_valid = sum(v for c, v in like.items() if is_valid(c))
    # unknown_mass = max(0.0, all_valid_mass(read, ch) - known_valid)
    shapes = shapes or {}
    known_valid = sum(shapes.get(shape_of(c), 1.0) * v for c, v in like.items() if is_valid(c))
    unknown_mass = max(0.0, all_valid_mass(read, ch, shapes) - known_valid)
    weights = {c: float(priors.get(c, 1.0)) * like[c] for c in cands}
    z = sum(weights.values()) + rho * unknown_mass
    if z <= 0.0:
        return OpenWorld({c: 0.0 for c in cands}, 1.0, like)
    return OpenWorld({c: w / z for c, w in weights.items()}, rho * unknown_mass / z, like)
