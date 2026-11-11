"""CONTEXT-ADDED (R3: the DOCS agency's own text index): tokeniser + BM25 + typo-tolerant anchor lookup.

The DOCS source ranks its documents itself, the way a real document store would: the mediator sends a weighted
term query and a few anchors (plate, chassis, FIR no., policy no.), and only the top k documents cross the network.

* Tokeniser keeps identifiers whole. Plates are written many ways in free text ("DL-05-CD-9876", "dl 05 cd 9876",
  OCR'd "DLO5CD9B76"); every plate-shaped span becomes ONE token, uppercase alphanumerics lowercased
  ("dl05cd9876"). Codes such as "FIR00028/2026" or "PUC-2026-100" are merged the same way.
* Ranking is Okapi BM25 (Robertson & Zaragoza): sum over query terms of w_t * idf(t) * tf(k1+1) / (tf + k1(1-b+b dl/avgdl)).
* Anchors are matched approximately (approximate entity extraction): a q-gram count filter over the vocabulary of
  identifier-like tokens shortlists candidates, then an OCR-confusion-weighted edit similarity verifies them
  (0<->O, 8<->B cost less than other substitutions). Each verified token is scored like a BM25 term, times
  ANCHOR_BOOST x similarity, so a typo'd plate still finds its documents but ranks a little below an exact one.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from mediator.similarity import ocr_edit_similarity, qgrams

# Indian state/UT codes that appear in our corpus. Trade-off: a closed list stops English pairs such as
# "on 12 th 2026" being read as a plate, at the cost of missing plates from states not listed.
STATE_CODES = {"DL", "HR", "UP", "MH", "KA", "RJ", "PB", "GJ", "TN", "WB", "MP", "TS", "AP", "KL", "CH", "UK",
               "BR", "JH", "OD", "HP", "JK", "GA", "CG"}
_DIGITISH = "0-9OoIlZzSsBbDQ"  # what OCR turns digits into
PLATE_RE = re.compile(rf"(?<![A-Za-z0-9])([A-Za-z]{{2}})[\s\-.]?([{_DIGITISH}]{{1,2}})[\s\-.]?"
                      rf"([A-Za-z0-9]{{1,3}})[\s\-.]?([{_DIGITISH}]{{3,4}})(?![A-Za-z0-9])")
CODE_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{2,4}[\-/]?\d{3,}(?:[\-/]\d+){0,2}(?![A-Za-z0-9])")
WORD_RE = re.compile(r"[A-Za-z0-9]+")
STOPWORDS = frozenset(
    "a an and are as at be by for from has have he her his in is it its of on or she that the their they this "
    "to was were which with who will been into after before near no not also said per under than then there "
    "ke ki ka ko ne hai tha thi ho se par aur mein bhi ek ps".split())   # "ps": "Karol Bagh PS" == "P.S. Karol Bagh"
# Spelling variants folded at index AND query time, so either spelling finds both.
VARIANTS = {"gurgaon": "gurugram", "kr": "kumar", "mohd": "mohammed", "md": "mohammed", "dilli": "delhi",
            "colour": "color", "grey": "gray", "nr": "near", "stn": "station"}
K1, B = 1.2, 0.75
ANCHOR_BOOST = 3.0       # an anchor hit is worth ~3 ordinary query terms: identifiers are what tie a text to a row
# OCR-aware similarity needed to accept a fuzzy identifier. Trade-off: OCR confusions are cheap (DLO5CD9B76 ~
# DL05CD9876 = 0.96) but any other single substitution costs a full edit (DL05CD9877 = 0.90) and is refused,
# because a plate one character off is usually a DIFFERENT vehicle (the hard negatives in the corpus).
ANCHOR_MIN_SIM = 0.92
ANCHOR_MAX_EDITS = 2


def canon(value: Any) -> str:
    """Uppercase-alphanumerics identity of a plate / code, lowercased for the index ("DL-05-CD-9876" -> "dl05cd9876")."""
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).lower()


def plate_spans(text: str) -> List[Tuple[int, int, str]]:
    """(start, end, canonical) for every plate-shaped span; validated so ordinary words are not taken for plates."""
    out = []
    for m in PLATE_RE.finditer(text):
        token = canon(m.group(0))
        tail_digits = sum(ch.isdigit() for ch in m.group(4))
        if m.group(1).upper() in STATE_CODES and 7 <= len(token) <= 11 and tail_digits >= 2:
            out.append((m.start(), m.end(), token))
    return out


def tokenize_spans(text: str) -> List[Tuple[str, int, int]]:
    """(token, start, end) in reading order: plates and codes as single tokens, then plain words."""
    taken: List[Tuple[int, int, str]] = list(plate_spans(text))
    covered = [(s, e) for s, e, _ in taken]

    def free(s: int, e: int) -> bool:
        return all(e <= cs or s >= ce for cs, ce in covered)

    for m in CODE_RE.finditer(text):
        if free(m.start(), m.end()):
            taken.append((m.start(), m.end(), canon(m.group(0))))
            covered.append((m.start(), m.end()))
    for m in WORD_RE.finditer(text):
        if not free(m.start(), m.end()):
            continue
        word = m.group(0).lower()
        word = VARIANTS.get(word, word)
        if word in STOPWORDS or len(word) < 2 and not word.isdigit():
            continue
        taken.append((m.start(), m.end(), word))
    return [(tok, s, e) for s, e, tok in sorted(taken)]


def tokenize(text: str) -> List[str]:
    return [tok for tok, _, _ in tokenize_spans(text or "")]


def is_identifier(token: str) -> bool:
    """Identifier-like = mixes letters and digits and is long enough to name one thing (plate, chassis, FIR)."""
    return len(token) >= 6 and any(c.isdigit() for c in token) and any(c.isalpha() for c in token)


@dataclass
class BM25Index:
    """In-memory inverted index over DOCUMENTS. Built from the agency's own table; rebuilt when it changes."""
    docs: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    postings: Dict[str, Dict[str, int]] = field(default_factory=lambda: defaultdict(dict))
    doc_len: Dict[str, int] = field(default_factory=dict)
    gram_index: Dict[str, set] = field(default_factory=lambda: defaultdict(set))  # q-gram -> identifier tokens
    avgdl: float = 1.0
    signature: Any = None

    @classmethod
    def build(cls, rows: Iterable[Mapping[str, Any]], signature: Any = None) -> "BM25Index":
        idx = cls(signature=signature)
        for row in rows:
            doc_id = str(row["doc_id"])
            idx.docs[doc_id] = dict(row)
            toks = tokenize(f"{row.get('title') or ''} {row.get('body') or ''}")
            idx.doc_len[doc_id] = len(toks)
            for tok, tf in Counter(toks).items():
                idx.postings[tok][doc_id] = tf
        idx.avgdl = (sum(idx.doc_len.values()) / len(idx.doc_len)) if idx.doc_len else 1.0
        for tok in idx.postings:
            if is_identifier(tok):
                for g in set(qgrams(tok, 2)):
                    idx.gram_index[g].add(tok)
        return idx

    @property
    def n_docs(self) -> int:
        return len(self.docs)

    def idf(self, token: str) -> float:
        df = len(self.postings.get(token, {}))
        return math.log(1.0 + (self.n_docs - df + 0.5) / (df + 0.5))

    def _add_term(self, scores: Dict[str, float], token: str, weight: float) -> None:
        idf = self.idf(token)
        for doc_id, tf in self.postings.get(token, {}).items():
            norm = K1 * (1 - B + B * self.doc_len[doc_id] / self.avgdl)
            scores[doc_id] += weight * idf * tf * (K1 + 1) / (tf + norm)

    def fuzzy_identifiers(self, value: str) -> List[Tuple[str, float]]:
        """Vocabulary identifiers within ANCHOR_MAX_EDITS of `value`: q-gram count filter, then OCR-aware verify."""
        target = canon(value)
        if len(target) < 4:
            return []
        grams = Counter(qgrams(target, 2))
        hits: Counter = Counter()
        for g, n in grams.items():
            for tok in self.gram_index.get(g, ()):
                hits[tok] += n
        out = []
        for tok, shared in hits.items():
            # count filter (Gravano et al.): k edits destroy at most k*q of the |s|+q-1 padded q-grams
            if shared < max(len(target), len(tok)) + 1 - 2 * ANCHOR_MAX_EDITS:
                continue
            sim = 1.0 if tok == target else ocr_edit_similarity(tok, target)
            if sim >= ANCHOR_MIN_SIM:
                out.append((tok, round(sim, 4)))
        return sorted(out, key=lambda p: -p[1])

    def search(self, terms: Sequence[Mapping[str, Any]], anchors: Sequence[Mapping[str, Any]] = (),
               k: int = 20, doc_types: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
        scores: Dict[str, float] = defaultdict(float)
        for term in terms:
            weight = float(term.get("weight", 1.0) or 0.0)
            toks = tokenize(str(term.get("term", "")))
            for tok in toks:  # a multi-word descriptor ("Sector 29") spreads its weight over its words
                self._add_term(scores, tok, weight / len(toks))
        hits: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for anchor in anchors:
            for tok, sim in self.fuzzy_identifiers(str(anchor.get("value", ""))):
                self._add_term(scores, tok, ANCHOR_BOOST * sim)
                for doc_id in self.postings.get(tok, {}):
                    hits[doc_id].append({"value": anchor.get("value"), "attr": anchor.get("attr"),
                                         "token": tok, "similarity": sim})
        allowed = {t.upper() for t in doc_types} if doc_types else None
        ranked = sorted(((s, d) for d, s in scores.items() if s > 0 and
                         (allowed is None or str(self.docs[d].get("doc_type", "")).upper() in allowed)),
                        key=lambda p: (-p[0], p[1]))
        out = []
        for score, doc_id in ranked[: max(0, int(k))]:
            row = self.docs[doc_id]
            out.append({"doc_id": doc_id, "doc_type": row.get("doc_type"), "title": row.get("title"),
                        "issued_on": None if row.get("issued_on") is None else str(row.get("issued_on")),
                        "issuer": row.get("issuer"), "score": round(score, 4), "body": row.get("body"),
                        "anchor_hits": hits.get(doc_id, [])})
        return out
