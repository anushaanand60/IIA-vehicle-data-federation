"""Step 1 of the two-step matching framework: candidate generation at scale (p. 44).

The naive solution compares every query against every record -- |A||B| calls to a costly
sim() (p. 40-43). At 605 registrations that is survivable; at the 50-million-record scale
the deck targets it is not, and the examiner's question "find *all* vehicles approximately
matching" is precisely the scalability question. This module implements the deck's four
classical FindCands variants (p. 46) over character q-grams:

  * inverted index          p. 47-51   (its limitations, p. 52)
  * size / length filtering p. 53-54
  * prefix filtering        p. 55-58, with the proof on p. 64
  * position index          p. 65

and the Week 8 recap of all four, slides 1-7.

Nothing here scores a pair: scoring is step 2 and lives in `mediator.similarity`, which this
module never duplicates. Nothing here performs I/O either -- `sql_prefilter` *builds* a
predicate string and does not execute it.

Design choices worth defending out loud
---------------------------------------
* **Recall first, pruning second.** Every bound below is the deck's, used in the safe
  direction. Where a choice existed we took the one that keeps more candidates: a blocking
  pass that drops the true vehicle cannot be repaired by any amount of step-2 scoring
  (p. 82 insists retrieval and scoring be evaluated separately for exactly this reason).
* **The index is built per call, not cached.** The mediator's standing invariant is that no
  source data is cached; a stale index would answer a query about a vehicle the examiner has
  just deleted. Building over 605 plates costs single-digit milliseconds. Callers that can
  prove freshness may pass their own index via `candidate_plates(..., index=...)`.
* **The strategies are cumulative.** `size` means index+size, `prefix` means index+size+
  prefix, `position` adds the p. 65 bound on top -- which is how the deck composes them
  ("Prefix + Inverted Index", p. 57-63; position index as a further limit, p. 65). The
  individual `QGramIndex` methods remain usable on their own, each with a `restrict`
  argument, so each filter can also be demonstrated in isolation.
"""
from __future__ import annotations

import bisect
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from mediator.similarity import qgrams
from mediator.transforms import norm_plate

DEFAULT_Q = 2
DEFAULT_MAX_EDITS = 1

Tokenizer = Callable[[str, int], List[str]]


@dataclass
class BlockingStats:
    """What one blocking call cost and what it saved -- the scalability evidence.

    `candidates_before` is always the size of the naive candidate universe (p. 40: every
    indexed string), so the reduction factor is readable straight off a single call.
    """

    queried: str = ""
    candidates_before: int = 0
    candidates_after: int = 0
    index_lookups: int = 0
    elapsed_ms: float = 0.0
    filters_applied: List[str] = field(default_factory=list)


def plate_tokenizer(s: str, q: int = DEFAULT_Q) -> List[str]:
    """Canonical plate form (`transforms.norm_plate`) then character q-grams (p. 45)."""
    return qgrams(norm_plate(s) or "", q)


