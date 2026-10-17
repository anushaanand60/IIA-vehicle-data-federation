"""
String similarity measures for approximate plate matching, implemented from the
Week 6-8 course deck "Data and Text Matching".

Every function is pure (no I/O, no state), takes two arbitrary strings and -- unless its
docstring says otherwise -- returns a similarity in [0, 1] where 1 means "identical".
Inputs are case-folded; nothing here assumes a plate, although the canonical plate form
(A-Z0-9, see mediator.transforms.norm_plate) is what the mediator actually feeds in.

Conventions used throughout, taken from the deck:
  * x = x1x2...xn, y = y1y2...ym               (p. 18)
  * d(x, y) = edit distance, s(x, y) = similarity in [0,1]   (p. 13, 17)
  * Bx = the set of tokens generated for x     (p. 25)

Empty-string rules (fixed across all measures, so the evaluation harness can compare
measures without special-casing any of them):
  * both empty  -> 1.0
  * one empty   -> 0.0
"""
from __future__ import annotations

import math
from collections import Counter
from typing import Callable, Dict, List, Optional, Sequence

from mediator.plate_resolve import CONFUSABLE

# Cost of a substitution between two documented ANPR confusables (0 <-> O, 1 <-> I, ...).
# Lower than 1.0 because the deck's plain edit distance (p. 16) charges every substitution
# the same, which is exactly the domain knowledge we have and it does not.
OCR_SUB_COST = 0.2


def _fold(s: str) -> str:
    """Case folding, the normalisation step of p. 10. Upper case keeps plates canonical."""
    return str(s or "").upper()


