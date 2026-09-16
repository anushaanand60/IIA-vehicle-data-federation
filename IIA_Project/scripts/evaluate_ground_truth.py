"""Grade the federated mediator against the ground truth (design PDF section 4).

    python scripts/evaluate_ground_truth.py                    # every plate in data/ground_truth.csv
    python scripts/evaluate_ground_truth.py --limit 30         # 30 plates, taken in turn from each decision class
    python scripts/evaluate_ground_truth.py --json             # machine-readable
    python scripts/evaluate_ground_truth.py --start-wrappers   # serve the local source wrappers from this process

Needs the source databases built (python scripts/load_source.py <SRC>, see docs/DEPLOYMENT.md) and the
wrappers running (python run_system.py) unless --start-wrappers is given. Without data/ground_truth.csv
the expectations are rebuilt from the CSVs on the fly.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # runnable as a plain script

from mediator.ground_truth import build_ground_truth, grade, read_ground_truth, stratified_sample  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, help="grade at most N plates, taken in turn from each decision class")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    parser.add_argument("--start-wrappers", action="store_true", help="start the local source wrappers in this process first")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="replace")  # decisions contain an em dash; never crash on a narrow console

    truth_csv = ROOT / "data" / "ground_truth.csv"
    sample = stratified_sample(read_ground_truth(truth_csv) if truth_csv.exists() else build_ground_truth(ROOT), args.limit)
    if not sample:
        print("no plates to grade: generate the CSVs first, then python data/make_ground_truth.py", file=sys.stderr)
        return 1

    cluster = None
    if args.start_wrappers:
        from sources.server_manager import get_cluster
        cluster = get_cluster()
        cluster.start_all(include_puc=True)
        time.sleep(1)
    try:
        from mediator.core import run_global_query
        detail = run_global_query(sample[0]["plate_number"])["plan_trace"]["sources_detail"]
        down = sorted(source_id for source_id, result in detail.items() if result["status"] != "OK")
        if down:
            print(f"warning: {', '.join(down)} not answering, so those plates will grade as UNDETERMINED; "
                  "start the wrappers (python run_system.py) or pass --start-wrappers", file=sys.stderr)
        started = time.perf_counter()
        report = grade(sample, run_global_query)
        report["seconds"] = round(time.perf_counter() - started, 1)
    finally:
        if cluster is not None:
            cluster.stop_all()

    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(f"mediator vs ground truth ({report['total']} plates, {report['seconds']} s)\n")
    print(f"{'expected':<46} {'plates':>6} {'correct':>7}")
    for label, tally in report["per_class"].items():
        print(f"{label:<46} {tally['total']:>6} {tally['correct']:>7}")
    print(f"\naccuracy {report['correct']}/{report['total']} = {report['accuracy']:.1%}")
    for miss in report["wrong"]:
        print(f"  {miss['plate_number']:<12} expected {miss['expected']:<44} got {miss['got']}  ({miss['basis']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
