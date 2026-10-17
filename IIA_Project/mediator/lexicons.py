"""Lexicons and pattern mining over live registration data (Week 8 deck, slides 16, 19-23).

The deck's pipeline (slide 16) puts a lexicon / alias stage and a pattern generator in front of RDR
inference. Here both are *mined* from the registration authority at the moment of the scan, read
through its wrapper like every other mediator query, never by opening its database:

  * **Lexicons** (slide 20, "dictionary matches"): canonical makes, models and colours, each with
    its support (how many registrations use it). A rare spelling one or two edits from a frequent
    one ("Hyundia" beside "Hyundai") is mined as a variant of it -- "from pattern frequency to a
    decision rule" (slide 23). A small curated alias table covers what frequency cannot discover
    (slide 16, "curated dictionaries").
  * **Plate shapes** (slide 20, "surface + shape": ``H171 -> alpha+digits``): ``DL01AB1234`` has
    the shape ``LLDDLLDDDD``; the shape frequencies over all registrations say which shapes are real.
  * **Sub-patterns** (slides 21-22): each plate is encoded as a symbol stream in the deck's
    notation -- ``S`` a known state code, ``^`` a numeric token, ``+`` an unknown token -- so
    ``DL01AB1234 -> S^+^`` and a misread ``DLO1AB1234 -> +^+^`` stands out by its rarity.

Everything is deterministic: ties break alphabetically, so two scans of the same data mine the
same lexicons.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx

from mediator.contract import SourceSpec
from mediator.similarity import levenshtein

PAGE_SIZE = 200          # the wrapper contract's own row cap (CLAUDE.md §5.1)
MIN_SUPPORT = 3          # a spelling used by fewer registrations than this is a variant candidate
MIN_SHAPE_SUPPORT = 2    # a plate shape seen fewer times than this is not a real shape

# Curated aliases (slide 16): only what frequency mining cannot find. Keyed by normalised token.
CURATED_ALIASES: Dict[str, Dict[str, str]] = {
    "colour": {"gray": "Grey", "gry": "Grey", "slvr": "Silver", "silvr": "Silver", "blk": "Black",
               "wht": "White", "pearlwhite": "White", "metallicsilver": "Silver"},
    "make": {"maruti": "Maruti Suzuki", "msil": "Maruti Suzuki", "hyundia": "Hyundai"},
    "model": {},
}


class SourceUnavailable(RuntimeError):
    """The source could not be read; callers report it, they never guess around it."""


# ------------------------------------------------------------------ live reads through a wrapper

def primary_key(spec: SourceSpec, client: Optional[httpx.Client] = None) -> str:
    """The base table's primary key, as the wrapper's own /schema publishes it."""
    body = _get(spec, "/schema", client)
    tables = body.get("tables") or {}
    tables = tables.values() if isinstance(tables, dict) else tables
    for table in tables:
        if str(table.get("table", "")).lower() == spec.table.lower():
            for col in table.get("columns", []):
                if col.get("pk"):
                    return str(col["name"])
    raise SourceUnavailable(f"{spec.source_id} publishes no primary key for {spec.table}")


def fetch_all(spec: SourceSpec, pk: str, client: Optional[httpx.Client] = None,
              limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Every row of the source's base table with its mapped columns, keyset-paginated by `pk`.

    SQL is built only from the registry entry (table, joins, attribute_map, query_template).
    """
    rows: List[Dict[str, Any]] = []
    cursor: Any = None
    while True:
        sql = _page_sql(spec, pk, cursor)
        page = _post(spec, sql, client).get("rows", [])
        rows.extend(page)
        if len(page) < PAGE_SIZE or (limit is not None and len(rows) >= limit):
            break
        cursor = page[-1][pk]
    return rows[:limit] if limit is not None else rows