def _empty_rule(a: str, b: str) -> Optional[float]:
    """Shared empty-string handling; returns None when both strings are non-empty."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return None


# --------------------------------------------------------------------------- sequence based

def levenshtein(s1: str, s2: str) -> int:
    """Edit distance d(x,y): minimal number of unit-cost insert/delete/substitute ops (p. 16).

    Dynamic program of p. 18:
        d(i,0) = i,  d(0,j) = j
        d(i,j) = min(d(i-1,j) + 1,            # delete xi
                     d(i,j-1) + 1,            # insert yj
                     d(i-1,j-1) + [xi != yj]) # substitute
    O(len1 * len2) time, O(min) space (one row of the table is enough).
    """
    x, y = _fold(s1), _fold(s2)
    n, m = len(x), len(y)
    if n == 0:
        return m
    if m == 0:
        return n
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        cur = [i] + [0] * m
        for j in range(1, m + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x[i - 1] != y[j - 1]))
        prev = cur
    return prev[m]


def edit_similarity(s1: str, s2: str) -> float:
    """s(x,y) = 1 - d(x,y) / max(|x|, |y|)  (p. 17).

    Slide example: s("David Smiths", "Davidd Simth") = 1 - 4/12 = 0.67.
    """
    x, y = _fold(s1), _fold(s2)
    rule = _empty_rule(x, y)
    if rule is not None:
        return rule
    return 1.0 - levenshtein(x, y) / max(len(x), len(y))


def weighted_edit_distance(
    s1: str,
    s2: str,
    sub_cost: Optional[Callable[[str, str], float]] = None,
) -> float:
    """Needleman-Wunsch style generalisation of p. 17: substitution cost comes from a callable.

    Insert and delete stay at 1.0; sub_cost(a, b) replaces the fixed 1.0 substitution charge,
    which is how domain knowledge (an OCR confusion table) enters a classical measure.
    Default behaviour is identical to `levenshtein`.
    """
    x, y = _fold(s1), _fold(s2)
    cost = sub_cost or (lambda a, b: 1.0)
    n, m = len(x), len(y)
    if n == 0:
        return float(m)
    if m == 0:
        return float(n)
    prev: List[float] = [float(j) for j in range(m + 1)]
    for i in range(1, n + 1):
        cur: List[float] = [float(i)] + [0.0] * m
        for j in range(1, m + 1):
            a, b = x[i - 1], y[j - 1]
            sub = prev[j - 1] + (0.0 if a == b else cost(a, b))
            cur[j] = min(prev[j] + 1.0, cur[j - 1] + 1.0, sub)
        prev = cur
    return prev[m]


def ocr_confusion_cost(a: str, b: str) -> float:
    """OCR_SUB_COST if (a,b) is a documented ANPR confusable pair, else 1.0.

    The pairs are imported from mediator.plate_resolve.CONFUSABLE (single source of truth)
    and the relation is treated as symmetric.
    """
    a, b = _fold(a), _fold(b)
    if a == b:
        return 0.0
    if b in CONFUSABLE.get(a, "") or a in CONFUSABLE.get(b, ""):
        return OCR_SUB_COST
    return 1.0


def ocr_edit_similarity(s1: str, s2: str) -> float:
    """Confusion-weighted edit distance, normalised as on p. 17."""
    x, y = _fold(s1), _fold(s2)
    rule = _empty_rule(x, y)
    if rule is not None:
        return rule
    d = weighted_edit_distance(x, y, ocr_confusion_cost)
    return 1.0 - d / max(len(x), len(y))


def jaro(s1: str, s2: str) -> float:
    """jaro(x,y) = 1/3 [ c/|x| + c/|y| + (c - t/2)/c ]   (p. 20-21).

    c = common characters: xi = yj within a matching window of max(|x|,|y|)/2 - 1 positions
    (p. 22 spells the window out). t = transpositions, the number of positions at which the
    two matched subsequences disagree.
    """
    x, y = _fold(s1), _fold(s2)
    rule = _empty_rule(x, y)
    if rule is not None:
        return rule
    if x == y:
        return 1.0
    n, m = len(x), len(y)
    window = max(max(n, m) // 2 - 1, 0)
    x_hit = [False] * n
    y_hit = [False] * m
    c = 0
    for i in range(n):
        lo, hi = max(0, i - window), min(i + window + 1, m)
        for j in range(lo, hi):
            if y_hit[j] or x[i] != y[j]:
                continue
            x_hit[i] = y_hit[j] = True
            c += 1
            break
    if c == 0:
        return 0.0
    t = 0
    k = 0
    for i in range(n):
        if not x_hit[i]:
            continue
        while not y_hit[k]:
            k += 1
        if x[i] != y[k]:
            t += 1
        k += 1
    return (c / n + c / m + (c - t / 2) / c) / 3.0


def jaro_winkler(s1: str, s2: str, p: float = 0.1, max_prefix: int = 4) -> float:
    """jaro-winkler(x,y) = (1 - PL*PW) * jaro(x,y) + PL*PW   (p. 22).

    PL = length of the longest common prefix (capped at max_prefix), PW = p, the prefix weight
    (the deck uses PW = 0.1). Algebraically identical to jaro + PL*PW*(1 - jaro).
    Slide check: "Vishwanathan" vs "Vishvnahtan" -> jaro 0.847, jaro-winkler 0.9082.
    """
    j = jaro(s1, s2)
    x, y = _fold(s1), _fold(s2)
    pl = 0
    for i in range(min(max_prefix, len(x), len(y))):
        if x[i] != y[i]:
            break
        pl += 1
    return (1.0 - pl * p) * j + pl * p


# --------------------------------------------------------------------------- set based

def qgrams(s: str, q: int = 2, pad: bool = True) -> List[str]:
    """Character q-grams Gq(s), |Gq(s)| = |s| - q + 1 (p. 45).

    With pad=True the string is padded with q-1 '#' at each end, the special character the
    deck uses "to handle the start and end of string" (p. 24). "dave" -> #d, da, av, ve, e#.
    Returned as a list so callers can count multiplicities (TF/IDF needs them); the overlap
    and Jaccard measures turn it into the set Bx of p. 25.
    """
    t = _fold(s)
    if pad and q > 1:
        t = "#" * (q - 1) + t + "#" * (q - 1)
    if q <= 0 or not t:
        return []
    return [t[i:i + q] for i in range(len(t) - q + 1)]


def overlap_count(s1: str, s2: str, q: int = 2) -> int:
    """O(x,y) = |Bx ∩ By|, the raw number of common tokens (p. 25). Slide: O("dave","dav") = 3."""
    return len(set(qgrams(s1, q)) & set(qgrams(s2, q)))


def overlap(s1: str, s2: str, q: int = 2) -> float:
    """Overlap coefficient: |Bx ∩ By| / min(|Bx|, |By|).

    p. 25 defines O(x,y) as the raw count, which is unbounded; the harness needs [0,1], so
    this normalises by the smaller token set and `overlap_count` keeps the slide's raw figure.
    """
    x, y = _fold(s1), _fold(s2)
    rule = _empty_rule(x, y)
    if rule is not None:
        return rule
    bx, by = set(qgrams(x, q)), set(qgrams(y, q))
    if not bx or not by:
        return 0.0
    return len(bx & by) / min(len(bx), len(by))


def jaccard(s1: str, s2: str, q: int = 2) -> float:
    """J(x,y) = |Bx ∩ By| / |Bx ∪ By|   (p. 26). Slide: J("dave","dav") = 3/6 = 0.5."""
    x, y = _fold(s1), _fold(s2)
    rule = _empty_rule(x, y)
    if rule is not None:
        return rule
    bx, by = set(qgrams(x, q)), set(qgrams(y, q))
    union = bx | by
    if not union:
        return 0.0
    return len(bx & by) / len(union)


def _tokens(s: str, q: Optional[int]) -> List[str]:
    """q-gram tokens, or whitespace-delimited word tokens when q is None (p. 24)."""
    if q is None:
        return [t for t in _fold(s).split() if t]
    return qgrams(s, q)


def tfidf_cosine(s1: str, s2: str, corpus: Sequence[str], q: Optional[int] = 2) -> float:
    """Cosine similarity of TF/IDF vectors (p. 27-31).

    tf(t,d) = occurrences of t in d; idf(t) = log(N / Nd), the smoothed form the deck
    recommends on p. 28. The document collection is `corpus` -- in this project the live
    registration list, so a rare plate series carries more weight than a common state code.
    s1 and s2 are appended to the collection when absent, so that Nd >= 1 for every term
    that actually occurs and the measure stays symmetric.
    q=None tokenises on whitespace, which is what the worked example on p. 29-30 does.
    """
    x, y = _fold(s1), _fold(s2)
    rule = _empty_rule(x, y)
    if rule is not None:
        return rule
    docs = [list(_tokens(c, q)) for c in corpus]
    for extra in (x, y):
        if not any(d == list(_tokens(extra, q)) for d in docs):
            docs.append(list(_tokens(extra, q)))
    n_docs = len(docs)
    df: Counter = Counter()
    for d in docs:
        for t in set(d):
            df[t] += 1

    def idf(t: str) -> float:
        nd = df.get(t, 1) or 1
        return math.log(n_docs / nd)

    tf_x, tf_y = Counter(_tokens(x, q)), Counter(_tokens(y, q))
    vx = {t: f * idf(t) for t, f in tf_x.items()}
    vy = {t: f * idf(t) for t, f in tf_y.items()}
    dot = sum(vx[t] * vy.get(t, 0.0) for t in vx)
    nx = math.sqrt(sum(v * v for v in vx.values()))
    ny = math.sqrt(sum(v * v for v in vy.values()))
    if nx == 0.0 or ny == 0.0:
        return 0.0
    return max(0.0, min(1.0, dot / (nx * ny)))


def tfidf_measure(corpus: Sequence[str], q: Optional[int] = 2) -> Callable[[str, str], float]:
    """Factory binding a corpus, so TF/IDF can sit in ALL_MEASURES as a 2-argument callable."""
    frozen = list(corpus)

    def measure(s1: str, s2: str) -> float:
        return tfidf_cosine(s1, s2, frozen, q)

    return measure


# --------------------------------------------------------------------------- similarity based

def _longest_common_substring(x: str, y: str) -> tuple:
    """Returns (start_in_x, start_in_y, length) of the longest common substring. O(|x||y|)."""
    best_i = best_j = best_len = 0
    prev = [0] * (len(y) + 1)
    for i in range(1, len(x) + 1):
        cur = [0] * (len(y) + 1)
        for j in range(1, len(y) + 1):
            if x[i - 1] == y[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best_len:
                    best_len = cur[j]
                    best_i, best_j = i - cur[j], j - cur[j]
        prev = cur
    return best_i, best_j, best_len


def _ro_matches(x: str, y: str, min_size: int = 1) -> int:
    """Recursive count of characters in common, steps 1-5 of p. 33."""
    if not x or not y:
        return 0
    i, j, length = _longest_common_substring(x, y)
    if length < min_size:
        return 0
    return (length
            + _ro_matches(x[:i], y[:j], min_size)
            + _ro_matches(x[i + length:], y[j + length:], min_size))


def ratcliff_obershelp(s1: str, s2: str) -> float:
    """2 * (characters found in common) / (total characters in the two strings)  (p. 33-34).

    The common characters are found by recursively taking the longest common substring and
    recursing left and right of it. Slide example: MATHEMATICS vs MATEMATICA = 2*9/21 = 0.857.
    """
    x, y = _fold(s1), _fold(s2)
    rule = _empty_rule(x, y)
    if rule is not None:
        return rule
    return 2.0 * _ro_matches(x, y) / (len(x) + len(y))


# --------------------------------------------------------------------------- phonetic

_SOUNDEX_DIGITS = {
    **{c: "1" for c in "BFPV"},
    **{c: "2" for c in "CGJKQSXZ"},
    **{c: "3" for c in "DT"},
    "L": "4",
    **{c: "5" for c in "MN"},
    "R": "6",
}


def soundex(s: str) -> str:
    """Four-character Soundex code (p. 37).

    Step 1 keep the first letter; step 2 remove W and H from the rest and replace letters by
    digits (B,F,P,V->1; C,G,J,K,Q,S,X,Z->2; D,T->3; L->4; M,N->5; R->6); step 3 collapse runs
    of identical digits; step 4 drop non-digits and return the first four characters, padded
    with 0. Slide check: Banarjee -> B562, Benerji -> B562.
    """
    t = "".join(ch for ch in _fold(s) if ch.isalpha())
    if not t:
        return ""
    first, rest = t[0], t[1:]
    rest = "".join(ch for ch in rest if ch not in "WH")
    mapped = [_SOUNDEX_DIGITS.get(ch, ch) for ch in rest]
    collapsed: List[str] = []
    for ch in mapped:
        if collapsed and ch == collapsed[-1] and ch.isdigit():
            continue
        collapsed.append(ch)
    digits = "".join(ch for ch in collapsed if ch.isdigit())
    return (first + digits + "000")[:4]


def soundex_similarity(s1: str, s2: str) -> float:
    """1.0 when the two Soundex codes agree, else 0.0 (p. 37: "judged similar if share the same
    code"). Expected to be useless on plates, which are not phonetic -- we report it anyway."""
    x, y = _fold(s1), _fold(s2)
    rule = _empty_rule(x, y)
    if rule is not None:
        return rule
    return 1.0 if soundex(x) == soundex(y) else 0.0


# --------------------------------------------------------------------------- harness registry

ALL_MEASURES: Dict[str, Callable[[str, str], float]] = {
    "edit_similarity": edit_similarity,
    "ocr_edit_similarity": ocr_edit_similarity,
    "jaro": jaro,
    "jaro_winkler": jaro_winkler,
    "overlap": overlap,
    "jaccard": jaccard,
    "ratcliff_obershelp": ratcliff_obershelp,
    "soundex_similarity": soundex_similarity,
}
