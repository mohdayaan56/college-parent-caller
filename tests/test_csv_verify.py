import unittest
from pathlib import Path

from app.csv_store import CsvError, find_by_phone, load_students
from app.phone import normalize_phone, phones_match
from app.verify import names_match

from tests.support import CsvFixtureMixin, DEMO_PHONE, FIXTURE_CSV


class PhoneTests(unittest.TestCase):
    def test_normalize_india(self):
        self.assertEqual(normalize_phone("9876543210"), "+919876543210")
        self.assertEqual(normalize_phone("+91 98765 43210"), "+919876543210")
        self.assertTrue(phones_match("09876543210", "+919876543210"))
        self.assertFalse(phones_match("+449876543210", "+919876543210"))


class CsvTests(CsvFixtureMixin, unittest.TestCase):
    def test_sample_row(self):
        rows = load_students()
        ahmed = next(r for r in rows if r.student_id == "1001")
        self.assertEqual(ahmed.parent_name, "Mohammed Khan")
        self.assertEqual(ahmed.parent_phone, DEMO_PHONE)
        self.assertEqual(ahmed.marks["maths"], 85)
        self.assertEqual(ahmed.total, 85 + 91 + 78 + 88)
        self.assertEqual(ahmed.percentage, round(ahmed.total / 400 * 100, 2))

    def test_lookup_phone(self):
        found = find_by_phone("9876543210")
        self.assertEqual(found[0].student_name, "Ahmed Khan")

    def test_missing_csv(self):
        with self.assertRaises(CsvError):
            load_students(Path("data/does-not-exist.csv"))

    def test_fixture_file_exists(self):
        self.assertTrue(FIXTURE_CSV.is_file())


class VerifyTests(unittest.TestCase):
    def test_full_name(self):
        self.assertTrue(names_match("this is Mohammed Khan", "Mohammed Khan"))

    def test_wrong_name(self):
        self.assertFalse(names_match("Random Person", "Mohammed Khan"))

    def test_surname_alone_does_not_verify(self):
        self.assertFalse(names_match("Khan", "Mohammed Khan"))


if __name__ == "__main__":
    unittest.main()
