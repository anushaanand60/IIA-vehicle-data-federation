import os
from sources.base_wrapper import create_source_wrapper

DB_PATH = os.path.join(os.path.dirname(__file__), "ins.db")
WHITELIST = ["INSURERS", "POLICY_RECORDS"]

app = create_source_wrapper(
    source_id="INS",
    dbms_name="MySQL",
    db_path=DB_PATH,
    whitelisted_tables=WHITELIST
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
