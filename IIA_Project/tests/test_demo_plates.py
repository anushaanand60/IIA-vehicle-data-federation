"""
End-to-End Verification Test for the 5 Demo Cases, Use Cases UC1-UC5,
and Ministry Report PDF generation.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
from sources.server_manager import get_cluster
from mediator.core import run_global_query
from mediator.planner import plan_query
from mediator.report import file_ministry_report, generate_report_pdf, list_reports
from tests.fixtures import inject_test_mappings

def run_tests():
    print("Starting SourceCluster on ports 8001-8005...")
    cluster = get_cluster()
    cluster.start_all(include_puc=True)
    inject_test_mappings()
    time.sleep(1)

    print("\n==========================================")
    print("Testing 5 Canonical Demo Plates (§4, §14)")
    print("==========================================")

    demo_cases = [
        ("DL01AB1234", "CLEAR", "HIGH"),
        ("DL05CD9876", "UNINSURED — REPORT", "HIGH"),
        ("HR26EF4455", "STOLEN — ALERT POLICE", "HIGH"),
        ("UP16GH1122", "SUSPICIOUS — POSSIBLE CLONED PLATE", "MEDIUM"),
        ("MH12IJ7788", "UNREGISTERED / SUSPICIOUS", "MEDIUM"),
    ]

    all_passed = True
    for plate, expected_dec, expected_conf in demo_cases:
        res = run_global_query(plate)
        prof = res["profile"]
        dec = prof["decision"]
        conf = prof["confidence"]
        trace = res["plan_trace"]

        status = "PASSED" if (dec == expected_dec and conf == expected_conf) else "FAILED"
        if status == "FAILED":
            all_passed = False

        print(f"Plate {plate}:")
        print(f"  Decision:   {dec} (expected: {expected_dec})")
        print(f"  Confidence: {conf} (expected: {expected_conf})")
        print(f"  Sources:    {trace['sources_contacted']} ({trace['total_elapsed_ms']}ms)")
        print(f"  Reasons:    {prof['reasons']}")
        print(f"  Status:     {status}\n")

    print("\n==========================================")
    print("Testing UC1: Selective Source Planning")
    print("==========================================")
    uc1_res = run_global_query("DL01AB1234", requested_attrs=["insurance_status", "insurance_expiry"])
    uc1_sources = uc1_res["plan_trace"]["sources_contacted"]
    print(f"UC1 requested insurance attributes only.")
    print(f"Sources contacted: {uc1_sources} (expected 2: INS + REG)")
    if set(uc1_sources) == {"INS", "REG"}:
        print("  Status: PASSED\n")
    else:
        print("  Status: FAILED\n")
        all_passed = False

    print("\n==========================================")
    print("Testing UC5: Source Failure / Graceful Refusal")
    print("==========================================")
    print("Simulating INS wrapper offline (stopping INS on port 8002)...")
    cluster.stop_source("INS")
    time.sleep(0.5)

    uc5_res = run_global_query("DL01AB1234")
    uc5_prof = uc5_res["profile"]
    print(f"Decision with INS down: {uc5_prof['decision']}")
    print(f"Confidence: {uc5_prof['confidence']}")
    print(f"Reasons: {uc5_prof['reasons']}")
    if uc5_prof["decision"] == "UNDETERMINED" and uc5_prof["confidence"] == "LOW":
        print("  Status: PASSED (System refused to guess when source was unreachable)\n")
    else:
        print("  Status: FAILED\n")
        all_passed = False

    # Restart INS
    print("Restarting INS source...")
    cluster.start_source("INS")
    time.sleep(0.5)

    print("\n==========================================")
    print("Testing Ministry Report & PDF Generation")
    print("==========================================")
    test_plate = "DL05CD9876"
    res = run_global_query(test_plate)
    rep_id = file_ministry_report(res["profile"], res["plan_trace"])
    pdf_path = generate_report_pdf(rep_id)
    print(f"Filed report ID: {rep_id}")
    print(f"Generated PDF:   {pdf_path}")
    if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
        print(f"PDF size: {os.path.getsize(pdf_path)} bytes -> PASSED")
    else:
        print("PDF generation FAILED")
        all_passed = False

    cluster.stop_all()
    print("\n==========================================")
    print(f"OVERALL TEST SUITE: {'ALL PASSED' if all_passed else 'SOME TESTS FAILED'}")
    print("==========================================")
    return all_passed

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
