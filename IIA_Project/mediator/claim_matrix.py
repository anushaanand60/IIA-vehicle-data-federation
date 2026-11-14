"""CONTEXT-ADDED (R3: claim matrix) who states what about the vehicle, and where the statements disagree.

Rows = facts; columns = agencies that state one, then each relevant document that does. A cell keeps the value as
stated, a comparison form and a status against the row's trust-weighted majority (agree / conflict / silent). Pure.
Sources of each fact (graph metadata first; doc_conflicts' precedent where a column is named):
  colour/make/model  columns the graph marks as that kind (column_kind); documents via mentions + detect() conflicts;
  owner name         graph `person.name` of the owner and policy-holder tables;
  dates              doc_conflicts.CUES classes, each tied to one table whose graph time column (and format) is read;
  case status        CRIME_RECORDS.case_status; a document says "recovered" through detect()'s recovery conflict.
Comparison: lower case, DOCS tokeniser variants (grey/gray, Kr./Kumar), a make by its first word (Maruti == Maruti
Suzuki, doc_conflicts' prefix rule), dates as ISO within each cue's day tolerance.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Mapping, Optional, Tuple

from mediator.context_types import ContextResult, DocResult, RetrievedDoc
from mediator.doc_conflicts import CUES, date_class, text_dates, to_date
from mediator.doc_retrieval import TRUST, column_kind, load_graph
from sources.docs.textindex import tokenize

# A document restates a fact second-hand: it weighs less than any agency, so one text cannot outvote a database,
# but text agreeing with the camera can outvote the registration (the clone story). Agencies: doc_retrieval.TRUST.
DOC_TRUST = 0.5
AGENCY_ORDER = ["REG", "INS", "THEFT", "CAM", "PUC"]
AGENCY_WORDS = {"REG": "registration", "INS": "insurance", "THEFT": "police record", "CAM": "camera",
                "PUC": "PUC centre"}
DOC_TYPES = {"FIR_NARRATIVE": "Police FIR narrative", "SURVEYOR_REPORT": "Surveyor report", "RTO_NOTICE": "RTO notice",
             "ACCIDENT_REPORT": "Accident report", "NEWS": "News item", "WITNESS_STATEMENT": "Witness statement"}
OWNER_TABLES = {("REG", "OWNERS"), ("INS", "POLICY_HOLDERS")}   # THEFT.PERSONS holds complainants AND accused
DATE_FACTS = {"policy_until": "Policy valid till", "incident_date": "Theft reported on",
              "recovered_on": "Recovered on", "loss_date": "Date of loss"}
FACTS = [("colour", "Colour"), ("make", "Make"), ("model", "Model"), ("owner", "Owner name")] \
    + list(DATE_FACTS.items()) + [("case_status", "Police case status")]
STATUS_COL = ("THEFT", "CRIME_RECORDS", "case_status")


@dataclass
class Column:
    key: str            # "REG" or "D00312"
    label: str          # "registration" / "Surveyor report"
    kind: str           # "agency" | "document"


@dataclass
class Cell:
    value: Any = None   # as stated (raw source value, or the document's words)
    norm: Optional[str] = None
    status: str = "silent"   # agree | conflict | silent


@dataclass
class FactRow:
    fact: str
    label: str
    cells: Dict[str, Cell] = field(default_factory=dict)
    majority: Optional[str] = None      # comparison form of the trust-weighted majority
    disputed: bool = False


@dataclass
class ClaimMatrix:
    columns: List[Column]
    rows: List[FactRow]


def norm(fact: str, value: Any) -> Optional[str]:
    if value is None or str(value).strip() == "":
        return None
    words = tokenize(str(value))
    if fact == "make":
        return words[0] if words else None
    return "".join(words) if fact == "model" else " ".join(words) or str(value).strip().lower()


def same(fact: str, a: Optional[str], b: Optional[str]) -> bool:
    if a is None or b is None:
        return False
    tol = next((t for _, cls, _, t in CUES if cls == fact), None)
    if tol is not None:
        return abs((date.fromisoformat(a) - date.fromisoformat(b)).days) <= tol
    return a == b


def _date_tables() -> Dict[str, Tuple[str, str]]:
    return {cls: src for _, cls, src, _ in CUES if cls in DATE_FACTS}


def agency_claims(context: ContextResult, graph: Mapping[str, Any]) -> Tuple[Dict[str, Dict[str, Cell]], Dict]:
    """fact -> source -> Cell, and fact -> the row key each date / status claim came from (its best-scored row)."""
    seen: Dict[Tuple[str, str], List[Tuple[Any, str]]] = {}
    chosen: Dict[str, Any] = {}
    date_tables = _date_tables()
    for r in context.rows:   # best first
        spec = graph.get("sources", {}).get(r.source_id, {}).get("tables", {}).get(r.table, {})
        facts: List[Tuple[str, Any]] = [(k, r.row.get(c)) for c in spec.get("text", [])
                                        if (k := column_kind(c, spec)) in ("colour", "make", "model")]
        if (r.source_id, r.table) in OWNER_TABLES and (spec.get("person") or {}).get("name"):
            facts.append(("owner", r.row.get(spec["person"]["name"])))
        time_spec = spec.get("time") or {}
        for cls, src in date_tables.items():
            if src == (r.source_id, r.table) and cls not in chosen and time_spec.get("col"):
                d = to_date(r.row.get(time_spec["col"]), time_spec.get("fmt", "iso_date"))
                if d:
                    chosen[cls] = (r.source_id, r.table, str(r.pk))
                    seen.setdefault((cls, r.source_id), []).append((r.row.get(time_spec["col"]), d.isoformat()))
        if (r.source_id, r.table) == STATUS_COL[:2] and "case_status" not in chosen and r.row.get(STATUS_COL[2]):
            chosen["case_status"] = (r.source_id, r.table, str(r.pk))
            facts.append(("case_status", r.row.get(STATUS_COL[2])))
        for fact, value in facts:
            if norm(fact, value):
                seen.setdefault((fact, r.source_id), []).append((value, norm(fact, value)))
    out: Dict[str, Dict[str, Cell]] = {}
    for (fact, sid), vals in seen.items():
        top = Counter(n for _, n in vals).most_common(1)[0][0]   # within one agency: its most repeated value
        out.setdefault(fact, {})[sid] = Cell(next(v for v, n in vals if n == top), top)
    return out, chosen


def doc_claims(doc: RetrievedDoc, chosen: Mapping[str, Any], agency: Mapping[str, Dict[str, Cell]]) -> Dict[str, Cell]:
    out: Dict[str, Cell] = {}
    linked = {(s, t, str(p)) for s, t, p in doc.linked_rows}
    for fact in ("colour", "make", "model"):
        said = [c.text_value for c in doc.conflicts if c.attr == fact] \
            or [m.text for m in doc.mentions if m.attr == fact]
        if said:
            out[fact] = Cell(said[0], norm(fact, said[0]))
    owners = {c.norm for c in agency.get("owner", {}).values()}
    for m in doc.mentions:
        if m.attr == "person" and norm("owner", m.matched_value) in owners and "owner" not in out:
            out["owner"] = Cell(m.text, norm("owner", m.matched_value))   # matched as the same person (name sim)
    body = doc.body or ""
    for cls in DATE_FACTS:
        if chosen.get(cls) and chosen[cls] not in linked:
            continue    # the document speaks about another record of that table, or about none
        dates = [(body[s:e], d.isoformat()) for d, s, e in text_dates(body)
                 if (date_class(body, s, e) or [""])[0] == cls]
        db = next(iter(agency.get(cls, {}).values()), None)
        if dates:
            off = [x for x in dates if db and not same(cls, x[1], db.norm)]
            out[cls] = Cell(*(off or dates)[0])   # a contradicting date first, as detect() does
    if any(c.attr == "recovery" for c in doc.conflicts):
        out["case_status"] = Cell("recovered", "recovered")
    return out


def _vote(row: FactRow, columns: List[Column]) -> None:
    trust = {c.key: TRUST.get(c.key, 0.7) if c.kind == "agency" else DOC_TRUST for c in columns}
    stated = [(k, c) for k, c in row.cells.items() if c.norm]
    groups: List[Tuple[str, float, float]] = []   # (representative norm, summed trust, best single trust)
    for k, c in sorted(stated, key=lambda kc: -trust[kc[0]]):
        i = next((i for i, g in enumerate(groups) if same(row.fact, g[0], c.norm)), None)
        if i is None:
            groups.append((c.norm, trust[k], trust[k]))
        else:
            groups[i] = (groups[i][0], groups[i][1] + trust[k], groups[i][2])
    if not groups:
        return
    row.majority = max(groups, key=lambda g: (round(g[1], 6), g[2]))[0]
    for c in row.cells.values():
        c.status = "silent" if not c.norm else "agree" if same(row.fact, c.norm, row.majority) else "conflict"
    row.disputed = any(c.status == "conflict" for c in row.cells.values())


def build(context: Optional[ContextResult], docs: Optional[DocResult],
          graph: Optional[Mapping[str, Any]] = None) -> ClaimMatrix:
    if context is None:
        return ClaimMatrix([], [])
    graph = graph if graph is not None else load_graph()
    agency, chosen = agency_claims(context, graph)
    by_doc = {d.doc_id: doc_claims(d, chosen, agency) for d in (docs.docs if docs else []) if d.relevant}
    columns = [Column(s, AGENCY_WORDS.get(s, s), "agency") for s in AGENCY_ORDER
               if any(s in v for v in agency.values())]
    columns += [Column(d.doc_id, DOC_TYPES.get(d.doc_type, d.doc_type.replace("_", " ").capitalize()), "document")
                for d in (docs.docs if docs else []) if by_doc.get(d.doc_id)]
    rows = []
    for fact, label in FACTS:
        found = {c.key: agency.get(fact, {}).get(c.key) if c.kind == "agency" else by_doc[c.key].get(fact)
                 for c in columns}
        cells = {k: Cell(c.value, c.norm) if c else Cell() for k, c in found.items()}
        if any(c.norm for c in cells.values()):
            row = FactRow(fact, label, cells)
            _vote(row, columns)
            rows.append(row)
    return ClaimMatrix(columns, rows)


def shown(fact: str, cell: Cell) -> str:
    """A cell's value for people: dates as '10 Jun 2026', the rest as stated."""
    if cell.norm and fact in DATE_FACTS:
        return date.fromisoformat(cell.norm).strftime("%d %b %Y").lstrip("0")
    return str(cell.value).strip().lower() if fact in ("colour", "case_status") else str(cell.value).strip()


