"""CONTEXT-ADDED (R3: search unstructured data correlated with the query context, and integrate it).

retrieve(context) turns the discovered context (rows from the five structured sources) into a query for the DOCS
source, lets DOCS rank its own documents (BM25 inside the source, POST /search), then links every returned
document back to context rows and flags text-vs-database conflicts.

Three modes, the ablation the evaluation reports:
  query_only       the user's words alone (baseline: what a plain search box would do);
  context          + weighted descriptors from the context rows (owner name, station, make/model/colour, places);
  context+anchors  + anchors (plate, chassis, FIR no., policy no.) matched OCR/typo-tolerantly inside DOCS.

Descriptor weight = row score x source trust x gamma^hops x rarity:
  row score   how strongly context discovery tied the row to the query (path affinity x relevance);
  trust       the agency's reliability (CAM's OCR reads count less than REG's registration);
  gamma^hops  a value two joins away says less about the query than one on the seed row (gamma from the graph);
  rarity      1 / (1 + ln c), c = how many context rows carry the value: "PETROL" on every row identifies nothing.
              Corpus rarity (IDF) is applied by BM25 inside DOCS, so it is not counted twice here.
"""
from __future__ import annotations

import json
import math
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from mediator.context_types import ContextResult, ContextRow, DocResult, RetrievedDoc
from mediator.doc_conflicts import detect, to_date
from mediator.doc_linking import CtxValue, find_mentions, link_rows, snippet
# CONTEXT-ADDED (R3: the plate check in the "why" reasons)
from mediator.context_types import Mention
from sources.docs.textindex import canon
# CONTEXT-END

GRAPH_PATH = Path(__file__).resolve().parent.parent / "data" / "context_graph.json"
TRUST = {"REG": 0.95, "INS": 0.90, "THEFT": 0.90, "CAM": 0.60, "PUC": 0.80}
MODES = ("query_only", "context", "context+anchors")
MAX_TERMS = 40
MAX_ANCHORS = 12
# Identifiers that are not declared as plate/chassis bridges in context_graph.json, with how the agency prints them
# on paper (an insurer prints policy 562 as POL-000562). Requested as an "anchor" key in the graph (see report).
EXTRA_ANCHORS: Dict[Tuple[str, str, str], Tuple[str, str]] = {
    ("THEFT", "CRIME_RECORDS", "fir_no"): ("fir_no", "{}"),
    ("INS", "POLICY_RECORDS", "policy_id"): ("policy_no", "POL-{:06d}"),
    ("INS", "CLAIMS", "claim_id"): ("claim_no", "CLM-{:06d}"),
    ("PUC", "POLLUTION_CERT", "cert_no"): ("cert_no", "{}"),
}
_ENUM = re.compile(r"^[A-Z0-9_\-/.]+$")   # status codes, flags, norms: not descriptive text
# CONTEXT-ADDED (R3: plain words for the "why was this document retrieved" reasons on each card)
ANCHOR_WORDS = {"chassis": "the chassis number", "fir_no": "the FIR", "policy_no": "the policy",
                "claim_no": "the claim", "cert_no": "the pollution certificate"}
PLACE_WORDS = {"station": "the police station", "place": "the place", "insurer": "the insurer",
               "address": "the address", "city": "the city"}
PERSON_ROLE = {("REG", "OWNERS"): "owner", ("INS", "POLICY_HOLDERS"): "policy holder",
               ("THEFT", "PERSONS"): "person in the police case"}
VEHICLE_WORDS = ("make", "model", "colour")
# CONTEXT-END


def load_graph(path: Path = GRAPH_PATH) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"sources": {}, "params": {}}


def column_kind(col: str, spec: Mapping[str, Any]) -> Optional[str]:
    """Role of a text column, from the graph's person spec first, then by its name (a light schema-matching rule)."""
    person = spec.get("person") or {}
    if col == person.get("name"):
        return "person"
    if col == person.get("address"):
        return "address"
    c = col.lower()
    for needle, kind in (("colour", "colour"), ("make", "make"), ("model", "model"), ("station", "station"),
                         ("insurer", "insurer"), ("surveyor", "person"), ("location", "place"), ("road", "place"),
                         ("zone_name", "place"), ("city", "city"), ("district", "city"), ("centre_name", "place"),
                         ("tested_at", "place")):
        if needle in c:
            return kind
    return None


def _fmt(value: Any, pattern: str) -> str:
    try:
        return pattern.format(int(value)) if "d}" in pattern else pattern.format(value)
    except (TypeError, ValueError):
        return str(value)


