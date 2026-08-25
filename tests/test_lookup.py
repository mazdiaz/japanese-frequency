import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import config
from japanese_frequency.database import get_connection, initialize_database
from japanese_frequency.errors import InvalidInputError
from japanese_frequency.lookup import classify_jpdb_rank, lookup_frequency


class CommonnessTests(unittest.TestCase):
    def test_classification_uses_configured_threshold_boundaries(self):
        previous_maximum = 0
        for maximum, category in config.JPDB_COMMONNESS_THRESHOLDS:
            with self.subTest(rank=previous_maximum + 1):
                self.assertEqual(
                    classify_jpdb_rank(previous_maximum + 1),
                    {"rank": previous_maximum + 1, "category": category},
                )
            with self.subTest(rank=maximum):
                self.assertEqual(
                    classify_jpdb_rank(maximum),
                    {"rank": maximum, "category": category},
                )
            previous_maximum = maximum

        rare_rank = previous_maximum + 1
        self.assertEqual(
            classify_jpdb_rank(rare_rank),
            {"rank": rare_rank, "category": "very_rare"},
        )

    def test_classification_rejects_non_positive_integers_and_booleans(self):
        for rank in (None, True, False, 0, -1, 1.0, "1"):
            with self.subTest(rank=rank):
                with self.assertRaises(InvalidInputError):
                    classify_jpdb_rank(rank)


class LookupTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary_directory.name) / "frequency.db"
        initialize_database(self.db_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def insert_frequency(
        self,
        word,
        reading,
        source,
        *,
        rank=None,
        frequency=None,
        frequency_per_million=None,
        kana_rank=None,
    ):
        with closing(get_connection(self.db_path)) as connection:
            connection.execute(
                "INSERT INTO frequency "
                "(word, reading, source, rank, frequency, frequency_per_million, kana_rank) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    word,
                    reading,
                    source,
                    rank,
                    frequency,
                    frequency_per_million,
                    kana_rank,
                ),
            )
            connection.commit()

    def test_precise_lookup_returns_available_source_fields_and_commonness(self):
        self.insert_frequency("読む", "よむ", "jpdb", rank=312, kana_rank=19896)
        self.insert_frequency(
            "読む",
            "よむ",
            "bccwj_luw",
            rank=2,
            frequency=12.0,
            frequency_per_million=1.2,
        )

        result = lookup_frequency(" 読む ", "ヨム", db_path=self.db_path)

        self.assertEqual(
            result,
            {
                "found": True,
                "word": "読む",
                "reading": "よむ",
                "frequency": {
                    "jpdb": {
                        "rank": 312,
                        "kana_rank": 19896,
                        "commonness": {
                            "rank": 312,
                            "category": "extremely_common",
                        },
                    },
                    "bccwj_luw": {
                        "rank": 2,
                        "frequency": 12.0,
                        "frequency_per_million": 1.2,
                    },
                },
            },
        )
        json.dumps(result)

    def test_precise_result_omits_nullable_fields(self):
        self.insert_frequency("読む", "よむ", "jpdb", rank=312)
        self.insert_frequency("読む", "よむ", "bccwj_luw", rank=2)

        result = lookup_frequency("読む", "よむ", db_path=self.db_path)

        self.assertNotIn("kana_rank", result["frequency"]["jpdb"])
        self.assertEqual(result["frequency"]["bccwj_luw"], {"rank": 2})

    def test_precise_unknown_identity_preserves_precise_shape(self):
        self.assertEqual(
            lookup_frequency("不存在", "ふそんざい", db_path=self.db_path),
            {
                "found": False,
                "word": "不存在",
                "reading": "ふそんざい",
                "frequency": {},
            },
        )

    def test_word_only_lookup_groups_sources_by_reading(self):
        self.insert_frequency("読む", "よむ", "jpdb", rank=312)
        self.insert_frequency(
            "読む",
            "よむ",
            "bccwj_luw",
            rank=2,
            frequency=12,
            frequency_per_million=1.2,
        )

        result = lookup_frequency("読む", db_path=self.db_path)

        self.assertEqual(
            result,
            {
                "found": True,
                "word": "読む",
                "matches": [
                    {
                        "reading": "よむ",
                        "frequency": {
                            "jpdb": {
                                "rank": 312,
                                "commonness": {
                                    "rank": 312,
                                    "category": "extremely_common",
                                },
                            },
                            "bccwj_luw": {
                                "rank": 2,
                                "frequency": 12.0,
                                "frequency_per_million": 1.2,
                            },
                        },
                    }
                ],
            },
        )
        json.dumps(result)

    def test_word_only_lookup_uses_explicit_rank_and_reading_order(self):
        self.insert_frequency("語", "ご", "bccwj_luw", rank=1)
        self.insert_frequency("語", "かたり", "jpdb", rank=50)
        self.insert_frequency("語", "ことば", "jpdb", rank=10)
        self.insert_frequency("語", "あ", "jpdb", rank=10, kana_rank=200)
        self.insert_frequency("語", "い", "jpdb", rank=10, kana_rank=100)
        self.insert_frequency("語", "あ", "bccwj_luw", rank=3)
        self.insert_frequency("語", "い", "bccwj_luw", rank=1)

        result = lookup_frequency("語", db_path=self.db_path)

        self.assertEqual(
            [match["reading"] for match in result["matches"]],
            ["い", "あ", "ことば", "かたり", "ご"],
        )

    def test_unknown_word_returns_found_false(self):
        self.assertEqual(
            lookup_frequency("不存在", db_path=self.db_path),
            {"found": False, "word": "不存在", "matches": []},
        )

    def test_lookup_rejects_invalid_word_and_reading(self):
        for word, reading in (("", None), (None, None), ("読む", 1)):
            with self.subTest(word=word, reading=reading):
                with self.assertRaises(InvalidInputError):
                    lookup_frequency(word, reading, db_path=self.db_path)


if __name__ == "__main__":
    unittest.main()
