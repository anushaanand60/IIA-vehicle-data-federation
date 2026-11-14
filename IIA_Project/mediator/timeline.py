"""CONTEXT-ADDED (R2/R3: cross-agency timeline with forks) - one vehicle's life, as no single agency can see it.

Five databases write dates four ways (ISO date, DD/MM/YYYY, epoch seconds, ISO timestamp) and documents add
free-text dates ("22nd September 2026"). build_timeline() puts all of them on one clock:
  * row events   - every context row's time column, named with its format in data/context_graph.json, read by
                   evidence.parse_when (the reader the ALIBI indicators use, so the two can never disagree);
  * text events  - dates in a linked document, each classified by its nearest cue word (doc_conflicts.date_class);
  * forks        - a text date of class X that differs from the linked row's X date by more than the cue's
                   tolerance ("text says / database says"), plus "text says recovered" against an open FIR;
  * intervals    - stolen intervals (analytics.stolen_intervals) and policy cover (start -> end);
  * inside_stolen - a presence event (PUC test, camera sighting, challan) inside a stolen interval: the ALIBI.
Pure: no network, deterministic order. Documents that link to none of this vehicle's rows are left out, because
a date in them may describe another car.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from mediator import analytics as A
from mediator import evidence as D
from mediator.doc_conflicts import CUES, date_class, text_dates, to_date
from mediator.doc_retrieval import EXTRA_ANCHORS, column_kind, load_graph

# (source, table) -> (noun for sentences, label of the graph's time column, is it physical presence?).
# Presentation words only; the presence tables are exactly the ones analytics' ALIBI checks read.
LABELS: Dict[Tuple[str, str], Tuple[str, str, bool]] = {
    ("REG", "VEHICLE_REGISTRATION"): ("registration", "Registered with the RTO", False),
    ("REG", "OWNERSHIP_HISTORY"): ("ownership transfer", "Ownership transfer", False),
    ("INS", "POLICY_RECORDS"): ("policy end", "Insurance policy {ref} ends", False),
    ("INS", "CLAIMS"): ("insurance claim", "Claim {ref}: date of loss", False),
    ("THEFT", "CRIME_RECORDS"): ("police FIR", "Police FIR {ref} filed", False),
    ("THEFT", "STOLEN_PROPERTY"): ("recovery", "Vehicle recovered", False),
    ("CAM", "PLATE_CAPTURES"): ("camera sighting", "Camera sighting{at}", True),
    ("CAM", "CHALLANS"): ("traffic challan", "Traffic challan {ref}{at}", True),
    ("PUC", "POLLUTION_CERT"): ("pollution certificate expiry", "Pollution certificate {ref} expires", False),
    ("PUC", "CERT_TESTS"): ("PUC test", "PUC test{at}", True),
}
# A second meaningful date per table. The graph's `time` key names ONE column per table (the one context discovery
# ranks and filters by), so these are not in it; proposed as a `time_extra` key in context_graph.json.
# (column, noun, label, interval kind it opens towards the main date or None); format = the agency's declared one.
SECOND: Dict[Tuple[str, str], Tuple[str, str, str, Optional[str]]] = {
    ("INS", "POLICY_RECORDS"): ("policy_start", "policy start", "Insurance policy {ref} starts", "cover"),
    ("INS", "CLAIMS"): ("intimation_date", "claim intimation", "Claim {ref}: insurer informed", None),
}
CLASS_WORDS = {"recovered_on": "recovery", "loss_date": "date of loss", "policy_until": "end of cover",
               "incident_date": "theft / report date"}


@dataclass
class Event:
    eid: str
    when: datetime
    source_id: str                 # "DOCS" for text events
    table: str
    pk: Any
    label: str
    kind: str = "row"              # "row" | "text"
    noun: str = ""
    doc_id: Optional[str] = None
    attr: Optional[str] = None     # text events: the date class from doc_conflicts; rows: the column read
    inside_stolen: bool = False
    future: bool = False
    detail: str = ""               # text events: the document's title


@dataclass
class Fork:                        # "text says X / database says Y"
    text_eid: str
    row_eid: str
    doc_id: str
    attr: str
    text_value: str
    db_value: str
    delta_days: Optional[int]      # None when the database has no such date at all (recovery vs open FIR)
    note: str


@dataclass
class Interval:
    kind: str                      # "stolen" | "cover"
    source_id: str
    start: datetime
    end: datetime
    label: str
    open_ended: bool = False       # stolen and not recovered: the band runs to "now"


@dataclass
class Timeline:
    events: List[Event] = field(default_factory=list)
    forks: List[Fork] = field(default_factory=list)
    intervals: List[Interval] = field(default_factory=list)
    counts: Dict[str, int] = field(default_factory=dict)
    now: Optional[datetime] = None


def _time(graph: Mapping[str, Any], sid: str, table: str) -> Dict[str, Any]:
    return graph.get("sources", {}).get(sid, {}).get("tables", {}).get(table, {}).get("time") or {}


def day(t: Any) -> str:
    return f"{t.day} {t:%b %Y}" if t else "?"


def _ref(sid: str, table: str, row: Mapping[str, Any], pk: Any) -> str:
    for (s, t, col), (_, pattern) in EXTRA_ANCHORS.items():
        if (s, t) == (sid, table) and row.get(col) is not None:
            try:
                return pattern.format(int(row[col])) if "d}" in pattern else pattern.format(row[col])
            except (TypeError, ValueError):
                return str(row[col])
    return str(pk)


def _place(sid: str, table: str, row: Mapping[str, Any], index: Mapping[Tuple[str, str], List[Any]],
           graph: Mapping[str, Any], depth: int = 2) -> str:
    """A place name reached along the source's declared FK edges (capture -> camera -> location), up to 2 hops."""
    spec = graph.get("sources", {}).get(sid, {})
    for e in spec.get("edges", []):
        if e.get("from") != table or row.get(e.get("col")) is None:
            continue
        for r in index.get((sid, e["to"]), []):
            if r.row.get(e["to_col"]) == row.get(e["col"]):
                text_cols = spec.get("tables", {}).get(e["to"], {}).get("text", [])
                for c in text_cols:
                    if column_kind(c, spec["tables"][e["to"]]) == "place" and r.row.get(c):
                        return str(r.row[c])
                if depth > 1 and (found := _place(sid, e["to"], r.row, index, graph, depth - 1)):
                    return found
    return ""


