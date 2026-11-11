"""CONTEXT-ADDED (R2): how context crosses from one database to another (a bind join over bridge values).

A bridge value found in source s (chassis, plate, person) becomes a *frontier state* for every other source t
that holds the same kind of value: weight = A(row) x c (affinity x bridge confidence), and
bound = weight x seed_factor(t) <= weight x R-bar_t -- nothing that state could return can score higher.
Confidence c: chassis 1.0; plate 1.0 between clean registries, OCR-confusion similarity when either side is a
camera read; person = Fellegi-Sunter posterior (mediator/person_resolution.py).

Person candidates are never fetched wholesale: only the blocking keys (mobile, pincode, 3-letter name-token
prefix) are pushed to each agency as a /query filter, so only candidate records cross the network.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from mediator.context_scoring import bridge_members, canon, noisy_plate_sources, ocr_variants, plate_confidence
from mediator.person_resolution import _digits, name_prefixes, records_from_rows
from sources.context_sql import ident

BLOOM_FP = 0.01


@dataclass
class FrontierState:
    target: str                 # source to ask
    kind: str                   # "plate" | "chassis" | "pk"
    value: str                  # canonical plate / chassis, or "TABLE#pk"
    weight: float               # affinity the seed row will start with = A(origin) x confidence
    bound: float                # weight x R-bar_target
    bridge: str                 # "plate" | "chassis" | "person"
    confidence: float
    origin_path: List[str] = field(default_factory=list)
    origin_hops: int = 0

    @property
    def key(self) -> Tuple[str, str, str]:
        return (self.target, self.kind, self.value)


def spawn_states(graph: Dict[str, Any], origin: str, attr: str, value: Any, affinity: float,
                 factor: Callable[[str, str, Optional[str]], float], dead: Iterable[str] = (), origin_path: Optional[List[str]] = None,
                 origin_hops: int = 0) -> List[FrontierState]:
    """Plate / chassis bridge states from one value seen in `origin` (person goes through resolution)."""
    if attr not in ("plate", "chassis") or value in (None, ""):
        return []
    noisy, dead, out = set(noisy_plate_sources(graph)), set(dead), []
    targets = sorted({m[0] for m in bridge_members(graph).get(attr, []) if m[0] != origin and m[0] not in dead})
    v = canon(value)
    for t in targets:
        if attr == "plate" and (origin in noisy or t in noisy):
            pairs = [(var, plate_confidence(v, var)) for var in ocr_variants(v)]
        else:
            pairs = [(v, 1.0)]
        for var, c in pairs:
            w = affinity * c
            out.append(FrontierState(t, attr, var, w, w * factor(t, attr, None), attr, c,
                                     list(origin_path or []) + [f"{attr}->{t}"], origin_hops))
    return out


def shipping_estimate(values: Sequence[str], fp: float = BLOOM_FP) -> Dict[str, Any]:
    """Semijoin-profitability check (SDD-1 style): value list vs a Bloom filter (Mackert & Lohman Bloomjoin).

    We always ship the list: membership in a Bloom filter needs the same hash functions inside SQLite,
    PostgreSQL and MySQL, which have no portable one. The estimate is logged so the choice is visible.
    """
    k = len(values)
    list_bytes = sum(len(str(v)) + 3 for v in values)
    bits = math.ceil(-k * math.log(fp) / (math.log(2) ** 2)) if k else 0
    bloom_bytes = (bits + 7) // 8 + 16
    return {"values": k, "list_bytes": list_bytes, "bloom_bytes_est": bloom_bytes, "bloom_fp_rate": fp,
            "shipped": "list", "bloom_would_be_smaller": bloom_bytes < list_bytes}


# --------------------------------------------------------------------------- people

def person_sources(graph: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Per source: anchor person table, its pk, and the LEFT JOINs that add contact columns (all from the graph)."""
    out: Dict[str, Dict[str, Any]] = {}
    for sid, table, pk in bridge_members(graph).get("person", []):
        src = graph["sources"].get(sid, {})
        tables = src.get("tables", {})
        fields = {f: f"a.{ident(c)}" for f, c in tables.get(table, {}).get("person", {}).items()}
        joins = []
        for i, e in enumerate(src.get("edges", [])):
            extra = tables.get(e["from"], {})
            if e["to"] == table and e["to_col"] == pk and extra.get("person") and e["from"] != table:
                alias = f"x{i}"
                joins.append(f"LEFT JOIN {ident(e['from'])} {alias} ON {alias}.{ident(e['col'])} = a.{ident(pk)}")
                fields.update({f: f"{alias}.{ident(c)}" for f, c in extra["person"].items() if f not in fields})
        out[sid] = {"table": table, "pk": pk, "fields": fields, "joins": joins}
    return out


def _quote(v: Any) -> Optional[str]:
    s = str(v)
    if any(bad in s for bad in (";", "--", "/*", "#", "\x00")):
        return None  # the /query guard would refuse the whole statement; drop just this key
    return "'" + s.replace("'", "''") + "'"


def _safe_prefix(p: str) -> bool:
    from sources.wrapper_template import QueryRejected, guard_sql
    try:  # a prefix like 'set' or 'int' would trip the textual keyword guard
        guard_sql(f"SELECT a FROM t WHERE n LIKE '{p}%'", ["t"])
        return True
    except QueryRejected:
        return False


def person_query(info: Dict[str, Any], pks: Sequence[Any] = (), mobiles: Sequence[str] = (),
                 pincodes: Sequence[str] = (), prefixes: Sequence[str] = ()) -> Optional[str]:
    f = info["fields"]
    conds = []
    if q := [x for x in map(_quote, pks) if x]:
        conds.append(f"a.{ident(info['pk'])} IN ({', '.join(q)})")
    if "mobile" in f and (q := [x for x in map(_quote, sorted(set(mobiles))) if x]):
        conds.append(f"{f['mobile']} IN ({', '.join(q)})")
    if "pincode" in f and (q := [x for x in map(_quote, sorted(set(pincodes))) if x]):
        conds.append(f"{f['pincode']} IN ({', '.join(q)})")
    if "name" in f:
        for p in sorted(set(prefixes)):
            if p.isalpha() and _safe_prefix(p):
                conds.append(f"LOWER({f['name']}) LIKE '{p}%' OR LOWER({f['name']}) LIKE '% {p}%'")
    if not conds:
        return None
    cols = [f"a.{ident(info['pk'])} AS pk"] + [f"{expr} AS {ident(name)}" for name, expr in f.items()]
    return (f"SELECT {', '.join(cols)} FROM {ident(info['table'])} a {' '.join(info['joins'])} "
            f"WHERE {' OR '.join(conds)}")


def fetch_person_records(client: Any, graph: Dict[str, Any], source_id: str, **keys: Any) -> List[Dict[str, Any]]:
    """Candidate person records from one agency, filtered at the source by the given keys (counted by the client)."""
    info = person_sources(graph).get(source_id)
    sql = person_query(info, **keys) if info else None
    if not sql:
        return []
    rows = client.query(source_id, sql)
    mapping = {f: f for f in info["fields"]}
    return records_from_rows(rows, mapping, source_id, info["table"], "pk")


def blocking_keys(records: Iterable[Dict[str, Any]]) -> Dict[str, List[str]]:
    mobiles, pins, prefixes = set(), set(), set()
    for r in records:
        if d := _digits(r.get("mobile")):
            mobiles.add(d)
        if d := _digits(r.get("pincode")):
            pins.add(d)
        prefixes.update(name_prefixes(r.get("name")))
    return {"mobiles": sorted(mobiles), "pincodes": sorted(pins), "prefixes": sorted(prefixes)}
