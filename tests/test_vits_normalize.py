import unittest
import importlib.util
if importlib.util.find_spec("torch") is None:
    raise unittest.SkipTest("VITS normalization tests run in the speech container with PyTorch")
from scripts.vits_service import normalize


class VitsNormalizeTests(unittest.TestCase):
    def test_college_with_attached_kannada_suffix_is_speakable(self):
        self.assertEqual(normalize("collegeದಿಂದ", "kn"), "ಕಾಲೇಜುದಿಂದ")
        self.assertEqual(normalize("college से", "hi"), "कॉलेज से")
        self.assertEqual(normalize("college", "en"), "college")
        self.assertEqual(normalize("the collegeದಿಂದ", "kn"), "ಕಾಲೇಜುದಿಂದ")
        self.assertEqual(normalize("the college से", "hi"), "कॉलेज से")
        self.assertEqual(normalize("colleges", "kn"), "colleges")

    def test_class_codes_are_pronounced_and_numbers_remain_exact(self):
        self.assertEqual(normalize('कक्षा 10, अनुभाग B', 'hi'), 'कक्षा दस, अनुभाग बी')
        self.assertEqual(normalize('ತರಗತಿ 10, ವಿಭಾಗ B', 'kn'), 'ತರಗತಿ ಹತ್ತು, ವಿಭಾಗ ಬಿ')
        self.assertIn('ಪಿ ಯು ಸಿ', normalize('PUC', 'kn'))


if __name__ == "__main__":
    unittest.main()
