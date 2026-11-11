"""CONTEXT-ADDED (R2): the dialect layer of the /context SQL generator (sources/context_sql.py).

Only scalar functions differ between SQLite, PostgreSQL and MySQL 8; WITH RECURSIVE, window functions,
CASE, COALESCE and joins are the same ANSI SQL:2003 everywhere. Each method returns a SQL fragment.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def ident(name: str) -> str:
    if not isinstance(name, str) or not IDENT.match(name):
        raise ValueError(f"not a plain SQL identifier: {name!r}")
    return name


class Dialect:
    """The handful of scalar functions that differ; everything else is ANSI SQL:2003."""

    def __init__(self, name: str) -> None:
        name = {"postgres": "postgresql", "mariadb": "mysql"}.get(name.lower(), name.lower())
        if name not in ("sqlite", "postgresql", "mysql"):
            raise ValueError(f"unsupported dialect {name!r}")
        self.name = name

    def txt(self, e: str, width: Optional[int] = None) -> str:
        # MySQL types a recursive CTE from its anchor only, so anchor strings need an explicit width.
        if self.name == "mysql":
            return f"CAST({e} AS CHAR({width}))" if width else f"CAST({e} AS CHAR)"
        return f"CAST({e} AS TEXT)"

    def dbl(self, e: str) -> str:
        return f"CAST({e} AS {dict(sqlite='REAL', postgresql='DOUBLE PRECISION', mysql='DOUBLE')[self.name]})"

    def cat(self, *parts: str) -> str:
        # MySQL reads || as OR unless PIPES_AS_CONCAT is set; CONCAT is unambiguous there.
        return f"CONCAT({', '.join(parts)})" if self.name == "mysql" else "(" + " || ".join(parts) + ")"

    def pos(self, hay: str, needle: str) -> str:
        return f"strpos({hay}, {needle})" if self.name == "postgresql" else f"INSTR({hay}, {needle})"

    def least(self, a: str, b: str) -> str:
        return f"MIN({a}, {b})" if self.name == "sqlite" else f"LEAST({a}, {b})"

    def greatest(self, a: str, b: str) -> str:
        return f"MAX({a}, {b})" if self.name == "sqlite" else f"GREATEST({a}, {b})"

    def canon(self, col: str) -> str:
        """Plate/chassis canonical form = uppercase alphanumerics, computed by the source itself."""
        if self.name == "postgresql":
            return f"UPPER(REGEXP_REPLACE({self.txt(col)}, '[^A-Za-z0-9]', '', 'g'))"
        if self.name == "mysql":
            return f"UPPER(REGEXP_REPLACE({self.txt(col)}, '[^A-Za-z0-9]', ''))"
        e = self.txt(col)  # SQLite has no regex replace built in: strip the separators agencies use
        for ch in (" ", "-", ".", "/"):
            e = f"REPLACE({e}, '{ch}', '')"
        return f"UPPER({e})"

    def day(self, col: str, fmt: str) -> str:
        """A comparable calendar day from each agency's own date encoding."""
        s, d = self.txt(col), self.name
        if fmt in ("iso_date", "iso_ts"):
            return {"sqlite": f"date(substr({s}, 1, 10))",
                    "postgresql": f"to_date(substr({s}, 1, 10), 'YYYY-MM-DD')",
                    "mysql": f"STR_TO_DATE(SUBSTRING({s}, 1, 10), '%Y-%m-%d')"}[d]
        if fmt == "ddmmyyyy":
            return {"sqlite": f"date(substr({s}, 7, 4) || '-' || substr({s}, 4, 2) || '-' || substr({s}, 1, 2))",
                    "postgresql": f"to_date({s}, 'DD/MM/YYYY')",
                    "mysql": f"STR_TO_DATE({s}, '%d/%m/%Y')"}[d]
        if fmt == "epoch":  # UTC on purpose: FROM_UNIXTIME / bare to_timestamp follow the session time zone
            return {"sqlite": f"date({col}, 'unixepoch')",
                    "postgresql": f"CAST(to_timestamp({col}) AT TIME ZONE 'UTC' AS DATE)",
                    "mysql": f"DATE(DATE_ADD('1970-01-01', INTERVAL {col} SECOND))"}[d]
        raise ValueError(f"unknown time format {fmt!r}")

    def day_param(self, bind: str) -> str:
        return bind if self.name == "sqlite" else f"CAST({bind} AS DATE)"


def pk_cols(spec: Dict[str, Any]) -> List[str]:
    pk = spec["pk"]
    return [ident(c) for c in (pk if isinstance(pk, list) else [pk])]


def pk_expr(d: Dialect, spec: Dict[str, Any], alias: str) -> str:
    cols = pk_cols(spec)
    if len(cols) == 1:
        return d.txt(f"{alias}.{cols[0]}")
    parts: List[str] = []
    for i, c in enumerate(cols):
        parts += ([] if i == 0 else ["'|'"]) + [d.txt(f"{alias}.{c}")]
    return d.cat(*parts)
