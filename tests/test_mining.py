import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

import config
from japanese_frequency.database import get_connection, initialize_database
from japanese_frequency.errors import InvalidInputError, MediaNotFoundError
from japanese_frequency.mining import analyze_media


class MiningTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary_directory.name) / "frequency.db"
        initialize_database(self.db_path)
        with closing(get_connection(self.db_path)) as connection:
            cursor = connection.execute(
                """
                INSERT INTO media_sources(
                    source_key, display_name, requested_filename, selected_filename,
                    format, imported_at, source_row_count, entry_count, sha256, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "media", "Test Media", "media.csv", "media.csv", "csv",
                    "2026-08-25T00:00:00Z", 0, 0, "0" * 64, "test source",
                ),
            )
            self.media_id = cursor.lastrowid
            connection.commit()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def execute(self, sql, parameters=()):
        with closing(get_connection(self.db_path)) as connection:
            connection.execute(sql, parameters)
            connection.commit()

    def seed_exact(
        self,
        word="読む",
        reading="よむ",
        *,
        occurrences=1,
        media_rank=1,
        definitions=None,
        example_sentence=None,
        dictionary_id=None,
    ):
        self.execute(
            """
            INSERT INTO media_words(
                media_id, word, reading, occurrences, media_rank,
                definitions, example_sentence, dictionary_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.media_id, word, reading, occurrences, media_rank,
                definitions, example_sentence, dictionary_id,
            ),
        )

    def seed_spelling(
        self,
        word,
        *,
        occurrences=None,
        media_rank=None,
        definitions=None,
        example_sentence=None,
        dictionary_id=None,
    ):
        self.execute(
            """
            INSERT INTO media_spellings(
                media_id, word, occurrences, media_rank,
                definitions, example_sentence, dictionary_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.media_id, word, occurrences, media_rank,
                definitions, example_sentence, dictionary_id,
            ),
        )

    def seed_frequency(
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
        self.execute(
            """
            INSERT INTO frequency(
                word, reading, source, rank, frequency,
                frequency_per_million, kana_rank
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                word, reading, source, rank, frequency,
                frequency_per_million, kana_rank,
            ),
        )

    def seed_user(
        self,
        word="読む",
        reading="よむ",
        *,
        known=None,
        in_anki=None,
        encounters=0,
    ):
        self.execute(
            """
            INSERT INTO user_words(word, reading, known, in_anki, encounter_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                word,
                reading,
                None if known is None else int(known),
                None if in_anki is None else int(in_anki),
                encounters,
            ),
        )

    def seed_known_spelling(self, word, source="migaku"):
        self.execute(
            "INSERT INTO known_spellings(word, source) VALUES (?, ?)",
            (word, source),
        )

    def all_candidates(self, result):
        return [
            candidate
            for tier in ("mine", "review", "skip")
            for candidate in result["candidates"][tier]
        ]

    def one_candidate(self, result):
        candidates = self.all_candidates(result)
        self.assertEqual(len(candidates), 1)
        return candidates[0]

    def test_scoring_constants_match_documented_policy(self):
        self.assertEqual(
            config.MINING_SCORE,
            {
                "known_spelling": -2,
                "known_identity_false": 3,
                "media_occurrences_10": 4,
                "media_occurrences_5": 3,
                "media_occurrences_2": 2,
                "media_occurrences_1": 1,
                "media_rank_100": 2,
                "media_rank_500": 1,
                "jpdb_rank_3000": 3,
                "jpdb_rank_10000": 2,
                "jpdb_rank_20000": 1,
                "jpdb_rank_70000": -1,
                "jpdb_rank_over_70000": -2,
                "bccwj_rank_3000": 2,
                "bccwj_rank_10000": 1,
                "bccwj_rank_over_50000": -1,
                "encounters_3": 2,
                "encounters_1": 1,
            },
        )
        self.assertEqual(config.MINING_MINE_SCORE, 5)
        self.assertEqual(
            config.CONTEXT_SCORE,
            {
                "failed_recall": 6,
                "personally_useful": 3,
                "successful_inference": -2,
                "transparent_composition": -2,
            },
        )

    def test_exact_media_identity_suppresses_and_retains_spelling_evidence(self):
        self.seed_exact("読む", "よむ", occurrences=5, media_rank=1)
        self.seed_spelling(
            "読む",
            occurrences=2,
            media_rank=2,
            definitions="spelling definition",
        )

        candidate = self.one_candidate(analyze_media("media", db_path=self.db_path))

        self.assertEqual((candidate["word"], candidate["reading"]), ("読む", "よむ"))
        self.assertEqual(candidate["identity_type"], "exact")
        self.assertEqual(candidate["media"]["occurrences"], 5)
        self.assertEqual(
            candidate["media"]["spelling"],
            {
                "occurrences": 2,
                "media_rank": 2,
                "definitions": "spelling definition",
                "example_sentence": None,
                "dictionary_id": None,
            },
        )

    def test_spelling_media_expands_to_sorted_corpus_identities(self):
        self.seed_spelling("開く", occurrences=4, media_rank=1)
        self.seed_frequency("開く", "ひらく", "jpdb", rank=100)
        self.seed_frequency("開く", "あく", "jpdb", rank=100)

        candidates = self.all_candidates(analyze_media("media", db_path=self.db_path))

        self.assertEqual(
            [(candidate["word"], candidate["reading"]) for candidate in candidates],
            [("開く", "あく"), ("開く", "ひらく")],
        )
        self.assertTrue(all(candidate["identity_type"] == "exact" for candidate in candidates))
        self.assertTrue(all(candidate["media"]["occurrences"] == 4 for candidate in candidates))

    def test_spelling_only_candidate_keeps_nullable_identity_evidence(self):
        self.seed_spelling("固有名")

        candidate = self.one_candidate(analyze_media("media", db_path=self.db_path))

        self.assertEqual(candidate["identity_type"], "spelling")
        self.assertIsNone(candidate["reading"])
        self.assertIsNone(candidate["personal"]["known_identity"])
        self.assertIsNone(candidate["personal"]["in_anki"])
        self.assertEqual(candidate["frequency"], {})

    def test_known_identity_and_anki_are_hard_skip_reasons(self):
        self.seed_exact("既知", "きち", occurrences=20, media_rank=1)
        self.seed_user("既知", "きち", known=True)
        self.seed_exact("語", "ご", occurrences=20, media_rank=2)
        self.seed_user("語", "ご", known=False, in_anki=True)

        candidates = {
            candidate["word"]: candidate
            for candidate in self.all_candidates(analyze_media("media", db_path=self.db_path))
        }

        self.assertEqual(candidates["既知"]["tier"], "skip")
        self.assertIn("known_identity", candidates["既知"]["reasons"])
        self.assertEqual(candidates["語"]["tier"], "skip")
        self.assertIn("already_in_anki", candidates["語"]["reasons"])
        self.assertNotIn("hard_skip", candidates["語"]["score_components"])

    def test_known_spelling_never_becomes_skip_by_itself(self):
        self.seed_exact("希語", "きご", occurrences=1, media_rank=1)
        self.seed_known_spelling("希語")
        self.seed_frequency("希語", "きご", "jpdb", rank=90000)

        candidate = self.one_candidate(analyze_media("media", db_path=self.db_path))

        self.assertEqual(candidate["tier"], "review")
        self.assertEqual(candidate["score_components"]["known_spelling"], -2)

    def test_score_uses_one_component_per_category_and_retains_raw_values(self):
        self.seed_exact("読む", "よむ", occurrences=5, media_rank=20)
        self.seed_user("読む", "よむ", known=False, encounters=3)
        self.seed_frequency("読む", "よむ", "jpdb", rank=5000, kana_rank=88)
        self.seed_frequency(
            "読む", "よむ", "bccwj_luw", rank=9000,
            frequency=12.5, frequency_per_million=1.25,
        )

        candidate = self.one_candidate(analyze_media("media", db_path=self.db_path))

        self.assertEqual(candidate["score_kind"], "ranking_heuristic")
        self.assertEqual(candidate["score"], 3 + 2 + 2 + 1 + 2 + 3)
        self.assertEqual(candidate["tier"], "mine")
        self.assertEqual(
            candidate["score_components"],
            {
                "bccwj_rank_10000": 1,
                "encounters_3": 2,
                "jpdb_rank_10000": 2,
                "known_identity_false": 3,
                "media_occurrences_5": 3,
                "media_rank_100": 2,
            },
        )
        self.assertEqual(candidate["frequency"]["jpdb"], {"rank": 5000, "kana_rank": 88})
        self.assertEqual(
            candidate["frequency"]["bccwj_luw"],
            {"rank": 9000, "frequency": 12.5, "frequency_per_million": 1.25},
        )
        json.dumps(candidate)

    def test_global_order_is_tier_score_null_last_rank_then_identity(self):
        self.seed_exact("乙", "おつ", occurrences=1, media_rank=2)
        self.seed_exact("甲", "こう", occurrences=1, media_rank=1)
        self.seed_spelling("丙")
        self.seed_frequency("丙", "へい", "jpdb", rank=20000)

        result = analyze_media("media", limit=2, db_path=self.db_path)

        self.assertEqual(
            [(candidate["word"], candidate["reading"]) for candidate in self.all_candidates(result)],
            [("甲", "こう"), ("乙", "おつ")],
        )
        self.assertEqual(result["summary"]["available_candidates"], 3)
        self.assertEqual(result["summary"]["returned_candidates"], 2)

    def test_word_and_reading_break_complete_rank_ties(self):
        self.seed_exact("開く", "ひらく", occurrences=1, media_rank=1)
        self.seed_exact("開く", "あく", occurrences=1, media_rank=1)
        self.seed_exact("会う", "あう", occurrences=1, media_rank=1)

        candidates = self.all_candidates(analyze_media("media", db_path=self.db_path))

        self.assertEqual(
            [(candidate["word"], candidate["reading"]) for candidate in candidates],
            [("会う", "あう"), ("開く", "あく"), ("開く", "ひらく")],
        )

    def test_source_and_summary_counts_are_deterministic(self):
        self.seed_exact("読む", "よむ", occurrences=5)
        self.seed_spelling("固有名")
        self.execute(
            "UPDATE media_sources SET source_row_count=2, entry_count=2 WHERE id=?",
            (self.media_id,),
        )

        result = analyze_media("media", db_path=self.db_path)

        self.assertEqual(result["source"]["source_key"], "media")
        self.assertEqual(result["source"]["exact_entry_count"], 1)
        self.assertEqual(result["source"]["spelling_entry_count"], 1)
        self.assertEqual(
            result["summary"],
            {
                "available_candidates": 2,
                "returned_candidates": 2,
                "mine": 1,
                "review": 1,
                "skip": 0,
            },
        )

    def test_missing_source_raises_typed_media_not_found(self):
        with self.assertRaises(MediaNotFoundError) as error:
            analyze_media("missing", db_path=self.db_path)

        self.assertEqual(error.exception.code, "media_not_found")

    def test_limit_requires_nonnegative_integer(self):
        for limit in (-1, True, 1.5, "1"):
            with self.subTest(limit=limit):
                with self.assertRaises(InvalidInputError):
                    analyze_media("media", limit=limit, db_path=self.db_path)


if __name__ == "__main__":
    unittest.main()
