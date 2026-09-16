"""One-command bring-up for a single laptop's source wrapper (no env-var juggling).

    python scripts/serve.py INS --db-url "mysql+pymysql://iia:pw@127.0.0.1:3306/insdb"
    python scripts/serve.py INS                                  # re-uses sources/ins/laptop.env
    python scripts/serve.py INS --port 8102 --readonly-admin
    python scripts/serve.py INS --check                          # verify only, don't serve

The first run's --db-url/--port/--readonly-admin are written to sources/<id>/laptop.env (gitignored,
simple KEY=VALUE lines: <ID>_DB_URL, <ID>_PORT, <ID>_ADMIN=off). A later run with no flags re-loads
that file, so a laptop that has already been configured once just needs `python scripts/serve.py INS`
after a reboot or a git pull. Flags given on the command line always win and are re-persisted.

Those settings are exported into os.environ *before* `sources.<id>.wrapper` is imported, because that
module builds its FastAPI app at import time via wrapper_template.from_env() (see sources/reg/wrapper.py
and friends) — the env vars have to exist first or the app would be built against the wrong database.
"""
from __future__ import annotations

import argparse
import importlib
import os
import re
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sources.defaults import sqlite_default  # noqa: E402

VALID_SOURCES = ["REG", "INS", "THEFT", "CAM", "PUC"]
# Kept in sync with scripts/configure_cluster.py::DEFAULT_PORTS (that module owns the mediator-side
# addressing; this one owns bring-up on the source's own laptop, so the constant is duplicated rather
# than imported to keep the two scripts independently runnable).
DEFAULT_PORTS = {"REG": 8001, "INS": 8002, "THEFT": 8003, "CAM": 8004, "PUC": 8005}
_SCHEME_LABELS = {"sqlite": "SQLite", "postgresql": "PostgreSQL", "postgres": "PostgreSQL",
                  "mysql": "MySQL", "mariadb": "MariaDB"}
# Fallback if scripts/load_source.py (a teammate-adjacent, but importable, plain script) ever can't be
# imported: the same plate column per source, so --check still means something.
_FALLBACK_PLATE_COLUMN = {
    "REG": ("VEHICLE_REGISTRATION", "registration_no"),
    "INS": ("POLICY_RECORDS", "vehicle_reg"),
    "THEFT": ("CRIME_RECORDS", "vehicle_number"),
    "CAM": ("PLATE_CAPTURES", "plate_id"),
    "PUC": ("POLLUTION_CERT", "regn_number"),
}
_FALLBACK_DEMO_PLATES = ("DL01AB1234", "DL05CD9876", "HR26EF4455", "UP16GH1122", "MH12IJ7788", "DL03SC5566")


@dataclass
class ResolvedConfig:
    db_url: str | None
    port: int
    admin_off: bool


def _mask(url: str) -> str:
    return re.sub(r"://[^@/]+@", "://***@", url)


def default_env_path(source_id: str) -> Path:
    folder = ROOT / "sources" / source_id.lower()
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "laptop.env"


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip()
    return values


