"""INS wrapper: Insurance provider. MySQL on laptop 2, API on :8002."""
from sources.wrapper_template import from_env, serve

app = from_env(
    "INS",
    default_url="mysql+pymysql://iia:iia@127.0.0.1:3306/insdb",
    default_dbms="mysql",
    default_tables=["POLICY_RECORDS", "INSURERS"],  # design PDF section 3.2
)

if __name__ == "__main__":
    serve(app, default_port=8002)
