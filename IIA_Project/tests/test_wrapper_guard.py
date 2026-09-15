"""The /query guard (CLAUDE.md §5.1). Pure function, no database needed."""
import pytest

from sources.wrapper_template import QueryRejected, guard_sql

TABLES = {"policy_records"}


# --- what the executor actually generates must pass ----------------------------

@pytest.mark.parametrize("sql", [
    "SELECT vehicle_number, policy_until FROM policy_records WHERE vehicle_number = 'DL-01-AB-1234'",
    "select insurer from policy_records where vehicle_number in ('DL01AB1234','DLO1AB1Z34') order by policy_id desc",
    "SELECT p.insurer FROM policy_records p WHERE p.vehicle_number = 'dl 01 ab 1234' LIMIT 5",
    "SELECT a.insurer FROM policy_records AS a, policy_records b WHERE a.policy_id = b.policy_id",
    "SELECT\n\tcreated_at, update_count\nFROM POLICY_RECORDS",  # keywords inside identifiers are not keywords
])
def test_executor_shaped_queries_are_accepted(sql):
    assert guard_sql(sql, TABLES).upper().startswith("SELECT")


# --- injection-style strings the guard must reject -----------------------------

@pytest.mark.parametrize("sql, reason", [
    ("SELECT * FROM policy_records; DROP TABLE policy_records", "multiple statements"),
    ("SELECT 1 FROM policy_records WHERE 1=1; ", "multiple statements"),
    ("SELECT * FROM policy_records WHERE vehicle_number = '' OR 1=1 --", "comment"),
    ("SELECT * FROM policy_records /* hidden */ WHERE 1=1", "comment"),
    ("SELECT * FROM policy_records WHERE 1=1 # mysql comment", "comment"),
    ("DELETE FROM policy_records", "must start with select"),
    ("REPLACE INTO policy_records SELECT * FROM policy_records", "must start with select"),
    ("  WITH x AS (DELETE FROM policy_records RETURNING *) SELECT * FROM x", "must start with select"),
    ("SELECT insurer FROM policy_records UNION SELECT passwd FROM pg_shadow", "one select only"),
    ("SELECT * FROM policy_records WHERE vehicle_number IN (SELECT plate FROM secrets)", "one select only"),
    ("SELECT * INTO backup_table FROM policy_records", "forbidden keyword into"),
    ("SELECT load_file('/etc/passwd') FROM policy_records", "forbidden keyword load_file"),
    ("SELECT pg_sleep(30) FROM policy_records", "forbidden keyword pg_sleep"),
    ("SELECT * FROM policy_records WHERE EXISTS (TABLE pg_shadow)", "forbidden keyword table"),
    ("SELECT * FROM pg_catalog.pg_user", "not in the whitelist"),
    ("SELECT * FROM policy_records JOIN users ON 1=1", "not in the whitelist"),
    ("SELECT * FROM (pg_user CROSS JOIN policy_records)", "unsupported from item"),
    ("SELECT * FROM generate_series(1, 10)", "unsupported from item"),
    ("SELECT version()", "references no table"),
    ("SELECT * FROM policy_records\x00", "nul byte"),
    ("   ", "empty"),
])
def test_injection_style_strings_are_rejected_with_a_reason(sql, reason):
    with pytest.raises(QueryRejected) as exc:
        guard_sql(sql, TABLES)
    assert reason in str(exc.value).lower()


def test_design_pdf_example_with_replace_normalisation_is_accepted():
    # Design PDF §8.1: REPLACE and UPPER are string functions here, not the MySQL REPLACE statement.
    sql = ("SELECT vehicle_reg, policy_type, policy_start, policy_until, is_active, insurer_name "
           "FROM POLICY_RECORDS p JOIN INSURERS i ON p.insurer_id=i.insurer_id "
           "WHERE REPLACE(REPLACE(UPPER(vehicle_reg),'-',''),' ','') = 'DL01AB1234'")
    assert guard_sql(sql, {"POLICY_RECORDS", "INSURERS"}).endswith(" LIMIT 200")


def test_executor_left_join_is_accepted_when_both_tables_are_whitelisted():
    sql = ("SELECT VEHICLE_REGISTRATION.registration_no, OWNERS.full_name FROM VEHICLE_REGISTRATION "
           "LEFT JOIN OWNERS ON VEHICLE_REGISTRATION.owner_id = OWNERS.owner_id "
           "WHERE VEHICLE_REGISTRATION.registration_no = 'DL01AB1234'")
    guard_sql(sql, {"VEHICLE_REGISTRATION", "OWNERS"})
    with pytest.raises(QueryRejected, match="OWNERS"):
        guard_sql(sql, {"VEHICLE_REGISTRATION"})


def test_overlong_query_is_rejected():
    with pytest.raises(QueryRejected, match="longer than"):
        guard_sql("SELECT a FROM policy_records WHERE a IN (" + "'x'," * 5000 + "'x')", TABLES)


# --- LIMIT -----------------------------------------------------------------------

def test_limit_is_appended_when_absent():
    assert guard_sql("SELECT a FROM policy_records", TABLES).endswith(" LIMIT 200")


def test_existing_trailing_limit_is_left_alone():
    out = guard_sql("SELECT a FROM policy_records LIMIT 5", TABLES)
    assert out.upper().count("LIMIT") == 1 and out.endswith("LIMIT 5")


def test_row_limit_is_configurable():
    assert guard_sql("SELECT a FROM policy_records", TABLES, row_limit=7).endswith(" LIMIT 7")
