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

LATEST_BY = "latest_by:"


def _order_column(mappings: List[Dict[str, Any]]) -> Optional[tuple]:
    """(column, table, transform) the source declared as its recency order, or None if it declared none."""
    for m in mappings:
        aggregate = str(m.get("aggregate") or "")
        if aggregate.startswith(LATEST_BY):
            column = aggregate[len(LATEST_BY):].strip()
            if column:
                return column, m.get("source_table"), m.get("transform_fn")
    return None


def _sort_key(value: Any) -> tuple:
    """Total order over one source's ordering column.

    The mapping's own transform has already turned the stored encoding (DD/MM/YYYY text, epoch
    seconds, ISO timestamps) into an ISO string or a number, so all that is left is to keep numbers
    numeric, strings lexicographic (ISO sorts correctly that way) and missing values lowest. The
    rank keeps the comparison total even if one row's value is unparseable, so latest-wins degrades
    to "ignore that row" instead of raising.
    """
    if value is None or value == "":
        return (0, 0.0, "")
    if isinstance(value, bool):
        return (1, float(value), "")
    if isinstance(value, (int, float)):
        return (1, float(value), "")
    if isinstance(value, (datetime, date)):
        return (2, 0.0, value.isoformat())
    text = str(value).strip()
    if not text:
        return (0, 0.0, "")
    try:
        return (1, float(text), "")
    except ValueError:
        return (2, 0.0, text)


def _pick_latest_row(rows: List[Dict[str, Any]], mappings: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Latest-wins driven by the registry: the source says which of its columns means "newest"."""
    spec = _order_column(mappings)
    if not spec:
        return rows[0]  # nothing declared: the order the wrapper returned stands
    column, table, transform_fn = spec
    transform = get_transform(transform_fn)

    def key(row: Dict[str, Any]) -> tuple:
        raw = row.get(column)
        if raw is None and table:
            raw = row.get(f"{table}__{column}")  # decomposer aliases duplicate column names
        try:
            return _sort_key(transform(raw))
        except Exception:
            return _sort_key(None)

    return max(rows, key=key)


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

        # Step 2: latest-wins, read out of this source's own mappings (aggregate "latest_by:<col>")
        # instead of a branch per source id: a new source orders its rows by registering a mapping.
        chosen_row = _pick_latest_row(raw_rows, mappings)

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

    # Step 4: Compute derived attributes
    # 4.1 Insurance status (derived only when this question asked INS)
    ins_status_avail = profile["source_availability"].get("INS")
    if "INS" not in requested_sources:
        profile["insurance_status"] = None  # not asked: no claim either way
    elif ins_status_avail in ("DOWN", "TIMEOUT", "ERROR"):  # a failed answer is not "no policy"
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

    # 4.2 Stolen status (derived only when this question asked THEFT)
    theft_status_avail = profile["source_availability"].get("THEFT")
    if "THEFT" not in requested_sources:
        profile["stolen_status"] = None  # not asked: no claim either way
    elif theft_status_avail in ("DOWN", "TIMEOUT", "ERROR"):  # a failed answer is not "not reported"
        profile["stolen_status"] = "UNKNOWN"
    elif "THEFT" in requested_sources and (not sources_data.get("THEFT", {}).get("rows")):
        profile["stolen_status"] = "NOT_REPORTED"
    else:
        # The flags come from the mapped global attributes (stolen_flag / recovered_flag /
        # case_status), so the source's own encoding is already the registry's problem, not ours.
        tf_yn = get_transform("yn_to_bool")
        is_stolen = tf_yn(profile.get("stolen_flag")) or False
        is_recovered = tf_yn(profile.get("recovered_flag")) or False
        cs = str(profile.get("case_status") or "").upper()

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

    return profile
