"""PUC wrapper: UC6 pollution authority, mock only (the real one is P3's), API on :8005."""
from sources.wrapper_template import from_env, serve

app = from_env(
    "PUC",
    default_url="sqlite:///sources/_mock/puc.db",
    default_dbms="sqlite",
    default_tables=["POLLUTION_CERT"],  # design PDF section 8.3
)

if __name__ == "__main__":
    serve(app, default_port=8005)
