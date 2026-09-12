"""
Query Planner for Federated Mediator.
Analyzes global query attributes and selects minimal covering sources from SOURCE_CATALOG.
"""

from typing import List, Dict, Any, Optional
from mediator.transforms import norm_plate
from mediator.catalog import get_source_catalog

def plan_query(plate: str, requested_attrs: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Given a plate string (any format) and optional list of global attributes,
    normalizes the plate key and determines the minimal set of covering sources.
    """
    canonical_plate = norm_plate(plate)
    catalog = get_source_catalog()

    if not requested_attrs or "all" in [a.lower() for a in requested_attrs]:
        # UC2: Complete history - select all active sources in catalog
        selected_sources = list(catalog.keys())
        all_attrs = []
        for s_id, s_info in catalog.items():
            all_attrs.extend(s_info.get("covers", []))
        active_attrs = list(set(all_attrs))
    else:
        active_attrs = list(set(requested_attrs))
        selected_sources = set()

        # Find sources covering requested attributes
        for attr in active_attrs:
            covered = False
            for s_id, s_info in catalog.items():
                if attr in s_info.get("covers", []):
                    selected_sources.add(s_id)
                    covered = True
            # Check derived attributes coverage
            if attr in ("insurance_status", "insurance_expiry", "insurance_start"):
                selected_sources.add("INS")
                selected_sources.add("REG") # UC1 needs REG to confirm vehicle existence
            elif attr in ("stolen_status", "last_incident_date", "case_status"):
                selected_sources.add("THEFT")
            elif attr in ("last_seen_location", "last_seen_time", "observed_make", "observed_model", "observed_colour"):
                selected_sources.add("CAM")

        selected_sources = list(selected_sources)

    return {
        "plate": canonical_plate,
        "raw_plate": plate,
        "requested_attrs": active_attrs,
        "sources": selected_sources,
        "source_count": len(selected_sources)
    }
