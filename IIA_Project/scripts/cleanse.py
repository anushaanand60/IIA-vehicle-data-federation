"""Cleanse recorded camera data with Ripple-Down Rules (rubric 2) from the command line.

    python scripts/cleanse.py --scan [--limit N] [--json]
    python scripts/cleanse.py --propose 916
    python scripts/cleanse.py --apply 916 --actor alice
    python scripts/cleanse.py --undo 3
    python scripts/cleanse.py --teach --capture 920 --field observed_colour --under R3 \
        --condition '{"field": "value", "op": "eq", "value": "Golden"}' \
        --conclusion '{"action": "flag", "flag": "REPAINT_SUSPECT"}' --because "a real colour REG lacks"
    python scripts/cleanse.py --tree
    python scripts/cleanse.py --log

Every command reads the sources live through their wrappers; --apply and --undo write only through
the camera source's own /admin/sql. Non-interactive: everything comes from flags.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mediator import catalog, cleansing, lexicons  # noqa: E402
from mediator.rdr import RDRConflict, RDRTree  # noqa: E402

TEACH_FLAGS = ("capture", "field", "under", "condition", "conclusion", "because")


def _print_proposal(prop: cleansing.Proposal) -> None:
    print(f"{prop.source_id} record {prop.record_id}: plate {prop.record.get('plate_number')}")
    for a in prop.anomalies:
        print(f"  anomaly  [{a.severity:6}] {a.code:32} {a.field}={a.observed!r}  {json.dumps(a.evidence, default=str)}")
    for f in prop.fixes:
        trail = " > ".join(f"{p['rule']}{'+' if p['fired'] else '-'}" for p in f.path)
        print(f"  {f.field:16} {f.action:10} {f.before!r} -> {f.after!r}   by {f.rule}  [{trail}]")
        if f.action != "keep":
            print(f"  {'':16} because {f.because}")


def _print_scan(report: dict, show: int) -> None:
    if report.get("status") != "OK":
        print(f"scan refused: {report.get('error')}")
        return
    print(f"{report['source_id']}: {report['records_scanned']} records scanned, "
          f"{report['records_with_anomalies']} with anomalies")
    for title, key in (("anomalies before", "anomalies_before"), ("proposals", "proposals"),
                       ("would remain after applying", "remaining_after"),
                       ("remaining, by disposition", "remaining_by_disposition")):
        counts = report.get(key) or {}
        print(f"  {title}: {sum(counts.values())}")
        for code, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"    {code:32} {n}")
    for item in report["items"][:show]:
        changes = [f"{f['field']} {f['action']}" + (f" -> {f['after']}" if f["action"] == "set" else "")
                   + f" ({f['rule']})" for f in item["fixes"] if f["action"] != "keep"]
        print(f"  record {item['record_id']} {item['record'].get('plate_number')}: "
              f"{', '.join(a['code'] for a in item['anomalies']) or '-'}  =>  {'; '.join(changes) or 'keep'}")


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except (cleansing.CleansingError, RDRConflict, lexicons.SourceUnavailable, ValueError, KeyError) as exc:
        # an operator mistake or an unreachable source is a message and exit code 2, never a traceback
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    act = ap.add_mutually_exclusive_group(required=True)
    act.add_argument("--scan", action="store_true")
    act.add_argument("--propose", metavar="CAPTURE_ID")
    act.add_argument("--apply", metavar="CAPTURE_ID")
    act.add_argument("--undo", metavar="LOG_ID", type=int)
    act.add_argument("--teach", action="store_true")
    act.add_argument("--tree", action="store_true")
    act.add_argument("--log", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--show", type=int, default=20, help="--scan: list this many records")
    ap.add_argument("--actor", default="operator")
    ap.add_argument("--capture", help="--teach: the record whose case becomes the cornerstone")
    ap.add_argument("--field", help="--teach: global attribute (plate_number, observed_colour, ..., record)")
    ap.add_argument("--under", help="--teach: id of the rule that fired wrongly (or failed to fire)")
    ap.add_argument("--branch", choices=["except", "false"], default="except")
    ap.add_argument("--condition", help="--teach: JSON condition")
    ap.add_argument("--conclusion", help="--teach: JSON conclusion")
    ap.add_argument("--because", default="", help="--teach: one-sentence reason")
    ap.add_argument("--rules", default=str(cleansing.RULES_PATH), help="RDR rule file")
    ap.add_argument("--meta", help="meta.db to use (registry, catalog and CLEANSING_LOG)")
    args = ap.parse_args(argv)
    if args.teach and (missing := [f"--{f}" for f in TEACH_FLAGS if not getattr(args, f)]):
        ap.error(f"--teach needs {', '.join(missing)}")
    if args.meta:
        catalog.META_DB_PATH = args.meta
    rules = Path(args.rules)

    if args.tree:
        print("\n".join(RDRTree.load(rules).render()))
        return 0
    if args.log:
        for row in cleansing.log_entries():
            print(json.dumps(row, default=str))
        return 0
    if args.undo is not None:
        print(json.dumps(cleansing.undo(args.undo, actor=args.actor), indent=2))
        return 0
    ctx = cleansing.Context.build(rules_path=rules)
    if args.scan:
        report = cleansing.scan(limit=args.limit, ctx=ctx)
        if args.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            _print_scan(report, args.show)
        return 0 if report.get("status") == "OK" else 2
    if args.teach:
        result = cleansing.teach(args.capture, args.field, args.under, json.loads(args.condition),
                                 json.loads(args.conclusion), args.because, args.branch, args.actor, ctx, rules)
        print(json.dumps(result, indent=2, default=str))
        return 0
    record_id = args.propose or args.apply
    prop = cleansing.propose(ctx.record(record_id), ctx)
    _print_proposal(prop)
    if args.apply:
        for done in cleansing.apply(prop, actor=args.actor, ctx=ctx):
            print(f"  applied  log {done['log_id']}: {done['field']} {done['action']}  {done['sql'] or '(no write)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
