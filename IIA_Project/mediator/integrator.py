"""
Result Integrator for Federated Mediator.
Performs GAV transformations, latest-wins aggregation, outer-joins on plate,
provenance tracking, deterministic conflict resolution, and decision execution.
"""

from datetime import datetime, date, timezone
from typing import Dict, Any, List, Optional
from mediator.catalog import get_source_catalog, get_mappings_for_source
from mediator.transforms import get_transform, norm_plate
from mediator.decide import evaluate_vehicle_decision, REFERENCE_TODAY

def integrate_results(execution_results: Dict[str, Any], canonical_plate: str, requested_sources: List[str]) -> Dict[str, Any]:
    catalog = get_source_catalog()
    sources_data = execution_results.get("sources_executed", {})

    profile: Dict[str, Any] = {
        "plate_number": canonical_plate,
        "owner_name": None,
        "vehicle_make": None,
        "vehicle_model": None,
        "vehicle_colour": None,
        "registration_date": None,
        "registration_status": None,
        "insurer_name": None,
        "policy_type": None,
        "insurance_start": None,
        "insurance_expiry": None,
        "insurance_status": None,
        "stolen_status": None,
        "last_incident_date": None,
        "case_status": None,
        "last_seen_location": None,
        "last_seen_time": None,
        "observed_make": None,
        "observed_model": None,
        "observed_colour": None,
        "puc_expiry": None,
        "provenance": {},
        "conflicts": [],
        "source_availability": {},
        "decision": "UNDETERMINED",
        "confidence": "LOW",
        "reasons": []
    }

    # Track raw values for conflict detection
    source_values_by_attr: Dict[str, Dict[str, Any]] = {}

    # Step 1: Process each source result
    for s_id in requested_sources:
        s_meta = catalog.get(s_id, {})
        authority = s_meta.get("authority", "OFFICIAL")
        trust = s_meta.get("trust_score", 0.8)

        s_exec = sources_data.get(s_id, {})
        status = s_exec.get("status", "DOWN")
        profile["source_availability"][s_id] = status

        if status != "OK":
            continue

        raw_rows = s_exec.get("rows", [])
        fetched_at = s_exec.get("fetched_at", datetime.now(timezone.utc).isoformat())
        mappings = get_mappings_for_source(s_id)

        if not raw_rows:
            continue

        # Step 2: Handle source-specific aggregation (latest-wins)
        chosen_row = None
        if s_id == "INS":
            # Pick row with latest policy_until
            def get_ins_key(r):
                pu = r.get("policy_until", "")
                try:
                    return datetime.strptime(pu, "%d/%m/%Y")
                except Exception:
                    return datetime.min
            chosen_row = max(raw_rows, key=get_ins_key)

        elif s_id == "CAM":
            # Pick row with latest captured_at
            def get_cam_key(r):
                ca = r.get("captured_at", "")
                try:
                    return datetime.fromisoformat(ca.replace("Z", "+00:00"))
                except Exception:
                    return datetime.min
            chosen_row = max(raw_rows, key=get_cam_key)

        elif s_id == "THEFT":
            # Pick row with latest reported_date
            def get_theft_key(r):
                try:
                    return int(r.get("reported_date") or 0)
                except Exception:
                    return 0
            chosen_row = max(raw_rows, key=get_theft_key)
        else:
            chosen_row = raw_rows[0]

        # Step 3: Apply transforms and map to global attributes
        for m in mappings:
            src_attr = m["source_attr"]
            glob_attr = m["global_attr"]
            trans_fn_name = m.get("transform_fn")
            tf = get_transform(trans_fn_name)

            if src_attr in chosen_row:
                raw_val = chosen_row[src_attr]
                clean_val = tf(raw_val)

                if clean_val is not None:
                    profile[glob_attr] = clean_val
                    profile["provenance"][glob_attr] = {
                        "source": s_id,
                        "authority": authority,
                        "trust": trust,
                        "fetched_at": fetched_at
                    }

                    if glob_attr not in source_values_by_attr:
                        source_values_by_attr[glob_attr] = {}
                    source_values_by_attr[glob_attr][s_id] = clean_val

        # Store extra helper attributes for derived calculations
        if s_id == "THEFT" and chosen_row:
            tf_yn = get_transform("yn_to_bool")
            profile["_theft_stolen_flag"] = tf_yn(chosen_row.get("stolen_flag"))
            profile["_theft_recovered_flag"] = tf_yn(chosen_row.get("recovered_flag"))
            profile["_theft_case_status"] = chosen_row.get("case_status")

    # Step 4: Compute derived attributes
    # 4.1 Insurance status
    ins_status_avail = profile["source_availability"].get("INS")
    if ins_status_avail in ("DOWN", "TIMEOUT"):
        profile["insurance_status"] = "UNKNOWN"
    elif "INS" in requested_sources and (not sources_data.get("INS", {}).get("rows")):
        profile["insurance_status"] = "NONE"
    else:
        expiry_str = profile.get("insurance_expiry")
        if not expiry_str:
            profile["insurance_status"] = "NONE"
        else:
            try:
                exp_date = datetime.strptime(expiry_str, "%Y-%m-%d").date()
                profile["insurance_status"] = "VALID" if exp_date >= REFERENCE_TODAY else "EXPIRED"
            except Exception:
                profile["insurance_status"] = "UNKNOWN"

    # 4.2 Stolen status
    theft_status_avail = profile["source_availability"].get("THEFT")
    if theft_status_avail in ("DOWN", "TIMEOUT"):
        profile["stolen_status"] = "UNKNOWN"
    elif "THEFT" in requested_sources and (not sources_data.get("THEFT", {}).get("rows")):
        profile["stolen_status"] = "NOT_REPORTED"
    else:
        is_stolen = profile.get("_theft_stolen_flag", False)
        is_recovered = profile.get("_theft_recovered_flag", False)
        cs = (profile.get("_theft_case_status") or "").upper()

        if is_stolen and not is_recovered and cs == "OPEN":
            profile["stolen_status"] = "STOLEN"
        elif is_recovered or cs == "CLOSED":
            profile["stolen_status"] = "RECOVERED"
        else:
            profile["stolen_status"] = "NOT_REPORTED"

    # Step 5: Conflict detection across overlapping attributes (REG vs CAM)
    overlap_checks = [
        ("make", profile.get("vehicle_make"), profile.get("observed_make")),
        ("model", profile.get("vehicle_model"), profile.get("observed_model")),
        ("colour", profile.get("vehicle_colour"), profile.get("observed_colour")),
    ]
    for attr_name, reg_val, cam_val in overlap_checks:
        if reg_val and cam_val and reg_val.strip().lower() != cam_val.strip().lower():
            profile["conflicts"].append({
                "attribute": f"vehicle_{attr_name}",
                "values_by_source": {
                    "REG": reg_val,
                    "CAM": cam_val
                },
                "resolution": "OFFICIAL authority (REG) preferred over OBSERVATIONAL (CAM)",
                "chosen": reg_val
            })

    # Step 6: Decision engine execution
    dec, conf, reasons = evaluate_vehicle_decision(profile, requested_sources)
    profile["decision"] = dec
    profile["confidence"] = conf
    profile["reasons"] = reasons

    # Clean up internal keys
    for k in ["_theft_stolen_flag", "_theft_recovered_flag", "_theft_case_status"]:
        profile.pop(k, None)

    return profile
