"""CAM wrapper: Road camera network. PostgreSQL on laptop 4, API on :8004.

Deployed engine: PostgreSQL, selected with CAM_DB_URL. Without it the local SQLite file is
served instead, so this module runs unchanged on one laptop or on its own (docs/DEPLOYMENT.md).
"""
from sources.defaults import sqlite_default
from sources.wrapper_template import from_env, serve

app = from_env(
    "CAM",
    default_url=sqlite_default("CAM"),
    default_dbms="PostgreSQL",
    default_tables=["PLATE_CAPTURES", "CAMERAS"],  # design PDF section 3.4
)

if __name__ == "__main__":
    serve(app, default_port=8004)
