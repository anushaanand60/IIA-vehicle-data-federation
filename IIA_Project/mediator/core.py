"""
Mediator Core Engine.
Coordinates planning, federated execution, GAV integration, and plan tracing.
"""

from typing import Dict, Any, List, Optional
from mediator.planner import plan_query
from mediator.executor import execute_federated_plan, check_all_sources_health
from mediator.integrator import integrate_results

def run_global_query(plate: str, requested_attrs: Optional[List[str]] = None) -> Dict[str, Any]:
    """
    Executes an end-to-end global query:
      1. Normalize plate and plan sources (UC1/UC2/UC3)
      2. Decompose and execute SQL sub-queries across wrappers in parallel
      3. Integrate results, resolve conflicts, evaluate decision rules
    Returns:
      {"profile": VEHICLE_PROFILE, "plan_trace": PLAN_TRACE}
    """
    plan = plan_query(plate, requested_attrs)
    exec_res = execute_federated_plan(plan["sources"], plan["plate"])
    profile = integrate_results(exec_res, plan["plate"], plan["sources"])

    plan_trace = {
        "canonical_plate": plan["plate"],
        "raw_plate": plan["raw_plate"],
        "requested_attrs": plan["requested_attrs"],
        "sources_contacted": plan["sources"],
        "source_count": plan["source_count"],
        "total_elapsed_ms": exec_res["total_elapsed_ms"],
        "sources_detail": exec_res["sources_executed"],
        "sqls": exec_res["sqls"]
    }

    return {
        "profile": profile,
        "plan_trace": plan_trace
    }