def _row_events(rows: Sequence[Any], graph: Mapping[str, Any], now: datetime) -> List[Event]:
    index: Dict[Tuple[str, str], List[Any]] = {}
    for r in rows:
        index.setdefault((r.source_id, r.table), []).append(r)
    out: List[Event] = []
    for r in rows:
        time = _time(graph, r.source_id, r.table)
        if not time:
            continue
        noun, label, _ = LABELS.get((r.source_id, r.table), (r.table.replace("_", " ").lower(), r.table.title(), False))
        ref = _ref(r.source_id, r.table, r.row, r.pk)
        place = _place(r.source_id, r.table, r.row, index, graph) if "{at}" in label else ""
        reads = [(time["col"], noun, label)]
        if (r.source_id, r.table) in SECOND:
            reads.append(SECOND[(r.source_id, r.table)][:3])
        for col, nn, lab in reads:
            when = D.parse_when(r.row.get(col), time.get("fmt"))   # one agency writes all its dates one way
            if when is None:
                continue
            out.append(Event(f"{r.source_id}:{r.table}#{r.pk}:{col}", when, r.source_id, r.table, r.pk,
                             lab.format(ref=ref, at=f" at {place}" if place else ""), "row", nn, attr=col,
                             future=when > now))
    return out


def _quote(body: str, s: int, e: int) -> str:
    """The clause leading up to the date, cut at a sentence or a word boundary (never mid-word)."""
    stop = body.rfind(". ", 0, s)
    start = max(s - 60, stop + 2 if stop >= 0 else 0)
    if start > (stop + 2 if stop >= 0 else 0) and " " in body[start:s]:
        start = body.index(" ", start) + 1
    return ("…" if start > 0 else "") + " ".join(body[start:e].strip(". ").split())