def _who(columns: List[Column], keys: List[str]) -> Tuple[str, bool]:
    agencies = [c.label for c in columns if c.key in keys and c.kind == "agency"]
    kinds = Counter(c.label if c.label[:2].isupper() else c.label[0].lower() + c.label[1:]
                    for c in columns if c.key in keys and c.kind == "document")
    numbers = {1: "one", 2: "two", 3: "three"}
    docs = [f"{numbers.get(n, n)} {k}{'s' if n > 1 else ''}" for k, n in kinds.items()]
    parts = agencies + docs
    text = ", ".join(parts[:-1]) + (" and " if len(parts) > 1 else "") + parts[-1] if parts else "nobody"
    return text, len(keys) > 1


def takeaway(matrix: ClaimMatrix) -> str:
    disputed = [r for r in matrix.rows if r.disputed]
    if not matrix.rows:
        return ""
    if not disputed:
        return f"All {len(matrix.rows)} facts agree wherever they are stated."
    parts = []
    for r in disputed:
        groups: Dict[str, List[str]] = {}
        for k, c in r.cells.items():
            if c.norm:
                rep = r.majority if c.status == "agree" else next((g for g in groups if g != r.majority
                                                                   and same(r.fact, g, c.norm)), c.norm)
                groups.setdefault(rep, []).append(k)
        said = []
        for rep, keys in sorted(groups.items(), key=lambda g: g[0] != r.majority):
            who, many = _who(matrix.columns, keys)
            said.append(f"{who} {'say' if many else 'says'} {shown(r.fact, r.cells[keys[0]])}")
        parts.append(f"{r.label.lower()} ({'; '.join(said)})")
    n = len(disputed)
    return f"{n} fact{'s' if n > 1 else ''} disputed: " + "; ".join(parts)
