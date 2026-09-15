"""THEFT wrapper: Police crime records. SQLite on laptop 3, API on :8003.

Deployed engine: SQLite, selected with THEFT_DB_URL. Without it the local SQLite file is
served instead, so this module runs unchanged on one laptop or on its own (docs/DEPLOYMENT.md).
"""
from sources.defaults import sqlite_default
from sources.wrapper_template import from_env, serve

app = from_env(
    "THEFT",
    default_url=sqlite_default("THEFT"),
    default_dbms="SQLite",
    default_tables=["CRIME_RECORDS"],  # design PDF section 3.3
)

if __name__ == "__main__":
    serve(app, default_port=8003)