def _page_sql(spec: SourceSpec, pk: str, cursor: Any) -> str:
    tables = {m.source_table.lower() for m in spec.attribute_map}
    joins = [j for j in spec.joins if j.table.lower() in tables]
    cols = [f"{spec.table}.{pk}"] + [f"{m.source_table}.{m.source_attr}" for m in spec.attribute_map]
    predicate = f"{spec.table}.{pk} > {cursor!r}" if cursor is not None else "1 = 1"
    holes = {"columns": ", ".join(dict.fromkeys(cols)), "table": spec.table,
             "joins": "".join(f" LEFT JOIN {j.table} ON {j.left} = {j.right}" for j in joins),
             "predicate": predicate, "order_by": f" ORDER BY {spec.table}.{pk}", "limit": "",
             "plate": ""}
    return spec.query_template.format_map(holes) + f" LIMIT {PAGE_SIZE}"


def _get(spec: SourceSpec, path: str, client: Optional[httpx.Client]) -> Dict[str, Any]:
    try:
        c = client or httpx.Client(trust_env=False)
        response = c.get(spec.base_url.rstrip("/") + path, timeout=spec.timeout_ms / 1000)
    except httpx.HTTPError as exc:
        raise SourceUnavailable(f"{spec.source_id} unreachable: {exc}") from None
    if response.status_code != 200:
        raise SourceUnavailable(f"{spec.source_id} {path} returned HTTP {response.status_code}")
    return response.json()


def _post(spec: SourceSpec, sql: str, client: Optional[httpx.Client]) -> Dict[str, Any]:
    try:
        c = client or httpx.Client(trust_env=False)
        response = c.post(spec.base_url.rstrip("/") + "/query", json={"sql": sql},
                          timeout=spec.timeout_ms / 1000)
    except httpx.HTTPError as exc:
        raise SourceUnavailable(f"{spec.source_id} unreachable: {exc}") from None
    if response.status_code != 200:
        raise SourceUnavailable(f"{spec.source_id} rejected the scan query: HTTP "
                                f"{response.status_code} {response.text[:200]}")
    return response.json()


# ------------------------------------------------------------------ features (slide 20)

def norm_key(token: Any) -> str:
    """Case, spacing and punctuation folded away: ``MARUTI-SUZUKI`` and ``Maruti Suzuki`` meet."""
    return re.sub(r"[^a-z0-9]", "", str(token or "").lower())


def shape(text: Any) -> str:
    """Surface shape: letters to L, digits to D (slide 20: ``H171 -> alpha+digits``)."""
    return "".join("L" if ch.isalpha() else "D" if ch.isdigit() else "?" for ch in str(text or ""))


def tokens(plate: str) -> List[str]:
    """Maximal runs of one character class: ``DL01AB1234 -> DL 01 AB 1234``."""
    return re.findall(r"[A-Za-z]+|[0-9]+", plate or "")


def pattern(plate: str, state_codes: Iterable[str]) -> str:
    """Slide 21's notation: S known state code, ^ numeric token, + unknown token."""
    states = set(state_codes)
    out = []
    for i, tok in enumerate(tokens(plate)):
        if tok.isdigit():
            out.append("^")
        elif i == 0 and tok.upper() in states:
            out.append("S")
        else:
            out.append("+")
    return "".join(out)


def _is_subsequence(short: str, long: str) -> bool:
    it = iter(long)
    return all(ch in it for ch in short)


# ------------------------------------------------------------------ the lexicon

@dataclass
class Lexicon:
    """Canonical values of one attribute plus every variant that maps onto them."""
    attr: str
    canonical: Dict[str, str] = field(default_factory=dict)       # key -> canonical surface
    support: Dict[str, int] = field(default_factory=dict)         # canonical surface -> count
    variants: Dict[str, Tuple[str, str]] = field(default_factory=dict)  # key -> (canonical, kind)

    def words(self) -> List[str]:
        return sorted(self.canonical.values())

    def lookup(self, raw: Any) -> Tuple[Optional[str], Optional[str]]:
        """(canonical, kind). kind is exact, surface, mined, alias, prefix, abbreviation, edit or None."""
        if raw is None or not str(raw).strip():
            return None, None
        text, key = str(raw).strip(), norm_key(raw)
        if key in self.canonical:
            canon = self.canonical[key]
            return canon, "exact" if text == canon else "surface"
        if key in self.variants:
            return self.variants[key]
        if len(key) >= 3:
            starts = sorted({c for k, c in self.canonical.items() if k.startswith(key)})
            if len(starts) == 1:
                return starts[0], "prefix"
            subs = sorted({c for k, c in self.canonical.items() if k[0] == key[0] and _is_subsequence(key, k)})
            if len(subs) == 1:
                return subs[0], "abbreviation"
        best = sorted((levenshtein(key, k), c) for k, c in self.canonical.items())
        allowed = 1 if len(key) <= 5 else 2
        if best and best[0][0] <= allowed and (len(best) == 1 or best[1][0] > best[0][0]):
            return best[0][1], "edit"
        return None, None