def context_values(context: ContextResult, graph: Mapping[str, Any]) -> List[CtxValue]:
    """Every anchor and descriptor value in the context, with its query weight and the rows that carry it."""
    gamma = float(graph.get("params", {}).get("gamma_hops", 0.8))
    found: Dict[Tuple[str, str], CtxValue] = {}
    weights: Dict[Tuple[str, str], List[float]] = {}
    for r in context.rows:
        spec = graph.get("sources", {}).get(r.source_id, {}).get("tables", {}).get(r.table, {})
        row_w = max(float(r.score or 0.0), 0.05) * TRUST.get(r.source_id, 0.7) * gamma ** max(r.hops, 0) \
            * float(r.bridge_confidence or 1.0)
        cols: List[Tuple[str, str, str]] = []
        for role in ("plate", "chassis"):
            if spec.get(role):
                cols.append((spec[role], role, "anchor"))
        for (sid, table, col), (attr, _) in EXTRA_ANCHORS.items():
            if (sid, table) == (r.source_id, r.table):
                cols.append((col, attr, "anchor"))
        person = spec.get("person") or {}
        for col in dict.fromkeys(list(spec.get("text", [])) + [person.get("name"), person.get("address")]):
            kind = column_kind(col, spec) if col else None
            if kind:
                cols.append((col, kind, "descriptor"))
        time_spec = spec.get("time") or {}
        if time_spec.get("col"):
            cols.append((time_spec["col"], "date", "date"))
        for col, attr, kind in cols:
            raw = r.row.get(col)
            if raw is None or str(raw).strip() == "":
                continue
            if kind == "anchor":
                pattern = EXTRA_ANCHORS.get((r.source_id, r.table, col), (attr, "{}"))[1]
                value = _fmt(raw, pattern)
            elif kind == "date":
                d = to_date(raw, time_spec.get("fmt", "iso_date"))
                if d is None:
                    continue
                value = d.isoformat()
            else:
                value = str(raw).strip()
                if len(value) < 3 or _ENUM.match(value) and kind not in ("make", "model"):
                    continue
            # one value per identity: DL01AB1234 (REG) and DL-01-AB-1234 (INS) are the same anchor
            key = (attr, re.sub(r"[^A-Za-z0-9]", "", value).upper() if kind == "anchor" else value.lower())
            cv = found.setdefault(key, CtxValue(value=value, attr=attr, kind=kind))
            cv.rows.append((r.source_id, r.table, r.pk))
            weights.setdefault(key, []).append(row_w)
    for key, cv in found.items():
        cv.rows = list(dict.fromkeys(cv.rows))
        cv.weight = round(max(weights[key]) / (1.0 + math.log(len(cv.rows))), 4)
    return sorted(found.values(), key=lambda v: -v.weight)


def build_search(context: ContextResult, mode: str, values: List[CtxValue], k: int) -> Dict[str, Any]:
    terms = [{"term": w, "weight": 1.0} for w in re.findall(r"\S+", context.query.text or "")]
    if not terms:
        terms = [{"term": p, "weight": 1.0} for p in context.query.plates]
    if mode in ("context", "context+anchors"):
        terms += [{"term": v.value, "weight": v.weight} for v in values if v.kind == "descriptor"][:MAX_TERMS]
    anchors = []
    if mode == "context+anchors":
        seen = set()
        for v in [v for v in values if v.kind == "anchor"]:
            key = re.sub(r"[^A-Za-z0-9]", "", v.value).upper()
            if key not in seen and len(anchors) < MAX_ANCHORS:
                seen.add(key)
                anchors.append({"value": v.value, "attr": v.attr})
        anchors += [{"value": p, "attr": "plate"} for p in context.query.plates if p.upper() not in seen]
    return {"terms": terms, "anchors": anchors, "k": k, "doc_types": None}


