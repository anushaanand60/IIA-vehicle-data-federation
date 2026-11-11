"""CONTEXT-ADDED (R2/R3): runs every measured experiment and returns the dict written to data/context_eval.json.

The backend is passed in (`Backend`), so tests drive this with fakes and the script with the real
modules; a backend part that does not exist yet becomes a note in the JSON, never a crash.
Each query gets a fresh counting client per mode, so one approach's traffic is never billed to another.
"""
from __future__ import annotations

import inspect
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from mediator import context_eval as ce

STORY_PLATES = ["DL01AB1234", "DL05CD9876", "DL09KL3321", "HR26EF4455", "UP16GH1122", "MH12IJ7788", "KA05MN9999"]
TEXT_QUERIES = [
    "stolen white Creta Gurgaon", "expired insurance Hyundai seen in Noida", "theft claim soon after the policy started",
    "red Swift with an open FIR in Delhi", "car sold just before a theft claim", "silver Venue seen by a camera in Gurgaon",
    "black Scorpio with a pending challan", "diesel car that failed a PUC test in Delhi", "accused person sharing a mobile number",
    "recovered stolen Honda City",
]
# Hub seeds: one value many rows hang off. (label, source, hub table) - the FK into it comes from the graph.
HUBS = [("busy owner", "REG", "OWNERS"), ("busy camera", "CAM", "CAMERAS"), ("busy police station", "THEFT", "STATIONS")]
SENS_N = (5, 10, 20)
SENS_THETA = (0.02, 0.05, 0.1)


@dataclass
class Backend:
    discover: Optional[Callable[..., Any]] = None
    parse: Optional[Callable[..., Any]] = None
    topk: Optional[Callable[..., Any]] = None
    leaderboard: Optional[Callable[..., Any]] = None     # analytics.leaderboard_result(k, client, method)
    evaluate_docs: Optional[Callable[..., Any]] = None
    missing: List[str] = field(default_factory=list)


def call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Call fn with only the keyword arguments its signature accepts (other workstreams may lag the contract)."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return fn(*args, **kwargs)
    if not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
        kwargs = {k: v for k, v in kwargs.items() if k in params}
    return fn(*args, **kwargs)


def accepts(fn: Optional[Callable[..., Any]], name: str) -> bool:
    try:
        return fn is not None and name in inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False


