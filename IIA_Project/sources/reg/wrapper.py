"""REG wrapper: Regional Transport Office. PostgreSQL on laptop 1, API on :8001."""
from sources.wrapper_template import from_env, serve

app = from_env(
    "REG",
    default_url="postgresql+psycopg2://iia:iia@127.0.0.1:5432/regdb",
    default_dbms="postgresql",
    default_tables=["VEHICLE_REGISTRATION", "OWNERS"],  # design PDF section 3.1
)

if __name__ == "__main__":
    serve(app, default_port=8001)
