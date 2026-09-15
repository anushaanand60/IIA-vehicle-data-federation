"""Validate a registry and print its coverage matrix (attribute x source).

    python scripts/validate_registry.py [path]     # default: mediator/meta.db, mappings.json, else the mock

Exit codes: 0 complete, 1 valid but some attribute has no source, 2 malformed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # runnable as a plain script

from mediator.registry_loader import RegistryError, default_registry_path, load_registry  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0]) if args else default_registry_path()
    try:
        registry = load_registry(path)
    except RegistryError as exc:
        print(f"REGISTRY INVALID ({path}):\n  {exc}", file=sys.stderr)
        return 2

    ids = [s.source_id for s in registry.sources]
    print(f"registry: {path}")
    for s in registry.sources:
        joins = ",".join(j.table for j in s.joins) or "-"
        role = "  (always asked: confirms the plate exists)" if s.identity_authority else ""
        print(f"  {s.source_id:<6} {s.dbms:<11} {s.authority:<13} trust={s.trust:.2f} timeout={s.timeout_ms}ms "
              f"match={s.key_predicate.match:<11} joins={joins:<9} {s.base_url}{role}")

    matrix = registry.coverage_matrix()
    labels = {a: f"{a} (derived)" if a in registry.derived else a for a in registry.vocabulary}
    width = max(map(len, labels.values())) + 2
    print("\n" + "global attribute".ljust(width) + " ".join(f"{i:>6}" for i in ids))
    for attr in registry.vocabulary:
        print(labels[attr].ljust(width) + " ".join(f"{'X' if i in matrix[attr] else '.':>6}" for i in ids))
    for name, inputs in registry.derived.items():
        print(f"  {name} is computed downstream from: {', '.join(inputs)}")

    uncovered = [a for a in registry.vocabulary if not matrix[a]]
    if uncovered:
        print(f"\nUNCOVERED: {', '.join(uncovered)}")
        return 1
    print(f"\nOK: {len(ids)} sources cover all {len(registry.vocabulary)} attributes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