class Runner:
    def __init__(self, backend: Backend, graph: Dict[str, Any], scenarios: List[Dict[str, Any]],
                 n: int = 10, theta: float = 0.05, log: Callable[[str], None] = print) -> None:
        self.b, self.graph, self.scenarios, self.n, self.theta, self.log = backend, graph, scenarios, n, theta, log
        self.notes: List[str] = [f"not available yet: {m}" for m in backend.missing]
        self.skipped_modes: set = set()

    # ---------------------------------------------------------------- one discover call
    def discover(self, query: Any, mode: str, client: Any, n: Optional[int] = None,
                 theta: Optional[float] = None, seeds: Optional[Dict[str, Any]] = None) -> Optional[Any]:
        if self.b.discover is None or mode in self.skipped_modes:
            return None
        kw: Dict[str, Any] = {"n": n or self.n, "theta": theta or self.theta, "mode": mode, "client": client}
        if seeds is not None:
            kw["seeds"] = seeds
        try:
            return call(self.b.discover, query, **kw)
        except (ValueError, NotImplementedError) as exc:
            self.skipped_modes.add(mode)
            self.notes.append(f"mode {mode!r} skipped: {exc}")
        except Exception as exc:  # one failing query must not lose the whole evaluation
            self.notes.append(f"discover({mode}) failed on {str(query)[:40]!r}: {type(exc).__name__}: {exc}")
            self.log(traceback.format_exc(limit=2))
        return None

    # ---------------------------------------------------------------- hub seeds
    def hub_value(self, client: Any, sid: str, hub_table: str) -> Optional[Any]:
        """The hub row with the most vehicles hanging off it, found through the FK the graph declares."""
        src = self.graph.get("sources", {}).get(sid, {})
        edge = next((e for e in src.get("edges", []) if e.get("to") == hub_table
                     and src.get("tables", {}).get(e.get("from"), {}).get("plate")), None)
        if not edge:
            return None
        rows = client.query(sid, f"SELECT {edge['col']} AS k, COUNT(*) AS c FROM {edge['from']} "
                                 f"GROUP BY {edge['col']} ORDER BY c DESC LIMIT 1")
        # A 'hub' with one row hanging off it is no hub: report it rather than measure a single vehicle twice.
        return rows[0].get("k") if rows and int(rows[0].get("c") or 0) > 1 else None

    def hub_discover(self, label: str, sid: str, table: str, value: Any, mode: str, client: Any) -> Optional[Any]:
        """discover(seeds=...) when the backend takes seeds; otherwise the hub's own wrapper /context directly."""
        seeds = {"pks": {table: [value]}}
        if accepts(self.b.discover, "seeds"):
            from mediator.context_types import ParsedQuery  # noqa: PLC0415
            return self.discover(ParsedQuery(text=f"{label} {value}"), mode, client, seeds=seeds)
        if mode not in ("bounded", "closure"):
            return None
        t0 = time.perf_counter()
        body = client.post(sid, "/context", {"seeds": seeds, "n": self.n, "theta": self.theta, "window": None,
                                             "keywords": [], "mode": mode})
        if body is None:
            return None
        rows = [{**r, "source_id": sid} for r in body.get("rows", [])]
        return {"rows": rows, "stats": client.stats(), "elapsed_ms": (time.perf_counter() - t0) * 1000,
                "complete": True}

    # ---------------------------------------------------------------- one query, every mode
    def measure(self, scale: str, qid: str, kind: str, text: str, make_client: Callable[[], Any],
                run: Callable[[str, Any], Optional[Any]], plate: Optional[str] = None,
                with_stability: bool = False) -> Dict[str, List[Dict[str, Any]]]:
        out: Dict[str, List[Dict[str, Any]]] = {"movement": [], "exactness": [], "stability": [], "traces": []}
        results: Dict[str, Any] = {}
        for mode, _label in ce.MODES:
            client = make_client()
            res = run(mode, client)
            if res is None:
                continue
            results[mode] = res
            mv = ce.movement(res, ce._get(res, "stats") or client.stats())
            pr = ce.pruned_share(ce._get(res, "trace"))
            out["movement"].append({"scale": scale, "query_id": qid, "kind": kind, "text": text, "mode": mode,
                                    **mv, "pruned": pr[0] if pr else None, "considered": pr[1] if pr else None})
            if mode == "bounded" and ce._get(res, "trace"):
                out["traces"].append({"scale": scale, "query_id": qid, "text": text, "trace": list(ce._get(res, "trace"))})
        closure = results.get("closure")
        if closure is not None:
            oracle_rows = list(ce._get(closure, "rows") or [])
            pool = [r for res in results.values() for r in (ce._get(res, "rows") or [])]
            grades = ce.grades_for(plate, pool, self.scenarios, self.graph) if plate else {}
            for mode, res in results.items():
                keys = [ce.row_key(r) for r in ce.ranked(ce._get(res, "rows") or [])]
                out["exactness"].append({"scale": scale, "query_id": qid, "kind": kind, "mode": mode,
                                         "overlap_at_n": ce.overlap_at_n(ce._get(res, "rows") or [], oracle_rows, self.n),
                                         "precision_at_n": ce.precision_at_n(keys, grades, self.n),
                                         "ndcg_at_n": ce.ndcg_at_n(keys, grades, self.n)})
            if with_stability and oracle_rows:
                out["stability"].append({"query_id": qid, "kind": kind, **ce.stability(oracle_rows, self.n)})
        return out

    # ---------------------------------------------------------------- sensitivity, top-k, documents
    def sensitivity(self, plates: Sequence[str], make_client: Callable[[], Any]) -> List[Dict[str, Any]]:
        out = []
        for plate in plates:
            closure = self.discover(plate, "closure", make_client(), n=max(SENS_N))
            oracle = list(ce._get(closure, "rows") or []) if closure is not None else []
            for n in SENS_N:
                for theta in SENS_THETA:
                    client = make_client()
                    res = self.discover(plate, "bounded", client, n=n, theta=theta)
                    if res is None:
                        continue
                    mv = ce.movement(res, ce._get(res, "stats") or client.stats())
                    out.append({"query_id": plate, "n": n, "theta": theta, "rows": mv["rows"], "bytes": mv["bytes"],
                                "round_trips": mv["round_trips"], "latency_ms": mv["latency_ms"],
                                "overlap_at_n": ce.overlap_at_n(ce._get(res, "rows") or [], oracle, n) if oracle else None})
        return ce.summarise(out, ("n", "theta"), ("rows", "bytes", "round_trips", "latency_ms", "overlap_at_n"))

    def topk(self, texts: Sequence[str], make_client: Callable[[], Any], k: int = 10) -> List[Dict[str, Any]]:
        if self.b.topk is None:
            return []
        methods = ["ta", "tput"] if accepts(self.b.topk, "method") else ["ta"]
        if methods == ["ta"]:
            self.notes.append("entity top-k has no method= switch yet: only the default (TA) is measured")
        jobs: List[Tuple[str, str, Callable[[str, Any], Any]]] = []
        for text in texts:
            query = call(self.b.parse, text, use_llm=False) if self.b.parse else text
            jobs.append(("free text", text, lambda m, c, q=query: call(self.b.topk, q, k=k, client=c, method=m)))
        if self.b.leaderboard is not None:   # the fleet-wide most-suspicious list runs the same top-k
            jobs.append(("fleet leaderboard", "most suspicious vehicles",
                         lambda m, c: call(self.b.leaderboard, k=k, client=c, method=m)))
        out = []
        for task, text, job in jobs:
            for method in methods:
                client = make_client()
                try:
                    res = job(method, client)
                except Exception as exc:
                    self.notes.append(f"top-k ({task}, {method}) failed on {text!r}: {type(exc).__name__}: {exc}")
                    continue
                trips = sum(int(ce._get(s, "round_trips", 0) or 0) for s in client.stats())
                out.append({"task": task, "text": text, "method": method, "sorted_accesses": ce._get(res, "sorted_accesses", 0),
                            "random_accesses": ce._get(res, "random_accesses", 0), "rows": ce._get(res, "rows_shipped", 0),
                            "baseline_rows": ce._get(res, "baseline_rows", 0), "round_trips": trips,
                            "depth": ce._get(res, "stopped_at_depth", 0), "latency_ms": ce._get(res, "elapsed_ms", 0)})
                if method == methods[0]:
                    out.append({"task": task, "text": text, "method": "naive", "sorted_accesses": None, "random_accesses": None,
                                "rows": ce._get(res, "baseline_rows", 0), "baseline_rows": ce._get(res, "baseline_rows", 0),
                                "round_trips": None, "depth": None, "latency_ms": None})
        return out

    def documents(self, make_client: Callable[[], Any]) -> List[Dict[str, Any]]:
        if self.b.evaluate_docs is None:
            return []
        try:
            raw = call(self.b.evaluate_docs, client=make_client(), k=10)
        except Exception as exc:
            self.notes.append(f"document evaluation failed: {type(exc).__name__}: {exc}")
            return []
        return normalise_doc_eval(raw)


def normalise_doc_eval(raw: Any) -> List[Dict[str, Any]]:
    """Accept {mode: {metric: v}}, {"modes": {...}} or [{"mode": ..., metric: v}]; keys -> ndcg_at_10 / mrr / ..."""
    if isinstance(raw, dict) and isinstance(raw.get("modes"), (dict, list)):
        raw = raw["modes"]
    items = ([{"mode": m, **(v if isinstance(v, dict) else {})} for m, v in raw.items()]
             if isinstance(raw, dict) else list(raw or []))
    out = []
    for item in items:
        if not isinstance(item, dict) or "mode" not in item:
            continue
        row = {"mode": str(item["mode"])}
        for key, val in item.items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                row[key.lower().replace("@", "_at_").replace("-", "_")] = round(float(val), 4)
        out.append(row)
    return out
