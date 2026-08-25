import unittest

from japanese_frequency.errors import InvalidInputError
from japanese_frequency.normalization import normalize_reading, normalize_word


class NormalizationTests(unittest.TestCase):
    def test_word_preserves_script_and_normalizes_nfc(self):
        self.assertEqual(normalize_word("  読む  "), "読む")
        self.assertEqual(normalize_word("か\u3099"), "が")
        self.assertNotEqual(normalize_word("よむ"), normalize_word("ヨム"))

    def test_word_rejects_non_string_or_empty_input(self):
        for value in (None, 1, " \t\n "):
            with self.subTest(value=value):
                with self.assertRaises(InvalidInputError) as context:
                    normalize_word(value)
                self.assertEqual(context.exception.code, "invalid_input")

    def test_reading_converts_katakana(self):
        self.assertEqual(normalize_reading(" ヨム "), "よむ")
        self.assertEqual(normalize_reading("ウ\u3099ァ"), "ゔぁ")

    def test_reading_preserves_none_and_empty_reading(self):
        self.assertIsNone(normalize_reading(None))
        self.assertEqual(normalize_reading("  "), "")

    def test_reading_rejects_non_string_input(self):
        with self.assertRaises(InvalidInputError) as context:
            normalize_reading(1)
        self.assertEqual(context.exception.code, "invalid_input")


if __name__ == "__main__":
    unittest.main()
