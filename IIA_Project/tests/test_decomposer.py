import unittest
from mediator.decomposer import decompose_query
from mediator.planner import plan_query
from mediator.planner import plan_query
from tests.fixtures import inject_test_mappings

class TestDecomposer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        inject_test_mappings()

    def test_planner_uc1(self):
        plan = plan_query("DL-01-AB-1234", requested_attrs=["insurance_status", "insurance_expiry"])
        self.assertEqual(plan["plate"], "DL01AB1234")
        self.assertIn("INS", plan["sources"])
        self.assertIn("REG", plan["sources"])
        self.assertEqual(len(plan["sources"]), 2)

    def test_planner_uc2(self):
        plan = plan_query("DL01AB1234", requested_attrs=["all"])
        from mediator.catalog import get_source_catalog
        self.assertEqual(set(plan["sources"]), set(get_source_catalog()))  # every registered source (PUC too after UC6)
        self.assertGreaterEqual(len(plan["sources"]), 4)

    def test_decomposer_pushdown(self):
        sql_reg = decompose_query("REG", "DL01AB1234")
        self.assertIn("UPPER(REPLACE(REPLACE(", sql_reg)
        self.assertIn("'DL01AB1234'", sql_reg)
        self.assertIn("OWNERS", sql_reg)

        sql_ins = decompose_query("INS", "DL01AB1234")
        self.assertIn("POLICY_RECORDS", sql_ins)
        self.assertIn("INSURERS", sql_ins)

        sql_cam = decompose_query("CAM", "DL01AB1234")
        self.assertIn("PLATE_CAPTURES", sql_cam)
        self.assertIn("CAMERAS", sql_cam)

if __name__ == "__main__":
    unittest.main()
