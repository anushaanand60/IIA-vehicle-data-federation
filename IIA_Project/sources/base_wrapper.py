import time
import sqlite3
import os
from datetime import datetime, timezone
from typing import Dict, Any, List
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

class QueryRequest(BaseModel):
    sql: str

def create_source_wrapper(source_id: str, dbms_name: str, db_path: str, whitelisted_tables: List[str]) -> FastAPI:
    app = FastAPI(title=f"{source_id} Source Wrapper", description=f"FastAPI Adapter for {source_id} ({dbms_name})")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def get_connection():
        if not os.path.exists(db_path):
            raise RuntimeError(f"Database file not found: {db_path}")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @app.get("/health")
    def health() -> Dict[str, Any]:
        try:
            conn = get_connection()
            conn.execute("SELECT 1").fetchone()
            conn.close()
            return {
                "source_id": source_id,
                "dbms": dbms_name,
                "up": True,
                "ts": datetime.now(timezone.utc).isoformat()
            }
        except Exception as e:
            return {
                "source_id": source_id,
                "dbms": dbms_name,
                "up": False,
                "error": str(e),
                "ts": datetime.now(timezone.utc).isoformat()
            }

    @app.get("/schema")
    def get_schema() -> Dict[str, Any]:
        try:
            conn = get_connection()
            cur = conn.cursor()
            tables_info = {}

            for table in whitelisted_tables:
                # PRAGMA table_info gives cid, name, type, notnull, dflt_value, pk
                cur.execute(f"PRAGMA table_info({table});")
                col_info = cur.fetchall()
                
                # PRAGMA foreign_key_list gives id, seq, table, from, to, on_update, on_delete, match
                cur.execute(f"PRAGMA foreign_key_list({table});")
                fk_info = cur.fetchall()
                fk_dict = {f["from"]: f["table"] for f in fk_info}

                columns = []
                for col in col_info:
                    c_name = col["name"]
                    c_type = col["type"]
                    is_pk = bool(col["pk"])
                    is_fk = c_name in fk_dict

                    # Fetch up to 20 sample distinct non-null values
                    try:
                        cur.execute(f"SELECT DISTINCT {c_name} FROM {table} WHERE {c_name} IS NOT NULL LIMIT 20;")
                        samples = [r[0] for r in cur.fetchall()]
                    except Exception:
                        samples = []

                    columns.append({
                        "name": c_name,
                        "type": c_type,
                        "is_pk": is_pk,
                        "is_fk": is_fk,
                        "fk_target": fk_dict.get(c_name),
                        "sample_values": samples
                    })

                tables_info[table] = {
                    "table_name": table,
                    "columns": columns
                }

            conn.close()
            return {
                "source_id": source_id,
                "dbms": dbms_name,
                "tables": tables_info
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Schema retrieval failed: {str(e)}")

    @app.post("/query")
    def execute_query(req: QueryRequest) -> Dict[str, Any]:
        sql = req.sql.strip()
        sql_upper = sql.upper()

        # Enforce SELECT-only security
        if not sql_upper.startswith("SELECT"):
            raise HTTPException(status_code=400, detail="Only SELECT queries are permitted.")
        disallowed = ["DROP ", "DELETE ", "INSERT ", "UPDATE ", "ALTER ", "TRUNCATE ", ";"]
        for d in disallowed:
            if d in sql_upper:
                raise HTTPException(status_code=400, detail=f"Disallowed token in SQL query: {d.strip()}")

        t_start = time.perf_counter()
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(sql)
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)

            return {
                "source_id": source_id,
                "rows": rows,
                "row_count": len(rows),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "elapsed_ms": elapsed_ms
            }
        except Exception as e:
            elapsed_ms = round((time.perf_counter() - t_start) * 1000, 2)
            raise HTTPException(status_code=500, detail=f"Query error: {str(e)}")

    return app
