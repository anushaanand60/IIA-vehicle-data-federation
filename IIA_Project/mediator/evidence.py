"""CONTEXT-ADDED (R2: analytics): fetch one vehicle's rows across agencies, and read their dates.

`gather(plate, client)` walks each source's FK graph (data/context_graph.json) from the plate table to the tables
the indicators need, one `SELECT * ... WHERE fk IN (...)` per hop (a bind join: the keys found in one table are
the filter for the next), so no table or join is hand-written per source. CAM plates are OCR reads: they are
fetched by the OCR skeleton and kept only when the read is OCR-similar to the plate.

`when(value, source, table, col)` parses a date in the agency's own format: the column's `time.fmt` from the
graph, or, for a date column the graph does not list, the format that agency uses for its declared dates
(each agency writes every date one way: INS DD/MM/YYYY, THEFT epoch seconds, CAM/PUC ISO timestamps).
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from mediator import topk_sql as S
from mediator.similarity import ocr_edit_similarity

Rows = Dict[Tuple[str, str], List[Dict[str, Any]]]
NEEDED: Dict[str, List[str]] = {
    "THEFT": ["CRIME_RECORDS", "STOLEN_PROPERTY", "INCIDENT_PERSONS", "PERSONS"],
    "PUC": ["POLLUTION_CERT", "CERT_TESTS"],
    "CAM": ["PLATE_CAPTURES", "CHALLANS"],
    "INS": ["POLICY_RECORDS", "CLAIMS", "POLICY_PARTIES", "POLICY_HOLDERS"],
    "REG": ["VEHICLE_REGISTRATION", "OWNERSHIP_HISTORY", "OWNERS", "OWNER_CONTACTS"],
}
IN_BATCH = 100
MIN_READ_SIMILARITY = 0.8    # OCR-aware similarity a camera read needs to count as this plate


def tables_meta(source: str) -> Dict[str, Any]:
    return S.graph()["sources"].get(source, {}).get("tables", {})


def pk_of(source: str, table: str, row: Dict[str, Any]) -> Any:
    pk = tables_meta(source).get(table, {}).get("pk")
    if isinstance(pk, list):
        return tuple(row.get(c) for c in pk)
    return row.get(pk) if pk else None


def fmt_of(source: str, table: str, col: str) -> Optional[str]:
    meta = tables_meta(source)
    tm = meta.get(table, {}).get("time")
    if tm:      # the declared column, or a sibling date column the agency writes the same way
        return tm["fmt"]
    return next((m["time"]["fmt"] for m in meta.values() if m.get("time")), None)


def parse_when(value: Any, fmt: Optional[str]) -> Optional[datetime]:
    """-> naive local (IST) datetime; None for empty or unreadable values."""
    if value is None or value == "":
        return None
    order = [fmt] + [f for f in ("iso_ts", "iso_date", "ddmmyyyy", "epoch") if f != fmt]
    for f in order:
        try:
            if f == "epoch":
                return datetime.fromtimestamp(int(float(value)), S.IST).replace(tzinfo=None)
            if f == "ddmmyyyy":
                return datetime.strptime(str(value).strip()[:10], "%d/%m/%Y")
            if f in ("iso_ts", "iso_date"):
                dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
                return dt.astimezone(S.IST).replace(tzinfo=None) if dt.tzinfo else dt
        except (ValueError, TypeError, OverflowError, OSError):
            continue
    return None


def when(value: Any, source: str, table: str, col: str) -> Optional[datetime]:
    return parse_when(value, fmt_of(source, table, col))


def _fetch(client: Any, source: str, table: str, col: str, values: Iterable[Any]) -> List[Dict[str, Any]]:
    vals = sorted({v for v in values if v is not None}, key=str)
    out: List[Dict[str, Any]] = []
    for i in range(0, len(vals), IN_BATCH):
        chunk = ", ".join(S.lit(v) for v in vals[i:i + IN_BATCH])
        out.extend(client.query(source, f"SELECT * FROM {table} WHERE {table}.{col} IN ({chunk}) LIMIT 200") or [])
    return out


Tree = Dict[str, Tuple[str, str, str]]    # table -> (parent table, parent column, this table's column)


def _tree(source: str, start: str) -> Tree:
    """Shortest FK paths from `start` to every table of the source (undirected over the declared edges)."""
    tree: Tree = {start: ("", "", "")}
    todo = deque([start])
    while todo:
        cur = todo.popleft()
        for e in S.graph()["sources"][source]["edges"]:
            for here, there, hc, tc in ((e["from"], e["to"], e["col"], e["to_col"]),
                                        (e["to"], e["from"], e["to_col"], e["col"])):
                if here == cur and there not in tree:
                    tree[there] = (cur, hc, tc)
                    todo.append(there)
    return tree


def _path(tree: Tree, target: str) -> List[str]:
    out = []
    while target in tree and tree[target][0]:
        out.append(target)
        target = tree[target][0]
    return out[::-1]


def _walk(client: Any, source: str, start: str, rows: List[Dict[str, Any]], wanted: List[str]) -> Rows:
    """Fetch every wanted table (and the tables on its shortest FK path) once, keys of one hop filtering the next."""
    tree = _tree(source, start)
    have: Rows = {(source, start): rows}
    for target in wanted:
        for t in _path(tree, target):
            if (source, t) in have:
                continue
            parent, pcol, tcol = tree[t]
            keys = [r.get(pcol) for r in have[(source, parent)]]
            have[(source, t)] = _fetch(client, source, t, tcol, keys) if any(k is not None for k in keys) else []
    return have


def gather(plate: str, client: Any, sources: Optional[List[str]] = None) -> Rows:
    rows: Rows = {}
    for sid in sources or list(NEEDED):
        try:
            start, col = S.plate_table(sid)
        except KeyError:
            continue
        noisy = S.identity_column(sid) is not None
        key = S.skeleton_expr(sid) if noisy else S.plate_expr(sid)
        lit = S.lit(S.fold(plate) if noisy else plate)
        first = client.query(sid, f"SELECT * FROM {start} WHERE {key} = {lit} LIMIT 200") or []
        if noisy:
            first = [r for r in first if read_confidence(r.get(col), plate) >= MIN_READ_SIMILARITY]
        rows.update(_walk(client, sid, start, first, NEEDED[sid]))
    return rows


def read_confidence(read: Any, plate: str) -> float:
    canon = "".join(ch for ch in str(read or "").upper() if ch.isalnum())
    return 1.0 if canon == plate else ocr_edit_similarity(canon, plate)


def from_context(context: Any) -> Rows:
    rows: Rows = {}
    for r in getattr(context, "rows", []) or []:
        rows.setdefault((r.source_id, r.table), []).append(dict(r.row))
    return rows


def _person_table(sid: str) -> Optional[Tuple[str, str]]:
    for t, m in tables_meta(sid).items():
        if m.get("person", {}).get("mobile"):
            return t, m["person"]["mobile"]
    return None


def links_in_rows(rows: Rows, mobiles: Iterable[str]) -> Dict[str, Dict[str, set]]:
    """mobile -> {canonical plate -> agencies}, using only the rows given (fetched or from a context)."""
    wanted = {str(m) for m in mobiles}
    out: Dict[str, Dict[str, set]] = {}
    for sid in S.graph()["sources"]:
        person = _person_table(sid)
        if not person or (sid, person[0]) not in rows:
            continue
        sub = dict(rows)
        sub[(sid, person[0])] = [r for r in rows[(sid, person[0])] if str(r.get(person[1])) in wanted]
        plate_table, pcol = S.plate_table(sid)
        for m, plates in link_mobiles(sid, sub, person[0], person[1], plate_table, pcol).items():
            for p in plates:
                out.setdefault(m, {}).setdefault(p, set()).add(sid)
    return out


def plates_for_mobiles(client: Any, mobiles: List[str]) -> Dict[str, Dict[str, set]]:
    """Walk from each agency's person table (rows with these mobiles) back to its plate table."""
    rows: Rows = {}
    for sid in S.graph()["sources"]:
        person = _person_table(sid)
        if not person or not mobiles:
            continue
        start_rows = _fetch(client, sid, person[0], person[1], mobiles)
        rows.update(_walk(client, sid, person[0], start_rows, [S.plate_table(sid)[0]]) if start_rows else {})
    return links_in_rows(rows, mobiles)


def link_mobiles(sid: str, rows: Rows, person_table: str, mcol: str, plate_table: str, pcol: str) -> Dict[str, set]:
    """Re-trace the walk row by row so each plate is credited to the mobile that reached it."""
    tree = _tree(sid, person_table)
    reach: List[set] = [{str(r.get(mcol))} for r in rows.get((sid, person_table), [])]
    cur = person_table
    for t in _path(tree, plate_table):
        _, pcol_, tcol = tree[t]
        here, there = rows.get((sid, cur), []), rows.get((sid, t), [])
        reach = [set().union(*[reach[i] for i, hr in enumerate(here)
                               if hr.get(pcol_) is not None and hr.get(pcol_) == tr.get(tcol)]) for tr in there]
        cur = t
    out: Dict[str, set] = {}
    for r, mobiles in zip(rows.get((sid, plate_table), []), reach if cur == plate_table else []):
        canon = "".join(ch for ch in str(r.get(pcol) or "").upper() if ch.isalnum())
        for m in mobiles:
            out.setdefault(m, set()).add(canon)
    return out
