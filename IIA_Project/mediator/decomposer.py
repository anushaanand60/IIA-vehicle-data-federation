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
                # LEFT: a vehicle with no matching lookup row (e.g. an orphaned owner_id) must
                # still come back, not disappear because of an INNER JOIN.
                join_clauses.append(f"LEFT JOIN {other_table} ON {jp}")
                seen_tables.add(other_table)

        # Push down aggregations (e.g., latest-wins) to SQL, but only when the sort column is a
        # plain number/ISO-ish string that every engine (SQLite/PostgreSQL/MySQL) orders the same
        # way. DD/MM/YYYY text (parse_ddmmyyyy) cannot be reassembled portably with substr()||...:
        # MySQL reads '||' as logical OR unless PIPES_AS_CONCAT is set, so an ORDER BY built that
        # way can silently pick the wrong row instead of failing loudly. For that case the
        # integrator fetches every row and picks the latest itself after parsing the date.
        agg = m.get("aggregate")
        if agg and agg.startswith("latest_by:"):
            sort_col = agg.split(":")[1]
            t = m.get("source_table", main_table)
            tf = m.get("transform_fn", "none")

            if tf != "parse_ddmmyyyy":
                order_by = f"ORDER BY {t}.{sort_col} DESC LIMIT 1"

    # Explicit column list instead of SELECT *: only the columns the registry actually maps for
    # this source, plus the identifier column, ever cross the wire. A column name that exists in
    # two joined tables (e.g. both carry an "id") is aliased as "table__col" for the non-main
    # table only, so the integrator keeps finding every global attribute's mapped source column
    # unambiguously while the main table's own spelling stays untouched.
    name_counts: Dict[str, int] = {}
    for m in mappings:
        attr = m.get("source_attr")
        if attr:
            name_counts[attr] = name_counts.get(attr, 0) + 1

    select_cols: List[str] = []
    seen_cols = set()

    def add_col(table: str, col: str) -> None:
        key = (table, col)
        if key in seen_cols:
            return
        seen_cols.add(key)
        if name_counts.get(col, 0) > 1 and table != main_table:
            select_cols.append(f"{table}.{col} AS {table}__{col}")
        else:
            select_cols.append(f"{table}.{col}")

    # Identifier column first so it is always present even if, unusually, no mapping names it.
    add_col(main_table, identifier_col)
    for m in mappings:
        t = m.get("source_table")
        c = m.get("source_attr")
        if t and c:
            add_col(t, c)

    cols_str = ", ".join(select_cols)
    joins_str = " ".join(join_clauses)
    plate_expr = f"UPPER(REPLACE(REPLACE({main_table}.{identifier_col}, '-', ''), ' ', ''))"
    plate_value = canonical_plate
    if str(source_meta.get("authority", "")).upper() == "OBSERVATIONAL":
        # Cameras OCR-misread plates (O/0, I/1 are the documented confusions): fold both the
        # column expression and the literal the same way so a misread capture still matches.
        # Plate formats fix which positions are letters vs digits, so no two valid plates fold
        # to the same string.
        plate_expr = f"REPLACE(REPLACE({plate_expr}, 'O', '0'), 'I', '1')"
        plate_value = canonical_plate.replace("O", "0").replace("I", "1")
    where_clause = f"{plate_expr} = '{plate_value}'"

    sql = f"SELECT {cols_str} FROM {main_table} {joins_str} WHERE {where_clause}".replace("  ", " ")
    if order_by:
        sql += f" {order_by}"

    return sql
