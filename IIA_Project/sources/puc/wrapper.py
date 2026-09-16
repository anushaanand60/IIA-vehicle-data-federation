"""PUC wrapper: Pollution-Under-Control certificates (UC6, the fifth source). SQLite, API on :8005.

Same template as the other four sources, so it exposes /health, /schema, /query and the
opt-in-by-default /admin/* endpoints. PUC_DB_URL selects another engine.
"""
from sources.defaults import sqlite_default
from sources.wrapper_template import from_env, serve

app = from_env(
    "PUC",
    default_url=sqlite_default("PUC"),
    default_dbms="SQLite",
    default_tables=["POLLUTION_CERT"],
)

if __name__ == "__main__":
    serve(app, default_port=8005)
