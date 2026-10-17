"""Step 1 of the two-step matching framework: candidate generation at scale (p. 44).

The naive solution compares every query against every record -- |A||B| calls to a costly
sim() (p. 40-43). At 605 registrations that is survivable; at the 50-million-record scale
the deck targets it is not, and the examiner's question "find *all* vehicles approximately
matching" is precisely the scalability question. This module implements the deck's four
classical FindCands variants (p. 46) over character q-grams:

  * inverted index          p. 47-51   (limitations p. 52)
  * size / length filtering p. 53-54
  * prefix filtering        p. 55-58, proof p. 64
  * position index          p. 65

Nothing here scores a pair. Scoring is step 2 and lives in `mediator.similarity`; this
module never reimplements a measure.
"""
from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from mediator.similarity import qgrams
from mediator.transforms import norm_plate

DEFAULT_Q = 2
DEFAULT_MAX_EDITS = 1


@dataclass
class BlockingStats:
    """What one blocking call cost and what it saved -- the scalability evidence."""

    queried: str = ""
    candidates_before: int = 0
    candidates_after: int = 0
    index_lookups: int = 0
    elapsed_ms: float = 0.0
    filters_applied: List[str] = field(default_factory=list)


def plate_tokenizer(s: str, q: int = DEFAULT_Q) -> List[str]:
    """Canonical plate form then character q-grams."""
    return qgrams(norm_plate(s) or "", q)


class QGramIndex:
    """Inverted index token -> set of string ids, as on p. 47-51."""

    def __init__(self, q: int = DEFAULT_Q,
                 tokenizer: Optional[Callable[[str, int], List[str]]] = None) -> None:
        self.q = q
        self._tokenizer = tokenizer or plate_tokenizer
        self.postings: Dict[str, Set[str]] = {}
        self.tokens: Dict[str, List[str]] = {}
        self.positions: Dict[str, Dict[str, int]] = {}
        self.df: Counter = Counter()
        self.ids: List[str] = []
        self.last_stats: Optional[BlockingStats] = None

    @classmethod
    def build(cls, strings: Iterable[str], q: int = DEFAULT_Q,
              tokenizer: Optional[Callable[[str, int], List[str]]] = None) -> "QGramIndex":
        idx = cls(q=q, tokenizer=tokenizer)
        raw: Dict[str, Set[str]] = {}
        for s in strings:
            toks = set(idx._tokenizer(s, q))
            raw[s] = toks
            idx.df.update(toks)
        for s, toks in raw.items():
            ordered = sorted(toks, key=idx._order_key)
            idx.tokens[s] = ordered
            idx.positions[s] = {t: i for i, t in enumerate(ordered)}
            for t in ordered:
                idx.postings.setdefault(t, set()).add(s)
        idx.ids = sorted(raw)
        return idx

    def _order_key(self, token: str) -> Tuple[int, str]:
        """Global order: increasing document frequency, ties alphabetical (p. 57-59)."""
        return (self.df.get(token, 0), token)

    def _query_tokens(self, query: str) -> List[str]:
        return sorted(set(self._tokenizer(query, self.q)), key=self._order_key)

    def __len__(self) -> int:
        return len(self.ids)

    def recommended_k(self, query: str, max_edits: int = DEFAULT_MAX_EDITS) -> int:
        """Overlap required of a true match, from the p. 45 inequality."""
        return max(1, len(self._query_tokens(query)) - self.q * max_edits)

    def candidates(self, query: str, min_overlap: int = 1) -> Set[str]:
        t0 = time.perf_counter()
        qt = self._query_tokens(query)
        counts: Counter = Counter()
        lookups = 0
        for tok in qt:
            lookups += 1
            for sid in self.postings.get(tok, ()):
                counts[sid] += 1
        out = {sid for sid, c in counts.items() if c >= min_overlap}
        self._record(query, len(self.ids), out, lookups, t0, ["inverted_index"])
        return out

    def candidates_size_filtered(self, query: str, delta: int = DEFAULT_MAX_EDITS) -> Set[str]:
        """p. 53-54: keep only ids whose token count can still reach the overlap bound."""
        t0 = time.perf_counter()
        qt = self._query_tokens(query)
        nx = len(qt)
        counts: Counter = Counter()
        lookups = 0
        for tok in qt:
            lookups += 1
            for sid in self.postings.get(tok, ()):
                counts[sid] += 1
        out = set()
        for sid, c in counts.items():
            ny = len(self.tokens[sid])
            if abs(nx - ny) > self.q * delta:
                continue
            if c >= max(nx, ny) - self.q * delta:
                out.add(sid)
        self._record(query, len(self.ids), out, lookups, t0, ["inverted_index", "size"])
        return out

    def candidates_prefix_filtered(self, query: str, k: int) -> Set[str]:
        """p. 55-58: probe only the prefix of x against the prefixes of each y."""
        t0 = time.perf_counter()
        qt = self._query_tokens(query)
        px = qt[:max(0, len(qt) - (k - 1))]
        out = set()
        lookups = 0
        for tok in px:
            lookups += 1
            for sid in self.postings.get(tok, ()):
                ny = len(self.tokens[sid])
                ylen = ny - (k - 1)
                if ylen > 0 and self.positions[sid][tok] < ylen:
                    out.add(sid)
        self._record(query, len(self.ids), out, lookups, t0,
                     ["inverted_index", "prefix"])
        return out

    def candidates_position_filtered(self, query: str, k: int) -> Set[str]:
        """p. 65: prune with max overlap = prefix overlap + min of the unseen tokens."""
        t0 = time.perf_counter()
        qt = self._query_tokens(query)
        nx = len(qt)
        px_len = max(0, nx - (k - 1))
        px = qt[:px_len]
        hits: Dict[str, List[Tuple[int, int]]] = {}
        lookups = 0
        for i, tok in enumerate(px):
            lookups += 1
            for sid in self.postings.get(tok, ()):
                ny = len(self.tokens[sid])
                ylen = ny - (k - 1)
                j = self.positions[sid][tok]
                if ylen > 0 and j < ylen:
                    hits.setdefault(sid, []).append((i, j))
        out = set()
        for sid, pairs in hits.items():
            ny = len(self.tokens[sid])
            last_i = max(p[0] for p in pairs)
            last_j = max(p[1] for p in pairs)
            ub = len(pairs) + min(nx - last_i - 1, ny - last_j - 1)
            if ub >= k:
                out.add(sid)
        self._record(query, len(self.ids), out, lookups, t0,
                     ["inverted_index", "prefix", "position"])
        return out

    def max_overlap_bound(self, query: str, sid: str, k: int) -> int:
        """The p. 65 estimate for one pair, exposed so the viva can show the arithmetic."""
        qt = self._query_tokens(query)
        nx = len(qt)
        ny = len(self.tokens[sid])
        px = qt[:max(0, nx - (k - 1))]
        py = set(self.tokens[sid][:max(0, ny - (k - 1))])
        pairs = [(i, self.positions[sid][t]) for i, t in enumerate(px) if t in py]
        if not pairs:
            return 0
        last_i = max(p[0] for p in pairs)
        last_j = max(p[1] for p in pairs)
        return len(pairs) + min(nx - last_i - 1, ny - last_j - 1)

    def _record(self, query: str, before: int, out: Set[str], lookups: int,
                t0: float, filters: List[str]) -> None:
        self.last_stats = BlockingStats(
            queried=query,
            candidates_before=before,
            candidates_after=len(out),
            index_lookups=lookups,
            elapsed_ms=(time.perf_counter() - t0) * 1000.0,
            filters_applied=filters,
        )


