"""Run any SQL against THIS laptop's own source database -- no `python -c` quoting in PowerShell.

    python scripts\\sql.py INS "SELECT policy_id, vehicle_reg, policy_until FROM POLICY_RECORDS WHERE vehicle_reg = 'DL-05-CD-9876'"
    python scripts\\sql.py INS "UPDATE POLICY_RECORDS SET policy_until = '31/12/2027', is_active = 1 WHERE vehicle_reg = 'DL-05-CD-9876'"
    python scripts\\sql.py INS --tables
    python scripts\\sql.py INS --url "mysql+pymysql://root:pw@127.0.0.1:3306/insdb" "SELECT COUNT(*) FROM POLICY_RECORDS"

The URL is resolved exactly like scripts/mutate_source.py (one resolver): --url, env <ID>_ADMIN_URL,
env <ID>_DB_URL, then the same keys in the sources/<id>/laptop.env that scripts/serve.py saved, then
the local SQLite file.

This is the agency owner's own tool on their own laptop, so it is deliberately NOT guarded like the
wrapper's /admin/sql: any statement runs, with whatever rights the resolved account has.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Sequence

from sqlalchemy import Engine, create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.mutate_source import resolve_url  # noqa: E402  single URL resolver for owner tools
from scripts.serve import _mask  # noqa: E402

MAX_ROWS = 200
_READ_PREFIXES = ("SELECT", "WITH", "SHOW", "DESCRIBE", "DESC", "PRAGMA", "EXPLAIN")


def is_read(sql: str) -> bool:
    words = sql.strip().split(None, 1)
    return bool(words) and words[0].upper() in _READ_PREFIXES


def format_table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    cells = [[("NULL" if v is None else str(v)) for v in row] for row in rows]
    widths = [max([len(h)] + [len(r[i]) for r in cells]) for i, h in enumerate(headers)]
    line = lambda values: "  ".join(v.ljust(w) for v, w in zip(values, widths)).rstrip()  # noqa: E731
    return "\n".join([line(list(headers)), line(["-" * w for w in widths])] + [line(r) for r in cells])


def run_statement(engine: Engine, sql: str) -> None:
    if is_read(sql):
        # fetchall, not fetchmany: these are small demo databases, and it gives an exact "of N".
        with engine.connect() as conn:
            result = conn.execute(text(sql))
            headers = list(result.keys())
            rows = result.fetchall()
        shown = rows[:MAX_ROWS]
        print(format_table(headers, shown))
        if len(rows) > MAX_ROWS:
            print(f"(showing {MAX_ROWS} of {len(rows)})")
        print(f"{len(rows)} row(s)")
        return
    with engine.begin() as conn:
        affected = conn.execute(text(sql)).rowcount
    print(f"{affected} row(s) affected")


def list_tables(engine: Engine) -> None:
    insp = inspect(engine)
    for table in insp.get_table_names():
        pk = set(insp.get_pk_constraint(table).get("constrained_columns") or [])
        with engine.connect() as conn:
            count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
        print(f"\n{table}  ({count} row(s))")
        rows = [(c["name"], str(c["type"]), "yes" if c.get("nullable", True) else "no",
                 "yes" if c["name"] in pk else "") for c in insp.get_columns(table)]
        print(format_table(["column", "type", "nullable", "pk"], rows))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # Windows cp1252 pipes must not crash on odd characters
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="REG, INS, THEFT, CAM or PUC")
    parser.add_argument("sql", nargs="?", help="one SQL statement, in double quotes")
    parser.add_argument("--url", help="SQLAlchemy URL (default: same resolution as mutate_source.py)")
    parser.add_argument("--tables", action="store_true", help="list every table: columns, types, row count")
    args = parser.parse_args(argv)
    if not args.tables and not args.sql:
        parser.error("give a SQL statement or --tables")

    source_id = args.source.upper()
    url = resolve_url(source_id, args.url)
    try:
        engine = create_engine(url)
        print(f"{source_id} -> {_mask(url)}  (engine: {engine.dialect.name})")
        if args.tables:
            list_tables(engine)
        else:
            run_statement(engine, args.sql)
    except Exception as exc:
        message = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
        print(f"error: {type(exc).__name__}: {_mask(message)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
