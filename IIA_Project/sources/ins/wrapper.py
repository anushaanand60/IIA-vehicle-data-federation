"""INS wrapper: Insurance provider. MySQL on laptop 2, API on :8002.

Deployed engine: MySQL, selected with INS_DB_URL. Without it the local SQLite file is
served instead, so this module runs unchanged on one laptop or on its own (docs/DEPLOYMENT.md).
"""
from sources.defaults import sqlite_default
from sources.wrapper_template import from_env, serve

app = from_env(
    "INS",
    default_url=sqlite_default("INS"),
    default_dbms="MySQL",
    # CONTEXT-REPLACED (R1: publish the phase-3 tables so /query and /schema can reach them):
    # default_tables=["POLICY_RECORDS", "INSURERS"],  # design PDF section 3.2
    default_tables=["POLICY_RECORDS", "INSURERS", "POLICY_HOLDERS", "POLICY_PARTIES", "CLAIMS"],
    # CONTEXT-END
)

if __name__ == "__main__":
    serve(app, default_port=8002)
