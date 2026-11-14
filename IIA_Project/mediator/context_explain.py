"""CONTEXT-ADDED (R2: "why is this row here?" and "leads not followed"): the admission proof of a context answer.

Every row of a bounded answer is there for a reason that can be read off numbers the sources already sent:
    score = affinity x relevance,   affinity = start x (product of step weights) x (bridge confidences)
and it was admitted because score >= tau_n (the n-th best score). Every follow-up the mediator did not take
had a bound below tau_n, so nothing it could have returned would have entered the answer. This module turns
those numbers into plain sentences, keeps the pruned follow-ups (leads), and can follow one on demand.
Pure except `pursue`, which asks one source (the user clicked "Pursue").
"""
from __future__ import annotations

import dataclasses
import math
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from mediator.context_types import ContextResult, ContextRow

BRIDGE_RE = re.compile(r"^(\w+)->(\w+)$")          # 'plate->INS': the path crossed into INS by plate
LEADS_KEPT = 20
SAME = {"plate": "plate", "chassis": "chassis number", "person": "person"}
Label = Callable[[str], str]


def humanise(table: str) -> str:
    """'OWNERSHIP_HISTORY' -> 'Ownership history' (fallback when the page passes no display names)."""
    return str(table).replace("_", " ").capitalize()


def table_sources(graph: Optional[Dict[str, Any]]) -> Dict[str, str]:
    return {t: sid for sid, src in (graph or {}).get("sources", {}).items() for t in src.get("tables", {})}


def split_path(path: Sequence[Any], owner: str, tsrc: Dict[str, str]) -> List[Dict[str, Any]]:
    """Path elements as nodes {"table","pk","source"} and crossings {"bridge","to"}; each node knows its source."""
    cur = owner   # no crossing: every node is the row's own source; else the first node's table names its source
    if path and any(BRIDGE_RE.match(str(p)) for p in path):
        cur = tsrc.get(str(path[0]).partition("#")[0], owner)
    out: List[Dict[str, Any]] = []
    for p in path:
        hop = BRIDGE_RE.match(str(p))
        if hop:
            cur = hop.group(2)
            out.append({"bridge": hop.group(1), "to": cur})
        else:
            table, _, pk = str(p).partition("#")
            out.append({"table": table, "pk": pk, "source": cur})
    return out


def _edges(ew: Sequence[Dict[str, Any]], a: str, b: str) -> List[float]:
    """Weights of every FK edge that can take a -> b, as the source measured them (fwd = child->parent, rev = back)."""
    return sorted({e["fwd"] for e in ew if e["from"] == a and e["to"] == b}
                  | {e["rev"] for e in ew if e["from"] == b and e["to"] == a})


def hop_weights(path: Sequence[Any], owner: str, ew: Dict[str, List[Dict[str, Any]]], graph: Dict[str, Any],
                target: Optional[float] = None) -> List[float]:
    """One effective weight (edge weight x per-hop discount) per FK step of `path`; [] if any step is unknown.

    Two FK edges can join the same pair of tables (an ownership transfer names a previous and a new owner); the
    path string does not say which one was walked, so the combination whose product best matches the affinity the
    source reported (`target`) is chosen -- an exact reconstruction whenever the edge weights differ."""
    gamma = float(graph.get("params", {}).get("gamma_hops", 1.0))
    parts, cands = split_path(path, owner, table_sources(graph)), [[1.0]]
    for prev, cur in zip(parts, parts[1:]):
        if "table" in prev and "table" in cur and prev["source"] == cur["source"]:
            ws = _edges(ew.get(cur["source"]) or [], prev["table"], cur["table"])
            if not ws:
                return []  # honest: no partial list that would misalign with the steps
            cands = [c + [round(w * gamma, 6)] for c in cands for w in ws][:256]
    best = min(cands, key=lambda c: abs(math.prod(c) - target) if target else -math.prod(c))
    return best[1:]


