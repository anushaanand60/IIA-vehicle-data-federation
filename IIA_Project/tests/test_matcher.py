import unittest
from fastapi.testclient import TestClient
from sources.reg.wrapper import app as reg_app
from sources.ins.wrapper import app as ins_app
from sources.theft.wrapper import app as theft_app
from sources.cam.wrapper import app as cam_app
from mediator.matcher import match_source_schema

class TestSchemaMatcher(unittest.TestCase):
    def test_reg_matcher(self):
        c = TestClient(reg_app)
        s = c.get("/schema").json()
        res = match_source_schema(s, theta=0.55)
        matches = {m["source_attr"]: m["global_attr"] for m in res["correspondences"]}
        
        self.assertEqual(matches.get("registration_no"), "plate_number")
        self.assertEqual(matches.get("full_name"), "owner_name")
        self.assertEqual(matches.get("make"), "vehicle_make")
        self.assertEqual(matches.get("model"), "vehicle_model")
        self.assertEqual(matches.get("reg_status"), "registration_status")
        
        # Unmapped extra columns
        unmapped_cols = [u["source_attr"] for u in res["unmapped"]]
        self.assertIn("fuel_type", unmapped_cols)
        self.assertIn("rto_code", unmapped_cols)

    def test_ins_matcher(self):
        c = TestClient(ins_app)
        s = c.get("/schema").json()
        res = match_source_schema(s, theta=0.55)
        matches = {m["source_attr"]: m["global_attr"] for m in res["correspondences"]}

        self.assertEqual(matches.get("vehicle_reg"), "plate_number")
        self.assertEqual(matches.get("policy_until"), "insurance_expiry")
        self.assertEqual(matches.get("policy_type"), "policy_type")
        
        unmapped_cols = [u["source_attr"] for u in res["unmapped"]]
        self.assertIn("premium_inr", unmapped_cols)

    def test_cam_matcher(self):
        c = TestClient(cam_app)
        s = c.get("/schema").json()
        res = match_source_schema(s, theta=0.55)
        matches = {m["source_attr"]: m["global_attr"] for m in res["correspondences"]}

        self.assertEqual(matches.get("plate_id"), "plate_number")
        self.assertEqual(matches.get("captured_at"), "last_seen_time")
        self.assertEqual(matches.get("observed_make"), "observed_make")
        
        unmapped_cols = [u["source_attr"] for u in res["unmapped"]]
        self.assertIn("ocr_confidence", unmapped_cols)

if __name__ == "__main__":
    unittest.main()