def write_env_file(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={val}" for key, val in values.items() if val is not None and val != ""]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def resolve_config(source_id: str, args: argparse.Namespace, persisted: dict[str, str]) -> ResolvedConfig:
    db_url = args.db_url or persisted.get(f"{source_id}_DB_URL")
    if args.port is not None:
        port = args.port
    elif persisted.get(f"{source_id}_PORT"):
        port = int(persisted[f"{source_id}_PORT"])
    else:
        port = DEFAULT_PORTS.get(source_id, 8000)
    admin_off = bool(args.readonly_admin) or persisted.get(f"{source_id}_ADMIN") == "off"
    return ResolvedConfig(db_url=db_url, port=port, admin_off=admin_off)


def config_to_env(source_id: str, cfg: ResolvedConfig) -> dict[str, str]:
    env: dict[str, str] = {f"{source_id}_PORT": str(cfg.port)}
    if cfg.db_url:
        env[f"{source_id}_DB_URL"] = cfg.db_url
    if cfg.admin_off:
        env[f"{source_id}_ADMIN"] = "off"
    return env


def apply_env(source_id: str, cfg: ResolvedConfig) -> None:
    if cfg.db_url:
        os.environ[f"{source_id}_DB_URL"] = cfg.db_url
    os.environ[f"{source_id}_PORT"] = str(cfg.port)
    if cfg.admin_off:
        os.environ[f"{source_id}_ADMIN"] = "off"


def local_ipv4_addresses() -> list[str]:
    """Every non-loopback IPv4 address this laptop currently answers to.

    Two independent lookups because neither is reliable alone: gethostbyname_ex misses an address
    that has no DNS/hosts entry (common on a phone-hotspot link), and the UDP-connect trick (no
    packet actually leaves the machine — connect() on SOCK_DGRAM just picks a route) can return
    only the one interface the OS would use to reach the internet.
    """
    ips: set[str] = set()
    try:
        _, _, addrs = socket.gethostbyname_ex(socket.gethostname())
        ips.update(a for a in addrs if not a.startswith("127."))
    except Exception:
        pass
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            ips.add(probe.getsockname()[0])
        finally:
            probe.close()
    except Exception:
        pass
    return sorted(ips)


def _scheme_label(url: str) -> str:
    scheme = url.split(":", 1)[0].split("+", 1)[0].lower()
    return _SCHEME_LABELS.get(scheme, scheme)


def _dbms_label(app, url: str) -> str:
    """The dbms label the app's own /health handler would report, without paying for a real server.

    Falls back to the URL scheme if the in-process call raises or the database itself is down —
    /health degrading to 503 is a normal, cheap outcome here, not a bug to propagate.
    """
    try:
        from fastapi.testclient import TestClient
        with TestClient(app) as client:
            response = client.get("/health")
        if response.status_code == 200:
            return str(response.json().get("dbms") or _scheme_label(url))
    except Exception:
        pass
    return _scheme_label(url)


def _plate_column_and_demo_plates(source_id: str) -> tuple[tuple[str, str], tuple[str, ...]]:
    scripts_dir = ROOT / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        import load_source  # the real, teammate-facing verify() this replicates (module docstring)
        return load_source.PLATE_COLUMN[source_id], load_source.DEMO_PLATES
    except Exception:
        return _FALLBACK_PLATE_COLUMN[source_id], _FALLBACK_DEMO_PLATES


def run_check(source_id: str, url: str) -> int:
    from sqlalchemy import create_engine, text

    print(source_id + " -> " + _mask(url))
    (table, column), demo_plates = _plate_column_and_demo_plates(source_id)
    try:
        engine = create_engine(url)
        found = 0
        with engine.connect() as conn:
            for plate in demo_plates:
                sql = text(f"SELECT COUNT(*) FROM {table} WHERE UPPER(REPLACE(REPLACE("
                          f"{column}, '-', ''), ' ', '')) = :plate")
                count = conn.execute(sql, {"plate": plate}).scalar_one()
                print(f"  {'OK ' if count else '-  '} {plate}  {count} row(s)")
                found += count
    except Exception as exc:
        print(f"cannot verify {source_id} at {table}.{column}: {type(exc).__name__}: {exc}")
        return 1
    if found == 0:
        print(f"{source_id}: none of the {len(demo_plates)} demo plates were found in "
              f"{table}.{column} — wrong database, empty database, or wrong plate spelling")
        return 1
    print(f"{source_id}: {found} demo-plate row(s) found across {table}.{column} — looks configured correctly")
    return 0


def print_config(source_id: str, cfg: ResolvedConfig, env_file: Path) -> None:
    effective_url = cfg.db_url or sqlite_default(source_id)
    print("config: source=" + source_id
          + " db_url=" + _mask(effective_url)
          + " port=" + str(cfg.port)
          + " admin=" + ("off" if cfg.admin_off else "on")
          + " env_file=" + str(env_file))


def print_bringup(source_id: str, app, cfg: ResolvedConfig, host: str) -> None:
    url = cfg.db_url or sqlite_default(source_id)
    print(source_id + " wrapper: dbms=" + _dbms_label(app, url) + " host=" + host + " port=" + str(cfg.port))
    ips = local_ipv4_addresses()
    if ips:
        print("  reachable at:")
        for ip in ips:
            print("    http://" + ip + ":" + str(cfg.port))
    primary = ips[0] if ips else "127.0.0.1"
    print("  on laptop 1, run: python scripts/configure_cluster.py --set " + source_id + "=" + primary)


def run_serve(source_id: str, cfg: ResolvedConfig, host: str) -> int:
    apply_env(source_id, cfg)  # must happen before the import below: from_env() reads env at import time
    module_name = f"sources.{source_id.lower()}.wrapper"
    if module_name in sys.modules:
        module = importlib.reload(sys.modules[module_name])
    else:
        module = importlib.import_module(module_name)
    app = module.app
    print_bringup(source_id, app, cfg, host)
    import uvicorn
    uvicorn.run(app, host=host, port=cfg.port)  # foreground; Ctrl+C stops
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", help="one of: " + ", ".join(VALID_SOURCES))
    parser.add_argument("--db-url", help="SQLAlchemy URL; persisted to laptop.env for later runs")
    parser.add_argument("--port", type=int, help="wrapper port; default is the source's conventional one")
    parser.add_argument("--readonly-admin", action="store_true",
                        help=f"disable /admin/* on this laptop ({{ID}}_ADMIN=off)")
    parser.add_argument("--check", action="store_true",
                        help="verify the configured database has the demo plates, then exit (no serving)")
    parser.add_argument("--host", default="0.0.0.0", help="bind address for uvicorn (DB stays on 127.0.0.1)")
    parser.add_argument("--env-file", help="override sources/<id>/laptop.env (mainly for tests)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the resolved config and exit, without serving or checking")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source_id = args.source.upper()
    if source_id not in VALID_SOURCES:
        print(f"unknown source {source_id!r}; expected one of {', '.join(VALID_SOURCES)}", file=sys.stderr)
        return 2

    env_file = Path(args.env_file) if args.env_file else default_env_path(source_id)
    persisted = read_env_file(env_file)
    cfg = resolve_config(source_id, args, persisted)
    write_env_file(env_file, config_to_env(source_id, cfg))

    if args.dry_run:
        print_config(source_id, cfg, env_file)
        return 0
    if args.check:
        return run_check(source_id, cfg.db_url or sqlite_default(source_id))
    return run_serve(source_id, cfg, args.host)


if __name__ == "__main__":
    raise SystemExit(main())
