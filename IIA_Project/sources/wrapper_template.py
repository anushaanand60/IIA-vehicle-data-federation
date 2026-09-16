"""Wrapper template: each agency publishes an API, not a database (CLAUDE.md §5.1).

GET /health, GET /schema, POST /query — identical on all four sources; only the URL and whitelist differ.

POST /admin/mutate — a fixed, named set of writes (renew a policy, log a sighting, ...), never
arbitrary SQL. ON by default (Task 0.1): the wrapper is given a *second*, writable connection,
derived automatically from `db_url` unless `<SOURCE_ID>_ADMIN_URL` overrides it. /query's connection
stays read-only regardless: this does not reopen that door, it adds a second, narrower one for the
agency's own operator console. Set `<SOURCE_ID>_ADMIN=off` to disable it; only then do /admin/* 404.

POST /admin/sql is the same door, one notch wider: the agency's own SQL console, accepting a single
INSERT/UPDATE/DELETE against the tables this wrapper already publishes (see write_guard_sql). It
shares /admin/mutate's writable engine and its 404, and never touches /query's read-only one.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import Connection, Engine, create_engine, event, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from pydantic import BaseModel

from mediator.contract import QueryRequest


class MutateRequest(BaseModel):
    action: str
    plate: str
    params: dict[str, Any] = {}

MAX_SQL_CHARS = 10_000
SAMPLE_SIZE = 20
# REPLACE is allowed: the design PDF §8.1 uses it as a string function, and the MySQL REPLACE statement
# can never pass the must-start-with-SELECT rule.
_FORBIDDEN = re.compile(r"\b(INSERT|UPDATE|DELETE|MERGE|UPSERT|DROP|CREATE|ALTER|TRUNCATE|RENAME|GRANT|"
                        r"REVOKE|ATTACH|DETACH|PRAGMA|VACUUM|REINDEX|EXEC|EXECUTE|CALL|COPY|LOAD|INTO|OUTFILE|"
                        r"DUMPFILE|SET|LOCK|UNLOCK|UNION|INTERSECT|EXCEPT|TABLE|HANDLER|COMMIT|ROLLBACK|BEGIN|"
                        r"SLEEP|PG_SLEEP|BENCHMARK|LOAD_FILE|PG_READ_FILE|LO_IMPORT|LO_EXPORT|DBLINK|"
                        r"QUERY_TO_XML|LOAD_EXTENSION)\b", re.I)
_CLAUSE_END = re.compile(r"\b(WHERE|GROUP|ORDER|LIMIT|HAVING|ON|USING|WINDOW|OFFSET|"
                         r"(?:NATURAL\s+|CROSS\s+|INNER\s+|(?:LEFT|RIGHT|FULL)(?:\s+OUTER)?\s+)?JOIN)\b", re.I)
_FROM_ITEM = re.compile(r"^([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)(?:\s+(?:AS\s+)?[A-Za-z_]\w*)?$", re.I)
_TRAILING_LIMIT = re.compile(r"\bLIMIT\s+\d+(?:\s+OFFSET\s+\d+)?\s*$", re.I)
_TIMEOUT_MARKERS = ("interrupted", "statement timeout", "maximum statement execution time")


class QueryRejected(ValueError):
    """The guard refused the SQL. Surfaces as HTTP 400: a mediator bug, not a source failure."""


def guard_sql(sql: str, whitelist: Iterable[str], row_limit: int = 200) -> str:
    # Trade-off: a textual guard is simple to defend but not a parser. It catches mediator bugs;
    # the security boundary is the read-only connection in _make_engine (plus a SELECT-only DB role).
    allowed = {t.lower() for t in whitelist}
    sql = sql.strip()
    checks = [
        (not sql, "empty query"),
        (len(sql) > MAX_SQL_CHARS, f"query longer than {MAX_SQL_CHARS} characters"),
        ("\x00" in sql, "NUL byte in query"),
        (";" in sql, "multiple statements are not allowed (';')"),
        (any(c in sql for c in ("--", "/*", "#")), "SQL comments are not allowed"),
        (not re.match(r"SELECT\b", sql, re.I), "query must start with SELECT"),
        (len(re.findall(r"\bSELECT\b", sql, re.I)) > 1, "subqueries and set operations are not allowed (one SELECT only)"),
    ]
    for failed, reason in checks:
        if failed:
            raise QueryRejected(reason)
    if m := _FORBIDDEN.search(sql):
        raise QueryRejected(f"forbidden keyword {m.group(1).upper()}")
    tables = _referenced_tables(sql)
    if not tables:
        raise QueryRejected("query references no table")
    for table in tables:
        if table.lower() not in allowed:
            raise QueryRejected(f"table {table!r} is not in the whitelist")
    return sql if _TRAILING_LIMIT.search(sql) else f"{sql} LIMIT {row_limit}"


_WRITE_START = re.compile(r"^(INSERT|UPDATE|DELETE)\b", re.I)
_WRITE_TARGET = re.compile(r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+"
                           r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)?)", re.I)
# Everything structural a write must never carry. INSERT/UPDATE/DELETE are absent on purpose:
# exactly one of them is allowed, and only as the statement's first keyword.
_FORBIDDEN_WRITE = re.compile(r"\b(DROP|CREATE|ALTER|TRUNCATE|RENAME|GRANT|REVOKE|ATTACH|DETACH|PRAGMA|"
                              r"VACUUM|REINDEX|EXEC|EXECUTE|CALL|COPY|LOAD|OUTFILE|DUMPFILE|HANDLER|"
                              r"MERGE|UPSERT|COMMIT|ROLLBACK|BEGIN|LOCK|UNLOCK|LOAD_FILE|LOAD_EXTENSION)\b", re.I)


def write_guard_sql(sql: str, whitelist: Iterable[str]) -> str:
    """Guard for /admin/sql: one INSERT/UPDATE/DELETE, whitelisted tables only, no DDL.

    Same trade-off as guard_sql: textual, not a parser. It is the *second* lock — the first is that
    this path exists at all only when the agency set <SOURCE_ID>_ADMIN_URL on its own laptop.
    """
    allowed = {t.lower() for t in whitelist}
    sql = sql.strip()
    checks = [
        (not sql, "empty statement"),
        (len(sql) > MAX_SQL_CHARS, f"statement longer than {MAX_SQL_CHARS} characters"),
        ("\x00" in sql, "NUL byte in statement"),
        (";" in sql, "multiple statements are not allowed (';')"),
        (any(c in sql for c in ("--", "/*", "#")), "SQL comments are not allowed"),
        (not _WRITE_START.match(sql), "statement must start with INSERT, UPDATE or DELETE "
                                      "(reads belong on /query)"),
    ]
    for failed, reason in checks:
        if failed:
            raise QueryRejected(reason)
    if m := _FORBIDDEN_WRITE.search(sql):
        raise QueryRejected(f"forbidden keyword {m.group(1).upper()}")
    targets = _WRITE_TARGET.findall(sql)
    if not targets:
        raise QueryRejected("statement names no target table")
    # A write may still read (INSERT ... SELECT, UPDATE ... WHERE x IN (SELECT ...)): those tables
    # go through the same whitelist, so the console can never exfiltrate a non-published table.
    for table in [*targets, *(_referenced_tables(sql) if re.search(r"\bSELECT\b", sql, re.I) else [])]:
        if table.lower() not in allowed:
            raise QueryRejected(f"table {table!r} is not in the whitelist")
    return sql


def _referenced_tables(sql: str) -> list[str]:
    tables = []
    for kw in re.finditer(r"\b(FROM|JOIN)\b", sql, re.I):
        rest = sql[kw.end():]
        end = _CLAUSE_END.search(rest)
        clause = rest[: end.start()] if end else rest
        for item in clause.split(",") if kw.group(1).upper() == "FROM" else [clause]:
            if not (m := _FROM_ITEM.match(item.strip())):  # parentheses, functions, quoted names: refused
                raise QueryRejected(f"unsupported FROM item {item.strip()!r}")
            tables.append(m.group(1))
    return tables


def create_app(db_url: str, *, source_id: str, dbms: str, tables: Iterable[str],
               statement_timeout_s: float = 3.0, row_limit: int = 200,
               admin_url: str | None = None, admin_enabled: bool = True) -> FastAPI:
    whitelist = list(tables)
    app = FastAPI(title=f"{source_id} wrapper")
    app.state.source_id = source_id
    try:
        app.state.engine, app.state.engine_error = _make_engine(db_url, statement_timeout_s), None
    except Exception as exc:  # e.g. DB driver not installed: degrade to 503, never crash at import
        app.state.engine, app.state.engine_error = None, _short(exc)

    def engine() -> Engine:
        if app.state.engine is None:
            raise SQLAlchemyError(app.state.engine_error)
        return app.state.engine

    # A second, writable engine, entirely separate from the one /query uses. /query's connection
    # keeps its read-only pragma/session regardless of whether this exists (see _make_engine): an
    # agency's admin console never widens what the mediator can do. Task 0.1: on by default —
    # <SOURCE_ID>_ADMIN=off is the only way back to a read-only wrapper.
    admin_engine: Engine | None = None
    if admin_enabled:
        try:
            admin_engine = create_engine(admin_url or _derive_admin_url(db_url), pool_pre_ping=True)
        except Exception as exc:
            app.state.admin_engine_error = _short(exc)
    actions = ADMIN_ACTIONS.get(source_id, {})

    def _admin_disabled_message() -> str:
        message = (f"{source_id} has no admin URL configured ({source_id}_ADMIN_URL) — "
                   f"this wrapper is read-only")
        if not admin_enabled:
            message += f"; set {source_id}_ADMIN=on"
        return message

    @app.exception_handler(RequestValidationError)
    async def _bad_body(_: Request, exc: RequestValidationError) -> JSONResponse:
        detail = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        return _error(400, f"malformed request body: {detail}")

    @app.get("/health")
    def health() -> JSONResponse:
        try:
            with engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        except SQLAlchemyError as exc:
            return JSONResponse(status_code=503, content={"source_id": source_id, "up": False, "error": _short(exc)})
        return JSONResponse(content={"source_id": source_id, "dbms": dbms, "up": True, "ts": _now()})

    @app.get("/schema")
    def schema() -> JSONResponse:
        try:
            eng = engine()
            present = {name.lower(): name for name in inspect(eng).get_table_names()}
            described = {present[t.lower()]: _describe_table(eng, present[t.lower()])
                         for t in whitelist if t.lower() in present}
        except SQLAlchemyError as exc:
            return _error(503, f"database unreachable: {_short(exc)}")
        return JSONResponse(content={"source_id": source_id, "tables": described})

    @app.post("/query")
    def query(req: QueryRequest) -> JSONResponse:
        try:
            sql = guard_sql(req.sql, whitelist, row_limit)
        except QueryRejected as exc:
            return _error(400, str(exc))
        started = time.perf_counter()
        try:
            with engine().connect() as conn, _deadline(conn, statement_timeout_s):
                # Escape ':' so text() never mistakes literal content for a bind parameter.
                result = conn.execute(text(sql.replace(":", r"\:")))
                rows = [{k: _wire(v) for k, v in r.items()} for r in result.mappings().fetchmany(row_limit)]
        except SQLAlchemyError as exc:
            if any(marker in _short(exc).lower() for marker in _TIMEOUT_MARKERS):
                return _error(504, f"statement timeout after {statement_timeout_s}s: {_short(exc)}")
            if not _reachable(app.state.engine):
                return _error(503, f"database unreachable: {_short(exc)}")
            return _error(400, f"database rejected the query: {_short(exc)}")
        elapsed = int((time.perf_counter() - started) * 1000)
        return JSONResponse(content={"rows": rows, "row_count": len(rows), "fetched_at": _now(), "elapsed_ms": elapsed})

    @app.post("/admin/mutate")
    def admin_mutate(req: MutateRequest) -> JSONResponse:
        if admin_engine is None:
            return _error(404, _admin_disabled_message())
        fn = actions.get(req.action)
        if fn is None:
            return _error(400, f"{source_id} supports no admin action {req.action!r}; "
                               f"has: {', '.join(actions) or '(none)'}")
        try:
            result = fn(admin_engine, req.plate, req.params)
        except Exception as exc:
            return _error(400, f"mutation failed: {_short(exc)}")
        return JSONResponse(content={"source_id": source_id, "action": req.action,
                                     "fetched_at": _now(), **result})

    @app.post("/admin/sql")
    def admin_sql(req: QueryRequest) -> JSONResponse:
        # The agency's own SQL console: arbitrary *writes*, but only INSERT/UPDATE/DELETE against
        # the tables this wrapper already publishes, and only on the separate writable engine.
        if admin_engine is None:
            return _error(404, _admin_disabled_message())
        try:
            sql = write_guard_sql(req.sql, whitelist)
        except QueryRejected as exc:
            return _error(400, str(exc))
        try:
            with admin_engine.begin() as conn:  # begin(): commit on success, roll back on error
                affected = conn.execute(text(sql.replace(":", r"\:"))).rowcount
        except SQLAlchemyError as exc:
            return _error(400, f"database rejected the statement: {_short(exc)}")
        return JSONResponse(content={"source_id": source_id, "rows_affected": max(affected, 0),
                                     "sql": sql, "executed_at": _now()})

    return app


def from_env(source_id: str, *, default_url: str, default_dbms: str, default_tables: list[str]) -> FastAPI:
    """Build a wrapper configured by <SOURCE_ID>_DB_URL, <SOURCE_ID>_DBMS and <SOURCE_ID>_TABLES.

    Admin writes are ON by default (Task 0.1): <SOURCE_ID>_ADMIN_URL picks an explicit writable
    connection when the read URL is not itself writable; <SOURCE_ID>_ADMIN=off turns writes back
    off, restoring the original read-only wrapper.
    """
    def env(key: str, default: str) -> str:
        return os.environ.get(f"{source_id}_{key}", default)
    tables = [t.strip() for t in env("TABLES", ",".join(default_tables)).split(",") if t.strip()]
    url = env("DB_URL", default_url)
    admin_enabled = os.environ.get(f"{source_id}_ADMIN", "on").lower() not in ("off", "0", "false")
    # /health must not claim PostgreSQL while serving the SQLite fallback, so the label follows
    # the URL unless the agency overrides it explicitly.
    return create_app(url, source_id=source_id, dbms=env("DBMS", _engine_label(url) or default_dbms),
                      tables=tables, statement_timeout_s=float(os.environ.get("WRAPPER_STATEMENT_TIMEOUT_S", "3")),
                      admin_url=os.environ.get(f"{source_id}_ADMIN_URL"), admin_enabled=admin_enabled)


def _engine_label(url: str) -> str:
    scheme = url.split(":", 1)[0].split("+", 1)[0].lower()
    return {"sqlite": "SQLite", "postgresql": "PostgreSQL", "postgres": "PostgreSQL",
            "mysql": "MySQL", "mariadb": "MariaDB"}.get(scheme, scheme)


def _derive_admin_url(db_url: str) -> str:
    """A writable URL for the same database `db_url` reads from, when no explicit
    <SOURCE_ID>_ADMIN_URL is given. /query's read-only guarantee comes from _make_engine's
    session pragmas/settings, not from the URL text, so reusing the URL is safe for
    PostgreSQL/MySQL. A SQLite read-only URI (`sqlite:///file:path?mode=ro&uri=true`) is the one
    case where the URL itself blocks writes, so that form is stripped back to a plain path.
    """
    if not db_url.startswith("sqlite"):
        return db_url
    prefix, sep, rest = db_url.partition(":///")
    if not sep:
        return db_url
    path = rest.split("?", 1)[0]
    if path.startswith("file:"):
        path = path[len("file:"):]
    return f"{prefix}{sep}{path}"


def serve(app: FastAPI, default_port: int) -> None:
    import uvicorn
    port = int(os.environ.get(f"{app.state.source_id}_PORT", default_port))
    uvicorn.run(app, host=os.environ.get("WRAPPER_HOST", "0.0.0.0"), port=port)  # DB stays on 127.0.0.1


def _make_engine(url: str, timeout_s: float) -> Engine:
    ms = int(timeout_s * 1000)
    if url.startswith("sqlite"):
        eng = create_engine(url, connect_args={"check_same_thread": False})
        event.listen(eng, "connect", lambda dbapi, _: dbapi.execute("PRAGMA query_only = ON"))
        return eng
    if url.startswith("postgresql"):
        return create_engine(url, pool_pre_ping=True, connect_args={
            "connect_timeout": max(1, int(timeout_s)),
            "options": f"-c statement_timeout={ms} -c default_transaction_read_only=on"})
    eng = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": max(1, int(timeout_s))})
    if url.startswith("mysql"):
        def _limits(dbapi: Any, _: Any) -> None:
            with dbapi.cursor() as cur:
                cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {ms}")
                # MySQL alone reads || as logical OR. Without this the decomposer's portable
                # substr()||substr() date ordering would silently evaluate to 0 or 1.
                cur.execute("SET SESSION sql_mode = CONCAT(@@sql_mode, ',PIPES_AS_CONCAT')")
                cur.execute("SET SESSION TRANSACTION READ ONLY")
        event.listen(eng, "connect", _limits)
    return eng


@contextmanager
def _deadline(conn: Connection, seconds: float) -> Iterator[None]:
    raw = conn.connection.dbapi_connection
    if not isinstance(raw, sqlite3.Connection):  # Postgres/MySQL enforce it server-side, see _make_engine
        yield
        return
    deadline = time.monotonic() + seconds
    raw.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    try:
        yield
    finally:
        raw.set_progress_handler(None, 1000)


def _describe_table(eng: Engine, table: str) -> dict[str, Any]:
    insp, q = inspect(eng), eng.dialect.identifier_preparer.quote
    pk = set(insp.get_pk_constraint(table).get("constrained_columns") or [])
    fks = {col: f"{fk['referred_table']}.{ref}" for fk in insp.get_foreign_keys(table)
           for col, ref in zip(fk["constrained_columns"], fk["referred_columns"])}
    columns = []
    with eng.connect() as conn:
        for col in insp.get_columns(table):
            name = col["name"]
            samples = conn.execute(text(f"SELECT DISTINCT {q(name)} FROM {q(table)} "
                                        f"WHERE {q(name)} IS NOT NULL LIMIT {SAMPLE_SIZE}")).scalars().all()
            values = [_wire(v) for v in samples]
            fk = fks.get(name)
            columns.append({"name": name, "type": str(col["type"]), "nullable": bool(col.get("nullable", True)),
                            "pk": name in pk, "fk": fk, "samples": values,
                            "is_pk": name in pk, "is_fk": fk is not None,
                            "fk_target": fk.split(".")[0] if fk else None, "sample_values": values})
    # Both spellings of every fact, so the matcher and CLAUDE.md §5.1 read the same response.
    return {"table": table, "table_name": table, "columns": columns}


def _reachable(eng: Engine | None) -> bool:
    try:
        with eng.connect() as conn:  # type: ignore[union-attr]
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def _wire(v: Any) -> Any:
    # JSON has no date or decimal type. str() keeps each DBMS's natural text form, so a Postgres
    # TIMESTAMP and a SQLite TEXT timestamp both arrive as "2026-08-30 21:14:05". Never reformatted.
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    return str(v)


def _short(exc: BaseException) -> str:
    msg = str(getattr(exc, "orig", None) or exc).strip()
    return msg.splitlines()[0][:300] if msg else type(exc).__name__


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": message})


# --- Admin mutations: a fixed, named menu per source, never arbitrary SQL --------------------
# Mirrors scripts/mutate_source.py's logic exactly, so the CLI and the GUI change data the same
# way; the CLI stays the fallback for a source that has no ADMIN_URL set.

def _canon(plate: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", plate).upper()


def _where_plate(column: str) -> str:
    return f"UPPER(REPLACE(REPLACE({column}, '-', ''), ' ', '')) = :plate"


def _ins_renew(engine: Engine, plate: str, params: dict[str, Any]) -> dict[str, Any]:
    until = params.get("until", "31/12/2027")
    with engine.begin() as conn:
        n = conn.execute(text(f"UPDATE POLICY_RECORDS SET policy_until = :u, is_active = 1 "
                              f"WHERE {_where_plate('vehicle_reg')}"),
                         {"u": until, "plate": _canon(plate)}).rowcount
    return {"rows_affected": n, "detail": f"policy_until = {until}, is_active = 1"}


def _ins_expire(engine: Engine, plate: str, params: dict[str, Any]) -> dict[str, Any]:
    until = params.get("until", "10/01/2026")
    with engine.begin() as conn:
        n = conn.execute(text(f"UPDATE POLICY_RECORDS SET policy_until = :u, is_active = 0 "
                              f"WHERE {_where_plate('vehicle_reg')}"),
                         {"u": until, "plate": _canon(plate)}).rowcount
    return {"rows_affected": n, "detail": f"policy_until = {until}, is_active = 0"}


def _theft_steal(engine: Engine, plate: str, params: dict[str, Any]) -> dict[str, Any]:
    p = _canon(plate)
    spaced = f"{p[0:2]} {p[2:4]} {p[4:6]} {p[6:]}".lower() if len(p) == 10 else plate.lower()
    with engine.begin() as conn:
        next_id = (conn.execute(text("SELECT MAX(incident_id) FROM CRIME_RECORDS")).scalar() or 0) + 1
        conn.execute(text(
            "INSERT INTO CRIME_RECORDS (incident_id, vehicle_number, fir_no, reported_date, "
            "incident_type, stolen_flag, recovered_flag, case_status, police_station) "
            "VALUES (:i, :p, :f, :d, 'THEFT', 'Y', 'N', 'OPEN', :ps)"),
            {"i": next_id, "p": spaced, "f": params.get("fir_no", f"FIR{next_id:05d}/2026"),
             "d": int(time.time()), "ps": params.get("police_station", "Live Demo PS")})
    return {"rows_affected": 1, "detail": f"incident {next_id}: {spaced!r} STOLEN, case OPEN"}


def _theft_clear(engine: Engine, plate: str, params: dict[str, Any]) -> dict[str, Any]:
    with engine.begin() as conn:
        n = conn.execute(text(f"DELETE FROM CRIME_RECORDS WHERE {_where_plate('vehicle_number')} "
                              f"AND police_station = 'Live Demo PS'"),
                         {"plate": _canon(plate)}).rowcount
    return {"rows_affected": n, "detail": "demo incident row(s) removed"}


def _cam_sight(engine: Engine, plate: str, params: dict[str, Any]) -> dict[str, Any]:
    with engine.begin() as conn:
        next_id = (conn.execute(text("SELECT MAX(capture_id) FROM PLATE_CAPTURES")).scalar() or 0) + 1
        camera = conn.execute(text("SELECT camera_id FROM CAMERAS")).scalar()
        conn.execute(text(
            "INSERT INTO PLATE_CAPTURES (capture_id, plate_id, camera_id, captured_at, "
            "observed_make, observed_model, observed_colour, ocr_confidence) "
            "VALUES (:i, :p, :c, :t, :mk, :md, :cl, 0.95)"),
            {"i": next_id, "p": _canon(plate), "c": camera, "t": _now()[:19],
             "mk": params.get("make", "Maruti Suzuki"), "md": params.get("model", "Swift"),
             "cl": params.get("colour", "White")})
    return {"rows_affected": 1, "detail": f"capture {next_id}: seen just now"}


def _reg_register(engine: Engine, plate: str, params: dict[str, Any]) -> dict[str, Any]:
    from datetime import date
    with engine.begin() as conn:
        owner_id = (conn.execute(text("SELECT MAX(owner_id) FROM OWNERS")).scalar() or 0) + 1
        reg_id = (conn.execute(text("SELECT MAX(registration_id) FROM VEHICLE_REGISTRATION")).scalar() or 0) + 1
        owner = params.get("owner", "Rajesh Khanna")
        conn.execute(text("INSERT INTO OWNERS (owner_id, full_name, address_line, city) "
                          "VALUES (:i, :n, 'Live Demo Address', 'New Delhi')"), {"i": owner_id, "n": owner})
        conn.execute(text(
            "INSERT INTO VEHICLE_REGISTRATION (registration_id, registration_no, owner_id, make, "
            "model, colour, fuel_type, registered_on, reg_status, rto_code) "
            "VALUES (:r, :p, :o, :mk, :md, :cl, 'PETROL', :d, 'ACTIVE', :rto)"),
            {"r": reg_id, "p": _canon(plate), "o": owner_id, "mk": params.get("make", "Maruti Suzuki"),
             "md": params.get("model", "Swift"), "cl": params.get("colour", "White"),
             "d": date.today().isoformat(), "rto": _canon(plate)[:4]})
    return {"rows_affected": 1, "detail": f"registered to {owner} (registration {reg_id})"}


ADMIN_ACTIONS: dict[str, dict[str, Any]] = {
    "INS": {"renew": _ins_renew, "expire": _ins_expire},
    "THEFT": {"steal": _theft_steal, "clear": _theft_clear},
    "CAM": {"sight": _cam_sight},
    "REG": {"register": _reg_register},
}