# ------------------------------------------------------------------ leads not followed

def note_leads(store: Dict[Tuple[str, str, str], Tuple[Any, int]], states: Sequence[Any], round_no: int) -> None:
    """Remember each pruned frontier state (best bound per key); converted to dicts only for the kept top few."""
    for f in states:
        if f.key not in store or f.bound > store[f.key][0].bound:
            store[f.key] = (f, round_no)


def top_leads(store: Dict[Tuple[str, str, str], Tuple[Any, int]], sent: Dict[Tuple[str, str, str], float],
              graph: Dict[str, Any], k: int = LEADS_KEPT) -> List[Dict[str, Any]]:
    """Leads never followed afterwards (a later round may have sent the same value with a higher weight).

    One record yields many states into the same agency (OCR look-alikes of its plate, several candidate people);
    they are kept as one lead -- the best -- with `alike` = how many more it stands for, so they do not crowd out
    leads from other records."""
    groups: Dict[Tuple[Any, ...], List[Tuple[Any, int]]] = {}
    for f, r in store.values():
        if sent.get(f.key, -1.0) < f.weight - 1e-12:
            groups.setdefault((f.target, f.bridge or f.kind, tuple(f.origin_path[-2:])), []).append((f, r))
    best = [(min(g, key=lambda x: (-x[0].bound, x[0].value)), len(g) - 1) for g in groups.values()]
    best.sort(key=lambda x: (-x[0][0].bound, x[0][0].target, x[0][0].value))
    tsrc, out = table_sources(graph), []
    for (f, r), alike in best[:k]:
        nodes = [p for p in split_path(f.origin_path, f.target, tsrc) if "table" in p]
        origin = (nodes[-1]["source"], nodes[-1]["table"], nodes[-1]["pk"]) if nodes else None
        out.append({"target": f.target, "attr": f.bridge or f.kind, "value": f.value, "bound": round(f.bound, 6),
                    "from": origin, "round": r, "kind": f.kind, "weight": round(f.weight, 6),
                    "confidence": round(f.confidence, 6), "alike": alike})
    return out


def pursue(lead: Dict[str, Any], ctx: ContextResult, client: Any = None, graph: Optional[Dict[str, Any]] = None
           ) -> Dict[str, Any]:
    """Follow one pruned lead anyway: ask only its target source, seeded with that value (one /context call).

    The seed starts at weight 1, so each returned score is multiplied by the lead's weight to give the score it
    would have had inside the original answer; then it is compared with that answer's cut-off."""
    from mediator.context_discovery import discover
    from mediator.context_scoring import load_graph
    g = graph or load_graph()
    only = {**g, "sources": {lead["target"]: g["sources"][lead["target"]]}}
    if lead.get("kind") == "pk":
        table, _, pk = str(lead["value"]).partition("#")
        seeds: Dict[str, Any] = {"pks": {table: [pk]}}
    else:
        seeds = {lead["kind"]: [lead["value"]]}
    pq = dataclasses.replace(ctx.query, plates=[])  # same keywords and window, but only the lead as the seed
    got = discover(pq, n=max(1, len(ctx.rows)), mode="topn", client=client, seeds=seeds, graph=only)
    have = {(r.source_id, r.table, str(r.pk)) for r in ctx.rows}
    w, cut = float(lead.get("weight", 1.0)), cutoff(ctx)
    new = [dataclasses.replace(r, score=round(r.score * w, 6)) for r in got.rows
           if (r.source_id, r.table, str(r.pk)) not in have]
    return {"rows": new, "above": [r for r in new if r.score > cut + 1e-9], "cutoff": cut,
            "bound": float(lead.get("bound", 0.0)), "complete": got.complete}


# ------------------------------------------------------------------ the proof of one row

