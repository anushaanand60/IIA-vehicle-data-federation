"""Change a source's data where it actually lives, to demonstrate freshness on stage.

Run this ON THE LAPTOP THAT OWNS THE SOURCE, then re-run the query on the mediator laptop.

    python scripts/mutate_source.py show   INS DL05CD9876
    python scripts/mutate_source.py renew  INS DL05CD9876 --until 31/12/2027
    python scripts/mutate_source.py expire INS DL05CD9876 --until 10/06/2026
    python scripts/mutate_source.py steal  THEFT DL01AB1234
    python scripts/mutate_source.py clear  THEFT DL01AB1234

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

from sqlalchemy import Engine, create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.load_source import sqlite_url  # noqa: E402  same file the wrapper falls back to

# source -> (table, plate column, date column, active-flag column or None)
SOURCES = {
    "INS": ("POLICY_RECORDS", "vehicle_reg", "policy_until", "is_active"),
    "THEFT": ("CRIME_RECORDS", "vehicle_number", "reported_date", None),
}


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
    table, col, *_ = SOURCES[source_id]
    with engine.connect() as conn:
        rows = conn.execute(text(f"SELECT * FROM {table} WHERE {where_plate(col)}"),
                            {"plate": canonical(plate)}).mappings().all()
    if not rows:
        print("  (no rows for " + plate + " in " + table + ")")
        return 0
    for r in rows:
        print("  " + "  ".join(str(k) + "=" + str(v) for k, v in dict(r).items()))
    return len(rows)


def renew(engine: Engine, plate: str, until: str) -> None:
    table, col, date_col, active = SOURCES["INS"]
    with engine.begin() as conn:
        n = conn.execute(text(f"UPDATE {table} SET {date_col} = :until, {active} = 1 "
                              f"WHERE {where_plate(col)}"),
                         {"until": until, "plate": canonical(plate)}).rowcount
    print(f"  updated {n} policy row(s): {date_col} = {until}, {active} = 1")
    if n == 0:
        print("  nothing matched - is this the laptop that owns INS, and is the plate right?")


def expire(engine: Engine, plate: str, until: str) -> None:
    table, col, date_col, active = SOURCES["INS"]
    with engine.begin() as conn:
        n = conn.execute(text(f"UPDATE {table} SET {date_col} = :until, {active} = 0 "
                              f"WHERE {where_plate(col)}"),
                         {"until": until, "plate": canonical(plate)}).rowcount
    print(f"  updated {n} policy row(s): {date_col} = {until}, {active} = 0")


def steal(engine: Engine, plate: str) -> None:
    """Insert an open theft case, stored in THEFT's own lower-case spaced plate format."""
    import time
    table, col, date_col, _ = SOURCES["THEFT"]
    p = canonical(plate)
    spaced = f"{p[0:2]} {p[2:4]} {p[4:6]} {p[6:]}".lower() if len(p) == 10 else plate.lower()
    with engine.begin() as conn:
        next_id = (conn.execute(text(f"SELECT MAX(incident_id) FROM {table}")).scalar() or 0) + 1
        conn.execute(text(
            f"INSERT INTO {table} (incident_id, {col}, fir_no, {date_col}, incident_type, "
            f"stolen_flag, recovered_flag, case_status, police_station) "
            f"VALUES (:i, :p, :f, :d, 'THEFT', 'Y', 'N', 'OPEN', 'Live Demo PS')"),
            {"i": next_id, "p": spaced, "f": f"FIR{next_id:05d}/2026",
             "d": int(time.time())})
    print(f"  inserted incident {next_id}: {spaced!r} reported STOLEN, case OPEN")


def clear_theft(engine: Engine, plate: str) -> None:
    table, col, *_ = SOURCES["THEFT"]
    with engine.begin() as conn:
        n = conn.execute(text(f"DELETE FROM {table} WHERE {where_plate(col)} "
                              f"AND police_station = 'Live Demo PS'"),
                         {"plate": canonical(plate)}).rowcount
    print(f"  removed {n} demo incident row(s) (rows loaded from CSV are left alone)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["show", "renew", "expire", "steal", "clear"])
    parser.add_argument("source", help="INS or THEFT")
    parser.add_argument("plate")
    parser.add_argument("--until", default="31/12/2027", help="DD/MM/YYYY for renew/expire")
    parser.add_argument("--url", help="owner SQLAlchemy URL (default: <SRC>_ADMIN_URL, <SRC>_DB_URL, SQLite)")
    args = parser.parse_args(argv)

    source_id = args.source.upper()
    if source_id not in SOURCES:
        raise SystemExit("this tool handles " + ", ".join(SOURCES) + "; got " + source_id)
    if args.action in ("renew", "expire") and source_id != "INS":
        raise SystemExit(args.action + " applies to INS")
    if args.action in ("steal", "clear") and source_id != "THEFT":
        raise SystemExit(args.action + " applies to THEFT")

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
    try:
        if args.action == "renew":
            renew(engine, args.plate, args.until)
        elif args.action == "expire":
            expire(engine, args.plate, args.until)
        elif args.action == "steal":
            steal(engine, args.plate)
        else:
            clear_theft(engine, args.plate)
    except Exception as exc:
        raise SystemExit("write failed: " + type(exc).__name__ + ": " + str(exc).splitlines()[0]
                         + "\n  a read-only account cannot write - pass --url with the owner account")
    print("after:")
    show(engine, source_id, args.plate)
    print("\nNow re-run the query on the mediator laptop - no restart needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
