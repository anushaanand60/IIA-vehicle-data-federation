"""Registry loader: turns the registry the team writes into validated SourceSpecs.

Reads JSON (CLAUDE.md §5.2, mediator/mappings.json) or the SQLite meta.db of design PDF §7.1
(SOURCE_CATALOG + MAPPING_REGISTRY). Every divergence absorbed here is catalogued in
docs/INTEGRATION_REPORT.md §3. Adapt in this file, never by asking the team to change their output.
"""
from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterable
from contextlib import closing
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from mediator.contract import DERIVED_ATTRIBUTES, GLOBAL_ATTRIBUTES, Registry, SourceSpec

ROOT = Path(__file__).resolve().parents[1]
META_DB = ROOT / "mediator" / "meta.db"
REAL_REGISTRY = ROOT / "mediator" / "mappings.json"
MOCK_REGISTRY = ROOT / "sources" / "_mock" / "mock_mappings.json"
DEFAULT_TEMPLATE = "SELECT {columns} FROM {table}{joins} WHERE {predicate}{order_by}"
DEFAULT_TIMEOUT_MS = 3000
SQLITE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}

_KNOWN_KEYS = set(SourceSpec.model_fields) | {"name", "url", "trust_score", "identifier_attr", "join_path"}
_RESERVED_TOP_LEVEL = {"global_schema"}


class RegistryError(ValueError):
    """The registry cannot become SourceSpecs. The message names the source and the field."""


def default_registry_path() -> Path:
    # Real team output beats the mock, so a demo never silently runs on fake data.
    if env := os.environ.get("IIA_REGISTRY"):
        return Path(env)
    return next((p for p in (META_DB, REAL_REGISTRY) if p.exists()), MOCK_REGISTRY)


def load_registry(path: str | Path | None = None) -> Registry:
    path = Path(path) if path else default_registry_path()
    if not path.exists():  # checked first: sqlite3 would otherwise create an empty meta.db
        raise RegistryError(f"registry file not found: {path}")
    if path.suffix.lower() in SQLITE_SUFFIXES:
        return normalize_registry(_read_meta_db(path))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RegistryError(f"{path} is not valid JSON: {exc}") from None
    return normalize_registry(raw)


def normalize_registry(raw: Any) -> Registry:
    schema = (raw.get("global_schema") or {}) if isinstance(raw, dict) else {}
    stored = list(dict.fromkeys([*GLOBAL_ATTRIBUTES, *_list(schema.get("attributes"))]))
    specs = [_normalize_source(key, entry, stored) for key, entry in _entries(raw)]
    derived = {k: list(v) for k, v in DERIVED_ATTRIBUTES.items() if k not in stored}
    derived |= {k: _list(v) for k, v in (schema.get("derived") or {}).items()}
    try:
        return Registry(sources=specs, global_attributes=stored, derived=derived)
    except ValidationError as exc:
        raise RegistryError(f"registry invalid: {_describe(exc)}") from None


