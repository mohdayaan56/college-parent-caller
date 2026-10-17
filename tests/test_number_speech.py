import unittest
from scripts.number_speech import cardinal, normalize_numbers


class NumberSpeechTests(unittest.TestCase):
    def test_total_and_marks_use_cardinals_in_every_language(self):
        expected = {
            'en': ('three hundred and forty-two out of four hundred', 'eighty-five'),
            'hi': ('तीन सौ बयालीस out of चार सौ', 'पचासी'),
            'kn': ('ಮುನ್ನೂರ ನಲವತ್ತೆರಡು out of ನಾನೂರು', 'ಎಂಭತ್ತೈದು'),
        }
        for language, (total, score) in expected.items():
            self.assertEqual(normalize_numbers('342 out of 400', language), total)
            self.assertEqual(normalize_numbers('85', language), score)

    def test_ratios_and_percentages_preserve_value_and_precision(self):
        for language, expected in {
            'en': 'three hundred and forty-two out of four hundred',
            'hi': 'चार सौ में से तीन सौ बयालीस',
            'kn': 'ನಾನೂರರಲ್ಲಿ ಮುನ್ನೂರ ನಲವತ್ತೆರಡು',
        }.items():
            self.assertEqual(normalize_numbers('342/400', language), expected)
        self.assertEqual(normalize_numbers('85.50%', 'en'), 'eighty-five point five zero%')
        self.assertEqual(normalize_numbers('0.05', 'hi'), 'शून्य दशमलव शून्य पाँच')
        self.assertEqual(normalize_numbers('85.5', 'kn'), 'ಎಂಭತ್ತೈದು ದಶಮಾಂಶ ಐದು')
        self.assertEqual(normalize_numbers('100ರಲ್ಲಿ 85', 'kn'), 'ನೂರರಲ್ಲಿ ಎಂಭತ್ತೈದು')

    def test_phone_numbers_identifiers_and_negative_numbers(self):
        self.assertEqual(normalize_numbers('+91 98765 43210', 'en'),
                         'plus nine one nine eight seven six five four three two one zero')
        self.assertEqual(normalize_numbers('007', 'en'), 'zero zero seven')
        self.assertEqual(normalize_numbers('9876543210', 'en'),
                         'nine eight seven six five four three two one zero')
        self.assertEqual(normalize_numbers('-12.5', 'en'), 'minus twelve point five')
        self.assertEqual(normalize_numbers('+85', 'en'), 'plus eighty-five')

    def test_native_written_digits_use_the_same_whole_number_pronunciation(self):
        self.assertEqual(normalize_numbers('३४२ out of ४००', 'hi'),
                         'तीन सौ बयालीस out of चार सौ')
        self.assertEqual(normalize_numbers('೮೫.೫', 'kn'), 'ಎಂಭತ್ತೈದು ದಶಮಾಂಶ ಐದು')

    def test_place_values_and_native_digit_words(self):
        self.assertEqual(cardinal(1001, 'en'), 'one thousand and one')
        self.assertEqual(cardinal(3420, 'hi'), 'तीन हज़ार चार सौ बीस')
        self.assertEqual(cardinal(91, 'hi'), 'इक्यानवे')
        self.assertEqual(cardinal(78, 'kn'), 'ಎಪ್ಪತ್ತೆಂಟು')
        self.assertEqual(cardinal(88, 'kn'), 'ಎಂಭತ್ತೆಂಟು')
        self.assertEqual(cardinal(100, 'kn'), 'ನೂರು')
        for language in ('en', 'hi', 'kn'):
            for number in range(1001):
                self.assertTrue(cardinal(number, language))


if __name__ == '__main__':
    unittest.main()
