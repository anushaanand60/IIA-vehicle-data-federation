"""REG wrapper: Regional Transport Office. PostgreSQL on laptop 1, API on :8001.

Deployed engine: PostgreSQL, selected with REG_DB_URL. Without it the local SQLite file is
served instead, so this module runs unchanged on one laptop or on its own (docs/DEPLOYMENT.md).
"""
from sources.defaults import sqlite_default
from sources.wrapper_template import from_env, serve

app = from_env(
    "REG",
    default_url=sqlite_default("REG"),
    default_dbms="PostgreSQL",
    default_tables=["VEHICLE_REGISTRATION", "OWNERS"],  # design PDF section 3.1
)

if __name__ == "__main__":
    serve(app, default_port=8001)
