"""Change a source's data where it actually lives, to demonstrate freshness on stage.

Run this ON THE LAPTOP THAT OWNS THE SOURCE, then re-run the query on the mediator laptop.

    python scripts/mutate_source.py show   INS DL05CD9876
    python scripts/mutate_source.py renew  INS DL05CD9876 --until 31/12/2027
    python scripts/mutate_source.py expire INS DL05CD9876 --until 10/06/2026
    python scripts/mutate_source.py steal  THEFT DL01AB1234
    python scripts/mutate_source.py clear  THEFT DL01AB1234
    python scripts/mutate_source.py sight  CAM   DL01AB1234 --make Kia --model Seltos --colour Blue
    python scripts/mutate_source.py register REG DL77NEW0001 --owner "Rajesh Khanna"

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
    "CAM": ("PLATE_CAPTURES", "plate_id", "captured_at", None),
    "REG": ("VEHICLE_REGISTRATION", "registration_no", "registered_on", None),
}
# Which action belongs to which source, so the GUI and the CLI agree.
ACTIONS = {"renew": "INS", "expire": "INS", "steal": "THEFT", "clear": "THEFT",
           "sight": "CAM", "register": "REG"}


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


def sight(engine: Engine, plate: str, make: str, model: str, colour: str) -> None:
    """Record a fresh camera capture. CAM stores the plate as OCR read it, uppercase."""
    from datetime import datetime
    table, col, date_col, _ = SOURCES["CAM"]
    with engine.begin() as conn:
        next_id = (conn.execute(text(f"SELECT MAX(capture_id) FROM {table}")).scalar() or 0) + 1
        camera = conn.execute(text("SELECT camera_id FROM CAMERAS")).scalar()
        conn.execute(text(
            f"INSERT INTO {table} (capture_id, {col}, camera_id, {date_col}, observed_make, "
            f"observed_model, observed_colour, ocr_confidence) "
            f"VALUES (:i, :p, :c, :t, :mk, :md, :cl, 0.95)"),
            {"i": next_id, "p": canonical(plate), "c": camera,
             "t": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
             "mk": make, "md": model, "cl": colour})
    print(f"  inserted capture {next_id}: seen just now as {make} {model} {colour}")


def register(engine: Engine, plate: str, owner: str, make: str, model: str, colour: str) -> None:
    """Register a brand-new vehicle, owner row included."""
    table, col, date_col, _ = SOURCES["REG"]
    from datetime import date
    with engine.begin() as conn:
        owner_id = (conn.execute(text("SELECT MAX(owner_id) FROM OWNERS")).scalar() or 0) + 1
        reg_id = (conn.execute(text(f"SELECT MAX(registration_id) FROM {table}")).scalar() or 0) + 1
        conn.execute(text("INSERT INTO OWNERS (owner_id, full_name, address_line, city) "
                          "VALUES (:i, :n, 'Live Demo Address', 'New Delhi')"),
                     {"i": owner_id, "n": owner})
        conn.execute(text(
            f"INSERT INTO {table} (registration_id, {col}, owner_id, make, model, colour, "
            f"fuel_type, {date_col}, reg_status, rto_code) "
            f"VALUES (:r, :p, :o, :mk, :md, :cl, 'PETROL', :d, 'ACTIVE', :rto)"),
            {"r": reg_id, "p": canonical(plate), "o": owner_id, "mk": make, "md": model,
             "cl": colour, "d": date.today().isoformat(), "rto": canonical(plate)[:4]})
    print(f"  registered {canonical(plate)} to {owner} (owner {owner_id}, registration {reg_id})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["show", "renew", "expire", "steal", "clear",
                                           "sight", "register"])
    parser.add_argument("source", help=", ".join(SOURCES))
    parser.add_argument("plate")
    parser.add_argument("--until", default="31/12/2027", help="DD/MM/YYYY for renew/expire")
    parser.add_argument("--owner", default="Rajesh Khanna", help="owner name for register")
    parser.add_argument("--make", default="Maruti Suzuki")
    parser.add_argument("--model", default="Swift")
    parser.add_argument("--colour", default="White")
    parser.add_argument("--url", help="owner SQLAlchemy URL (default: <SRC>_ADMIN_URL, <SRC>_DB_URL, SQLite)")
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
    try:
        if args.action == "renew":
            renew(engine, args.plate, args.until)
        elif args.action == "expire":
            expire(engine, args.plate, args.until)
        elif args.action == "steal":
            steal(engine, args.plate)
        elif args.action == "clear":
            clear_theft(engine, args.plate)
        elif args.action == "sight":
            sight(engine, args.plate, args.make, args.model, args.colour)
        else:
            register(engine, args.plate, args.owner, args.make, args.model, args.colour)
    except Exception as exc:
        raise SystemExit("write failed: " + type(exc).__name__ + ": " + str(exc).splitlines()[0]
                         + "\n  a read-only account cannot write - pass --url with the owner account")
    print("after:")
    show(engine, source_id, args.plate)
    print("\nNow re-run the query on the mediator laptop - no restart needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