STRATEGIES = ("none", "index", "size", "prefix", "position")


def candidate_plates(read: str, registry: Sequence[str], *, strategy: str = "position",
                     **kw) -> Tuple[List[str], BlockingStats]:
    """The one entry point the rest of the code calls."""
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; expected one of {STRATEGIES}")
    q = int(kw.get("q", DEFAULT_Q))
    max_edits = int(kw.get("max_edits", DEFAULT_MAX_EDITS))
    tokenizer = kw.get("tokenizer")
    registry = list(registry)

    if strategy == "none":
        t0 = time.perf_counter()
        out = sorted(set(registry))
        return out, BlockingStats(queried=read, candidates_before=len(registry),
                                  candidates_after=len(out), index_lookups=0,
                                  elapsed_ms=(time.perf_counter() - t0) * 1000.0,
                                  filters_applied=["full_scan"])

    idx = QGramIndex.build(registry, q=q, tokenizer=tokenizer)
    k = int(kw.get("k", idx.recommended_k(read, max_edits)))
    if strategy == "index":
        out = idx.candidates(read, min_overlap=int(kw.get("min_overlap", 1)))
    elif strategy == "size":
        out = idx.candidates_size_filtered(read, int(kw.get("delta", max_edits)))
    elif strategy == "prefix":
        out = idx.candidates_prefix_filtered(read, k)
    else:
        out = idx.candidates_position_filtered(read, k)
    return sorted(out), idx.last_stats


_LENGTH_FN = {"sqlite": "LENGTH", "postgres": "LENGTH", "postgresql": "LENGTH",
              "mysql": "CHAR_LENGTH"}


def sql_prefilter(read: str, column: str, dialect: str) -> str:
    """A blocking predicate cheap enough to push into the source's own SQL.

    Same normalisation expression as mediator/decomposer.py, same length window as the
    size filter (p. 53) and the same first two characters as a one-token prefix filter.
    Builds the string only; it executes nothing.
    """
    plate = norm_plate(read) or ""
    expr = f"UPPER(REPLACE(REPLACE({column}, '-', ''), ' ', ''))"
    length_fn = _LENGTH_FN.get(dialect.lower(), "LENGTH")
    n = len(plate)
    lo, hi = max(1, n - 1), n + 1
    head = plate[:2]
    return (f"({expr} LIKE '{head}%' AND "
            f"{length_fn}({expr}) BETWEEN {lo} AND {hi})")
