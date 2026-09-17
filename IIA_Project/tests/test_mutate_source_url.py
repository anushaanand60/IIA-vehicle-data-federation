"""scripts/mutate_source.py::resolve_url must follow the URL scripts/serve.py saved to laptop.env,
otherwise a write lands in the local SQLite copy instead of the database the wrapper serves."""
from __future__ import annotations

import pytest

from scripts import mutate_source, serve

KEYS = ("INS_ADMIN_URL", "INS_DB_URL")


@pytest.fixture()
def clean_env(monkeypatch):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


@pytest.fixture()
def laptop_env(tmp_path, clean_env):
    env_file = tmp_path / "laptop.env"
    clean_env.setattr(mutate_source, "default_env_path", lambda source_id: env_file)
    return env_file


def test_falls_back_to_sqlite_without_env_or_laptop_env(laptop_env):
    assert mutate_source.resolve_url("INS", None) == mutate_source.sqlite_url("INS")


def test_laptop_env_db_url_beats_sqlite_default(laptop_env, tmp_path):
    db_url = f"sqlite:///{(tmp_path / 'served.db').as_posix()}"
    serve.write_env_file(laptop_env, {"INS_DB_URL": db_url})
    assert mutate_source.resolve_url("INS", None) == db_url


def test_laptop_env_admin_url_beats_laptop_env_db_url(laptop_env):
    serve.write_env_file(laptop_env, {"INS_DB_URL": "sqlite:///reader.db", "INS_ADMIN_URL": "sqlite:///owner.db"})
    assert mutate_source.resolve_url("INS", None) == "sqlite:///owner.db"


def test_env_var_beats_laptop_env(laptop_env, clean_env):
    serve.write_env_file(laptop_env, {"INS_ADMIN_URL": "sqlite:///from_file.db"})
    clean_env.setenv("INS_DB_URL", "sqlite:///from_env.db")
    assert mutate_source.resolve_url("INS", None) == "sqlite:///from_env.db"


def test_override_beats_everything(laptop_env, clean_env):
    serve.write_env_file(laptop_env, {"INS_ADMIN_URL": "sqlite:///from_file.db"})
    clean_env.setenv("INS_ADMIN_URL", "sqlite:///from_env.db")
    assert mutate_source.resolve_url("INS", "sqlite:///cli.db") == "sqlite:///cli.db"


def test_main_uses_laptop_env_database_and_masks_url(laptop_env, tmp_path, capsys):
    from sqlalchemy import create_engine, text
    db = tmp_path / "ins_served.db"
    with create_engine(f"sqlite:///{db.as_posix()}").begin() as conn:
        conn.execute(text("CREATE TABLE POLICY_RECORDS (policy_id INTEGER, vehicle_reg TEXT)"))
        conn.execute(text("INSERT INTO POLICY_RECORDS VALUES (7, 'DL-05-CD-9876')"))
    serve.write_env_file(laptop_env, {"INS_DB_URL": f"sqlite:///{db.as_posix()}"})
    assert mutate_source.main(["show", "INS", "DL05CD9876"]) == 0
    out = capsys.readouterr().out
    assert "ins_served.db" in out and "policy_id=7" in out
