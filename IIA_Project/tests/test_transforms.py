import unittest
from mediator.transforms import (
    norm_plate, parse_ddmmyyyy, epoch_to_date, yn_to_bool,
    tinyint_to_bool, status_map, title_case, fix_make
)

class TestTransforms(unittest.TestCase):
    def test_norm_plate(self):
        self.assertEqual(norm_plate("dl 01 ab 1234"), "DL01AB1234")
        self.assertEqual(norm_plate("DL-01-AB-1234"), "DL01AB1234")
        self.assertEqual(norm_plate("Dl 05 Cd 9876"), "DL05CD9876")
        self.assertEqual(norm_plate("HR26EF4455"), "HR26EF4455")
        self.assertIsNone(norm_plate(None))

    def test_parse_ddmmyyyy(self):
        self.assertEqual(parse_ddmmyyyy("15/01/2026"), "2026-01-15")
        self.assertEqual(parse_ddmmyyyy("04/09/2024"), "2024-09-04")
        self.assertIsNone(parse_ddmmyyyy(None))

    def test_epoch_to_date(self):
        # 1787337000 is in 2026
        d = epoch_to_date(1787337000)
        self.assertTrue(d.startswith("2026"))
        self.assertIsNone(epoch_to_date(None))

    def test_booleans(self):
        self.assertTrue(yn_to_bool("Y"))
        self.assertFalse(yn_to_bool("N"))
        self.assertTrue(tinyint_to_bool(1))
        self.assertFalse(tinyint_to_bool(0))

    def test_fix_make(self):
        self.assertEqual(fix_make("hyundia"), "Hyundai")
        self.assertEqual(fix_make("maruti"), "Maruti Suzuki")
        self.assertEqual(fix_make("Toyota"), "Toyota")

    def test_status_map(self):
        self.assertEqual(status_map("ACT"), "ACTIVE")
        self.assertEqual(status_map("ACTIVE"), "ACTIVE")
        self.assertEqual(status_map("SUSP"), "SUSPENDED")

if __name__ == "__main__":
    unittest.main()
