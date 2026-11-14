"""CONTEXT-ADDED (R2/R3): shared types for phase-3 context discovery, top-k, documents and analytics.

Every phase-3 module imports these instead of defining its own, so the parts built in parallel fit together.
Plain dataclasses: they cross module boundaries and are rendered by the website, so keep them JSON-friendly
(`asdict` works on all of them).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ------------------------------------------------------------------ query understanding (R2, free text)

@dataclass
class Predicate:
    attr: str                  # global attribute, e.g. "vehicle_make", "stolen_status", "location", "vehicle_colour"
    op: str                    # "=", "~" (fuzzy / contains), ">=", "<=", "between"
    value: Any
    confidence: float = 1.0    # 1.0 for rule-parsed, model probability for LLM-parsed


@dataclass
class ParsedQuery:
    text: str
    plates: List[str] = field(default_factory=list)          # canonical plates found in the text
    predicates: List[Predicate] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)        # leftover free terms
    time_window: Optional[Tuple[str, str]] = None            # ISO dates, inclusive
    parser: str = "rules"                                    # "rules" | "rules+llm"
    notes: List[str] = field(default_factory=list)           # why each predicate was produced


# ------------------------------------------------------------------ context discovery (R2)

@dataclass
class ContextRow:
    source_id: str
    table: str
    pk: Any
    row: Dict[str, Any]                  # raw row, source column names
    score: float                         # path affinity x query relevance, in [0, 1]
    path: List[str]                      # e.g. ["VEHICLE_REGISTRATION#405", "OWNERS#405"]
    hops: int
    via_bridge: Optional[str] = None     # "chassis" | "plate" | "person" when reached from another source
    bridge_confidence: float = 1.0
    # CONTEXT-ADDED (R2: "why is this row here?" - the admission proof shown in the case file)
    affinity: Optional[float] = None     # product of edge weights along `path` (x bridge confidence), before relevance
    edge_weights: List[float] = field(default_factory=list)   # one weight per FK hop in `path`
    # CONTEXT-END


@dataclass
class SourceStats:
    source_id: str
    rows_shipped: int = 0
    bytes_shipped: int = 0
    round_trips: int = 0
    sql_statements: int = 0
    latency_ms: float = 0.0
    bound: float = 0.0                   # upper bound on any row this source did not return
    hints_shipped: int = 0               # bridge-only hint rows (table, pk, affinity, bridge value), counted separately
    status: str = "OK"                   # OK / TIMEOUT / DOWN / ERROR (phase-1 meaning)
    error: Optional[str] = None


@dataclass
class PersonCluster:
    cluster_id: str
    members: List[Tuple[str, str, Any]]  # (source_id, table, pk)
    canonical: Dict[str, Any]            # golden record: name, mobile, address
    probability: float                   # Fellegi-Sunter posterior of the weakest link kept


@dataclass
class ContextResult:
    query: ParsedQuery
    rows: List[ContextRow]               # global top rows, best first
    stats: List[SourceStats]
    persons: List[PersonCluster] = field(default_factory=list)
    rounds: int = 1
    complete: bool = True                # False when a source was down: answer is a lower bound
    baseline_rows: Optional[int] = None  # rows the naive full-closure approach would ship (for the counter)
    elapsed_ms: float = 0.0
    trace: List[Dict[str, Any]] = field(default_factory=list)   # per round: tau_n, max frontier bound, expanded, pruned
    hints_shipped: Dict[str, int] = field(default_factory=dict) # source_id -> bridge-only hint rows shipped
    # CONTEXT-ADDED (R2: "leads not followed" - cross-database follow-ups the bound pruned, kept for the case file;
    # each: {"target", "attr", "value", "bound", "from": (source_id, table, pk) | None, "round"})
    pruned_leads: List[Dict[str, Any]] = field(default_factory=list)
    # CONTEXT-END


# ------------------------------------------------------------------ entity top-k (R2, free text)

@dataclass
class RankedVehicle:
    plate: str
    score: float
    partial: Dict[str, float]            # source_id -> partial score
    identity_confidence: float = 1.0     # how sure the per-source rows are the same vehicle
    explanation: List[str] = field(default_factory=list)


@dataclass
class TopKResult:
    query: ParsedQuery
    vehicles: List[RankedVehicle]
    sorted_accesses: int = 0
    random_accesses: int = 0
    rows_shipped: int = 0
    baseline_rows: int = 0
    stopped_at_depth: int = 0
    elapsed_ms: float = 0.0
    round_trips: int = 0                 # requested by WS-C: charted TA vs TPUT
    method: str = "ta"                   # "ta" (Fagin threshold algorithm) | "tput" (three-phase uniform threshold)


# ------------------------------------------------------------------ documents (R3)

@dataclass
class Mention:
    text: str                            # what the document says, as written
    kind: str                            # "anchor" | "descriptor"
    attr: str                            # plate, chassis, fir_no, policy_no, person, station, make, colour, place
    start: int
    end: int
    matched_value: str                   # the context value it matched
    similarity: float
    weight: float


@dataclass
class Conflict:
    attr: str
    db_value: Any
    db_source: str
    text_value: Any
    doc_id: str
    span: Tuple[int, int]
    note: str


@dataclass
class RetrievedDoc:
    doc_id: str
    doc_type: str
    title: str
    score: float
    snippet: str
    mentions: List[Mention] = field(default_factory=list)
    linked_rows: List[Tuple[str, str, Any]] = field(default_factory=list)   # (source_id, table, pk)
    conflicts: List[Conflict] = field(default_factory=list)
    # CONTEXT-ADDED (R3: documents are opened in full in the case file, and say who issued them, when, and why)
    body: str = ""                       # full text as stored by DOCS; Mention.start/end index into it
    issuer: Optional[str] = None
    issued_on: Optional[str] = None      # ISO date string as DOCS stores it
    why: List[str] = field(default_factory=list)   # plain-language reasons it was retrieved, strongest first
    relevant: bool = True                # False: matched only generic words, no anchor and no linked row
    # CONTEXT-END


@dataclass
class DocResult:
    docs: List[RetrievedDoc]
    mode: str                            # "query_only" | "context" | "context+anchors"
    rows_shipped: int = 0
    elapsed_ms: float = 0.0


# ------------------------------------------------------------------ analytics

@dataclass
class Indicator:
    code: str                            # ALIBI_PUC, ALIBI_CAMERA, ALIBI_CHALLAN, QUICK_CLAIM, TRANSFER_BEFORE_LOSS, PERSON_RING, ...
    severity: str                        # "high" | "medium" | "low"
    title: str
    explanation: str
    evidence: List[Tuple[str, str, Any]] = field(default_factory=list)      # (source_id, table, pk)
