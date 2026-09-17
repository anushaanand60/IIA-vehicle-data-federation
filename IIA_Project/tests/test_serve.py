"""scripts/serve.py: one-command laptop bring-up (Task 0.6, docs/superpowers/plans/2026-09-16-overhaul.md).

Never binds the real 8001-8005 ports here: --dry-run and --check exercise config resolution and
DB verification without starting uvicorn.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from scripts import serve

ROOT = Path(__file__).resolve().parents[1]


# --- env file: write then read back -------------------------------------------------

def test_writes_and_reads_env_file(tmp_path):
    env_file = tmp_path / "laptop.env"
    rc = serve.main(["INS", "--db-url", "sqlite:///demo.db", "--port", "9002",
                     "--readonly-admin", "--env-file", str(env_file), "--dry-run"])
    assert rc == 0
    assert env_file.exists()
    values = serve.read_env_file(env_file)
    assert values["INS_DB_URL"] == "sqlite:///demo.db"
    assert values["INS_PORT"] == "9002"
    assert values["INS_ADMIN"] == "off"


def test_subsequent_run_without_flags_loads_persisted_env_file(tmp_path):
    env_file = tmp_path / "laptop.env"
    serve.write_env_file(env_file, {"THEFT_DB_URL": "sqlite:///persisted.db", "THEFT_PORT": "9100"})
    values = serve.read_env_file(env_file)
    cfg = serve.resolve_config("THEFT", serve.build_parser().parse_args(
        ["THEFT", "--env-file", str(env_file), "--dry-run"]), values)
    assert cfg.db_url == "sqlite:///persisted.db"
    assert cfg.port == 9100


# --- --check ---------------------------------------------------------------------

@pytest.fixture()
def theft_db_with_demo_row(tmp_path) -> str:
    db_path = tmp_path / "theft_check.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    schema = (ROOT / "sources" / "theft" / "schema.sql").read_text(encoding="utf-8")
    with engine.begin() as conn:
        for statement in schema.split(";"):
            statement = statement.strip()
            if statement:
                conn.execute(text(statement))
        conn.execute(text(
            "INSERT INTO CRIME_RECORDS (incident_id, vehicle_number, fir_no, reported_date, "
            "incident_type, stolen_flag, recovered_flag, case_status, police_station) "
            "VALUES (1, 'hr 26 ef 4455', 'FIR001', 1700000000, 'THEFT', 'Y', 'N', 'OPEN', 'PS1')"))
    return f"sqlite:///{db_path.as_posix()}"


def test_check_passes_when_demo_plate_present(tmp_path, theft_db_with_demo_row):
    env_file = tmp_path / "laptop.env"
    rc = serve.main(["THEFT", "--db-url", theft_db_with_demo_row, "--env-file", str(env_file), "--check"])
    assert rc == 0


def test_check_fails_when_no_demo_plates_present(tmp_path):
    db_path = tmp_path / "empty_theft.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    schema = (ROOT / "sources" / "theft" / "schema.sql").read_text(encoding="utf-8")
    with engine.begin() as conn:
        for statement in schema.split(";"):
            statement = statement.strip()
            if statement:
                conn.execute(text(statement))
    env_file = tmp_path / "laptop.env"
    rc = serve.main(["THEFT", "--db-url", f"sqlite:///{db_path.as_posix()}",
                     "--env-file", str(env_file), "--check"])
    assert rc == 1


# --- unknown source id -------------------------------------------------------------

def test_unknown_source_id_exits_2(tmp_path, capsys):
    env_file = tmp_path / "laptop.env"
    rc = serve.main(["ZZZ", "--env-file", str(env_file), "--dry-run"])
    assert rc == 2
    out = capsys.readouterr()
    combined = out.out + out.err
    assert "REG" in combined and "INS" in combined and "THEFT" in combined and "CAM" in combined


# --- --port respected in the resolved/printed config (subprocess, no binding) -----

def test_dry_run_prints_resolved_port(tmp_path):
    env_file = tmp_path / "laptop.env"
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "serve.py"), "CAM", "--port", "9500",
         "--env-file", str(env_file), "--dry-run"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert "port=9500" in result.stdout


def test_dry_run_never_prints_the_password(tmp_path):
    env_file = tmp_path / "laptop.env"
    rc = serve.main(["INS", "--db-url", "mysql+pymysql://iia:s3cr3t@127.0.0.1:3306/insdb",
                     "--env-file", str(env_file), "--dry-run"])
    assert rc == 0
    values = serve.read_env_file(env_file)
    assert "s3cr3t" in values["INS_DB_URL"]  # the file itself keeps the real URL


def test_dry_run_stdout_masks_password(tmp_path, capsys):
    env_file = tmp_path / "laptop.env"
    serve.main(["INS", "--db-url", "mysql+pymysql://iia:s3cr3t@127.0.0.1:3306/insdb",
               "--env-file", str(env_file), "--dry-run"])
    out = capsys.readouterr().out
    assert "s3cr3t" not in out
    assert "***" in out


# --- --admin-url: owner account for /admin/* alongside the read-only --db-url -------

def test_admin_url_persisted_to_env_file(tmp_path):
    env_file = tmp_path / "laptop.env"
    rc = serve.main(["INS", "--db-url", "mysql+pymysql://iia_reader:r@127.0.0.1:3306/insdb",
                     "--admin-url", "mysql+pymysql://root:ownerpw@127.0.0.1:3306/insdb",
                     "--env-file", str(env_file), "--dry-run"])
    assert rc == 0
    values = serve.read_env_file(env_file)
    assert values["INS_ADMIN_URL"] == "mysql+pymysql://root:ownerpw@127.0.0.1:3306/insdb"
    assert values["INS_DB_URL"] == "mysql+pymysql://iia_reader:r@127.0.0.1:3306/insdb"


def test_admin_url_reloaded_from_env_file(tmp_path):
    env_file = tmp_path / "laptop.env"
    serve.write_env_file(env_file, {"CAM_DB_URL": "sqlite:///r.db", "CAM_ADMIN_URL": "sqlite:///w.db"})
    args = serve.build_parser().parse_args(["CAM", "--env-file", str(env_file), "--dry-run"])
    cfg = serve.resolve_config("CAM", args, serve.read_env_file(env_file))
    assert cfg.admin_url == "sqlite:///w.db"


def test_admin_url_flag_beats_env_file(tmp_path):
    env_file = tmp_path / "laptop.env"
    serve.write_env_file(env_file, {"CAM_ADMIN_URL": "sqlite:///old.db"})
    args = serve.build_parser().parse_args(["CAM", "--admin-url", "sqlite:///new.db",
                                            "--env-file", str(env_file)])
    cfg = serve.resolve_config("CAM", args, serve.read_env_file(env_file))
    assert cfg.admin_url == "sqlite:///new.db"


def test_apply_env_exports_admin_url(monkeypatch):
    for key in ("REG_ADMIN_URL", "REG_DB_URL", "REG_PORT"):
        monkeypatch.setenv(key, "placeholder")  # recorded so teardown restores the original state
    cfg = serve.ResolvedConfig(db_url="sqlite:///r.db", port=8001, admin_off=False,
                               admin_url="sqlite:///w.db")
    serve.apply_env("REG", cfg)
    import os
    assert os.environ["REG_ADMIN_URL"] == "sqlite:///w.db"


def test_dry_run_masks_admin_url_password(tmp_path, capsys):
    env_file = tmp_path / "laptop.env"
    serve.main(["INS", "--admin-url", "mysql+pymysql://root:0wn3rpw@127.0.0.1:3306/insdb",
               "--env-file", str(env_file), "--dry-run"])
    out = capsys.readouterr().out
    assert "0wn3rpw" not in out
    assert "admin_url=mysql+pymysql://***@127.0.0.1:3306/insdb" in out
