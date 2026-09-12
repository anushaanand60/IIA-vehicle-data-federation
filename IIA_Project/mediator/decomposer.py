"""
Query Decomposer for Federated Mediator.
Translates canonical global queries into source-specific SQL with pushdown key predicates.
"""

from typing import Dict, Any, List
from mediator.catalog import get_mappings_for_source, get_source_catalog

def decompose_query(source_id: str, canonical_plate: str) -> str:
    """
    Constructs the source-specific SQL query for the given source and plate.
    Uses pushdown predicates with formatting normalization (handling spaces and hyphens).
    """
    catalog = get_source_catalog()
    source_meta = catalog.get(source_id, {})
    
    # identifier_attr tells us which column in the source uniquely identifies the vehicle
    identifier_col = source_meta.get("identifier_attr", "plate")
    mappings = get_mappings_for_source(source_id)

    # Generic metadata-driven decomposition
    tables = list(set(m["source_table"] for m in mappings if m.get("source_table")))
    if not tables:
        return f"SELECT * WHERE 1=0"

    # Identify the main table: the one that holds the identifier column
    main_table = tables[0]
    found_main = False
    for m in mappings:
        if m.get("global_attr") == "plate_number":
            main_table = m.get("source_table", main_table)
            identifier_col = m.get("source_attr", identifier_col)
            found_main = True
            break
            
    if not found_main:
        for m in mappings:
            if m.get("source_attr") == identifier_col:
                main_table = m.get("source_table", main_table)
                break

    join_clauses = []
    seen_tables = {main_table}
    order_by = ""

    for m in mappings:
        jp = m.get("join_path")
        if jp and "=" in jp:
            left, right = jp.split("=", 1)
            ltable = left.split(".")[0]
            rtable = right.split(".")[0]
            other_table = rtable if ltable == main_table else ltable
            if other_table not in seen_tables:
                join_clauses.append(f"JOIN {other_table} ON {jp}")
                seen_tables.add(other_table)
                
        # Push down aggregations (e.g., latest-wins) to SQL
        agg = m.get("aggregate")
        if agg and agg.startswith("latest_by:"):
            sort_col = agg.split(":")[1]
            t = m.get("source_table", main_table)
            tf = m.get("transform_fn", "none")
            
            if tf == "parse_ddmmyyyy":
                # Convert DD/MM/YYYY to YYYYMMDD string for SQLite sorting
                order_by = f"ORDER BY substr({t}.{sort_col}, 7, 4) || substr({t}.{sort_col}, 4, 2) || substr({t}.{sort_col}, 1, 2) DESC LIMIT 1"
            else:
                order_by = f"ORDER BY {t}.{sort_col} DESC LIMIT 1"

    cols_str = "*"
    joins_str = " ".join(join_clauses)
    where_clause = f"UPPER(REPLACE(REPLACE({main_table}.{identifier_col}, '-', ''), ' ', '')) = '{canonical_plate}'"

    sql = f"SELECT {cols_str} FROM {main_table} {joins_str} WHERE {where_clause}"
    if order_by:
        sql += f" {order_by}"
        
    return sql
