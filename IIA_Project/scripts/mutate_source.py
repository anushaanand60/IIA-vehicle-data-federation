"""Change a source's data where it actually lives, to demonstrate freshness on stage.

Run this ON THE LAPTOP THAT OWNS THE SOURCE, then re-run the query on the mediator laptop.

    python scripts/mutate_source.py show          INS   DL05CD9876
    python scripts/mutate_source.py renew         INS   DL05CD9876 --until 31/12/2027
    python scripts/mutate_source.py expire        INS   DL05CD9876 --until 10/06/2026
    python scripts/mutate_source.py add_policy    INS   DL77NEW0001 --policy-type COMPREHENSIVE
    python scripts/mutate_source.py delete_policies INS DL05CD9876
    python scripts/mutate_source.py steal         THEFT DL01AB1234
    python scripts/mutate_source.py shred         THEFT DL01AB1234
    python scripts/mutate_source.py clear         THEFT DL01AB1234
    python scripts/mutate_source.py delete_incidents THEFT DL01AB1234
    python scripts/mutate_source.py sight         CAM   DL01AB1234 --make Kia --model Seltos --colour Blue
    python scripts/mutate_source.py delete_sightings CAM DL01AB1234
    python scripts/mutate_source.py register      REG   DL77NEW0001 --owner "Rajesh Khanna"
    python scripts/mutate_source.py set_status    REG   DL77NEW0001 --status SUSPENDED
    python scripts/mutate_source.py unregister    REG   DL77NEW0001
    python scripts/mutate_source.py issue         PUC   DL01AB1234 --emission-norm BS-VI
    python scripts/mutate_source.py revoke        PUC   DL01AB1234

Any parameter an action takes can also be set generically with repeated `--param name=value`,
which is handy for a parameter that has no dedicated flag below.

The action menu itself is not duplicated here: it is imported from
sources/wrapper_template.ADMIN_ACTIONS, the same table the wrapper's own /admin/mutate and
/admin/actions endpoints use, so the CLI and the GUI change data exactly the same way and can
never drift apart. This script just resolves an owner connection and calls the same function.

Why this exists alongside live_update.py. That module writes to sources/<id>/<id>.db directly, so it
only works when the source is the local SQLite file. Once INS is MySQL on another laptop, the
sidebar mutator edits a file nobody queries and the decision never changes. This goes through
SQLAlchemy to the same database the wrapper serves, so it works on SQLite, PostgreSQL and MySQL.

Writing needs an owner account; <SOURCE>_DB_URL usually holds the read-only one the wrapper uses.
Pass --url with the owner account, or set <SOURCE>_ADMIN_URL.

Plate matching uses the same normalisation as the decomposer -- UPPER(REPLACE(REPLACE(col,'-',''),
' ','')) -- so it finds the row whatever spelling that source stores.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.load_source import sqlite_url  # noqa: E402  same file the wrapper falls back to
from sources.wrapper_template import ADMIN_ACTIONS  # noqa: E402  single source of truth for actions

# source -> (table, plate column), used only by `show` -- the actions themselves come from
# ADMIN_ACTIONS and already know their own tables/columns.
SOURCES = {
    "REG": ("VEHICLE_REGISTRATION", "registration_no"),
    "INS": ("POLICY_RECORDS", "vehicle_reg"),
    "THEFT": ("CRIME_RECORDS", "vehicle_number"),
    "CAM": ("PLATE_CAPTURES", "plate_id"),
    "PUC": ("POLLUTION_CERT", "regn_number"),
}
# Which action belongs to which source, derived from the same table the wrapper serves, so the
# GUI and this CLI can never name an action for the wrong source.
ACTIONS: dict[str, str] = {name: source_id for source_id, actions in ADMIN_ACTIONS.items()
                           for name in actions}
# One optional --flag per distinct parameter name across every action (e.g. --until, --owner,
# --make, --status, --emission-norm, ...), generated from the same table instead of hand-listed,
# so a new action's params get a CLI flag for free.
_PARAM_HELP: dict[str, str] = {p["name"]: p["help"] for actions in ADMIN_ACTIONS.values()
                               for spec in actions.values() for p in spec["params"]}


def resolve_url(source_id: str, override: str | None) -> str:
    if override:
        return override
    for key in (source_id + "_ADMIN_URL", source_id + "_DB_URL"):
        if os.environ.get(key):
            return os.environ[key]
    return sqlite_url(source_id)


def canonical(plate: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", plate).upper()


def where_plate(column: str) -> str:
    return f"UPPER(REPLACE(REPLACE({column}, '-', ''), ' ', '')) = :plate"


def show(engine: Engine, source_id: str, plate: str) -> int:
    table, col = SOURCES[source_id]
    with engine.connect() as conn:
        rows = conn.execute(text(f"SELECT * FROM {table} WHERE {where_plate(col)}"),
                            {"plate": canonical(plate)}).mappings().all()
    if not rows:
        print("  (no rows for " + plate + " in " + table + ")")
        return 0
    for r in rows:
        print("  " + "  ".join(str(k) + "=" + str(v) for k, v in dict(r).items()))
    return len(rows)


def build_params(args: argparse.Namespace) -> dict[str, Any]:
    """Merge the per-parameter flags with any --param key=value overrides (which win)."""
    params: dict[str, Any] = {}
    for name in _PARAM_HELP:
        value = getattr(args, name, None)
        if value is not None:
            params[name] = value
    for item in args.param or []:
        if "=" not in item:
            raise SystemExit(f"--param must be key=value, got {item!r}")
        key, _, value = item.partition("=")
        params[key] = value
    return params


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["show", *sorted(ACTIONS)],
                        help="show, or one of: " + ", ".join(sorted(ACTIONS)))
    parser.add_argument("source", help=", ".join(SOURCES))
    parser.add_argument("plate")
    parser.add_argument("--url", help="owner SQLAlchemy URL (default: <SRC>_ADMIN_URL, <SRC>_DB_URL, SQLite)")
    parser.add_argument("--param", action="append", metavar="key=value",
                        help="set any action parameter generically, repeatable")
    for name, help_text in sorted(_PARAM_HELP.items()):
        parser.add_argument(f"--{name.replace('_', '-')}", dest=name, default=None, help=help_text)
    args = parser.parse_args(argv)

    source_id = args.source.upper()
    if source_id not in SOURCES:
        raise SystemExit("this tool handles " + ", ".join(SOURCES) + "; got " + source_id)
    expected = ACTIONS.get(args.action)
    if expected and source_id != expected:
        raise SystemExit(args.action + " applies to " + expected + ", not " + source_id)

    url = resolve_url(source_id, args.url)
    print(source_id + " -> " + re.sub(r"://[^@/]+@", "://***@", url))
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        raise SystemExit("cannot connect: " + type(exc).__name__ + ": " + str(exc).splitlines()[0])

    if args.action == "show":
        show(engine, source_id, args.plate)
        return 0

    print("before:")
    show(engine, source_id, args.plate)
    fn = ADMIN_ACTIONS[source_id][args.action]["fn"]
    params = build_params(args)
    try:
        result = fn(engine, args.plate, params)
    except Exception as exc:
        raise SystemExit("write failed: " + type(exc).__name__ + ": " + str(exc).splitlines()[0]
                         + "\n  a read-only account cannot write - pass --url with the owner account")
    print(f"  {result['detail']}  (rows_affected={result['rows_affected']})")
    print("after:")
    show(engine, source_id, args.plate)
    print("\nNow re-run the query on the mediator laptop - no restart needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
