import os
from sources.base_wrapper import create_source_wrapper

DB_PATH = os.path.join(os.path.dirname(__file__), "theft.db")
WHITELIST = ["CRIME_RECORDS"]

app = create_source_wrapper(
    source_id="THEFT",
    dbms_name="SQLite",
    db_path=DB_PATH,
    whitelisted_tables=WHITELIST
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
