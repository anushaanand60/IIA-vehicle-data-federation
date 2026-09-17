"""scripts/sql.py: the agency owner's own SQL console for this laptop's database (tmp SQLite only)."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from scripts import sql as sql_script


@pytest.fixture()
def db_url(tmp_path) -> str:
    url = f"sqlite:///{(tmp_path / 'ins.db').as_posix()}"
    with create_engine(url).begin() as conn:
        conn.execute(text("CREATE TABLE POLICY_RECORDS (policy_id INTEGER PRIMARY KEY, "
                          "vehicle_reg TEXT NOT NULL, policy_until TEXT)"))
        conn.execute(text("INSERT INTO POLICY_RECORDS VALUES (1, 'DL-05-CD-9876', '12/07/2026'), "
                          "(2, 'DL-01-AB-1234', '01/01/2030')"))
    return url


def test_select_prints_masked_url_engine_and_rows(db_url, capsys):
    rc = sql_script.main(["INS", "--url", db_url,
                          "SELECT policy_id, vehicle_reg, policy_until FROM POLICY_RECORDS ORDER BY policy_id"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "ins.db" in out and "sqlite" in out.lower()
    assert "vehicle_reg" in out and "DL-05-CD-9876" in out and "01/01/2030" in out
    assert "2 row(s)" in out


def test_update_prints_affected_and_persists(db_url, capsys):
    rc = sql_script.main(["INS", "--url", db_url,
                          "UPDATE POLICY_RECORDS SET policy_until = '31/12/2027' WHERE vehicle_reg = 'DL-05-CD-9876'"])
    assert rc == 0
    assert "1 row(s) affected" in capsys.readouterr().out
    with create_engine(db_url).connect() as conn:
        value = conn.execute(text("SELECT policy_until FROM POLICY_RECORDS WHERE policy_id = 1")).scalar_one()
    assert value == "31/12/2027"


def test_tables_lists_columns_and_counts(db_url, capsys):
    rc = sql_script.main(["INS", "--url", db_url, "--tables"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "POLICY_RECORDS" in out and "2 row(s)" in out
    for column in ("policy_id", "vehicle_reg", "policy_until"):
        assert column in out


def test_bad_sql_exits_1_without_traceback(db_url, capsys):
    rc = sql_script.main(["INS", "--url", db_url, "SELECT * FROM NO_SUCH_TABLE"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "Traceback" not in captured.out + captured.err
    assert "NO_SUCH_TABLE" in captured.out + captured.err


def test_select_caps_at_200_rows(tmp_path, capsys):
    url = f"sqlite:///{(tmp_path / 'big.db').as_posix()}"
    with create_engine(url).begin() as conn:
        conn.execute(text("CREATE TABLE T (n INTEGER)"))
        conn.execute(text("INSERT INTO T VALUES " + ",".join(f"({i})" for i in range(250))))
    assert sql_script.main(["INS", "--url", url, "SELECT n FROM T"]) == 0
    assert "showing 200 of 250" in capsys.readouterr().out


def test_password_masked(capsys, monkeypatch):
    rc = sql_script.main(["INS", "--url", "mysql+pymysql://root:t0psecret@127.0.0.1:1/insdb", "SELECT 1"])
    out = capsys.readouterr()
    assert rc == 1
    assert "t0psecret" not in out.out + out.err