def retrieve(context: ContextResult, k: int = 5, mode: str = "context+anchors", client: Any = None,
             graph: Optional[Mapping[str, Any]] = None) -> DocResult:
    """Never raises: a DOCS failure yields an empty DocResult (the client records the status)."""
    started = time.perf_counter()
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    graph = graph if graph is not None else load_graph()
    if client is None:
        from mediator.source_client import SourceClient
        client = SourceClient()
    values = context_values(context, graph)
    body = client.post("DOCS", "/search", build_search(context, mode, values, k)) or {}
    rows_by_key: Dict[Tuple[str, str, str], ContextRow] = {(r.source_id, r.table, str(r.pk)): r for r in context.rows}
    docs: List[RetrievedDoc] = []
    for hit in body.get("rows", []) or []:
        text = str(hit.get("body") or "")   # spans index the body, which is what the UI highlights
        mentions = find_mentions(text, values)
        linked = link_rows(mentions, values)
        linked_rows = [rows_by_key[(s, t, str(p))] for s, t, p in linked if (s, t, str(p)) in rows_by_key]
        # CONTEXT-REPLACED (R3: the card shows the full body, issuer, date, plain reasons and the relevance gate):
        # docs.append(RetrievedDoc(
        #     doc_id=str(hit.get("doc_id")), doc_type=str(hit.get("doc_type") or ""), title=str(hit.get("title") or ""),
        #     score=float(hit.get("score") or 0.0), snippet=snippet(text, mentions), mentions=mentions,
        #     linked_rows=linked, conflicts=detect(str(hit.get("doc_id")), text, mentions, linked_rows, graph)))
        docs.append(RetrievedDoc(
            doc_id=str(hit.get("doc_id")), doc_type=str(hit.get("doc_type") or ""), title=str(hit.get("title") or ""),
            score=float(hit.get("score") or 0.0), snippet=snippet(text, mentions), mentions=mentions,
            linked_rows=linked, conflicts=detect(str(hit.get("doc_id")), text, mentions, linked_rows, graph),
            body=text, issuer=hit.get("issuer"),
            issued_on=None if hit.get("issued_on") is None else str(hit["issued_on"]),
            why=why_reasons(mentions, values), relevant=is_relevant(mentions, linked, graph)))
        # CONTEXT-END
    return DocResult(docs=docs, mode=mode, rows_shipped=len(body.get("rows", []) or []),
                     elapsed_ms=round((time.perf_counter() - started) * 1000, 1))


# CONTEXT-ADDED (R3: relevance gate and plain-language reasons; ranking is unchanged, these only label a document)
def lookup_table(graph: Mapping[str, Any], sid: str, table: str) -> bool:
    """A reference table (insurers, stations, cameras): no plate, chassis, person or time column of its own."""
    spec = graph.get("sources", {}).get(sid, {}).get("tables", {}).get(table, {})
    return not any(spec.get(k) for k in ("plate", "chassis", "person", "time"))


def is_relevant(mentions: List[Mention], linked: List[Tuple[str, str, Any]], graph: Mapping[str, Any]) -> bool:
    # Relevant = it prints one of the case's identifiers, or it supports a record about the case itself. A link to a
    # lookup row alone does not count: "TrustLine Insurers" names the insurer of thousands of cars, not this one.
    return any(m.kind == "anchor" for m in mentions) or any(not lookup_table(graph, s, t) for s, t, _ in linked)


def why_reasons(mentions: List[Mention], values: List[CtxValue]) -> List[str]:
    """One plain sentence per kind of evidence: identifiers first, then by weight; make/model/colour fold into one."""
    best: Dict[str, Mention] = {}
    for m in mentions:
        if m.attr not in best or m.weight > best[m.attr].weight:
            best[m.attr] = m
    rows = {(v.attr, v.value): v.rows for v in values}
    out: List[Tuple[bool, float, str]] = []
    for attr, m in best.items():
        if attr in VEHICLE_WORDS:
            continue
        if attr == "plate":
            text = "names this plate" + ("" if m.text == canon(m.matched_value).upper() else f" (written {m.text})")
        elif m.kind == "anchor":
            text = f"names {ANCHOR_WORDS.get(attr, 'the record')} {m.text}"
        elif attr == "person":
            role = next((PERSON_ROLE[(s, t)] for s, t, _ in rows.get((attr, m.matched_value), [])
                         if (s, t) in PERSON_ROLE), "person")
            text = f"names the {role} {m.text}"
        elif attr == "date":
            text = f"gives a date found in the records ({m.text})"
        else:
            text = f"names {PLACE_WORDS.get(attr, 'the')} {m.text}"
        out.append((m.kind == "anchor", m.weight, text))
    car = [a for a in VEHICLE_WORDS if a in best]
    if car:
        words = " and ".join(car) if len(car) < 3 else "make, model and colour"
        described = " ".join(best[a].text for a in ("colour", "make", "model") if a in best)
        out.append((False, sum(best[a].weight for a in car), f"same {words} ({described})"))
    return [t for _, _, t in sorted(out, key=lambda p: (not p[0], -p[1]))]
# CONTEXT-END
