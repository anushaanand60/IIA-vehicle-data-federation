"""Regenerate tests/fixtures/source_results.json: real executor output over the mocks, annotated.

    python tests/fixtures/make_source_results.py

The responses are captured rather than typed, so every SQL string and raw row is exactly what the
executor emits. The prose around them (story, look_at) is the hand-written handoff to Teammate C.
Re-run after any change to the mock schemas, the mock registry or the executor.
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mediator.executor import execute  # noqa: E402
from mediator.registry_loader import MOCK_REGISTRY, normalize_registry  # noqa: E402
from sources._mock.run_all import running  # noqa: E402

OUT = Path(__file__).resolve().parent / "source_results.json"
PORTS = {"REG": 17001, "INS": 17002, "THEFT": 17003, "CAM": 17004}

HEADER = {
    "about": ("FederationResponse examples for the six story vehicles, one narrow UC1 request and one insurer-down "
              "variant: the handoff from the transport layer to Teammate C (transforms, integration, conflicts, "
              "decisions). Rows are RAW: source column names and source value formats, exactly as each agency "
              "stores them. The schemas are the team design PDF's (section 3)."),
    "regenerate": "python tests/fixtures/make_source_results.py (starts its own mocks on ports 17001-17004)",
    "contract": "mediator/contract.py. Load a case with FederationResponse.model_validate(case['response']).",
    "status_rules": {
        "OK": "The wrapper answered. Zero rows is still OK: absence is data.",
        "TIMEOUT": "No reply within the source's timeout_ms, or the database cancelled a slow statement (HTTP 504). Undetermined.",
        "DOWN": "No TCP connection to the wrapper, or the wrapper replied 503 because its own database is unreachable. Undetermined.",
        "ERROR": "Our bug: the guard or the database rejected the SQL, or the reply was not valid JSON. Undetermined.",
    },
    "selection_rules": ("A source is asked when it covers a requested attribute; derived attributes such as insurance_status "
                        "count as their inputs. REG is also asked on every narrow request to confirm the plate is "
                        "registered, and then its row holds registration_no only. Skipped sources carry a reason."),
    "source_formats": {
        "REG": ("one row per plate; VEHICLE_REGISTRATION LEFT JOIN OWNERS. registration_no 'DL01AB1234'; registered_on "
                "'YYYY-MM-DD'; reg_status ACTIVE / SUSPENDED / CANCELLED; make may be null or misspelt"),
        "INS": ("many policies per plate, unordered; POLICY_RECORDS LEFT JOIN INSURERS. vehicle_reg 'DL-01-AB-1234'; "
                "policy_start and policy_until are DD/MM/YYYY text; policy_type THIRD_PARTY / COMPREHENSIVE"),
        "THEFT": ("many incidents per plate, newest first. vehicle_number 'dl 01 ab 1234'; reported_date Unix seconds; "
                  "incident_type THEFT / SHREDDING / HIT_AND_RUN; stolen_flag and recovered_flag Y/N; case_status OPEN/CLOSED"),
        "CAM": ("many sightings per plate, newest first; PLATE_CAPTURES LEFT JOIN CAMERAS. plate_id is raw OCR and may be "
                "misread O<->0 or I<->1 ('DLOIAB1234'); captured_at 'YYYY-MM-DD HH:MM:SS+05:30'; observed_make and "
                "observed_model upper case; observed_colour may be null"),
    },
}

CASES = [
    {"id": "clean", "plate_entered": "dl 01 ab 1234",
     "story": "Clean vehicle. Registered, insured, never reported stolen, and the cameras saw the registered car.",
     "look_at": [
         "THEFT is OK with zero rows. That is data (no incident on record), not a failure.",
         "CAM matched 'DLOIAB1234' as well as 'DL01AB1234': variant expansion found the O/I misread. Group CAM rows by the plate you asked for, not by plate_id.",
         "REG full_name arrives through the OWNERS join and INS insurer_name through the INSURERS join; both are ordinary columns in the rows.",
         "REG says make 'Maruti Suzuki' / colour 'Red', CAM says 'MARUTI SUZUKI' / 'red'. Same car, different casing: normalise before comparing."]},
    {"id": "expired", "plate_entered": "DL-05-CD-9876",
     "story": "Policy expired on 12/07/2026.",
     "look_at": [
         "INS returns two policies with no ORDER BY: latest-by insurance_expiry is yours to apply after parse_ddmmyyyy.",
         "Trap: policy_until is DD/MM/YYYY text, so a string sort picks '19/07/2025' over '12/07/2026'.",
         "The later policy is THIRD_PARTY and the earlier COMPREHENSIVE: policy_type can change between renewals."]},
    {"id": "no_policy", "plate_entered": "DL09KL3321",
     "story": "Registered and on the road, but no insurance policy row exists at all: the uninsured vehicle.",
     "look_at": [
         "INS is OK with rows [] and error null. The insurer answered and holds no policy: insurance_status NONE.",
         "Compare with case 'ins_down': identical rows, opposite meaning.",
         "CAM captured it as 'DLO9KL3321' (0 read as O)."]},
    {"id": "stolen", "plate_entered": "hr 26 ef 4455",
     "story": "Reported stolen and the police case is still open. It is also insured and still being seen on camera.",
     "look_at": [
         "THEFT rows arrive newest first (ORDER BY reported_date DESC, pushed down because it is an integer). rows[0] is the current state: incident_type THEFT, stolen_flag Y, recovered_flag N, case_status OPEN.",
         "reported_date is Unix seconds (an integer), not a date string.",
         "CAM saw it twice after the theft; last_seen_location (CAMERAS.location_name) matters for the Ministry report.",
         "The live contract test uses this plate because every source holds at least one row for it."]},
    {"id": "cloned", "plate_entered": "UP-16-GH-1122",
     "story": "Possible cloned plate: REG registers a white Hyundai Creta, the cameras keep seeing a silver Hyundai Venue.",
     "look_at": [
         "The conflict is passed through untouched: REG model 'Creta' / colour 'White' against CAM observed_model 'VENUE' / observed_colour 'silver'.",
         "REG is authoritative (trust 0.95) and CAM observational (0.60), but CAM is consistent across two sightings, one of them misread as 'UPI6GH1122'.",
         "Do not merge REG and CAM rows into one vehicle on plate alone (INTEGRATION_REPORT B4)."]},
    {"id": "unregistered", "plate_entered": "MH12IJ7788",
     "story": "Seen by a camera, absent from registration and from insurance.",
     "look_at": [
         "REG is OK with zero rows: the RTO answered and has never registered this plate.",
         "CAM also matched 'MH121J7788': the letter I read as the digit 1. The OCR error model runs both ways.",
         "The only evidence is one observational source (trust 0.60)."]},
    {"id": "insured_query", "plate_entered": "DL05CD9876", "attrs": ["insurance_status"],
     "story": "UC1, 'is this vehicle insured?': a narrow request for the derived attribute insurance_status.",
     "look_at": [
         "insurance_status is never stored. The planner expanded it to insurance_expiry, so INS was asked for vehicle_reg and policy_until only.",
         "REG was asked as well, but only for registration_no: its single row confirms the plate is registered.",
         "THEFT and CAM were skipped; trace.sources_skipped says why. The plan trace shows 2 sources, as in the demo script."]},
    {"id": "ins_down", "plate_entered": "DL09KL3321", "kill": "INS",
     "story": "Same vehicle as 'no_policy', but the insurer's laptop has dropped off the network when the query runs.",
     "look_at": [
         "INS is DOWN with rows [] and an error message. The rows look exactly like 'no_policy'; only status and error differ.",
         "The only defensible insurance decision is 'undetermined'. Never read DOWN as 'no policy'.",
         "The other three sources still answered: a dead source degrades the answer, it does not crash the query.",
         "INS failed fast (no TCP connection), so the whole query still returned well under 2 s."]},
]


def main() -> None:
    entries = json.loads(MOCK_REGISTRY.read_text(encoding="utf-8"))["sources"]
    for entry in entries:
        entry["base_url"] = f"http://127.0.0.1:{PORTS[entry['source_id']]}"
    registry = normalize_registry(entries)
    captured = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, running(ports=PORTS, db_dir=tmp) as procs:
        for case in CASES:  # the kill case is last, so it cannot disturb the others
            if victim := case.get("kill"):
                procs[victim].kill()
                procs[victim].wait(timeout=10)
            response = execute(registry, case["plate_entered"], case.get("attrs"))
            documented = {k: v for k, v in case.items() if k != "kill"}
            captured.append(documented | {"response": json.loads(response.model_dump_json())})
    OUT.write_text(json.dumps(HEADER | {"cases": captured}, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(captured)} cases)")


if __name__ == "__main__":
    main()
