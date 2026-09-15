"""Shared contract: every shape that crosses a module or network boundary.

Owned by the transport layer, imported by everyone. Changes are announced (CLAUDE.md §3).
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

# Stored attributes every team agreed on (CLAUDE.md §9). A registry may add more (UC6), never remove these.
GLOBAL_ATTRIBUTES: tuple[str, ...] = (
    "plate_number", "owner_name", "vehicle_make", "vehicle_model", "vehicle_colour",
    "registration_date", "registration_status", "insurer_name", "policy_type",
    "insurance_start", "insurance_expiry", "stolen_status", "last_incident_date",
    "case_status", "last_seen_location", "last_seen_time", "observed_make",
    "observed_model", "observed_colour",
)
# Computed downstream from stored attributes, never fetched as such (design PDF §5).
DERIVED_ATTRIBUTES: dict[str, tuple[str, ...]] = {"insurance_status": ("insurance_expiry",)}

# Registry names are spliced into SQL by the executor, so they are held to identifier syntax here.
Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)?$")]
QualifiedColumn = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_]\w*\.[A-Za-z_]\w*$")]
Status = Literal["OK", "TIMEOUT", "DOWN", "ERROR"]

_NON_ALNUM = re.compile(r"[^A-Z0-9]")


def canonical_plate(raw: str) -> str:
    """Uppercase alphanumerics only: the one lookup token every module agrees on."""
    plate = _NON_ALNUM.sub("", raw.upper())
    if not plate:
        raise ValueError(f"plate {raw!r} contains no letters or digits")
    return plate


# --- Wrapper HTTP API (§5.1) --------------------------------------------------

class HealthResponse(BaseModel):
    source_id: str
    up: bool
    dbms: str | None = None
    ts: datetime | None = None
    error: str | None = None


class ColumnInfo(BaseModel):
    """One column as /schema publishes it.

    The matcher (mediator/matcher.py) reads is_pk / is_fk / sample_values; CLAUDE.md §5.1 names the
    same facts pk / fk / samples. Rather than force one consumer to change, the wire format carries
    both and _mirror keeps them in step, so a column can never report two different stories.
    """
    name: str
    type: str
    nullable: bool
    pk: bool
    fk: str | None = None  # "table.column" this column references, if any
    samples: list[Any] = Field(default_factory=list, max_length=20)
    is_pk: bool = False
    is_fk: bool = False
    fk_target: str | None = None
    sample_values: list[Any] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _mirror(self) -> ColumnInfo:
        self.is_pk = self.pk
        self.is_fk = self.fk is not None
        self.fk_target = self.fk.split(".")[0] if self.fk else None
        self.sample_values = self.samples
        return self


class TableInfo(BaseModel):
    table: str
    table_name: str = ""  # the matcher's spelling of `table`; filled by _mirror
    columns: list[ColumnInfo]

    @model_validator(mode="after")
    def _mirror(self) -> TableInfo:
        self.table_name = self.table
        return self


class SchemaResponse(BaseModel):
    source_id: str
    # Keyed by table name, because mediator/matcher.py iterates source_schema["tables"].items().
    tables: dict[str, TableInfo]


class QueryRequest(BaseModel):
    sql: str = Field(min_length=1)


class QueryResponse(BaseModel):
    rows: list[dict[str, Any]]
    row_count: int
    fetched_at: datetime
    elapsed_ms: int


class ErrorResponse(BaseModel):
    error: str


# --- Registry (§5.2 and design PDF §7, as normalised by registry_loader) ------

class RenderSpec(BaseModel):
    """How one source spells a canonical plate. Pure data; the executor applies it generically."""
    model_config = ConfigDict(extra="forbid")
    groups: list[Annotated[int, Field(gt=0)]] = Field(default_factory=list)  # [] = no grouping
    separator: str = ""
    case: Literal["upper", "lower"] = "upper"


class KeyPredicate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: Identifier
    match: Literal["exact", "in_variants"] = "exact"
    render: RenderSpec = Field(default_factory=RenderSpec)
    variant_map: dict[str, list[str]] = Field(default_factory=dict)  # the source's OCR error model
    max_variants: int = Field(default=64, ge=1, le=512)

    @model_validator(mode="after")
    def _variants_need_a_map(self) -> KeyPredicate:
        if self.match == "in_variants" and not self.variant_map:
            raise ValueError("match='in_variants' needs a non-empty variant_map")
        return self


class Aggregate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy: Literal["all", "latest"] = "all"
    global_attr: str | None = None
    # ORDER BY is pushed to the source only when the column sorts correctly as stored (epoch, timestamp),
    # never for DD/MM/YYYY text. Rows are never truncated either way; the latest is picked downstream.
    pushdown: bool = False

    @model_validator(mode="after")
    def _latest_needs_an_attr(self) -> Aggregate:
        if self.strategy == "latest" and not self.global_attr:
            raise ValueError("strategy='latest' needs a global_attr to order by")
        return self


class JoinSpec(BaseModel):
    """LEFT JOIN table ON left = right. LEFT, so a missing lookup row never hides a base row."""
    model_config = ConfigDict(extra="forbid")
    table: Identifier
    left: QualifiedColumn
    right: QualifiedColumn


class AttributeMapping(BaseModel):
    source_table: Identifier
    source_attr: Identifier
    global_attr: str  # checked against the registry's vocabulary, which a registry may extend
    transform: str | None = None  # named for Teammate C; the transport layer never applies it
    score: float = Field(default=1.0, ge=0.0, le=1.0)


class SourceSpec(BaseModel):
    source_id: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    display_name: str
    dbms: str
    base_url: str = Field(pattern=r"^https?://\S+$")
    authority: Literal["authoritative", "observational"]
    trust: float = Field(ge=0.0, le=1.0)
    timeout_ms: int = Field(gt=0)
    covers: list[str]
    key_predicate: KeyPredicate
    query_template: str
    table: Identifier
    joins: list[JoinSpec] = Field(default_factory=list)
    identity_authority: bool = False  # asked on every query to confirm the plate exists (design PDF UC1)
    aggregate: Aggregate = Field(default_factory=Aggregate)
    attribute_map: list[AttributeMapping] = Field(min_length=1)
    extra: dict[str, Any] = Field(default_factory=dict)  # registry keys we don't understand: kept, not dropped
    covers_unmapped: list[str] = Field(default_factory=list)  # covered but derived downstream; set in _consistent

    @field_validator("query_template")
    @classmethod
    def _read_only(cls, v: str) -> str:
        if not v.lstrip().upper().startswith("SELECT") or ";" in v:
            raise ValueError("query_template must be a single SELECT statement")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> SourceSpec:
        mapped = {m.global_attr for m in self.attribute_map}
        # covers[] declares what the source can contribute; attribute_map records column-level
        # correspondences only. THEFT covers stolen_status, which the integrator derives from
        # stolen_flag + recovered_flag + case_status, so no single column maps to it. Such entries
        # are kept (they must still drive source selection) and listed for the coverage report
        # rather than rejected. A covers[] typo therefore surfaces in
        # scripts/validate_registry.py as an attribute with no mapping, not as a hard failure.
        self.covers_unmapped = [a for a in self.covers if a not in mapped]
        if (agg := self.aggregate.global_attr) and agg not in mapped:
            raise ValueError(f"{self.source_id}: aggregate orders by unmapped attribute {agg!r}")
        reachable = {self.table.lower(), *(j.table.lower() for j in self.joins)}
        for m in self.attribute_map:
            if m.source_table.lower() not in reachable:
                raise ValueError(f"{self.source_id}: {m.source_table}.{m.source_attr} comes from a table that is "
                                 f"neither {self.table} nor declared in joins")
        return self


class Registry(BaseModel):
    sources: list[SourceSpec] = Field(min_length=1)
    global_attributes: list[str] = Field(default_factory=lambda: list(GLOBAL_ATTRIBUTES))
    derived: dict[str, list[str]] = Field(default_factory=lambda: {k: list(v) for k, v in DERIVED_ATTRIBUTES.items()})

    @model_validator(mode="after")
    def _consistent(self) -> Registry:
        ids = [s.source_id for s in self.sources]
        if dupes := sorted({i for i in ids if ids.count(i) > 1}):
            raise ValueError(f"duplicate source_id(s): {dupes}")
        stored = set(self.global_attributes)
        for name, inputs in self.derived.items():
            if name in stored:
                raise ValueError(f"derived attribute {name!r} is also declared as a stored global attribute")
            if unknown := [a for a in inputs if a not in stored]:
                raise ValueError(f"derived attribute {name!r} is built from unknown attribute(s) {unknown}")
        for s in self.sources:
            for m in s.attribute_map:
                if m.global_attr not in stored:
                    raise ValueError(f"{s.source_id}: attribute_map maps {m.source_attr!r} to unknown global "
                                     f"attribute {m.global_attr!r}")
        return self

    @property
    def vocabulary(self) -> list[str]:
        """Every attribute a caller may ask for: stored first, then derived."""
        return [*self.global_attributes, *self.derived]

    def resolve(self, attrs: list[str]) -> list[str]:
        """The stored attributes needed to answer `attrs`; derived ones expand to their inputs."""
        if unknown := [a for a in attrs if a not in self.global_attributes and a not in self.derived]:
            raise ValueError(f"unknown global attribute(s): {unknown}")
        return list(dict.fromkeys(x for a in attrs for x in self.derived.get(a, [a])))

    def by_id(self, source_id: str) -> SourceSpec | None:
        return next((s for s in self.sources if s.source_id == source_id), None)

    def coverage_matrix(self) -> dict[str, list[str]]:
        """attribute -> sources that can supply it; a derived attribute is covered by whoever supplies its inputs."""
        def suppliers(attrs: list[str]) -> list[str]:
            return sorted({s.source_id for s in self.sources for a in attrs if a in s.covers})
        return {a: suppliers(self.derived.get(a, [a])) for a in self.vocabulary}


# --- What the executor hands downstream (§5.3) --------------------------------

class SourceResult(BaseModel):
    source_id: str
    status: Status
    rows: list[dict[str, Any]] = Field(default_factory=list)  # RAW: source names, source formats
    row_count: int = Field(default=0, ge=0)
    sql_sent: str | None = None
    fetched_at: datetime | None = None
    elapsed_ms: int = Field(ge=0)
    error: str | None = None

    @model_validator(mode="after")
    def _status_is_unambiguous(self) -> SourceResult:
        if self.row_count != len(self.rows):
            raise ValueError(f"row_count={self.row_count} but {len(self.rows)} rows present")
        if self.status == "OK" and self.error is not None:
            raise ValueError("status OK cannot carry an error")
        if self.status != "OK" and not self.error:
            raise ValueError(f"status {self.status} requires an error message")
        if self.status != "OK" and self.rows:
            raise ValueError(f"status {self.status} cannot carry rows")
        return self


class PlanTrace(BaseModel):
    plate_raw: str
    plate_normalized: str
    requested_attrs: list[str]
    sources_selected: list[str]
    sources_skipped: dict[str, str]  # source_id -> reason
    total_elapsed_ms: int = Field(ge=0)


class FederationResponse(BaseModel):
    results: list[SourceResult]
    trace: PlanTrace

    def by_source(self, source_id: str) -> SourceResult | None:
        return next((r for r in self.results if r.source_id == source_id), None)