def _text_events(docs: Sequence[Any], rows: Sequence[Any], graph: Mapping[str, Any],
                 intervals: Sequence[Tuple[Interval, Any]], now: datetime) -> Tuple[List[Event], List[Fork]]:
    by_key = {(r.source_id, r.table, str(r.pk)): r for r in rows}
    targets = {cls: (tgt, tol) for _, cls, tgt, tol in CUES}
    events: Dict[str, Event] = {}
    forks: Dict[Tuple[str, str, str], Fork] = {}
    for d in docs:
        linked = [tuple(map(str, k)) for k in (getattr(d, "linked_rows", None) or [])]
        if not getattr(d, "relevant", True) or not linked:
            continue
        body = getattr(d, "body", "") or getattr(d, "snippet", "") or ""
        found = [(t, date_class(body, s, e), _quote(body, s, e)) for t, s, e in text_dates(body)]
        for c in getattr(d, "conflicts", None) or []:   # detect() saw the full body at retrieval time
            if c.attr == "recovery":
                found += [(t, ("recovered_on",) + targets["recovered_on"], str(c.text_value))
                          for t, _, _ in text_dates(str(c.text_value))]
            elif c.attr in targets and (t := to_date(c.text_value, "iso_date")):
                found.append((t, (c.attr,) + targets[c.attr], f"{CLASS_WORDS.get(c.attr, c.attr)} {day(t)}"))
        for when, cls, quote in found:
            attr = cls[0] if cls else "mention"
            eid = f"DOCS:{d.doc_id}:{when.isoformat()}:{attr}"
            events.setdefault(eid, Event(eid, datetime.combine(when, datetime.min.time()), "DOCS", "", d.doc_id,
                                          f"“{quote}”", "text", "document date", d.doc_id, attr,
                                              future=datetime.combine(when, datetime.min.time()) > now,
                                              detail=str(getattr(d, "title", "") or "")))
            if not cls:
                continue
            _, (sid, table), tol = cls
            time = _time(graph, sid, table)
            for key in [k for k in linked if k[:2] == (sid, table)]:
                row = by_key.get(key)
                db = to_date(row.row.get(time.get("col")), time.get("fmt", "iso_date")) if row and time else None
                if db and abs((db - when).days) > tol:
                    forks.setdefault((d.doc_id, attr, when.isoformat()), Fork(
                        eid, f"{sid}:{table}#{row.pk}:{time['col']}", d.doc_id, attr, day(when), day(db),
                        (when - db).days, f"Document {d.doc_id} gives the {CLASS_WORDS.get(attr, attr)} as {day(when)}; "
                                          f"the database says {day(db)}."))
            if attr == "recovered_on":   # text says recovered, the police record has no recovery at all
                for iv, fir in intervals:
                    if iv.open_ended and fir is not None and (fir.source_id, fir.table, str(fir.pk)) in linked:
                        tcol = _time(graph, fir.source_id, fir.table).get("col")
                        forks.setdefault((d.doc_id, attr, when.isoformat()), Fork(
                            eid, f"{fir.source_id}:{fir.table}#{fir.pk}:{tcol}", d.doc_id, attr, f"recovered {day(when)}",
                            "no recovery recorded; the FIR is still open", None,
                            f"Document {d.doc_id} says the car was recovered on {day(when)}, but the police record "
                            f"shows no recovery: the FIR is still open."))
    return list(events.values()), list(forks.values())


def _intervals(rows: Sequence[Any], events: Sequence[Event], now: datetime) -> List[Tuple[Interval, Any]]:
    out: List[Tuple[Interval, Any]] = []
    for start, end, fir, how in A.stolen_intervals(D.from_context(SimpleNamespace(rows=rows)), now):
        src = next((r for r in rows if r.row == fir), None)
        out.append((Interval("stolen", src.source_id if src else "THEFT", start, end,
                             f"reported stolen {day(start)}, {how}", how == "not recovered"), src))
    by_row: Dict[Tuple[str, str, str], Dict[str, datetime]] = {}
    for ev in events:
        by_row.setdefault((ev.source_id, ev.table, str(ev.pk)), {})[str(ev.attr)] = ev.when
    for (sid, table), (col, _, _, kind) in SECOND.items():
        for (s, t, pk), cols in by_row.items():
            main = next((w for c, w in cols.items() if c != col), None)
            if kind and (s, t) == (sid, table) and col in cols and main and main >= cols[col]:
                out.append((Interval(kind, sid, cols[col], main, f"covered {day(cols[col])} to {day(main)}"), None))
    return out


