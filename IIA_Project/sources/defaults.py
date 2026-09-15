"""Default database URL shared by the four wrappers.

The deployed engine per source is PostgreSQL (REG, CAM), MySQL (INS) and SQLite (THEFT); each
laptop selects it with <SOURCE_ID>_DB_URL (see docs/DEPLOYMENT.md). The default here is the local
SQLite file that scripts/load_source.py writes, so a fresh clone runs the whole federation on one
machine with no database server installed. Trade-off: forgetting to export <SOURCE_ID>_DB_URL
serves SQLite instead of failing, so /health reports the engine actually in use.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sqlite_default(source_id: str) -> str:
    """Same file scripts/load_source.py and data/load_all.py write, kept in one place."""
    folder = ROOT / "sources" / source_id.lower()
    return "sqlite:///" + (folder / (source_id.lower() + ".db")).as_posix()