class QGramIndex:
    """Inverted index, token -> set of string ids, as built on p. 47-51.

    The id of a string is the string as supplied, so the caller gets its own registry
    values back untouched; tokens come from the (normalising) tokenizer. Alongside the
    postings we keep, per id, its token list in the global order and each token's position
    in that list -- the position index of p. 65.
    """

    def __init__(self, q: int = DEFAULT_Q, tokenizer: Optional[Tokenizer] = None) -> None:
        self.q = q
        self._tokenizer: Tokenizer = tokenizer or plate_tokenizer
        self.postings: Dict[str, Set[str]] = {}
        self.tokens: Dict[str, List[str]] = {}
        self.positions: Dict[str, Dict[str, int]] = {}
        self.df: Counter = Counter()
        self.ids: List[str] = []
        self.last_stats: Optional[BlockingStats] = None
        self._sizes: List[int] = []  # sorted token counts, for the size bound
        self._qcache: Dict[str, List[str]] = {}

    # ------------------------------------------------------------------ construction

    @classmethod
    def build(cls, strings: Iterable[str], q: int = DEFAULT_Q,
              tokenizer: Optional[Tokenizer] = None) -> "QGramIndex":
        """Build the inverted index over `strings` (p. 47). Duplicates collapse to one id."""
        idx = cls(q=q, tokenizer=tokenizer)
        raw: Dict[str, Set[str]] = {}
        for s in strings:
            if s in raw:
                continue
            raw[s] = set(idx._tokenizer(s, q))
        # Document frequency is counted over the indexed collection only -- p. 58 orders the
        # universe of terms by frequency and builds the prefix index over Y, so Y is the
        # collection whose statistics define the order. (The Week 8 slide 5 order is taken
        # over X u Y; on that example both orders yield the same candidate set.)
        for toks in raw.values():
            idx.df.update(toks)
        for s, toks in raw.items():
            ordered = sorted(toks, key=idx._order_key)
            idx.tokens[s] = ordered
            idx.positions[s] = {t: i for i, t in enumerate(ordered)}
            for t in ordered:
                idx.postings.setdefault(t, set()).add(s)
        idx.ids = sorted(raw)
        idx._sizes = sorted(len(v) for v in idx.tokens.values())
        return idx

    def _order_key(self, token: str) -> Tuple[int, str]:
        """Global order O: increasing document frequency, ties broken alphabetically.

        p. 57 -- "impose an ordering O over the universe of all possible terms in increasing
        frequency. This makes prefixes more discriminative." The alphabetical tie-break is
        ours; it only has to be *a* total order, and a deterministic one makes the candidate
        set reproducible months later, which the appeal story needs. Tokens the index has
        never seen sort first (df 0), which is correct: they are maximally discriminative.
        """
        return (self.df.get(token, 0), token)

    def _query_tokens(self, query: str) -> List[str]:
        """Bx for the query, deduplicated and sorted into the global order O (p. 57).

        Memoised per index instance: one `candidate_plates` call runs up to three stages
        over the same query and the sort is pure arithmetic on the query string. This
        caches a *derivation of the query*, never a source row, so the no-cache invariant
        is untouched.
        """
        hit = self._qcache.get(query)
        if hit is None:
            hit = sorted(set(self._tokenizer(query, self.q)), key=self._order_key)
            if len(self._qcache) > 4096:
                self._qcache.clear()
            self._qcache[query] = hit
        return hit

    def __len__(self) -> int:
        return len(self.ids)

    # ------------------------------------------------------------------ bounds

    def size_window(self, n_query_tokens: int, delta: int) -> Tuple[int, int]:
        """Token-count window a match may occupy (p. 53, edit-distance form).

        p. 53 gives two forms: |‖x|-|y‖ <= k for edit distance, and |x|/t >= |y| >= |x|*t
        for Jaccard (see `jaccard_size_window`). We use the edit-distance form because our
        noise model *is* an edit process: the OCR confusion table substitutes characters.
        Each of the <= delta edits moves the q-gram count by at most q (an insertion or
        deletion shifts it by 1 and can break q grams), so q*delta is the safe half-width.
        """
        half = self.q * max(0, delta)
        return (max(0, n_query_tokens - half), n_query_tokens + half)

    @staticmethod
    def jaccard_size_window(n_query_tokens: int, t: float) -> Tuple[float, float]:
        """p. 53-54 / Week 8 slide 3: |x|*t <= |y| <= |x|/t for J(x,y) >= t."""
        if not 0 < t <= 1:
            raise ValueError("Jaccard threshold t must be in (0, 1]")
        return (n_query_tokens * t, n_query_tokens / t)

    def required_overlap(self, n_query_tokens: int, n_other_tokens: int,
                         max_edits: int = DEFAULT_MAX_EDITS) -> int:
        """p. 45: ED(s,t) <= k  =>  ov_q(s,t) >= max(|Gq(s)|,|Gq(t)|) - q*k."""
        return max(0, max(n_query_tokens, n_other_tokens) - self.q * max_edits)

    def recommended_k(self, query: str, max_edits: int = DEFAULT_MAX_EDITS) -> int:
        """A deliberately conservative overlap requirement k for the prefix filter.

        The prefix theorem (p. 57, proved p. 64) is conditional: it preserves every pair
        whose true overlap is at least k. A k that is too *large* silently drops true
        matches, so we take the smallest k any plausible partner could force on us -- the
        p. 45 bound evaluated with the *smallest* indexed token count still inside the size
        window, not with the query's own count. On a registry of equal-length plates the two
        coincide (k = 11 - 2*1 = 9 for a ten-character plate at q=2); they diverge exactly
        when a short string could be the true match, and there we want the smaller k.
        """
        nx = len(self._query_tokens(query))
        lo, hi = self.size_window(nx, max_edits)
        i = bisect.bisect_left(self._sizes, lo)
        in_window = nx
        if i < len(self._sizes) and self._sizes[i] <= hi:
            in_window = min(nx, self._sizes[i])
        return max(1, in_window - self.q * max_edits)

    # ------------------------------------------------------------------ candidate sets

    def _probe(self, tokens: Sequence[str],
               restrict: Optional[Set[str]]) -> Tuple[Counter, int]:
        """Walk the inverted lists for `tokens`, counting shared tokens per id (p. 50-51)."""
        counts: Counter = Counter()
        lookups = 0
        for tok in tokens:
            lookups += 1
            for sid in self.postings.get(tok, ()):
                if restrict is None or sid in restrict:
                    counts[sid] += 1
        return counts, lookups

    def candidates(self, query: str, min_overlap: int = 1,
                   restrict: Optional[Set[str]] = None) -> Set[str]:
        """Plain inverted-index lookup (p. 47-51).

        Every id sharing at least `min_overlap` tokens with the query. p. 52 is the honest
        caveat: this still enumerates every pair sharing one token, which on q-grams of a
        plate is a large fraction of the registry -- which is why the filters below exist.
        """
        t0 = time.perf_counter()
        counts, lookups = self._probe(self._query_tokens(query), restrict)
        out = {sid for sid, c in counts.items() if c >= min_overlap}
        self._record(query, out, lookups, t0, ["inverted_index"])
        return out

    def candidates_size_filtered(self, query: str, delta: int = DEFAULT_MAX_EDITS,
                                 restrict: Optional[Set[str]] = None) -> Set[str]:
        """Size filtering, p. 53-54: discard ids whose length forbids the needed overlap.

        Two conditions, both from the deck: the token count must lie in the p. 53 window,
        and the observed shared-token count must reach the p. 45 lower bound for `delta`
        edits. Both are necessary conditions for ED <= delta, so neither can lose a match
        that is genuinely within delta edits.
        """
        t0 = time.perf_counter()
        qt = self._query_tokens(query)
        nx = len(qt)
        lo, hi = self.size_window(nx, delta)
        counts, lookups = self._probe(qt, restrict)
        out = set()
        for sid, c in counts.items():
            ny = len(self.tokens[sid])
            if not (lo <= ny <= hi):
                continue
            if c >= self.required_overlap(nx, ny, delta):
                out.add(sid)
        self._record(query, out, lookups, t0, ["inverted_index", "size"])
        return out

    def _prefix_hits(self, query: str, k: int, restrict: Optional[Set[str]]
                     ) -> Tuple[Dict[str, List[Tuple[int, int]]], int, int]:
        """Shared machinery for the prefix and position filters (p. 58).

        Probes only x' = the first |x| - (k-1) tokens of the query in the global order, and
        accepts an id only when the matching token also falls inside y' = the first
        |y| - (k-1) tokens of that id. Returns, per surviving id, the (pos_in_x, pos_in_y)
        pairs that matched. Ids with |y| < k are dropped: they cannot reach overlap k.
        """
        qt = self._query_tokens(query)
        nx = len(qt)
        px = qt[:max(0, nx - (k - 1))]
        hits: Dict[str, List[Tuple[int, int]]] = {}
        lookups = 0
        for i, tok in enumerate(px):
            lookups += 1
            for sid in self.postings.get(tok, ()):
                if restrict is not None and sid not in restrict:
                    continue
                ny = len(self.tokens[sid])
                y_prefix_len = ny - (k - 1)
                if y_prefix_len > 0 and self.positions[sid][tok] < y_prefix_len:
                    hits.setdefault(sid, []).append((i, self.positions[sid][tok]))
        return hits, lookups, nx

    def candidates_prefix_filtered(self, query: str, k: int,
                                   restrict: Optional[Set[str]] = None) -> Set[str]:
        """Prefix filtering over the prefix-inverted index (p. 55-58, proof p. 64).

        "if |x n y| >= k, then x' and y' must overlap, where x' is the prefix of size
        |x| - (k-1) of x and y' is the prefix of size |y| - (k-1) of y" (p. 57). k is the
        *required overlap*; pass `recommended_k(query)` unless you have a tighter one.
        """
        if k < 1:
            raise ValueError("k (required overlap) must be at least 1")
        t0 = time.perf_counter()
        hits, lookups, _ = self._prefix_hits(query, k, restrict)
        out = set(hits)
        self._record(query, out, lookups, t0, ["inverted_index", "prefix"])
        return out

    def candidates_position_filtered(self, query: str, k: int,
                                     restrict: Optional[Set[str]] = None) -> Set[str]:
        """Position index, p. 65 -- the upper bound that kills surviving prefix candidates.

        "Estimation of max overlap = overlap in prefixes + min of unseen tokens". Because x
        and y are both ordered by the same global order O, the token positions recorded in
        the prefix probe are co-monotone, so the last matched pair is the furthest in both.
        Everything after it is unseen, and at most min(unseen_x, unseen_y) of it can match.
        If that upper bound is below k the pair cannot reach overlap k and sim() is never
        called on it.
        """
        if k < 1:
            raise ValueError("k (required overlap) must be at least 1")
        t0 = time.perf_counter()
        hits, lookups, nx = self._prefix_hits(query, k, restrict)
        out = set()
        for sid, pairs in hits.items():
            if self._overlap_upper_bound(nx, len(self.tokens[sid]), pairs) >= k:
                out.add(sid)
        self._record(query, out, lookups, t0, ["inverted_index", "prefix", "position"])
        return out

    @staticmethod
    def _overlap_upper_bound(nx: int, ny: int, pairs: Sequence[Tuple[int, int]]) -> int:
        if not pairs:
            return 0
        last_i, last_j = pairs[-1]
        return len(pairs) + min(nx - last_i - 1, ny - last_j - 1)

    def max_overlap_bound(self, query: str, sid: str, k: int) -> int:
        """The p. 65 estimate for one pair, exposed so the viva can show the arithmetic."""
        hits, _, nx = self._prefix_hits(query, k, {sid})
        return self._overlap_upper_bound(nx, len(self.tokens[sid]), hits.get(sid, []))

    # ------------------------------------------------------------------ stats

    def _record(self, query: str, out: Set[str], lookups: int, t0: float,
                filters: List[str]) -> None:
        self.last_stats = BlockingStats(
            queried=query,
            candidates_before=len(self.ids),
            candidates_after=len(out),
            index_lookups=lookups,
            elapsed_ms=(time.perf_counter() - t0) * 1000.0,
            filters_applied=list(filters),
        )


