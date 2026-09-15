"""Create one source's tables on any DBMS and load its CSVs. Run this on the laptop that owns it.

    python scripts/load_source.py REG                          # uses REG_DB_URL, else a local SQLite file
    python scripts/load_source.py REG --url postgresql+psycopg2://iia:pw@127.0.0.1:5432/regdb
    python scripts/load_source.py THEFT --sqlite               # force the SQLite fallback
    python scripts/load_source.py INS --verify                 # load, then show the demo plates

The DDL is each source's own sources/<id>/schema.sql, unchanged: it is plain ANSI SQL, so the same
file creates the tables on SQLite, PostgreSQL and MySQL. Column types are read back from the live
database and CSV text is coerced to them, which is why no per-column type list appears here.

Destructive: the source's tables are dropped and rebuilt, so the load is repeatable.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.types import Integer, Numeric

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Which CSV feeds which table, parents first so foreign keys resolve on insert and on drop-reverse.
SOURCES: dict[str, list[tuple[str, str]]] = {
    "REG": [("reg_owners.csv", "OWNERS"), ("reg_vehicle_registration.csv", "VEHICLE_REGISTRATION")],
    "INS": [("ins_insurers.csv", "INSURERS"), ("ins_policy_records.csv", "POLICY_RECORDS")],
    "THEFT": [("theft_crime_records.csv", "CRIME_RECORDS")],
    "CAM": [("cam_cameras.csv", "CAMERAS"), ("cam_plate_captures.csv", "PLATE_CAPTURES")],
    "PUC": [("puc_records.csv", "POLLUTION_CERT")],
}
PLATE_COLUMN = {
    "REG": ("VEHICLE_REGISTRATION", "registration_no"),
    "INS": ("POLICY_RECORDS", "vehicle_reg"),
    "THEFT": ("CRIME_RECORDS", "vehicle_number"),
    "CAM": ("PLATE_CAPTURES", "plate_id"),
    "PUC": ("POLLUTION_CERT", "regn_number"),
}
DEMO_PLATES = ("DL01AB1234", "DL05CD9876", "HR26EF4455", "UP16GH1122", "MH12IJ7788")


def sqlite_url(source_id: str) -> str:
    """Same path their data/load_all.py uses, so the SQLite fallback stays drop-in compatible."""
    folder = ROOT / "sources" / source_id.lower()
    folder.mkdir(parents=True, exist_ok=True)
    return "sqlite:///" + (folder / (source_id.lower() + ".db")).as_posix()


def resolve_url(source_id: str, override: str | None, force_sqlite: bool) -> str:
    if override:
        return override
    env = os.environ.get(source_id + "_DB_URL")
    if env and not force_sqlite:
        return env
    return sqlite_url(source_id)


def ddl_statements(source_id: str) -> tuple[list[str], list[str]]:
    schema = ROOT / "sources" / source_id.lower() / "schema.sql"
    if not schema.exists():
        raise SystemExit("no schema file at " + str(schema))
    body = re.sub(r"--[^\n]*", "", schema.read_text(encoding="utf-8"))  # strip comments
    creates = [s.strip() for s in body.split(";") if s.strip()]
    tables = re.findall(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([A-Za-z_]\w*)", body, re.I)
    return creates, tables


def rebuild_schema(engine: Engine, source_id: str) -> list[str]:
    creates, tables = ddl_statements(source_id)
    cascade = " CASCADE" if engine.dialect.name == "postgresql" else ""
    with engine.begin() as conn:
        for table in reversed(tables):  # children before parents
            conn.execute(text("DROP TABLE IF EXISTS " + table + cascade))
        for statement in creates:
            conn.execute(text(statement))
    return tables


def coercers(engine: Engine, table: str) -> dict[str, Any]:
    """Map each column to a converter chosen from the type the database actually created."""
    out: dict[str, Any] = {}
    for col in inspect(engine).get_columns(table):
        kind = col["type"]
        if isinstance(kind, Integer):
            out[col["name"]] = int
        elif isinstance(kind, Numeric):
            out[col["name"]] = float
        else:
            out[col["name"]] = str
    return out


def load_table(engine: Engine, table: str, csv_path: Path) -> int:
    if not csv_path.exists():
        raise SystemExit("missing " + csv_path.name + " - generate the CSVs first (docs/DEPLOYMENT.md)")
    convert = {k.lower(): v for k, v in coercers(engine, table).items()}
    with csv_path.open(newline="", encoding="utf-8", errors="replace") as handle:
        reader = csv.DictReader(handle)
        columns = list(reader.fieldnames or [])
        rows = []
        for record in reader:
            row: dict[str, Any] = {}
            for column in columns:
                raw = (record.get(column) or "").strip()
                cast = convert.get(column.lower(), str)
                try:
                    row[column] = None if raw == "" else cast(raw)
                except ValueError:
                    row[column] = None  # a value the source stored dirty stays NULL, not a crash
            rows.append(row)
    if not rows:
        return 0
    placeholders = ", ".join(":" + c for c in columns)
    statement = text("INSERT INTO " + table + " (" + ", ".join(columns) + ") VALUES (" + placeholders + ")")
    with engine.begin() as conn:
        conn.execute(statement, rows)
    return len(rows)


def verify(engine: Engine, source_id: str) -> None:
    table, column = PLATE_COLUMN[source_id]
    print("\ndemo plates in " + table + "." + column + ":")
    with engine.connect() as conn:
        for plate in DEMO_PLATES:
            sql = text("SELECT COUNT(*) FROM " + table + " WHERE UPPER(REPLACE(REPLACE("
                       + column + ", '-', ''), ' ', '')) = :plate")
            count = conn.execute(sql, {"plate": plate}).scalar_one()
            note = "" if count else "   (absent - may be intentional for this source)"
            print("  " + plate + "  " + str(count) + " row(s)" + note)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="one of: " + ", ".join(SOURCES))
    parser.add_argument("--url", help="SQLAlchemy URL (default: <SOURCE>_DB_URL, else a local SQLite file)")
    parser.add_argument("--sqlite", action="store_true", help="ignore <SOURCE>_DB_URL, use the SQLite file")
    parser.add_argument("--verify", action="store_true", help="after loading, count the demo plates")
    args = parser.parse_args(argv)

    source_id = args.source.upper()
    if source_id not in SOURCES:
        raise SystemExit("unknown source " + source_id + "; expected one of " + ", ".join(SOURCES))
    url = resolve_url(source_id, args.url, args.sqlite)
    print(source_id + " -> " + re.sub(r"://[^@/]+@", "://***@", url))

    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        raise SystemExit("cannot connect: " + type(exc).__name__ + ": " + str(exc).splitlines()[0])

    tables = rebuild_schema(engine, source_id)
    print("created " + str(len(tables)) + " table(s): " + ", ".join(tables))
    total = 0
    for csv_name, table in SOURCES[source_id]:
        loaded = load_table(engine, table, ROOT / csv_name)
        total += loaded
        print("  " + table.ljust(22) + str(loaded).rjust(6) + " row(s) from " + csv_name)
    print(source_id + " loaded: " + str(total) + " row(s)")
    if args.verify:
        verify(engine, source_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
