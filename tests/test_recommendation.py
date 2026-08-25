import csv
import json
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from japanese_frequency.database import get_connection, initialize_database
from japanese_frequency.errors import (
    AmbiguousReadingError,
    InvalidInputError,
    SourceFormatError,
)
from japanese_frequency.mining import (
    analyze_media,
    export_media_analysis_csv,
    recommend_media_word,
)
from japanese_frequency.user_words import mark_known


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.db_path = self.root / "frequency.db"
        self.output = self.root / "reports" / "analysis.csv"
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

    def seed_exact(self, word, reading, *, occurrences=1, media_rank=1):
        self.execute(
            """
            INSERT INTO media_words(
                media_id, word, reading, occurrences, media_rank
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (self.media_id, word, reading, occurrences, media_rank),
        )

    def seed_spelling_only(self, word, *, occurrences=1, media_rank=1):
        self.execute(
            """
            INSERT INTO media_spellings(media_id, word, occurrences, media_rank)
            VALUES (?, ?, ?, ?)
            """,
            (self.media_id, word, occurrences, media_rank),
        )

    def seed_user(self, word, reading, *, known=None, in_anki=None):
        self.execute(
            """
            INSERT INTO user_words(word, reading, known, in_anki)
            VALUES (?, ?, ?, ?)
            """,
            (
                word,
                reading,
                None if known is None else int(known),
                None if in_anki is None else int(in_anki),
            ),
        )

    def test_omitted_reading_resolves_one_identity(self):
        self.seed_exact("開く", "あく")

        result = recommend_media_word("media", "開く", db_path=self.db_path)

        self.assertEqual(result["reading"], "あく")

    def test_omitted_reading_returns_sorted_ambiguity_matches(self):
        self.seed_exact("開く", "ひらく")
        self.seed_exact("開く", "あく")

        with self.assertRaises(AmbiguousReadingError) as error:
            recommend_media_word("media", "開く", db_path=self.db_path)

        self.assertEqual(error.exception.matches, ["あく", "ひらく"])
        self.assertEqual(error.exception.code, "ambiguous_reading")
        json.dumps(error.exception.matches)

    def test_existing_ambiguity_message_and_type_gain_sorted_matches(self):
        self.execute(
            "INSERT INTO frequency(word, reading, source) VALUES (?, ?, ?)",
            ("開く", "ひらく", "jpdb"),
        )
        self.execute(
            "INSERT INTO frequency(word, reading, source) VALUES (?, ?, ?)",
            ("開く", "あく", "bccwj_luw"),
        )

        with self.assertRaises(AmbiguousReadingError) as error:
            mark_known("開く", db_path=self.db_path)

        self.assertEqual(str(error.exception), "multiple corpus readings: あく, ひらく")
        self.assertEqual(error.exception.matches, ["あく", "ひらく"])

    def test_spelling_only_media_stays_spelling_level(self):
        self.seed_spelling_only("固有名")

        result = recommend_media_word("media", "固有名", db_path=self.db_path)

        self.assertEqual(result["identity_type"], "spelling")
        self.assertIsNone(result["reading"])

    def test_failed_recall_outweighs_inference_and_transparency(self):
        self.seed_exact("宇宙飛行士", "うちゅうひこうし")

        result = recommend_media_word(
            "media",
            "宇宙飛行士",
            "うちゅうひこうし",
            failed_recall=True,
            successful_inference=True,
            transparent_composition=True,
            db_path=self.db_path,
        )

        self.assertEqual(result["context_score"], 2)
        self.assertEqual(
            result["context_reasons"],
            ["failed_recall", "successful_inference", "transparent_composition"],
        )
        self.assertGreater(result["contextual_score"], result["default_score"])

    def test_failed_recall_moves_known_identity_to_review_but_not_anki_skip(self):
        self.seed_exact("既知", "きち", occurrences=10)
        self.seed_user("既知", "きち", known=True)
        self.seed_exact("語", "ご", occurrences=10)
        self.seed_user("語", "ご", known=False, in_anki=True)

        known = recommend_media_word(
            "media", "既知", "きち", failed_recall=True, db_path=self.db_path
        )
        anki = recommend_media_word(
            "media", "語", "ご", failed_recall=True, db_path=self.db_path
        )

        self.assertEqual(known["default_tier"], "skip")
        self.assertEqual(known["contextual_tier"], "review")
        self.assertEqual(anki["default_tier"], "skip")
        self.assertEqual(anki["contextual_tier"], "skip")

    def test_context_preserves_default_evidence_and_uses_exact_scores(self):
        self.seed_exact("読む", "よむ", occurrences=2)
        baseline = recommend_media_word("media", "読む", "よむ", db_path=self.db_path)

        result = recommend_media_word(
            "media",
            "読む",
            "よむ",
            personally_useful=True,
            successful_inference=True,
            db_path=self.db_path,
        )

        self.assertEqual(result["tier"], baseline["tier"])
        self.assertEqual(result["score"], baseline["score"])
        self.assertEqual(result["score_components"], baseline["score_components"])
        self.assertEqual(result["reasons"], baseline["reasons"])
        self.assertEqual(result["default_tier"], baseline["tier"])
        self.assertEqual(result["default_score"], baseline["score"])
        self.assertEqual(result["context_score"], 1)
        self.assertEqual(result["contextual_score"], baseline["score"] + 1)
        self.assertEqual(
            result["context"],
            {
                "failed_recall": False,
                "successful_inference": True,
                "transparent_composition": False,
                "personally_useful": True,
            },
        )

    def test_context_flags_require_actual_bool(self):
        self.seed_exact("読む", "よむ")

        for name in (
            "failed_recall",
            "successful_inference",
            "transparent_composition",
            "personally_useful",
        ):
            with self.subTest(name=name):
                with self.assertRaises(InvalidInputError):
                    recommend_media_word(
                        "media", "読む", "よむ", db_path=self.db_path, **{name: 1}
                    )

    def test_report_contains_stable_review_columns_and_unicode(self):
        self.seed_exact("読む", "よむ", occurrences=2)
        analysis = analyze_media("media", db_path=self.db_path)

        report = export_media_analysis_csv(analysis, self.output)

        with self.output.open(encoding="utf-8-sig", newline="") as source:
            rows = list(csv.DictReader(source))
        self.assertEqual(report, {"output_path": str(self.output), "row_count": len(rows)})
        self.assertEqual(
            list(rows[0]),
            [
                "tier", "score", "score_kind", "score_components", "reasons",
                "word", "reading", "identity_type", "occurrences", "media_rank",
                "known_spelling", "known_identity", "in_anki", "encounters",
                "jpdb_rank", "bccwj_rank",
            ],
        )
        self.assertEqual(rows[0]["word"], "読む")
        self.assertEqual(rows[0]["score_kind"], "ranking_heuristic")
        self.assertEqual(
            rows[0]["reasons"],
            '["repeated_in_media","unknown_spelling"]',
        )
        self.assertIsInstance(json.loads(rows[0]["score_components"]), dict)
        self.assertTrue(self.output.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_report_flushes_unique_part_before_atomic_replace(self):
        self.seed_exact("読む", "よむ")
        analysis = analyze_media("media", db_path=self.db_path)
        real_replace = os.replace
        observed = {}

        def inspect_replace(source, destination):
            source = Path(source)
            observed["source"] = source
            observed["bytes"] = source.read_bytes()
            real_replace(source, destination)

        with patch("japanese_frequency.mining.os.replace", side_effect=inspect_replace):
            export_media_analysis_csv(analysis, self.output)

        self.assertNotEqual(observed["source"], self.output)
        self.assertEqual(observed["source"].suffix, ".part")
        self.assertIn("読む".encode(), observed["bytes"])
        self.assertFalse(observed["source"].exists())

    def test_replace_failure_is_typed_and_preserves_existing_destination(self):
        self.seed_exact("読む", "よむ")
        analysis = analyze_media("media", db_path=self.db_path)
        self.output.parent.mkdir(parents=True)
        self.output.write_text("stale report", encoding="utf-8")

        def remove_part_then_fail(source, destination):
            Path(source).unlink()
            raise PermissionError("replace denied")

        with patch(
            "japanese_frequency.mining.os.replace", side_effect=remove_part_then_fail
        ):
            with self.assertRaises(SourceFormatError) as error:
                export_media_analysis_csv(analysis, self.output)

        self.assertIsInstance(error.exception.__cause__, PermissionError)
        self.assertIn("replace denied", str(error.exception.__cause__))
        self.assertEqual(self.output.read_text(encoding="utf-8"), "stale report")

    def test_report_cleanup_preserves_primary_io_error(self):
        self.seed_exact("読む", "よむ")
        analysis = analyze_media("media", db_path=self.db_path)

        with patch(
            "japanese_frequency.mining.os.replace",
            side_effect=PermissionError("replace denied"),
        ), patch.object(Path, "unlink", side_effect=OSError("cleanup denied")):
            with self.assertRaises(SourceFormatError) as error:
                export_media_analysis_csv(analysis, self.output)

        self.assertIsInstance(error.exception.__cause__, PermissionError)
        self.assertIn("replace denied", str(error.exception.__cause__))


if __name__ == "__main__":
    unittest.main()
