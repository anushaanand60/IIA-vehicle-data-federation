"""CONTEXT-ADDED (R3: measure the document integration) retrieval / linking / conflict metrics vs the generator gold.

Qrels come from data/phase3_gold.json (the generator knows which plate each document was written about: grade 2 =
subject, 1 = mentioned). Per mode (query_only -> context -> context+anchors):
  nDCG@k, MRR                 ranking quality;
  link precision / recall     predicted (source, table, pk) links vs gold links, recall counted over gold links whose
                              row is in the context (a row the context never found cannot be linked);
  conflict recall / precision planted conflicts found (same attribute) in retrieved relevant documents.
evaluate_docs(contexts=None, client=None, k=10) is what scripts/evaluate_context.py calls; without contexts it builds
them with mediator.context_discovery.discover, or - when that is unavailable - with a plain FK closure over the CSVs.
"""
from __future__ import annotations

import csv
import json
import math
import random
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from mediator.context_types import ContextResult, ContextRow, ParsedQuery
from mediator.doc_retrieval import MODES, load_graph, retrieve
from sources.docs.textindex import canon

ROOT = Path(__file__).resolve().parent.parent
GOLD_PATH = ROOT / "data" / "phase3_gold.json"
STORY = ["DL01AB1234", "DL05CD9876", "HR26EF4455", "UP16GH1122", "MH12IJ7788"]
CSV_NAME = {("PUC", "POLLUTION_CERT"): "puc_records.csv"}


def load_gold(path: Path = GOLD_PATH) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"docs": {}, "by_plate": {}}


def ndcg_at(ranked: Sequence[str], grades: Dict[str, int], k: int) -> float:
    dcg = sum((2 ** grades.get(d, 0) - 1) / math.log2(i + 2) for i, d in enumerate(ranked[:k]))
    ideal = sorted(grades.values(), reverse=True)[:k]
    idcg = sum((2 ** g - 1) / math.log2(i + 2) for i, g in enumerate(ideal))
    return dcg / idcg if idcg else 0.0


def reciprocal_rank(ranked: Sequence[str], grades: Dict[str, int]) -> float:
    return next((1.0 / (i + 1) for i, d in enumerate(ranked) if grades.get(d, 0) > 0), 0.0)


def _key(link: Iterable[Any]) -> Tuple[str, str, str]:
    s, t, p = list(link)
    return str(s), str(t), str(p)


def score_plate(plate: str, context: ContextResult, client: Any, k: int, mode: str, gold: Dict[str, Any]) -> Dict[str, Any]:
    res = retrieve(context, k=k, mode=mode, client=client)
    grades = {d: int(g) for d, g in gold.get("by_plate", {}).get(plate, {}).items()}
    ranked = [d.doc_id for d in res.docs]
    ctx_keys = {(r.source_id, r.table, str(r.pk)) for r in context.rows}
    tp = pred = linkable = tp_linkable = 0
    planted = found = flagged = flagged_ok = 0
    for d in res.docs:
        g = gold.get("docs", {}).get(d.doc_id, {})
        gl = {_key(l) for l in g.get("links", [])}
        pl = {_key(l) for l in d.linked_rows}
        pred += len(pl)
        tp += len(pl & gl)
        linkable += len(gl & ctx_keys)
        tp_linkable += len(pl & gl & ctx_keys)
        planted_attrs = {c["attr"] for c in g.get("conflicts", [])}
        flagged += len(d.conflicts)
        flagged_ok += sum(c.attr in planted_attrs for c in d.conflicts)
    for doc_id, grade in grades.items():   # every planted conflict on a relevant doc, retrieved or not
        if grade < 2:
            continue
        for c in gold["docs"].get(doc_id, {}).get("conflicts", []):
            planted += 1
            hit = next((d for d in res.docs if d.doc_id == doc_id), None)
            found += bool(hit and any(x.attr == c["attr"] for x in hit.conflicts))
    return {"plate": plate, "mode": mode, "ndcg": ndcg_at(ranked, grades, k), "mrr": reciprocal_rank(ranked, grades),
            "tp": tp, "pred": pred, "linkable": linkable, "tp_linkable": tp_linkable, "planted": planted,
            "found": found, "flagged": flagged, "flagged_ok": flagged_ok, "rows_shipped": res.rows_shipped}


def pick_plates(gold: Dict[str, Any], n: int = 40, seed: int = 7) -> List[str]:
    """Story plates, every plate with a planted conflict, then a seeded sample of the rest (all with a subject doc)."""
    subj = {p for p, docs in gold.get("by_plate", {}).items() if any(g >= 2 for g in docs.values())}
    real = {p for p in subj if not all(gold["docs"][d].get("distractor_for") for d in gold["by_plate"][p])}
    conflicted = sorted({p for p in real for d in gold["by_plate"][p] if gold["docs"][d].get("conflicts")})
    rest = sorted(real - set(conflicted) - set(STORY))
    random.Random(seed).shuffle(rest)
    return list(dict.fromkeys([p for p in STORY if p in real] + conflicted + rest))[:n]


