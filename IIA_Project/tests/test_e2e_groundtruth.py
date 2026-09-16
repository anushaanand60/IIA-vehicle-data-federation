import unittest
import csv
import os
import time
import pytest
from mediator.core import run_global_query

GROUND_TRUTH_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "mediator_test_cases.csv")
from tests.fixtures import inject_test_mappings


@pytest.mark.usefixtures("local_cluster")
class TestE2EGroundTruth(unittest.TestCase):
    # The cluster itself is started/stopped once for the whole test session by the `local_cluster`
    # fixture in tests/conftest.py (`usefixtures` above), not by this class -- two test modules
    # each starting/stopping the same process-wide sources.server_manager cluster independently is
    # what caused the intermittent DL01AB1234 -> UNDETERMINED race this class used to hit.
    @classmethod
    def setUpClass(cls):
        inject_test_mappings()
        time.sleep(1)

    def test_demo_ground_truth(self):
        self.assertTrue(os.path.exists(GROUND_TRUTH_PATH), "ground_truth.csv must exist")
        with open(GROUND_TRUTH_PATH, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                plate = row["plate_number"]
                expected_dec = row["expected_decision"]
                expected_conf = row["expected_confidence"]

                res = run_global_query(plate)
                prof = res["profile"]
                self.assertEqual(
                    prof["decision"],
                    expected_dec,
                    f"Decision mismatch for {plate}: got {prof['decision']}, expected {expected_dec}"
                )
                self.assertEqual(
                    prof["confidence"],
                    expected_conf,
                    f"Confidence mismatch for {plate}: got {prof['confidence']}, expected {expected_conf}"
                )

if __name__ == "__main__":
    unittest.main()
