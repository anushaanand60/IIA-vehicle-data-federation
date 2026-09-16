"""
Rule-Based Decision Engine for Federated Mediator.
Strict ordered rules (first match wins) producing deterministic, explainable outcomes
with confidence level and human-readable justification reasons.
"""

from typing import Dict, Any, List, Tuple
from datetime import date

REFERENCE_TODAY = date(2026, 9, 4)

# A plate no asked source has ever heard of. Exported so the GUI can offer the onboarding
# wizard (app/tabs/onboarding.py) on exactly this decision, without matching on prose.
UNKNOWN_VEHICLE = "UNKNOWN VEHICLE — NOT REGISTERED"

def evaluate_vehicle_decision(profile: Dict[str, Any], requested_sources: List[str]) -> Tuple[str, str, List[str]]:
    """
    Evaluates vehicle profile against the 7 ordered rules (§10.2).
    Returns (decision, confidence, reasons).
    """
    reasons: List[str] = []
    source_avail = profile.get("source_availability", {})

    # Rule 1: THEFT source down or INS source down and question needs it
    if "THEFT" in requested_sources and source_avail.get("THEFT") in ("DOWN", "TIMEOUT"):
        return (
            "UNDETERMINED",
            "LOW",
            [f"THEFT records source is currently {source_avail.get('THEFT')}; cannot verify crime/stolen status safely."]
        )
    if "INS" in requested_sources and source_avail.get("INS") in ("DOWN", "TIMEOUT"):
        return (
            "UNDETERMINED",
            "LOW",
            [f"Insurance records source is currently {source_avail.get('INS')}; cannot verify insurance status safely."]
        )

    # Rule 2: stolen_status = STOLEN and case OPEN
    stolen_status = profile.get("stolen_status")
    case_status = profile.get("case_status")
    if stolen_status == "STOLEN" and case_status == "OPEN":
        inc_date = profile.get("last_incident_date", "recent date")
        reasons.append(f"Vehicle reported STOLEN on {inc_date} and police case status is OPEN.")
        return ("STOLEN — ALERT POLICE", "HIGH", reasons)

    # Rule 2b: officially scrapped/shredded. A shredded vehicle legally no longer exists, so a
    # sighting means the plate is being reused. Ordered above the registration and insurance
    # rules because "this vehicle was destroyed" outranks "its paperwork lapsed" -- otherwise a
    # scrapped car would be reported as merely UNINSURED.
    scrapped = (profile.get("incident_type") or "").strip().upper() == "SHREDDING"
    if scrapped:
        scrapped_on = profile.get("last_incident_date", "an earlier date")
        if profile.get("last_seen_time") is not None:
            loc = profile.get("last_seen_location", "a road camera")
            ts = profile.get("last_seen_time", "")
            reasons.append(
                f"Vehicle was officially scrapped/shredded on {scrapped_on} (police case CLOSED), "
                f"but its plate was sighted at {loc} ({ts}). A scrapped vehicle cannot lawfully be "
                f"on the road, so the plate is being reused."
            )
            return ("SCRAPPED — ALERT POLICE", "HIGH", reasons)
        reasons.append(
            f"Vehicle was officially scrapped/shredded on {scrapped_on}; its registration should "
            f"be void and it must not be driven."
        )
        return ("SCRAPPED — REGISTRATION VOID", "HIGH", reasons)

    # Rule 3: Plate seen by CAM but absent in REG
    cam_seen = profile.get("last_seen_time") is not None
    reg_present = profile.get("registration_status") is not None
    if cam_seen and not reg_present:
        loc = profile.get("last_seen_location", "road camera")
        ts = profile.get("last_seen_time", "")
        reasons.append(f"Vehicle plate sighted by camera at {loc} ({ts}) but has NO official registration record in REG authority.")
        return ("UNREGISTERED / SUSPICIOUS", "MEDIUM", reasons)

    # Rule 3b: the plate is simply unknown -- no registration, no policy, no crime record and no
    # sighting anywhere we asked. Ordered after the camera-only rule (a sighting makes it
    # UNREGISTERED / SUSPICIOUS, not unknown) and before the insurance rule, because accusing a
    # vehicle of being uninsured when no authority has ever heard of it is the wrong claim: the
    # right answer is "we have nothing on this plate -- onboard it or check the spelling".
    # Requires REG asked *and* OK: absence is only evidence when the source actually answered.
    reg_asked_ok = "REG" in requested_sources and source_avail.get("REG") == "OK"
    no_theft_record = profile.get("last_incident_date") is None and profile.get("case_status") is None
    no_insurance_record = not profile.get("insurance_expiry") and not profile.get("insurer_name")
    if reg_asked_ok and not reg_present and not cam_seen and no_theft_record and no_insurance_record:
        reasons.append("No registration, insurance, crime or camera record exists for this "
                       "plate in any asked source.")
        return (UNKNOWN_VEHICLE, "MEDIUM", reasons)

    # Rule 4: REG make/model/colour != CAM observed (>= 2 attrs)
    mismatches = 0
    mismatch_details = []

    reg_make = (profile.get("vehicle_make") or "").strip().lower()
    obs_make = (profile.get("observed_make") or "").strip().lower()
    if reg_make and obs_make and reg_make != obs_make:
        mismatches += 1
        mismatch_details.append(f"Make mismatch (REG: {profile.get('vehicle_make')} vs CAM: {profile.get('observed_make')})")

    reg_model = (profile.get("vehicle_model") or "").strip().lower()
    obs_model = (profile.get("observed_model") or "").strip().lower()
    if reg_model and obs_model and reg_model != obs_model:
        mismatches += 1
        mismatch_details.append(f"Model mismatch (REG: {profile.get('vehicle_model')} vs CAM: {profile.get('observed_model')})")

    reg_colour = (profile.get("vehicle_colour") or "").strip().lower()
    obs_colour = (profile.get("observed_colour") or "").strip().lower()
    if reg_colour and obs_colour and reg_colour != obs_colour:
        mismatches += 1
        mismatch_details.append(f"Colour mismatch (REG: {profile.get('vehicle_colour')} vs CAM: {profile.get('observed_colour')})")

    if mismatches >= 2:
        reasons.append(f"Visual identity discrepancy ({mismatches} attributes differ): " + "; ".join(mismatch_details) + ". Possible cloned plate.")
        return ("SUSPICIOUS — POSSIBLE CLONED PLATE", "MEDIUM", reasons)

    # Rule 5: No policy or insurance_expiry < today
    ins_status = profile.get("insurance_status")
    expiry = profile.get("insurance_expiry")
    if ins_status == "NONE":
        reasons.append("No active or past insurance policy found on record for this vehicle in INS authority.")
        return ("UNINSURED — REPORT", "HIGH", reasons)
    elif ins_status == "EXPIRED":
        reasons.append(f"Insurance policy has expired (expired on {expiry}, prior to reference evaluation date {REFERENCE_TODAY.isoformat()}).")
        return ("UNINSURED — REPORT", "HIGH", reasons)

    # Rule 6: registration_status != ACTIVE
    reg_status = profile.get("registration_status")
    if reg_status and reg_status != "ACTIVE":
        reasons.append(f"Vehicle registration status is {reg_status} (not ACTIVE).")
        return ("REGISTRATION INVALID — REPORT", "HIGH", reasons)

    # Rule 7: Otherwise -> CLEAR
    confidence = "HIGH" if cam_seen else "MEDIUM"
    reasons.append("Vehicle registration is ACTIVE.")
    if profile.get("insurance_status") == "VALID":
        reasons.append(f"Insurance is VALID until {profile.get('insurance_expiry')}.")
    if stolen_status in ("NOT_REPORTED", "RECOVERED"):
        reasons.append(f"Theft status is {stolen_status}.")
    if not cam_seen:
        reasons.append("No recent camera capture observed (confidence downgraded to MEDIUM).")
    else:
        reasons.append(f"Camera sighting matches registered vehicle specifications.")

    return ("CLEAR", confidence, reasons)