def evaluate_docs(contexts: Optional[Dict[str, ContextResult]] = None, client: Any = None, k: int = 10,
                  plates: Optional[List[str]] = None, modes: Sequence[str] = MODES) -> Dict[str, Any]:
    gold = load_gold()
    builder = "given"
    if contexts is None:
        plates = plates or pick_plates(gold)
        contexts, builder = build_contexts(plates, client)
    per: List[Dict[str, Any]] = []
    for plate, ctx in contexts.items():
        for mode in modes:
            per.append(score_plate(canon(plate).upper(), ctx, client, k, mode, gold))
    out: Dict[str, Any] = {}
    for mode in modes:
        rows = [r for r in per if r["mode"] == mode]
        n = len(rows) or 1
        out[mode] = {f"ndcg_at_{k}": sum(r["ndcg"] for r in rows) / n, "mrr": sum(r["mrr"] for r in rows) / n,
                     "link_precision": _div(sum(r["tp"] for r in rows), sum(r["pred"] for r in rows)),
                     "link_recall": _div(sum(r["tp_linkable"] for r in rows), sum(r["linkable"] for r in rows)),
                     "conflict_recall": _div(sum(r["found"] for r in rows), sum(r["planted"] for r in rows)),
                     "conflict_precision": _div(sum(r["flagged_ok"] for r in rows), sum(r["flagged"] for r in rows)),
                     "rows_shipped": sum(r["rows_shipped"] for r in rows), "n_queries": len(rows)}
    return {"modes": out, "per_plate": per, "k": k, "context_builder": builder, "gold_docs": len(gold.get("docs", {}))}


def _div(a: float, b: float) -> float:
    return a / b if b else 0.0


def build_contexts(plates: List[str], client: Any) -> Tuple[Dict[str, ContextResult], str]:
    try:
        from mediator.context_discovery import discover
        return {p: discover(p, client=client) for p in plates}, "context_discovery.discover"
    except Exception:  # discovery not built yet / sources down: the CSV FK closure keeps the evaluation runnable
        return {p: csv_context(p) for p in plates}, "csv_fk_closure"


@lru_cache(maxsize=None)
def _table(source_id: str, table: str) -> Tuple[Dict[str, Any], ...]:
    path = ROOT / CSV_NAME.get((source_id, table), f"{source_id.lower()}_{table.lower()}.csv")
    if not path.exists():
        return ()
    with path.open(newline="", encoding="utf-8", errors="replace") as f:
        return tuple(csv.DictReader(f))


def csv_context(plate: str, max_hops: int = 2) -> ContextResult:
    """Offline stand-in for context discovery: plate rows (hop 0), FK parents out to max_hops, and the detail rows
    that hang off a hop-0 row (claims of the policy, stolen property of the FIR). Score = gamma^hops."""
    graph, p = load_graph(), canon(plate).upper()
    gamma = float(graph.get("params", {}).get("gamma_hops", 0.8))
    rows: Dict[Tuple[str, str, str], ContextRow] = {}
    frontier: List[ContextRow] = []
    for sid, src in graph.get("sources", {}).items():
        for table, spec in src.get("tables", {}).items():
            if spec.get("plate"):
                for r in _table(sid, table):
                    if canon(r.get(spec["plate"])).upper() == p:
                        cr = ContextRow(sid, table, r[spec["pk"]], dict(r), 1.0, [f"{table}#{r[spec['pk']]}"], 0, "plate")
                        rows[(sid, table, str(cr.pk))] = cr
                        frontier.append(cr)
    for hop in range(1, max_hops + 1):
        nxt: List[ContextRow] = []
        for cr in frontier:
            for e in graph["sources"][cr.source_id].get("edges", []):
                tables = graph["sources"][cr.source_id]["tables"]
                if e["from"] == cr.table:                        # outward: FK -> referenced parent
                    targets = [(e["to"], r) for r in _table(cr.source_id, e["to"]) if str(r.get(e["to_col"])) == str(cr.row.get(e["col"]))]
                elif e["to"] == cr.table and cr.hops == 0:       # inward: details of a seed row only (no hub fan-out)
                    targets = [(e["from"], r) for r in _table(cr.source_id, e["from"]) if str(r.get(e["col"])) == str(cr.row.get(e["to_col"]))]
                else:
                    continue
                for table, r in targets:
                    pkcol = tables.get(table, {}).get("pk")
                    pk = "|".join(str(r[c]) for c in pkcol) if isinstance(pkcol, list) else r.get(pkcol)
                    key = (cr.source_id, table, str(pk))
                    if key not in rows:
                        rows[key] = ContextRow(cr.source_id, table, pk, dict(r), gamma ** hop, cr.path + [f"{table}#{pk}"], hop)
                        nxt.append(rows[key])
        frontier = nxt
    return ContextResult(query=ParsedQuery(text=plate, plates=[p]), rows=list(rows.values()), stats=[])
