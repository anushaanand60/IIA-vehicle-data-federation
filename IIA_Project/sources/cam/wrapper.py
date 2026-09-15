"""CAM wrapper: Road camera network. PostgreSQL on laptop 4, API on :8004."""
from sources.wrapper_template import from_env, serve

app = from_env(
    "CAM",
    default_url="postgresql+psycopg2://iia:iia@127.0.0.1:5432/camdb",
    default_dbms="postgresql",
    default_tables=["PLATE_CAPTURES", "CAMERAS"],  # design PDF section 3.4
)

if __name__ == "__main__":
    serve(app, default_port=8004)