def build_timeline(context: Any, docs: Any = None, now: Optional[datetime] = None,
                   graph: Optional[Mapping[str, Any]] = None) -> Timeline:
    now, graph = now or datetime.now(), graph or load_graph()
    rows = list(getattr(context, "rows", None) or [])
    events = _row_events(rows, graph, now)
    ivs = _intervals(rows, events, now)
    text, forks = _text_events(list(getattr(docs, "docs", None) or []), rows, graph, ivs, now)
    stolen = [iv for iv, _ in ivs if iv.kind == "stolen"]
    for ev in events:
        presence = LABELS.get((ev.source_id, ev.table), ("", "", False))[2] and ev.attr == _time(
            graph, ev.source_id, ev.table).get("col")
        ev.inside_stolen = bool(presence and any(iv.start < ev.when <= iv.end for iv in stolen))
    order = {s: i for i, s in enumerate(list(graph.get("sources", {})) + ["DOCS"])}
    allev = sorted(events + text, key=lambda e: (e.when, order.get(e.source_id, 99), e.label, str(e.pk), e.eid))
    forks.sort(key=lambda f: (f.doc_id, f.attr, f.text_value))
    intervals = sorted((iv for iv, _ in ivs), key=lambda iv: (iv.start, iv.kind, iv.source_id))
    counts = {"events": len(allev), "row_events": len(events), "text_events": len(text), "forks": len(forks),
              "inside_stolen": sum(e.inside_stolen for e in allev),
              "agencies": len({e.source_id for e in events})}
    return Timeline(allev, forks, intervals, counts, now)


def takeaway(tl: Timeline) -> str:
    """One sentence computed from the data: the alibi first, then a fork, then use after cover ended, else the span."""
    inside = [e for e in tl.events if e.inside_stolen]
    stolen = [iv for iv in tl.intervals if iv.kind == "stolen"]
    if inside:
        e = inside[0]
        iv = next(iv for iv in stolen if iv.start < e.when <= iv.end)
        since = f"FIR open since {day(iv.start)}" if iv.open_ended else f"stolen {day(iv.start)} to {day(iv.end)}"
        more = f" and {len(inside) - 1} more sighting(s)" if len(inside) > 1 else ""
        return f"A {e.noun} on {day(e.when)}{more} happened while the car was officially stolen ({since})."
    if tl.forks:
        extra = f" ({len(tl.forks)} contradictions in time in all)" if len(tl.forks) > 1 else ""
        return tl.forks[0].note.rstrip(".") + extra + "."
    covers = [iv for iv in tl.intervals if iv.kind == "cover"]
    ended = max((iv.end for iv in covers), default=None)
    if ended and tl.now and ended < tl.now:     # latest cover over: was the car physically about afterwards?
        later = [e for e in tl.events if e.when > ended and LABELS.get((e.source_id, e.table), ("", "", False))[2]]
        if later:
            return (f"A {later[0].noun} on {day(later[0].when)} came {(later[0].when - ended).days} days after the "
                    f"insurance cover ended ({day(ended)}).")
    rows = [e for e in tl.events if e.kind == "row"]
    if not rows:
        return "No dated records were found for this vehicle."
    span = f"{rows[0].when:%b %Y}" + (f" to {rows[-1].when:%b %Y}" if rows[-1].when.date() != rows[0].when.date() else "")
    n, k = len(rows), tl.counts.get("agencies", 0)
    return (f"{n} dated record{'s' * (n != 1)} from {k} agenc{'ies' if k != 1 else 'y'}, {span}; "
            "no contradictions in time.")
