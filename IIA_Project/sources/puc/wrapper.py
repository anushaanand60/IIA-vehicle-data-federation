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
    # CONTEXT-REPLACED (R1: publish the phase-3 tables so /query and /schema can reach them):
    # default_tables=["POLLUTION_CERT"],
    default_tables=["POLLUTION_CERT", "TEST_CENTRES", "ANALYSERS", "CERT_TESTS"],
    # CONTEXT-END
)

if __name__ == "__main__":
    serve(app, default_port=8005)