def cutoff(ctx: ContextResult) -> float:
    final = next((t for t in reversed(ctx.trace or []) if "tau_n" in t), None)
    if final is not None:
        return float(final["tau_n"])
    return min((r.score for r in ctx.rows), default=0.0)


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def guarantee(ctx: ContextResult, sid: str, name: Callable[[str], str] = str) -> str:
    st = next((s for s in ctx.stats if s.source_id == sid), None)
    if st is None:
        return f"{name(sid)} was not asked."
    if st.status != "OK":
        return f"{name(sid)} did not answer ({st.status}), so this part of the answer is a lower bound."
    if st.bound <= 0:
        return f"{name(sid)} sent everything it found within reach; it held nothing back."
    return f"Nothing {name(sid)} held back could score above {st.bound:.2f}."


def explain_row(row: ContextRow, ctx: ContextResult, graph: Optional[Dict[str, Any]] = None,
                label: Label = humanise, name: Callable[[str], str] = str) -> Dict[str, Any]:
    """Plain-language admission proof of one row: path with weights, affinity, relevance, cut-off, guarantee."""
    parts = split_path(row.path or [f"{row.table}#{row.pk}"], row.source_id, table_sources(graph))
    weights, steps, factors, wi = list(row.edge_weights or []), [], [], 0
    last = max([i for i, p in enumerate(parts) if "bridge" in p], default=-1)  # only the last crossing's confidence is kept
    for i, p in enumerate(parts):
        if "bridge" in p:
            factors.append(row.bridge_confidence if i == last else None)
            steps.append(f"same {SAME.get(p['bridge'], p['bridge'])} found in {name(p['to'])}"
                         + (f" ({row.bridge_confidence:.0%} sure)" if i == last else ""))
            continue
        text = f"{label(p['table'])} #{p['pk']}"
        prev = parts[i - 1] if i else None
        if prev and "table" in prev and wi < len(weights):
            text += f" (weight {weights[wi]:.2f})"
            factors.append(weights[wi])
            wi += 1
        steps.append(text)
    start = row.bridge_confidence if row.via_bridge is None else 1.0   # a camera read of the query plate starts < 1
    known = start
    for f in factors:
        known *= f if f is not None else 1.0
    aff = float(row.affinity) if row.affinity is not None else known
    carried = aff / known if known > 0 else 1.0
    rel = row.score / aff if aff > 0 else 0.0
    rank = next((i + 1 for i, r in enumerate(ctx.rows)
                 if (r.source_id, r.table, str(r.pk)) == (row.source_id, row.table, str(row.pk))), None)
    cut, n = cutoff(ctx), len(ctx.rows)
    shown = [f"{start:.2f} (how well the starting record matches)"] if start < 0.999 else []
    shown += [f"{f:.2f}" for f in factors if f is not None]
    if abs(carried - 1.0) > 0.01:
        shown.append(f"{carried:.2f} (carried from the record that led here)")
    direct = len(parts) == 1   # the record itself holds the plate / chassis / key that was asked for
    return {
        "path": " → ".join(steps) + (" — found directly from what you asked" if direct else ""),
        "affinity": (f"Affinity {aff:.2f} = " + " × ".join(shown) if shown else
                     f"Affinity {aff:.2f}: found directly, so nothing is discounted")
                    + ("; each weight includes the per-step discount" if weights else ""),
        "relevance": f"Relevance {rel:.2f} = score {row.score:.2f} ÷ affinity {aff:.2f}",
        "admitted": (f"Admitted because its score {row.score:.2f} ≥ the cut-off {cut:.2f} (the {_ordinal(n)} best score)"
                     if rank and cut > 0 else
                     f"Admitted because fewer than {max(n, 1)} records were found, so every one reached counts"
                     if rank else "Not part of the final answer"),
        "guarantee": guarantee(ctx, row.source_id, name),
        "numbers": {"score": row.score, "affinity": aff, "relevance": rel, "cutoff": cut, "rank": rank,
                    "weights": weights, "carried": carried},
    }
