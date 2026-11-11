"""CONTEXT-ADDED (R2): baseline B2 -- what a typical mediator does: walk the FK graph itself, one /query per edge.

No ranking inside the DBMS, no bounds: seed rows by plate, then for every hop and every FK edge (both
directions) one `SELECT * ... WHERE col IN (values)` through the phase-1 /query guard; rows are scored in
Python with the same rules (mediator/context_scoring.py) and the top n kept. Bridge values (plate, chassis)
are followed once into the other sources. Its rows / round trips are the comparison point for our design.
The /query 200-row cap can truncate a hop; that is recorded in the trace rather than hidden.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from mediator.context_scoring import (bridge_members, canon, edge_weight, noisy_plate_sources, ocr_variants,
                                      params_of, pk_key, relevance)
from mediator.context_types import ContextResult, ContextRow, ParsedQuery

Node = Tuple[str, str]  # (table, pk_key)


def _lit(v: Any) -> Optional[str]:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    s = str(v)
    return None if any(b in s for b in (";", "--", "/*", "#", "\x00")) else "'" + s.replace("'", "''") + "'"


def _get(row: Dict[str, Any], col: str) -> Any:
    return {k.lower(): v for k, v in row.items()}.get(col.lower())


def _in(values: List[Any]) -> str:
    return ", ".join(x for x in (_lit(v) for v in sorted(set(values), key=str)) if x)


class _B2:
    def __init__(self, graph: Dict[str, Any], client: Any, keywords: List[str], window: Optional[List[str]],
                 max_hops: int) -> None:
        self.graph, self.client, self.kw, self.window, self.max_hops = graph, client, keywords, window, max_hops
        self.prm = params_of(graph)
        self.found: Dict[Tuple[str, str, str], ContextRow] = {}
        self.truncated = 0
        self._weights: Dict[str, List[Tuple[float, float]]] = {}

    def q(self, sid: str, sql: str) -> List[Dict[str, Any]]:
        rows = self.client.query(sid, sql)
        self.truncated += len(rows) >= 200
        return rows

    def weights(self, sid: str) -> List[Tuple[float, float]]:
        if sid in self._weights:
            return self._weights[sid]
        w0, out = float(self.prm.get("w0", 1.0)), []
        for e in self.graph["sources"][sid].get("edges", []):
            if e.get("w") is not None:
                out.append((edge_weight(e["w"], 1, w0),) * 2)
                continue
            r = self.q(sid, f"SELECT COUNT({e['col']}) AS n, COUNT(DISTINCT {e['col']}) AS d FROM {e['from']}")
            fan = (r[0].get("n") or 0) / max(1, r[0].get("d") or 0) if r else 1.0
            out.append((edge_weight(None, 1, w0), edge_weight(None, fan, w0)))
        self._weights[sid] = out
        return out

    def walk(self, sid: str, seeds: Dict[str, List[str]], start_aff: float, via: Optional[str]) -> None:
        tables, gamma = self.graph["sources"][sid]["tables"], float(self.prm.get("gamma_hops", 1.0))
        frontier: Dict[str, List[Tuple[Dict[str, Any], float, List[str]]]] = {}
        for kind, vals in seeds.items():
            for t, spec in tables.items():
                if spec.get(kind) and _in(vals):
                    col = spec[kind]
                    sql = f"SELECT * FROM {t} WHERE UPPER(REPLACE(REPLACE({col}, '-', ''), ' ', '')) IN ({_in(vals)})"
                    frontier.setdefault(t, []).extend((r, start_aff, []) for r in self.q(sid, sql))
        seen: set = set()
        edges, weights = self.graph["sources"][sid].get("edges", []), self.weights(sid)
        for hop in range(self.max_hops + 1):
            nxt: Dict[str, List[Tuple[Dict[str, Any], float, List[str]]]] = {}
            for t, items in frontier.items():
                for row, aff, path in items:
                    node = (t, pk_key(tables[t], row))
                    if node in seen:
                        continue
                    seen.add(node)
                    self.keep(sid, t, row, aff, path + [f"{t}#{node[1]}"], hop, via)
            if hop == self.max_hops:
                break
            for e, (wf, wr) in zip(edges, weights):
                for src_t, src_c, dst_t, dst_c, w in ((e["from"], e["col"], e["to"], e["to_col"], wf),
                                                      (e["to"], e["to_col"], e["from"], e["col"], wr)):
                    best: Dict[str, Tuple[float, List[str]]] = {}
                    for row, aff, path in frontier.get(src_t, []):
                        v = _get(row, src_c)
                        if v is not None and aff * w * gamma > best.get(str(v), (-1.0, []))[0]:
                            best[str(v)] = (aff * w * gamma, path + [f"{src_t}#{pk_key(tables[src_t], row)}"])
                    if not best:
                        continue
                    raw = [v for row, _, _ in frontier[src_t] if (v := _get(row, src_c)) is not None]
                    if not _in(raw):
                        continue
                    for r in self.q(sid, f"SELECT * FROM {dst_t} WHERE {dst_c} IN ({_in(raw)})"):
                        key = str(_get(r, dst_c))
                        if key in best:
                            nxt.setdefault(dst_t, []).append((r, best[key][0], best[key][1]))
            frontier = nxt

    def keep(self, sid: str, t: str, row: Dict[str, Any], aff: float, path: List[str], hops: int,
             via: Optional[str]) -> None:
        spec = self.graph["sources"][sid]["tables"][t]
        score = aff * relevance(spec, row, self.kw, self.window, self.prm)
        key = (sid, t, pk_key(spec, row))
        if key not in self.found or score > self.found[key].score:
            self.found[key] = ContextRow(sid, t, key[2], row, score, path, hops, via, 1.0)


def run_mediator_baseline(pq: ParsedQuery, graph: Dict[str, Any], client: Any, n: int = 10, theta: float = 0.05,
                          max_hops: Optional[int] = None) -> ContextResult:
    t0 = time.perf_counter()
    from mediator.context_discovery import _keywords
    b = _B2(graph, client, _keywords(pq), list(pq.time_window) if pq.time_window else None,
            int(max_hops if max_hops is not None else params_of(graph).get("max_hops", 3)))
    noisy = set(noisy_plate_sources(graph))
    for sid in graph["sources"]:
        plates = [v for p in pq.plates for v in (ocr_variants(p) if sid in noisy else [canon(p)])]
        b.walk(sid, {"plate": plates}, 1.0, None)
    members = bridge_members(graph)
    values = {k: {canon(_get(r.row, c)) for r in list(b.found.values()) for s, t, c in members.get(k, [])
                  if (r.source_id, r.table) == (s, t)} for k in ("plate", "chassis")}
    for sid in graph["sources"]:  # follow every bridge value once (no bounds: that is the point of a baseline)
        b.walk(sid, {k: sorted(v - {""} - set(pq.plates)) for k, v in values.items()}, 1.0, "bridge")
    rows = sorted(b.found.values(), key=lambda r: (-r.score, r.source_id, r.table, str(r.pk)))
    stats = client.stats()
    res = ContextResult(query=pq, rows=rows[:n], stats=stats, rounds=2,
                        complete=all(s.status == "OK" for s in stats), elapsed_ms=(time.perf_counter() - t0) * 1000)
    res.trace = [{"round": 1, "final": True, "queries": sum(s.round_trips for s in stats),  # type: ignore[attr-defined]
                  "rows_shipped": sum(s.rows_shipped for s in stats), "truncated_hops": b.truncated,
                  "expanded": 0, "pruned": 0}]
    return res
