import sys
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.append(BASE_DIR)

from mediator.catalog import get_mappings_for_source, init_meta_db

def decompose_query(source_id: str, canonical_plate: str) -> str:
    mappings = get_mappings_for_source(source_id)
    
    tables = list(set(m["source_table"] for m in mappings if m.get("source_table")))
    if not tables:
        return f"SELECT * WHERE 1=0"

    # Assume identifier is mapped to 'plate_number'
    main_table = tables[0]
    identifier_col = None
    for m in mappings:
        if m["global_attr"] == "plate_number":
            main_table = m["source_table"]
            identifier_col = m["source_attr"]
            break

    if not identifier_col:
        # Fallback if no plate_number mapping
        identifier_col = "plate_id" 

    select_cols = []
    join_clauses = []
    seen_tables = {main_table}
    
    order_by = ""

    for m in mappings:
        t = m["source_table"]
        c = m["source_attr"]
        select_cols.append(f"{t}.{c}")
        
        jp = m.get("join_path")
        if jp and "=" in jp:
            left, right = jp.split("=", 1)
            ltable = left.split(".")[0]
            rtable = right.split(".")[0]
            other_table = rtable if ltable == main_table else ltable
            if other_table not in seen_tables:
                join_clauses.append(f"JOIN {other_table} ON {jp}")
                seen_tables.add(other_table)
                
        agg = m.get("aggregate")
        if agg and agg.startswith("latest_by:"):
            sort_col = agg.split(":")[1]
            tf = m.get("transform_fn", "none")
            
            # Map transformation to SQL sorting
            if tf == "parse_ddmmyyyy":
                # Convert DD/MM/YYYY to YYYYMMDD for sorting
                order_by = f"ORDER BY substr({t}.{sort_col}, 7, 4) || substr({t}.{sort_col}, 4, 2) || substr({t}.{sort_col}, 1, 2) DESC LIMIT 1"
            else:
                order_by = f"ORDER BY {t}.{sort_col} DESC LIMIT 1"

    cols_str = "*" # "*, " + ", ".join(set(select_cols)) if select_cols else "*"
    joins_str = " ".join(join_clauses)
    where_clause = f"UPPER(REPLACE(REPLACE({main_table}.{identifier_col}, '-', ''), ' ', '')) = '{canonical_plate}'"

    sql = f"SELECT {cols_str} FROM {main_table} {joins_str} WHERE {where_clause}"
    if order_by:
        sql += f" {order_by}"
    return sql

def main():
    init_meta_db()
    for s_id in ["REG", "INS", "THEFT", "CAM", "PUC"]:
        sql = decompose_query(s_id, "DL01AB1234")
        print(f"--- {s_id} ---")
        print(sql)

if __name__ == "__main__":
    main()
