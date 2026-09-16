"""Print the schema matcher's precision and recall against data/gold_mapping.json (design PDF section 6).

    python scripts/evaluate_matcher.py              # table, plus every false positive and miss
    python scripts/evaluate_matcher.py --json       # machine-readable
    python scripts/evaluate_matcher.py --theta 0.6  # try another acceptance threshold

Needs the source databases built (python scripts/load_source.py <SRC>, see docs/DEPLOYMENT.md);
no wrapper process has to be running -- schemas are read in-process via each wrapper's FastAPI app.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # runnable as a plain script

from mediator.matcher_eval import evaluate  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--theta", type=float, default=0.55, help="acceptance threshold (default 0.55, as in the PDF)")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    args = parser.parse_args(argv)
    report = evaluate(theta=args.theta)
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"matcher vs gold mapping (theta = {args.theta})\n")
    print(f"{'source':<8} {'tp':>3} {'fp':>3} {'fn':>3} {'precision':>10} {'recall':>7} {'f1':>6}")
    for source_id, r in [*report["per_source"].items(), ("OVERALL", report["overall"])]:
        print(f"{source_id:<8} {r['tp']:>3} {r['fp']:>3} {r['fn']:>3} {r['precision']:>10.2f} {r['recall']:>7.2f} {r['f1']:>6.2f}")
    for source_id, r in report["per_source"].items():
        for table, column, attr in r["false_positives"]:
            print(f"  {source_id:<6} false positive  {table}.{column} -> {attr}")
        for table, column, attr in r["missed"]:
            print(f"  {source_id:<6} missed          {table}.{column} -> {attr}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