def mine_lexicon(attr: str, values: Iterable[Any]) -> Lexicon:
    """Frequent spellings become canonical; rare ones near a frequent one become its variants."""
    counts = Counter(str(v).strip() for v in values if v is not None and str(v).strip())
    by_key: Dict[str, Counter] = {}
    for surface, n in counts.items():
        by_key.setdefault(norm_key(surface), Counter())[surface] += n
    lex = Lexicon(attr)
    rare: List[Tuple[str, str, int]] = []
    for key in sorted(by_key):
        surfaces = by_key[key]
        top = sorted(surfaces.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        total = sum(surfaces.values())
        if total >= MIN_SUPPORT:
            lex.canonical[key], lex.support[top] = top, total
        else:
            rare.append((key, top, total))
    for key, top, total in rare:
        near = sorted((levenshtein(key, k), c) for k, c in lex.canonical.items())
        if near and near[0][0] <= 2:
            lex.variants[key] = (near[0][1], "mined")
        else:  # rare but close to nothing: we cannot call it wrong, so it stays a word
            lex.canonical[key], lex.support[top] = top, total
    for alias, canon in CURATED_ALIASES.get(attr, {}).items():
        if norm_key(canon) in lex.canonical and alias not in lex.canonical:
            lex.variants.setdefault(alias, (lex.canonical[norm_key(canon)], "alias"))
    return lex


@dataclass
class Lexicons:
    make: Lexicon
    model: Lexicon
    colour: Lexicon
    model_make: Dict[str, str]          # model key -> the make that registers it most
    shapes: Counter                     # plate shape -> count
    patterns: Counter                   # slide-21 symbol stream -> count
    state_codes: Counter                # leading letter token -> count
    plates: int

    def for_attr(self, attr: str) -> Lexicon:
        return {"make": self.make, "model": self.model, "colour": self.colour}[attr]

    def as_rdr(self) -> Dict[str, List[str]]:
        """Named word lists for the RDR condition language (``op: in_lexicon``)."""
        return {"makes": self.make.words(), "models": self.model.words(),
                "colours": self.colour.words(), "state_codes": sorted(self.state_codes)}

    def plate_features(self, plate: str) -> Dict[str, Any]:
        s, p = shape(plate), pattern(plate, self.state_codes)
        return {"shape": s, "shape_support": self.shapes.get(s, 0),
                "shape_valid": self.shapes.get(s, 0) >= MIN_SHAPE_SUPPORT,
                "pattern": p, "pattern_support": self.patterns.get(p, 0)}


def build(registrations: List[Dict[str, Any]]) -> Lexicons:
    """Mine all lexicons from registration rows already expressed in global attributes (raw values)."""
    plates = [norm_key(r.get("plate_number")).upper() for r in registrations if r.get("plate_number")]
    make = mine_lexicon("make", (r.get("vehicle_make") for r in registrations))
    model = mine_lexicon("model", (r.get("vehicle_model") for r in registrations))
    colour = mine_lexicon("colour", (r.get("vehicle_colour") for r in registrations))
    pairs: Dict[str, Counter] = {}
    for r in registrations:
        mk, _ = make.lookup(r.get("vehicle_make"))
        if mk and r.get("vehicle_model"):
            pairs.setdefault(norm_key(r["vehicle_model"]), Counter())[mk] += 1
    model_make = {k: sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] for k, c in pairs.items()}
    states = Counter(t[0].upper() for t in (tokens(p) for p in plates) if t and t[0].isalpha())
    return Lexicons(make, model, colour, model_make, Counter(shape(p) for p in plates),
                    Counter(pattern(p, states) for p in plates), states, len(plates))
