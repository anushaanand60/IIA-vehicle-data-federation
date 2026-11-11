"""CONTEXT-ADDED (R2/R3): measure context discovery against its baselines -> data/context_eval.json.

    python scripts/evaluate_context.py            # every scale found under data/scale/
    python scripts/evaluate_context.py --quick    # x1 only, fewer queries (the page's "re-run" button)

Headless; never binds a port. Every generator scale (x1 = the demo data, x10 / x100 from
scripts/phase3_scale.py) is served by the real wrapper factory in-process (mediator/context_eval_serve.py),
so this can run while the demo cluster is up. Experiments:
  1. data moved per approach (B0 closure, B1 top-n, B2 mediator walk, ours bounded) for story plates,
     scenario plates, free-text queries and hub seeds, at every scale;
  2. exactness vs the central oracle (closure re-ranked), then precision@n / nDCG@n on scenario grades;
  3. rank stability under +/-50 % edge-weight noise; 4. sensitivity over n and theta;
  5. threshold convergence (tau_n vs best frontier bound per round); 6. entity top-k (TA / TPUT vs naive);
  7. document retrieval ablation via mediator.doc_eval.evaluate_docs.
The page reads only the JSON; nothing here is cached between runs.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mediator import context_eval as ce  # noqa: E402
from mediator.context_eval_run import HUBS, STORY_PLATES, TEXT_QUERIES, Backend, Runner, call  # noqa: E402
from mediator.context_eval_serve import InProcessClient, build_apps, load_graph, scale_files  # noqa: E402

OUT = ROOT / "data" / "context_eval.json"
SCENARIOS = ROOT / "data" / "phase3_scenarios.json"


def load_backend() -> Backend:
    """Import each backend part on its own, so one missing module only removes its own experiment."""
    b = Backend()
    for attr, module, name in (("discover", "mediator.context_discovery", "discover"),
                               ("parse", "mediator.query_understanding", "parse"),
                               ("topk", "mediator.entity_topk", "topk"),
                               ("evaluate_docs", "mediator.doc_eval", "evaluate_docs"),
                               ("leaderboard", "mediator.analytics", "leaderboard_result")):
        try:
            setattr(b, attr, getattr(importlib.import_module(module), name))
        except (ImportError, AttributeError) as exc:
            b.missing.append(f"{module}.{name} ({type(exc).__name__})")
    return b


def scenario_queries(scenarios: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    seen, out = set(STORY_PLATES), []
    for sc in scenarios:
        for plate in ce.scenario_plates(sc)[:1]:
            if plate not in seen:
                seen.add(plate)
                label = str(sc.get("type") or sc.get("code") or sc.get("id") or "scenario")
                out.append({"id": plate, "kind": "scenario", "text": plate, "label": label})
    return out


def evaluate(quick: bool = False, scales_wanted: List[str] | None = None, n: int = 10, theta: float = 0.05,
             backend: Backend | None = None) -> Dict[str, Any]:
    t_start = time.perf_counter()
    graph = load_graph()
    scenarios = ce.load_scenarios(SCENARIOS)
    backend = backend or load_backend()
    runner = Runner(backend, graph, scenarios, n=n, theta=theta)
    if not scenarios:
        runner.notes.append("data/phase3_scenarios.json not found: no scenario plates, nDCG uses plate rows only")

    plates = [{"id": p, "kind": "story", "text": p, "label": "story vehicle"} for p in STORY_PLATES]
    plates += scenario_queries(scenarios)
    texts = [{"id": f"T{i + 1}", "kind": "text", "text": t, "label": "free text"} for i, t in enumerate(TEXT_QUERIES)]
    if quick:
        plates, texts = plates[:4] + [q for q in plates if q["kind"] == "scenario"][:3], texts[:3]

    all_scales = scale_files(graph)
    scales = {s: f for s, f in all_scales.items() if (not scales_wanted or s in scales_wanted)}
    if quick:
        scales = {s: f for s, f in scales.items() if s == "x1"}
    if len(all_scales) == 1:
        runner.notes.append("no data/scale/x10 or x100 yet (scripts/phase3_scale.py): only x1 measured")

    movement: List[Dict[str, Any]] = []
    exactness: List[Dict[str, Any]] = []
    stability: List[Dict[str, Any]] = []
    traces: List[Dict[str, Any]] = []
    hubs_used: List[Dict[str, Any]] = []
    sens: List[Dict[str, Any]] = []
    topk: List[Dict[str, Any]] = []
    docs: List[Dict[str, Any]] = []

    for scale, files in scales.items():
        print(f"[{scale}] serving {sorted(files)} in-process", flush=True)
        apps = build_apps(files, graph)
        make_client = lambda apps=apps: InProcessClient(apps)  # noqa: E731 - fresh counters per call
        queries = plates + (texts if scale == "x1" else [])
        for q in queries:
            print(f"[{scale}] {q['kind']:8s} {q['text']}", flush=True)
            # Free text is parsed once, rules only: the LLM would make the run slow and non-deterministic.
            qobj = call(backend.parse, q["text"], use_llm=False) if q["kind"] == "text" and backend.parse else q["text"]
            got = runner.measure(scale, q["id"], q["kind"], q["text"], make_client,
                                 lambda mode, client, qo=qobj: runner.discover(qo, mode, client),
                                 plate=q["text"] if q["kind"] != "text" else None, with_stability=scale == "x1")
            movement += got["movement"]; exactness += got["exactness"]; stability += got["stability"]; traces += got["traces"]
        for label, sid, table in HUBS:
            if sid not in files:
                continue
            value = runner.hub_value(make_client(), sid, table)
            if value is None:
                runner.notes.append(f"[{scale}] no {label}: no {table} row has more than one vehicle row at this scale")
                continue
            qid = f"HUB:{label}"
            hubs_used.append({"scale": scale, "label": label, "source": sid, "table": table, "value": str(value)})
            print(f"[{scale}] hub      {label} = {value}", flush=True)
            got = runner.measure(scale, qid, "hub", f"{label} ({value})", make_client,
                                 lambda mode, client, a=(label, sid, table, value): runner.hub_discover(*a, mode, client))
            movement += got["movement"]; exactness += got["exactness"]
        if scale == "x1":
            sens_plates = [q["text"] for q in plates if q["kind"] != "text"][: 3 if quick else 6]
            print("[x1] sensitivity over n and theta", flush=True)
            sens = runner.sensitivity(sens_plates, make_client)
            print("[x1] entity top-k", flush=True)
            topk = runner.topk([t["text"] for t in texts], make_client)
            print("[x1] document retrieval ablation", flush=True)
            docs = runner.documents(make_client)

    convergence = max(traces, key=lambda t: len(t["trace"]), default=None)
    if backend.discover is not None and not traces:
        runner.notes.append("discover() attaches no `trace` yet: the threshold-convergence chart is empty")
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "elapsed_s": round(time.perf_counter() - t_start, 1),
        "params": {"n": n, "theta": theta, "quick": quick, "scales": list(scales),
                   "serving": "in-process wrappers (sources.wrapper_template.create_app + TestClient); "
                              "latency excludes the network"},
        "available": {"discover": backend.discover is not None, "topk": backend.topk is not None,
                      "documents": backend.evaluate_docs is not None, "scenarios": bool(scenarios)},
        "modes": [{"mode": m, "label": l} for m, l in ce.MODES],
        "queries": plates + texts, "hubs": hubs_used,
        "movement": movement,
        "movement_summary": ce.summarise(movement, ("scale", "kind", "mode"),
                                         ("rows", "bytes", "round_trips", "latency_ms")),
        "exactness": exactness,
        "exactness_summary": ce.summarise(exactness, ("mode",), ("overlap_at_n", "precision_at_n", "ndcg_at_n")),
        "exactness_by_kind": ce.summarise(exactness, ("kind", "mode"), ("overlap_at_n", "precision_at_n", "ndcg_at_n")),
        "stability": stability,
        "stability_summary": ce.summarise([dict(s, all="all") for s in stability], ("all",),
                                          ("tau_mean", "tau_min", "overlap_mean")),
        "sensitivity": sens, "convergence": convergence,
        "pruning": ce.summarise([m for m in movement if m["mode"] == "bounded" and m.get("considered")],
                                ("scale",), ("pruned", "considered")),
        "topk": topk, "topk_summary": ce.summarise(topk, ("task", "method"), ("sorted_accesses", "random_accesses",
                                                                        "rows", "round_trips", "latency_ms")),
        "retrieval": docs,
        "notes": runner.notes,
    }


def main(argv: List[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--quick", action="store_true", help="x1 only, fewer queries")
    ap.add_argument("--scales", default="", help="comma list, e.g. x1,x10 (default: every scale found)")
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--theta", type=float, default=0.05)
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)
    result = evaluate(quick=args.quick, scales_wanted=[s for s in args.scales.split(",") if s], n=args.n,
                      theta=args.theta)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    print(f"wrote {args.out} in {result['elapsed_s']} s; notes: {len(result['notes'])}")
    for note in result["notes"]:
        print("  -", note)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