STRATEGIES: Tuple[str, ...] = ("none", "index", "size", "prefix", "position")


def candidate_plates(read: str, registry: Sequence[str], *, strategy: str = "position",
                     **kw) -> Tuple[List[str], BlockingStats]:
    """The one entry point the rest of the code calls.

    `read` is a (possibly OCR-corrupted) plate; `registry` is the collection to search.
    Strategies are cumulative, mirroring how the deck composes the filters:

      none      full scan, the p. 40-43 baseline -- every registry value, no index
      index     inverted index, p. 47-51
      size      index + size filtering, p. 53
      prefix    index + size + prefix filtering, p. 55-58
      position  the above + the position-index bound, p. 65

    Keyword arguments: ``q`` (q-gram size), ``max_edits`` (the noise budget the bounds are
    made safe for, default 1 -- one OCR confusion), ``k`` (override the required overlap),
    ``delta`` (override the size tolerance), ``min_overlap`` (for `index`), ``tokenizer``,
    and ``index`` (a prebuilt `QGramIndex`, for callers that batch many reads against one
    registry snapshot). Returns a sorted list -- determinism is a grading requirement --
    and the stats for the whole pipeline.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; expected one of {STRATEGIES}")
    q = int(kw.get("q", DEFAULT_Q))
    max_edits = int(kw.get("max_edits", DEFAULT_MAX_EDITS))
    delta = int(kw.get("delta", max_edits))
    t0 = time.perf_counter()

    if strategy == "none":
        out = sorted(set(registry))
        return out, BlockingStats(
            queried=read, candidates_before=len(set(registry)), candidates_after=len(out),
            index_lookups=0, elapsed_ms=(time.perf_counter() - t0) * 1000.0,
            filters_applied=["full_scan"])

    # `is None`, not truthiness: an index over an empty registry is falsy (__len__ == 0)
    # and would otherwise be silently rebuilt.
    idx: Optional[QGramIndex] = kw.get("index")
    if idx is None:
        idx = QGramIndex.build(registry, q=q, tokenizer=kw.get("tokenizer"))

    lookups = 0
    filters: List[str] = ["inverted_index"]

    if strategy == "index":
        cur = idx.candidates(read, min_overlap=int(kw.get("min_overlap", 1)))
    else:
        # The size filter probes exactly the same inverted lists as a plain lookup and then
        # discards; running `candidates` first as well would double the index traffic for a
        # set we immediately shrink. The stage is still recorded in `filters_applied`.
        cur = idx.candidates_size_filtered(read, delta)
        filters.append("size")
    lookups += idx.last_stats.index_lookups

    if strategy in ("prefix", "position"):
        k = int(kw.get("k", idx.recommended_k(read, max_edits)))
        if strategy == "prefix":
            cur = idx.candidates_prefix_filtered(read, k, restrict=cur)
            filters.append("prefix")
        else:
            cur = idx.candidates_position_filtered(read, k, restrict=cur)
            filters.extend(["prefix", "position"])
        lookups += idx.last_stats.index_lookups

    stats = BlockingStats(
        queried=read, candidates_before=len(idx), candidates_after=len(cur),
        index_lookups=lookups, elapsed_ms=(time.perf_counter() - t0) * 1000.0,
        filters_applied=filters)
    idx.last_stats = stats
    return sorted(cur), stats


# --------------------------------------------------------------------- pushdown predicate

# LENGTH() counts bytes in MySQL, characters in SQLite and PostgreSQL -- on a plate column
# that is the same number today, but CHAR_LENGTH is the correct one and costs nothing.
_LENGTH_FN: Dict[str, str] = {
    "sqlite": "LENGTH", "sqlite3": "LENGTH",
    "postgres": "LENGTH", "postgresql": "LENGTH", "psql": "LENGTH",
    "mysql": "CHAR_LENGTH", "mariadb": "CHAR_LENGTH",
}
_SAFE_COLUMN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def sql_prefilter(read: str, column: str, dialect: str) -> str:
    """A blocking predicate cheap enough to push into a source's own SQL.

    The federated half of step 1: rather than pull all 605 registrations across the network
    and block locally, send the source a predicate it can answer with tens of rows. It is
    the same two ideas as above, in SQL:

      * the size filter of p. 53 -- plate length within one character of the read;
      * a one-token prefix filter in the spirit of p. 55 -- the first two characters.

    The normalisation expression is copied from `mediator/decomposer.py`, deliberately
    character for character, so the pushed predicate and the mediator's own plate matching
    can never disagree about what a plate *is*.

    This function builds a string. It opens no connection and executes nothing.

    **Recall bound, stated because it is a real one.** The two-character head is *not* safe
    against an OCR confusion in position 0 or 1: a state code read as `0L` instead of `DL`
    will not satisfy the LIKE and the true vehicle will not come back. Positions 0-1 are the
    state code, the most legible part of an Indian plate and the part our camera corpus
    never corrupts, so we accept the bound here and rely on the local q-gram pass -- which
    has no such hole -- whenever the whole registry is already in hand. Callers that need a
    guaranteed-recall pushdown should widen the head or drop the LIKE and keep the length
    window only.
    """
    plate = norm_plate(read) or ""          # A-Z0-9 only, so no quote can reach the SQL
    if not _SAFE_COLUMN.match(column or ""):
        raise ValueError(f"unsafe column reference {column!r}")
    key = (dialect or "").strip().lower()
    if key not in _LENGTH_FN:
        raise ValueError(f"unknown dialect {dialect!r}; expected one of "
                         f"{sorted(set(_LENGTH_FN))}")
    length_fn = _LENGTH_FN[key]
    expr = f"UPPER(REPLACE(REPLACE({column}, '-', ''), ' ', ''))"
    n = len(plate)
    if n < 2:
        # Nothing to block on; a predicate that excludes rows would be worse than none.
        return "1=1"
    lo, hi = max(1, n - 1), n + 1
    return (f"({expr} LIKE '{plate[:2]}%' AND "
            f"{length_fn}({expr}) BETWEEN {lo} AND {hi})")