def _read_meta_db(path: Path) -> dict[str, Any]:
    """Design PDF §7.1: SOURCE_CATALOG rows are sources, MAPPING_REGISTRY rows their attribute_map."""
    try:
        with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as con:  # never write it
            con.row_factory = sqlite3.Row
            tables = {r[0].upper(): r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            if missing := [t for t in ("SOURCE_CATALOG", "MAPPING_REGISTRY") if t not in tables]:
                raise RegistryError(f"{path} has no {' or '.join(missing)} table (design PDF §7.1)")
            sources = {r["source_id"]: dict(r) for r in con.execute(f"SELECT * FROM {tables['SOURCE_CATALOG']}")}
            for m in con.execute(f"SELECT * FROM {tables['MAPPING_REGISTRY']}"):
                if m["source_id"] not in sources:
                    raise RegistryError(f"MAPPING_REGISTRY has rows for {m['source_id']!r}, which is not in SOURCE_CATALOG")
                sources[m["source_id"]].setdefault("attribute_map", []).append(dict(m))
            schema = {"attributes": [], "derived": {}}
            if "GLOBAL_SCHEMA" in tables:  # optional: how a new attribute is declared for UC6
                for row in map(dict, con.execute(f"SELECT * FROM {tables['GLOBAL_SCHEMA']}")):
                    if row.get("derived_from"):
                        schema["derived"][row["global_attr"]] = _list(row["derived_from"])
                    elif row.get("global_attr"):
                        schema["attributes"].append(row["global_attr"])
    except sqlite3.Error as exc:
        raise RegistryError(f"{path} is not a readable SQLite registry: {exc}") from None
    return {"sources": list(sources.values()), "global_schema": schema}


def _entries(raw: Any) -> list[tuple[str | None, Any]]:
    """Accept a bare list, {"sources": list | dict}, or a top-level {"REG": {...}} object."""
    if isinstance(raw, dict) and "sources" in raw:
        raw = raw["sources"]
    if isinstance(raw, list):
        return [(None, entry) for entry in raw]
    if isinstance(raw, dict):
        return [(k, e) for k, e in raw.items() if isinstance(e, dict) and k not in _RESERVED_TOP_LEVEL]
    raise RegistryError(f"registry must be a JSON list or object, got {type(raw).__name__}")


def _normalize_source(key: str | None, entry: Any, vocabulary: list[str]) -> SourceSpec:
    source_id = str((entry.get("source_id") if isinstance(entry, dict) else None) or key or "").upper()
    label = source_id or "<unnamed source>"
    try:
        if not isinstance(entry, dict):
            raise ValueError("source entry is not a JSON object")
        raw_maps = [m for m in entry.get("attribute_map") or []]
        mappings = [m for m in map(_mapping, raw_maps) if m["global_attr"]]  # None = column the matcher left unmatched
        if unknown := next((m for m in mappings if m["global_attr"] not in vocabulary), None):
            # Checked before SourceSpec, so a typo is named instead of surfacing as a coverage gap.
            raise ValueError(f"attribute_map maps {unknown['source_attr']!r} to unknown global attribute "
                             f"{unknown['global_attr']!r}")
        key_predicate = _key_predicate(entry.get("key_predicate") or entry.get("identifier_attr"))
        table = entry.get("table") or _table_for(key_predicate, mappings)
        return SourceSpec(
            source_id=source_id,
            display_name=entry.get("display_name") or entry.get("name") or source_id,
            dbms=str(entry.get("dbms", "unknown")).lower(),
            base_url=_base_url(entry.get("base_url") or entry.get("url")),
            authority=_authority(entry.get("authority")),
            trust=_unit(_first(entry, "trust", "trust_score")),
            timeout_ms=entry.get("timeout_ms") or DEFAULT_TIMEOUT_MS,
            covers=_list(entry.get("covers")) or _dedupe(m["global_attr"] for m in mappings),
            key_predicate=key_predicate,
            query_template=entry.get("query_template") or DEFAULT_TEMPLATE,
            table=table,
            joins=_joins(entry, raw_maps, table),
            identity_authority=str(entry.get("identity_authority", "")).strip().lower() in {"true", "1", "yes", "y"},
            aggregate=_aggregate(entry.get("aggregate"), raw_maps, mappings),
            attribute_map=mappings,
            extra={**(entry.get("extra") or {}), **{k: v for k, v in entry.items() if k not in _KNOWN_KEYS}},
        )
    except ValidationError as exc:
        raise RegistryError(f"{label}: {_describe(exc)}") from None
    except (TypeError, ValueError, AttributeError) as exc:
        raise RegistryError(f"{label}: {exc}") from None


def _mapping(m: Any) -> dict[str, Any]:
    if not isinstance(m, dict):
        raise ValueError(f"attribute_map entry is not an object: {m!r}")
    return {
        "source_table": m.get("source_table") or m.get("table"),
        "source_attr": m.get("source_attr") or m.get("source_column") or m.get("column"),
        "global_attr": m.get("global_attr") or m.get("global") or m.get("target"),
        "transform": m.get("transform") or m.get("transform_fn"),
        "score": _unit(_first(m, "score", "match_score", default=1.0)),
    }


def _joins(entry: dict[str, Any], raw_maps: list[Any], table: str | None) -> list[dict[str, str]]:
    """Explicit joins, or "A.x=B.y" join_path strings on the source or its mappings (design PDF §7.1)."""
    declared = list(entry.get("joins") or []) + [entry.get("join_path")]
    declared += [m.get("join_path") for m in raw_maps if isinstance(m, dict)]
    joins: dict[str, dict[str, str]] = {}
    for item in filter(None, declared):
        join = _join_from_path(item, table) if isinstance(item, str) else item
        joins.setdefault(str(join.get("table", "")).lower(), join)
    return list(joins.values())


def _join_from_path(path: str, base_table: str | None) -> dict[str, str]:
    left, sep, right = (part.strip() for part in path.partition("="))
    if not sep:
        raise ValueError(f"join_path {path!r} is not of the form TABLE.column=TABLE.column")
    left_table, right_table = left.split(".")[0], right.split(".")[0]
    joined = left_table if right_table.lower() == str(base_table).lower() else right_table  # the non-base side
    return {"table": joined, "left": left, "right": right}


def _aggregate(value: Any, raw_maps: list[Any], mappings: list[dict[str, Any]]) -> Any:
    if isinstance(value, dict):
        return value
    specs = [value] + [m.get("aggregate") for m in raw_maps if isinstance(m, dict)]
    for spec in (s for s in specs if isinstance(s, str) and s.strip()):
        strategy, _, column = spec.partition(":")
        if strategy.strip().lower() != "latest_by" or not column.strip():
            raise ValueError(f"unrecognised aggregate {spec!r}; expected latest_by:<column>")
        column = column.strip().split(".")[-1].lower()
        attr = next((m["global_attr"] for m in mappings if str(m["source_attr"]).lower() == column), None)
        if attr is None:
            raise ValueError(f"aggregate {spec!r} orders by a column that has no mapping")
        # Not pushed down: design PDF §8.1 applies aggregates after transforms, e.g. after parse_ddmmyyyy.
        return {"strategy": "latest", "global_attr": attr, "pushdown": False}
    return {}


def _key_predicate(v: Any) -> dict[str, Any]:
    if isinstance(v, str) and v:
        return {"column": v}  # bare column name: exact match on the canonical plate
    if isinstance(v, dict):
        return v
    raise ValueError("missing or unreadable key_predicate")


def _first(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    return next((d[k] for k in keys if d.get(k) is not None), default)


def _list(v: Any) -> list[Any]:
    """A list, JSON list text, or comma-separated text (how SQLite ends up storing covers[])."""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        text = v.strip()
        return json.loads(text) if text.startswith("[") else [p.strip() for p in text.split(",") if p.strip()]
    raise ValueError(f"expected a list, got {v!r}")


def _unit(v: Any) -> Any:
    # Values in (1, 100] are read as percentages. Ambiguous for e.g. 1.5, but no matcher emits that.
    if isinstance(v, (int, float)) and not isinstance(v, bool) and 1 < v <= 100:
        return v / 100
    return v


def _authority(v: Any) -> Any:
    folded = str(v or "").strip().lower()
    if folded.startswith(("auth", "offic")):  # design PDF §7.1 spells it OFFICIAL
        return "authoritative"
    if folded.startswith("obs"):
        return "observational"
    return v  # leave it for the contract to reject with a clear message


def _base_url(v: Any) -> Any:
    if not isinstance(v, str):
        return v
    v = v.strip().rstrip("/")
    return v if v.startswith(("http://", "https://")) else f"http://{v}"


def _table_for(key_predicate: dict[str, Any], mappings: list[dict[str, Any]]) -> str | None:
    for m in mappings:
        if m["source_attr"] == key_predicate.get("column"):
            return m["source_table"]
    return mappings[0]["source_table"] if mappings else None


def _dedupe(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _describe(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"])
        parts.append(f"{loc}: {err['msg']}" if loc else err["msg"])
    return "; ".join(parts)
