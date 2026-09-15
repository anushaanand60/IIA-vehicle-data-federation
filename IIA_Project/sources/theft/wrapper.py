"""THEFT wrapper: Police crime records. SQLite on laptop 3, API on :8003."""
from sources.wrapper_template import from_env, serve

app = from_env(
    "THEFT",
    default_url="sqlite:///data/theft.db",
    default_dbms="sqlite",
    default_tables=["CRIME_RECORDS"],  # design PDF section 3.3
)

if __name__ == "__main__":
    serve(app, default_port=8003)
