"""CONTEXT-ADDED (R2): POST /context on every wrapper -- in-DBMS, scored, bounded context discovery.

The wrapper loads its own section of data/context_graph.json (env CONTEXT_GRAPH overrides the path),
measures edge fan-out live (cached <= 60 s: statistics, never data), generates ONE `WITH RECURSIVE`
statement (sources/context_sql.py) and runs it on the read-only connection under the same statement
timeout as /query. Ranking, pruning (theta), diversification (per-parent cap) and the bound all happen
inside the DBMS; only the top n rows (+ bridge-column hints) cross the network.

Row columns are fetched with one `SELECT * ... WHERE pk IN (...)` per table *after* ranking, inside the
wrapper: heterogeneous columns cannot share one recursive CTE portably, and this second read is
wrapper<->DB local, so it moves nothing extra over the network.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Literal, Optional, Tuple

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from sources.context_sql import (build_context_sql, fanout_sql, pk_expr, usable_graph, weights_from_counts,
                                 Dialect, ident)
from sources.wrapper_template import (_FORBIDDEN, _TIMEOUT_MARKERS, QueryRejected, _deadline, _error,
                                      _reachable, _short, _wire)

GRAPH_PATH = Path(__file__).resolve().parents[1] / "data" / "context_graph.json"
STATS_TTL_S = 60.0
_CTES = {"step", "seed", "walk", "best", "node", "scored", "ranked", "elig", "hint", "hint_top"}
# The /query guard forbids UNION (exfiltration via a second SELECT); the recursive CTE needs UNION ALL.
# Safe here because this SQL is generated from metadata, never typed by a client.
_FORBIDDEN_CTX = re.compile(_FORBIDDEN.pattern.replace("UNION|", ""), re.I)
_stats_cache: Dict[Tuple[Any, ...], Tuple[float, List[Tuple[float, float]]]] = {}


class ContextRequest(BaseModel):
    seeds: Dict[str, Any] = {}
    n: int = 10
    theta: float = 0.05
    window: Optional[List[str]] = None
    keywords: List[str] = []
    mode: Literal["bounded", "closure"] = "bounded"
    max_hops: Optional[int] = None
    per_parent: Optional[int] = None
    max_hints: int = 500
    hint_min: float = 0.0
    count_only: bool = False   # CONTEXT-ADDED (R2: B0 baseline size without shipping it) -- use with mode="closure"
    explain: bool = False

    @field_validator("window")
    @classmethod
    def _iso_window(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is not None and (len(v) != 2 or not all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) for s in v)):
            raise ValueError("window must be [YYYY-MM-DD, YYYY-MM-DD]")
        return v

    @field_validator("n")
    @classmethod
    def _n(cls, v: int) -> int:
        if not 1 <= v <= 10_000:
            raise ValueError("n must be within 1..10000")
        return v


def load_graph(path: Optional[str] = None) -> Dict[str, Any]:
    p = Path(path or os.environ.get("CONTEXT_GRAPH") or GRAPH_PATH)
    return json.loads(p.read_text(encoding="utf-8"))


def guard_context_sql(sql: str, whitelist: Iterable[str]) -> str:
    """Defence in depth for the generated statement: read-only WITH RECURSIVE, whitelisted tables only.

    String literals are blanked first (values travel as bind parameters anyway), then the same
    keyword/comment/statement checks as the /query guard apply, minus UNION (needed by the CTE).
    """
    bare = re.sub(r"'(?:[^']|'')*'", "''", sql)
    allowed = {t.lower() for t in whitelist} | _CTES
    checks = [(not re.match(r"\s*WITH\s+RECURSIVE\b", bare, re.I), "context SQL must start with WITH RECURSIVE"),
              (";" in bare, "multiple statements are not allowed (';')"),
              (any(c in bare for c in ("--", "/*", "#")), "SQL comments are not allowed")]
    for failed, reason in checks:
        if failed:
            raise QueryRejected(reason)
    if m := _FORBIDDEN_CTX.search(bare):
        raise QueryRejected(f"forbidden keyword {m.group(1).upper()}")
    for m in re.finditer(r"\b(?:FROM|JOIN)\s+([A-Za-z_]\w*)", bare, re.I):
        if m.group(1).lower() not in allowed:
            raise QueryRejected(f"table {m.group(1)!r} is not in the whitelist")
    return sql


def _edge_weights(conn: Any, key: Tuple[Any, ...], edges: List[Dict[str, Any]], w0: float
                  ) -> List[Tuple[float, float]]:
    hit = _stats_cache.get(key)
    if hit and time.monotonic() - hit[0] < STATS_TTL_S:
        return hit[1]
    counts = dict(conn.execute(text(fanout_sql(edges))).mappings().one()) if edges else {}
    weights = weights_from_counts(edges, counts, w0)
    _stats_cache[key] = (time.monotonic(), weights)
    return weights


def run_context(conn: Any, graph: Dict[str, Any], source_id: str, whitelist: List[str],
                req: Dict[str, Any]) -> Dict[str, Any]:
    """Everything /context does after the connection is open. Raises SQLAlchemyError / QueryRejected."""
    published = {t.lower() for t in whitelist}
    present = [t for t in inspect(conn).get_table_names() if t.lower() in published]
    tables, edges = usable_graph(graph, source_id, present)
    if not tables:
        return {"rows": [], "hints": [], "bound": 0.0, "bound_cut": 0.0, "sql": None}
    key = (source_id, str(conn.engine.url), tuple(sorted(tables)), json.dumps(edges, sort_keys=True))
    weights = _edge_weights(conn, key, edges, float(graph.get("params", {}).get("w0", 1.0)))
    dialect = conn.engine.dialect.name
    sql, params = build_context_sql(graph, source_id, dialect, req, weights, present)
    if not sql:  # no seed applies to this source: an empty answer, not an error
        return {"rows": [], "hints": [], "bound": 0.0, "bound_cut": 0.0, "sql": None}
    guard_context_sql(sql, whitelist)
    out = [dict(r) for r in conn.execute(text(sql), params).mappings().all()]
    # CONTEXT-ADDED (R2: count_only answer -- one integer + the closure's distinct bridge values, no rows)
    if req.get("count_only"):
        values: Dict[str, List[str]] = {}
        for r in out:
            if r["kind"] == "value":
                values.setdefault(r["battr"], []).append(str(r["bval"]))
        total = next((int(r["n_rows"] or 0) for r in out if r["kind"] == "count"), 0)
        return {"rows": [], "hints": [], "bound": 0.0, "bound_cut": 0.0, "closure_rows": total,
                "bridge_values": {k: sorted(v) for k, v in values.items()}, "sql": sql if req.get("explain") else None}
    # CONTEXT-END
    top = [r for r in out if r["kind"] == "row"]
    full = _fetch_rows(conn, Dialect(dialect), tables, top)
    rows = [{"table": r["t"], "pk": _native_pk(tables[r["t"]], full.get((r["t"], r["pk"])), r["pk"]),
             "pk_key": r["pk"], "row": full.get((r["t"], r["pk"]), {}), "score": float(r["score"]),
             "affinity": float(r["aff"]), "path": [p for p in str(r["path"]).split(">") if p],
             "hops": int(r["hops"]), "seed": r["skey"], "bridge": _hint_value(r)} for r in top]
    rows.sort(key=lambda r: (-r["score"], r["table"], r["pk_key"]))
    hints = [{"table": r["t"], "pk": r["pk"], "affinity": float(r["aff"]), "attr": r["battr"],
              "value": r["bval"], "hops": int(r["hops"]), "seed": r["skey"]} for r in out if r["kind"] == "hint"]
    first = top[0] if top else {}
    u_cut = float(first.get("u_cut") or 0.0)
    u_pruned, u_hint = float(first.get("u_pruned") or 0.0), float(first.get("u_hint") or 0.0)
    u_div = float(first.get("u_div") or 0.0)
    # bound: nothing unsent scores higher. bound_cut: the same, among rows the per-parent cap allows
    # (what the mediator's stop rule needs, since diversification is part of the answer's definition).
    return {"rows": rows, "hints": hints, "bound": max(u_cut, u_div, u_pruned, u_hint), "bound_cut": u_cut,
            "bound_diversity": u_div,
            "bound_pruned": u_pruned, "bound_hints": u_hint,
            "edge_weights": [{"from": e["from"], "to": e["to"], "col": e["col"], "fwd": round(wf, 6), "rev": round(wr, 6)}
                             for e, (wf, wr) in zip(edges, weights)],
            "sql": sql if req.get("explain") else None, "params": params if req.get("explain") else None}


def _hint_value(r: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    return {"attr": r["battr"], "value": r["bval"]} if r.get("battr") and r.get("bval") is not None else None


def _fetch_rows(conn: Any, d: Dialect, tables: Dict[str, Any], top: List[Dict[str, Any]]
                ) -> Dict[Tuple[str, str], Dict[str, Any]]:
    """Second, wrapper-local read: full columns of the <= n selected rows, one statement per table."""
    by_table: Dict[str, List[str]] = {}
    for r in top:
        by_table.setdefault(r["t"], []).append(r["pk"])
    full: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for t, pks in by_table.items():
        binds = {f"k{i}": v for i, v in enumerate(pks)}
        pkx = pk_expr(d, tables[t], "x")
        sql = f"SELECT {pkx} AS ctx_pk_key, x.* FROM {ident(t)} x WHERE {pkx} IN ({', '.join(':' + k for k in binds)})"
        for row in conn.execute(text(sql), binds).mappings():
            rec = {k: _wire(v) for k, v in row.items() if k != "ctx_pk_key"}
            full[(t, str(row["ctx_pk_key"]))] = rec
    return full


def _native_pk(spec: Dict[str, Any], row: Optional[Dict[str, Any]], key: str) -> Any:
    cols = spec["pk"] if isinstance(spec["pk"], list) else [spec["pk"]]
    if not row:
        return key
    lowered = {k.lower(): v for k, v in row.items()}
    vals = [lowered.get(c.lower(), key) for c in cols]
    return vals[0] if len(vals) == 1 else vals


def mount_context(app: FastAPI, *, source_id: str, whitelist: List[str], engine_fn: Callable[[], Engine],
                  statement_timeout_s: float) -> None:
    """Add POST /context to a wrapper built by sources.wrapper_template.create_app."""

    @app.post("/context")
    def context(req: ContextRequest) -> JSONResponse:
        try:
            graph = load_graph()
        except (OSError, ValueError) as exc:
            return _error(503, f"context graph unavailable: {exc}")
        if source_id not in graph.get("sources", {}):
            return _error(404, f"{source_id} is not described in the context graph")
        started = time.perf_counter()
        try:
            with engine_fn().connect() as conn, _deadline(conn, statement_timeout_s):
                body = run_context(conn, graph, source_id, whitelist, req.model_dump())
        except (QueryRejected, ValueError, KeyError) as exc:
            return _error(400, f"context request rejected: {exc}")
        except SQLAlchemyError as exc:
            if any(marker in _short(exc).lower() for marker in _TIMEOUT_MARKERS):
                return _error(504, f"statement timeout after {statement_timeout_s}s: {_short(exc)}")
            if not _reachable(app.state.engine):
                return _error(503, f"database unreachable: {_short(exc)}")
            return _error(400, f"database rejected the context query: {_short(exc)}")
        body.update(source_id=source_id, row_count=len(body["rows"]), hint_count=len(body["hints"]),
                    elapsed_ms=int((time.perf_counter() - started) * 1000))
        return JSONResponse(content=body)
