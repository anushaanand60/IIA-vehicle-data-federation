"""
Mediator Core Engine.
Coordinates planning, federated execution, GAV integration, and plan tracing.
"""

import sys
from dataclasses import asdict
from typing import Dict, Any, List, Optional
from mediator.planner import plan_query
from mediator.executor import execute_federated_plan, check_all_sources_health
from mediator.integrator import integrate_results
from mediator import watchlist
from mediator import catalog
from mediator.risk import score as risk_score


def _annotate(profile: Dict[str, Any]) -> None:
    """Attach the risk score and any watchlist alerts (Task 2.3).

    Both are *commentary on* the decision, never part of it, so neither may ever change or block
    the answer: a broken meta.db degrades to "no score, no alerts" and the verdict still ships.
    That is the same refuse-don't-crash rule the executor applies to a dead source.
    """
    try:
        profile["risk"] = asdict(risk_score(profile))
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[core] risk scoring failed for {profile.get('plate_number')}: {exc}",
              file=sys.stderr)
        profile["risk"] = None
    try:
        profile["alerts"] = watchlist.check(profile)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[core] watchlist check failed for {profile.get('plate_number')}: {exc}",
              file=sys.stderr)
        profile["alerts"] = []

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
    _annotate(profile)

    try:
        catalog.log_query({
            "plate": plan["plate"],
            "requested_attrs": plan["requested_attrs"],
            "sources_asked": plan["sources"],
            "statuses": profile.get("source_availability", {}),
            "decision": profile.get("decision"),
            "confidence": profile.get("confidence"),
            "elapsed_ms": exec_res["total_elapsed_ms"],
        })
    except Exception as exc:  # pragma: no cover - defensive, mirrors _annotate above
        print(f"[core] query audit log failed for {plan.get('plate')}: {exc}", file=sys.stderr)

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
