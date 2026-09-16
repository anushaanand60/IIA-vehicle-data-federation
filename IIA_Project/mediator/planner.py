"""
Query Planner for Federated Mediator.
Analyzes global query attributes and selects the covering sources from SOURCE_CATALOG.

Metadata only: coverage comes from SOURCE_CATALOG.covers, a derived attribute expands through
"derived_from" in mediator/schema.py, and a question whose attributes are flagged
"needs_identity_check" also asks every catalog source flagged identity_authority. No source is
named here, so registering a source is enough to have it planned.
"""

from typing import Any, Dict, List, Optional

from mediator.catalog import get_source_catalog
from mediator.schema import GLOBAL_SCHEMA_ATTRIBUTES
from mediator.transforms import norm_plate


def plan_query(plate: str, requested_attrs: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Given a plate string (any format) and optional list of global attributes,
    normalizes the plate key and determines the minimal set of covering sources.
    """
    canonical_plate = norm_plate(plate)
    catalog = get_source_catalog()

    if not requested_attrs or "all" in [a.lower() for a in requested_attrs]:
        # UC2: complete history - every source in the catalog
        selected_sources = list(catalog.keys())
        active_attrs = sorted({attr for s_info in catalog.values() for attr in s_info.get("covers", [])})
    else:
        active_attrs = list(dict.fromkeys(requested_attrs))  # keep the caller's order, drop repeats
        wanted = set(active_attrs)
        for attr in active_attrs:
            wanted.update(GLOBAL_SCHEMA_ATTRIBUTES.get(attr, {}).get("derived_from", []))
        selected = {s_id for s_id, s_info in catalog.items() if wanted & set(s_info.get("covers", []))}
        if any(GLOBAL_SCHEMA_ATTRIBUTES.get(a, {}).get("needs_identity_check") for a in active_attrs):
            # e.g. UC1 "is X insured?" also confirms the vehicle exists at the registration authority
            selected.update(s_id for s_id, s_info in catalog.items() if s_info.get("identity_authority"))
        selected_sources = [s_id for s_id in catalog if s_id in selected]  # keep catalog order

    return {
        "plate": canonical_plate,
        "raw_plate": plate,
        "requested_attrs": active_attrs,
        "sources": selected_sources,
        "source_count": len(selected_sources)
    }
