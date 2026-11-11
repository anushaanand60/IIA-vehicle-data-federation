"""CONTEXT-ADDED (R2: entity top-k): predicates -> per-source scoring SQL, built from metadata.

Each source scores *vehicles* on the predicates it can answer, inside its own DBMS:

    SELECT <plate expr> AS plate, MAX(CASE WHEN <cond_i> THEN 1 ELSE 0 END) AS f0, ..., (<sum w_i f_i>) AS score
    FROM <plate table> LEFT JOIN ... GROUP BY <plate expr> HAVING <score> > 0 ORDER BY score DESC, plate LIMIT d OFFSET o

Where the names come from:
  * plate table / plate column / time columns / FK join paths: data/context_graph.json;
  * the column behind a global attribute (vehicle_make -> REG.VEHICLE_REGISTRATION.make): the mapping registry
    (mediator/catalog.py), with a frozen copy as fallback so a fresh clone still works;
  * how a *status* is encoded (stolen = stolen_flag 'Y' and recovered_flag 'N'): ENCODED below. The registry maps
    source -> global but its transforms are not invertible, so this one small table states the inverse explicitly;
  * place columns (zones, stations, test-centre cities): PLACE_COLUMNS below, because those tables are new and
    have no global attribute yet.
Only portable SQL is emitted (CASE, MAX, LOWER, UPPER, REPLACE, LIKE, IN): it runs unchanged on PostgreSQL,
MySQL 8 and SQLite and passes the wrapper guard (one SELECT, no ';', no comments, whitelisted tables).
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from mediator.context_types import ParsedQuery
from mediator.query_vocab import MAKES, place_aliases
from mediator.transforms import MAKE_CORRECTIONS, status_map

GRAPH_PATH = Path(__file__).resolve().parent.parent / "data" / "context_graph.json"
IST = timezone(timedelta(hours=5, minutes=30))   # epochs in THEFT were written from IST midnights
EVENT_FMTS = ("iso_ts", "epoch")                 # timestamps mark events; plain dates are standing record dates
OCR_FOLD = (("O", "0"), ("I", "1"), ("B", "8"), ("S", "5"), ("Z", "2"), ("G", "6"))  # confusable -> digit

FALLBACK_REGISTRY = [  # frozen copy of MAPPING_REGISTRY for the attributes top-k uses
    ("REG", "VEHICLE_REGISTRATION", "make", "vehicle_make"), ("REG", "VEHICLE_REGISTRATION", "model", "vehicle_model"),
    ("REG", "VEHICLE_REGISTRATION", "colour", "vehicle_colour"), ("REG", "OWNERS", "full_name", "owner_name"),
    ("REG", "VEHICLE_REGISTRATION", "reg_status", "registration_status"), ("INS", "POLICY_RECORDS", "is_active", "is_active"),
    ("THEFT", "CRIME_RECORDS", "stolen_flag", "stolen_flag"), ("THEFT", "CRIME_RECORDS", "recovered_flag", "recovered_flag"),
    ("THEFT", "CRIME_RECORDS", "case_status", "case_status"), ("CAM", "CAMERAS", "location_name", "last_seen_location"),
    ("CAM", "PLATE_CAPTURES", "ocr_confidence", "ocr_confidence"),
]
# global status value -> [(registry global attr of the column, op, source literal)]; all conditions must hold
ENCODED: Dict[Tuple[str, Any], List[Tuple[str, str, Any]]] = {
    ("stolen_status", True): [("stolen_flag", "=", "Y"), ("recovered_flag", "=", "N")],
    ("recovered_status", True): [("recovered_flag", "=", "Y")],
    ("insurance_status", "ACTIVE"): [("is_active", "=", 1)],
    ("insurance_status", "EXPIRED"): [("is_active", "!", 1)],     # "!": no policy of this vehicle is active
}
PLACE_COLUMNS: Dict[str, List[Tuple[str, str]]] = {
    "CAM": [("ZONES", "city"), ("ZONES", "zone_name"), ("CAMERAS", "location_name")],
    "THEFT": [("STATIONS", "district"), ("STATIONS", "zone"), ("CRIME_RECORDS", "police_station")],
    "REG": [("OWNERS", "city")],
    "PUC": [("TEST_CENTRES", "city"), ("POLLUTION_CERT", "tested_at")],
}
# indicator -> the part each agency can check alone; the cross-source timing is verified on the k winners
INDICATOR_PRECONDITION: Dict[str, List[Tuple[str, str, str, Any]]] = {
    "ALIBI_PUC": [("PUC", "CERT_TESTS", "test_id", None)],
    "ALIBI_CAMERA": [("CAM", "PLATE_CAPTURES", "capture_id", None)],
    "ALIBI_CHALLAN": [("CAM", "CHALLANS", "challan_id", None)],
    "QUICK_CLAIM": [("INS", "CLAIMS", "loss_type", "THEFT")],
    "TRANSFER_BEFORE_LOSS": [("REG", "OWNERSHIP_HISTORY", "transfer_id", None)],
    "LAPSED_COVER_SEEN": [("CAM", "PLATE_CAPTURES", "capture_id", None)],
    "PERSON_RING": [("THEFT", "INCIDENT_PERSONS", "role", "ACCUSED")],
}


@dataclass(frozen=True)
class Feature:
    source: str
    tables: Tuple[str, ...]   # tables whose columns the condition reads (joined to the plate table via FK edges)
    cond: str           # portable SQL boolean over table-qualified columns
    weight: float
    label: str          # for explanations
    negate: bool = False


@lru_cache(maxsize=1)
def graph() -> Dict[str, Any]:
    return json.loads(GRAPH_PATH.read_text(encoding="utf-8"))


def registry_rows() -> List[Tuple[str, str, str, str]]:
    try:
        from mediator.catalog import get_all_mappings
        rows = [(m["source_id"], m["source_table"], m["source_attr"], m["global_attr"]) for m in get_all_mappings()]
        return rows or FALLBACK_REGISTRY
    except Exception:  # catalog unreadable: the frozen copy keeps top-k working
        return FALLBACK_REGISTRY


def columns_for(global_attr: str) -> List[Tuple[str, str, str]]:
    return [(s, t, c) for s, t, c, g in registry_rows() if g == global_attr]


def lit(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def plate_table(source: str) -> Tuple[str, str]:
    for name, meta in graph()["sources"][source]["tables"].items():
        if meta.get("plate"):
            return name, meta["plate"]
    raise KeyError(f"{source} has no plate column in context_graph.json")


def plate_expr(source: str) -> str:
    table, col = plate_table(source)
    return f"UPPER(REPLACE(REPLACE({table}.{col}, '-', ''), ' ', ''))"


def fold(text: str) -> str:
    """OCR skeleton: every confusable letter folded to its digit, so DLO1AB1Z34 and DL01AB1234 collide."""
    out = text.upper()
    for a, b in OCR_FOLD:
        out = out.replace(a, b)
    return out


def skeleton_expr(source: str) -> str:
    expr = plate_expr(source)
    for a, b in OCR_FOLD:
        expr = f"REPLACE({expr}, '{a}', '{b}')"
    return expr


def identity_column(source: str) -> Optional[str]:
    """A per-row read confidence (CAM's ocr_confidence) marks a noisy-identity source."""
    hits = [f"{t}.{c}" for s, t, c in columns_for("ocr_confidence") if s == source]
    return hits[0] if hits else None


def join_path(source: str, target: str) -> List[str]:
    """LEFT JOIN clauses from the plate table to `target`, shortest path over the declared FK edges."""
    start, _ = plate_table(source)
    if target == start:
        return []
    adj: Dict[str, List[Tuple[str, str]]] = {}
    for e in graph()["sources"][source]["edges"]:
        on = f"{e['from']}.{e['col']} = {e['to']}.{e['to_col']}"
        adj.setdefault(e["from"], []).append((e["to"], on))
        adj.setdefault(e["to"], []).append((e["from"], on))
    prev: Dict[str, Tuple[str, str]] = {start: ("", "")}
    todo = deque([start])
    while todo:
        cur = todo.popleft()
        for nxt, on in adj.get(cur, []):
            if nxt not in prev:
                prev[nxt] = (cur, on)
                todo.append(nxt)
    if target not in prev:
        raise KeyError(f"{target} is not reachable from {start} in {source}")
    clauses, node = [], target
    while node != start:
        parent, on = prev[node]
        clauses.append(f"LEFT JOIN {node} ON {on}")
        node = parent
    return clauses[::-1]


def event_time(source: str, table: str) -> Optional[Tuple[str, str]]:
    """(qualified column, fmt) of the event timestamp a time window constrains for a feature on `table`."""
    tables = graph()["sources"][source]["tables"]
    for t in (table, plate_table(source)[0]):
        tm = tables.get(t, {}).get("time")
        if tm and tm["fmt"] in EVENT_FMTS:
            return f"{t}.{tm['col']}", tm["fmt"]
    return None


def window_cond(source: str, table: str, window: Optional[Tuple[str, str]]) -> str:
    ev = event_time(source, table) if window else None
    if not ev or not window:
        return ""
    col, fmt = ev
    if fmt == "epoch":
        lo = int(datetime.fromisoformat(window[0]).replace(tzinfo=IST).timestamp())
        hi = int((datetime.fromisoformat(window[1]) + timedelta(days=1)).replace(tzinfo=IST).timestamp()) - 1
        return f" AND {col} >= {lo} AND {col} <= {hi}"
    return f" AND {col} >= {lit(window[0])} AND {col} <= {lit(window[1] + 'T23:59:59')}"


def _value_cond(attr: str, value: Any, op: str) -> List[Tuple[str, str, str]]:
    """(source, table, condition) for one value predicate, via the registry."""
    out = []
    for s, t, c in columns_for(attr):
        col = f"{t}.{c}"
        if attr == "vehicle_make":
            spellings = sorted({k for k, v in MAKE_CORRECTIONS.items() if v == value} |
                               {k for k, v in MAKES.items() if v == value} | {str(value).lower()})
            out.append((s, t, f"LOWER({col}) IN ({', '.join(lit(x) for x in spellings)})"))
        elif attr == "registration_status" or attr == "case_status":
            codes = sorted({str(value).upper()} | {k for k in ("ACT", "SUSP", "CANC", "OPEN", "CLOSED")
                                                   if status_map(k) == str(value).upper()})
            out.append((s, t, f"UPPER({col}) IN ({', '.join(lit(x) for x in codes)})"))
        elif op == "~":
            words = [w for w in str(value).lower().split() if w.isalpha()]
            out.append((s, t, " AND ".join(f"LOWER({col}) LIKE {lit('%' + w + '%')}" for w in words) or "1 = 0"))
        else:
            out.append((s, t, f"LOWER({col}) = {lit(str(value).lower())}"))
    return out


def features_for(query: ParsedQuery) -> Dict[str, List[Feature]]:
    """Every predicate becomes one feature per source able to judge it."""
    feats: Dict[str, List[Feature]] = {}

    def add(source: str, tables: Tuple[str, ...], cond: str, w: float, label: str, negate: bool = False) -> None:
        cond = f"({cond}){window_cond(source, tables[0], query.time_window)}"
        feats.setdefault(source, []).append(Feature(source, tables, cond, round(w, 6), label, negate))

    for p in query.predicates:
        label = f"{p.attr} {p.op} {p.value}"
        if (p.attr, p.value) in ENCODED:
            by_src: Dict[Tuple[str, str], List[str]] = {}
            negate = False
            for gattr, op, v in ENCODED[(p.attr, p.value)]:
                for s, t, c in columns_for(gattr):
                    by_src.setdefault((s, t), []).append(f"{t}.{c} = {lit(v)}")
                    negate = negate or op == "!"
            for (s, t), conds in by_src.items():
                add(s, (t,), " AND ".join(conds), p.confidence, label, negate)
        elif p.attr in ("location", "last_seen_location"):
            # one predicate, several agencies able to attest it: each attestation is worth an equal share
            sources = ["CAM"] if p.attr == "last_seen_location" else list(PLACE_COLUMNS)
            for s in sources:
                cols = PLACE_COLUMNS[s]
                cond = " OR ".join(f"LOWER({t}.{c}) LIKE {lit('%' + a + '%')}"
                                   for t, c in cols for a in place_aliases(str(p.value)))
                add(s, tuple(dict.fromkeys(t for t, _ in cols)), cond, p.confidence / len(sources), label)
        elif p.attr == "indicator":
            for s, t, c, v in INDICATOR_PRECONDITION.get(str(p.value), []):
                cond = f"{t}.{c} IS NOT NULL" if v is None else f"{t}.{c} = {lit(v)}"
                add(s, (t,), cond, p.confidence, f"precondition of {p.value}")
        elif p.op == "~" and len(str(p.value).split()) > 1:
            # a name is matched word by word, so "Rahul Sharma" still ranks the other Sharmas, just lower
            words = str(p.value).split()
            for w in words:
                for s, t, cond in _value_cond(p.attr, w, p.op):
                    add(s, (t,), cond, p.confidence / len(words), f"{p.attr} ~ {w}")
        else:
            for s, t, cond in _value_cond(p.attr, p.value, p.op):
                add(s, (t,), cond, p.confidence, label)
    return feats


def _from_clause(source: str, feats: Sequence[Feature]) -> str:
    base, _ = plate_table(source)
    joins: List[str] = []
    for f in feats:
        for t in f.tables:
            for j in join_path(source, t):
                if j not in joins:
                    joins.append(j)
    return " ".join([f"FROM {base}"] + joins)


def _flag(f: Feature) -> str:
    inner = f"MAX(CASE WHEN {f.cond} THEN 1 ELSE 0 END)"
    return f"(1 - {inner})" if f.negate else inner


def score_sql(source: str, feats: Sequence[Feature], *, limit: Optional[int] = None, offset: int = 0,
              plates: Optional[Sequence[str]] = None, min_score: Optional[float] = None) -> str:
    """Sorted access (limit/offset), range access (min_score) or random access (plates) for one source."""
    pe = plate_expr(source)
    score = " + ".join(f"{round(f.weight, 6)} * {_flag(f)}" for f in feats) or "0"
    cols = [f"{pe} AS plate"] + [f"{_flag(f)} AS f{i}" for i, f in enumerate(feats)]
    idc = identity_column(source)
    if idc:
        cols.append(f"MAX({idc}) AS idc")
    sql = f"SELECT {', '.join(cols)}, ({score}) AS score {_from_clause(source, feats)}"
    if plates is not None:
        key, vals = (skeleton_expr(source), [fold(p) for p in plates]) if idc else (pe, list(plates))
        sql += f" WHERE {key} IN ({', '.join(lit(v) for v in sorted(set(vals)))}) GROUP BY {pe}"
        return sql + " ORDER BY plate LIMIT 200"
    sql += f" GROUP BY {pe} HAVING ({score}) {'>= ' + repr(round(min_score, 6)) if min_score else '> 0'}"
    return sql + f" ORDER BY score DESC, plate LIMIT {limit or 200} OFFSET {offset}"


def count_sql(source: str, feats: Sequence[Feature]) -> str:
    """Rows a naive mediator would ship from this source: every joined row matching any predicate."""
    conds = " OR ".join(f"NOT {f.cond}" if f.negate else f.cond for f in feats) or "1 = 0"
    return f"SELECT COUNT(*) AS n {_from_clause(source, feats)} WHERE {conds}"
