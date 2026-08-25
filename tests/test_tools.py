import io
import socket
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from japanese_frequency.database import get_connection, initialize_database
from japanese_frequency.errors import DatabaseBusyError, DatabaseError
from japanese_frequency.tools import (
    analyze_japanese_media,
    get_japanese_word_profile,
    import_japanese_media_vocabulary,
    import_migaku_known_vocabulary,
    lookup_japanese_frequency,
    mark_japanese_word_known,
    recommend_japanese_media_word,
    record_japanese_encounter,
    set_japanese_word_anki_status,
)


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.db_path = self.root / "frequency.db"
        self.migaku_path = self.root / "known.txt"
        self.media_path = self.root / "media.csv"
        self.migaku_path.write_text("読む\n", encoding="utf-8")
        self.media_path.write_text(
            "Word,ReadingKana,Occurences\n"
            "読む,よむ,5\n"
            "開く,ひらく,2\n"
            "開く,あく,3\n",
            encoding="utf-8",
        )
        initialize_database(self.db_path)
        with closing(get_connection(self.db_path)) as connection:
            connection.executemany(
                "INSERT INTO frequency(word, reading, source, rank) "
                "VALUES (?, ?, 'jpdb', ?)",
                (("読む", "よむ", 312), ("開く", "あく", 10), ("開く", "ひらく", 20)),
            )
            connection.commit()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def test_all_tools_use_exact_success_envelope_and_stay_silent_offline(self):
        calls = (
            lambda: lookup_japanese_frequency("読む", "よむ", db_path=self.db_path),
            lambda: get_japanese_word_profile("読む", "よむ", db_path=self.db_path),
            lambda: record_japanese_encounter("読む", "よむ", db_path=self.db_path),
            lambda: mark_japanese_word_known("読む", "よむ", False, db_path=self.db_path),
            lambda: set_japanese_word_anki_status("読む", "よむ", False, db_path=self.db_path),
            lambda: import_migaku_known_vocabulary(
                self.migaku_path, db_path=self.db_path
            ),
            lambda: import_japanese_media_vocabulary(
                self.media_path, "media", db_path=self.db_path
            ),
            lambda: analyze_japanese_media("media", db_path=self.db_path),
            lambda: recommend_japanese_media_word(
                "media", "読む", "よむ", db_path=self.db_path
            ),
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        class OfflineSocket(socket.socket):
            def connect(self, *args, **kwargs):
                raise AssertionError("socket.connect used")

            def connect_ex(self, *args, **kwargs):
                raise AssertionError("socket.connect_ex used")

        with patch(
            "socket.create_connection", side_effect=AssertionError("network used")
        ), patch("socket.socket", OfflineSocket):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                responses = [call() for call in calls]

        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        for response in responses:
            with self.subTest(response=response):
                self.assertEqual(set(response), {"ok", "result"})
                self.assertIs(response["ok"], True)
                self.assertIsInstance(response["result"], dict)

    def test_ambiguous_tool_error_contains_sorted_safe_matches(self):
        import_japanese_media_vocabulary(
            self.media_path, "media", db_path=self.db_path
        )

        response = recommend_japanese_media_word(
            "media", "開く", db_path=self.db_path
        )

        self.assertEqual(set(response), {"ok", "error"})
        self.assertIs(response["ok"], False)
        self.assertEqual(set(response["error"]), {"type", "message", "matches"})
        self.assertEqual(response["error"]["type"], "ambiguous_reading")
        self.assertTrue(response["error"]["message"])
        self.assertEqual(response["error"]["matches"], ["あく", "ひらく"])

    def test_import_errors_use_exact_safe_failure_envelopes(self):
        missing = self.root / "missing.txt"
        responses = (
            (
                import_migaku_known_vocabulary(None, db_path=self.db_path),
                "invalid_input",
                "path must be a string or path-like object",
            ),
            (
                import_japanese_media_vocabulary(None, "media", db_path=self.db_path),
                "invalid_input",
                "path must be a string or path-like object",
            ),
            (
                import_migaku_known_vocabulary(missing, db_path=self.db_path),
                "source_not_found",
                f"source file not found: {missing}",
            ),
            (
                import_japanese_media_vocabulary(
                    missing, "media", db_path=self.db_path
                ),
                "source_not_found",
                f"source file not found: {missing}",
            ),
            (
                analyze_japanese_media("media", limit=-1, db_path=self.db_path),
                "invalid_input",
                "limit must be a nonnegative integer or null",
            ),
        )

        for response, error_type, message in responses:
            with self.subTest(error_type=error_type, message=message):
                self.assertEqual(set(response), {"ok", "error"})
                self.assertIs(response["ok"], False)
                self.assertEqual(
                    response["error"], {"type": error_type, "message": message}
                )

    def test_invalid_and_ambiguous_inputs_use_stable_failure_envelopes(self):
        responses = (
            (lookup_japanese_frequency("", db_path=self.db_path), "invalid_input"),
            (mark_japanese_word_known("開く", db_path=self.db_path), "ambiguous_reading"),
        )

        for response, error_type in responses:
            with self.subTest(error_type=error_type):
                self.assertEqual(set(response), {"ok", "error"})
                self.assertIs(response["ok"], False)
                self.assertEqual(response["error"]["type"], error_type)
                self.assertEqual(
                    set(response["error"]),
                    {"type", "message", "matches"}
                    if error_type == "ambiguous_reading"
                    else {"type", "message"},
                )
                self.assertIsInstance(response["error"]["message"], str)
                self.assertTrue(response["error"]["message"])

    def test_database_and_busy_failures_keep_distinct_stable_types(self):
        errors = (
            DatabaseError("no such table: private_frequency_schema"),
            DatabaseBusyError("database is locked: private_frequency_schema"),
            sqlite3.OperationalError("no such table: private_frequency_schema"),
            sqlite3.OperationalError("database is locked: private_frequency_schema"),
        )

        for error in errors:
            error_type = (
                error.code
                if isinstance(error, (DatabaseError, DatabaseBusyError))
                else "database_busy" if "locked" in str(error) else "database_error"
            )
            with self.subTest(error_type=error_type, source=type(error).__name__):
                with patch("japanese_frequency.tools.lookup_frequency", side_effect=error):
                    response = lookup_japanese_frequency("読む", db_path=self.db_path)

                self.assertEqual(set(response), {"ok", "error"})
                self.assertIs(response["ok"], False)
                self.assertEqual(response["error"]["type"], error_type)
                self.assertEqual(set(response["error"]), {"type", "message"})
                self.assertTrue(response["error"]["message"])
                self.assertNotIn("private_frequency_schema", response["error"]["message"])

    def test_database_parent_path_conflict_uses_tool_error_envelope(self):
        conflict = self.db_path.parent / "conflict"
        conflict.write_text("not a directory", encoding="utf-8")

        response = lookup_japanese_frequency(
            "読む", db_path=conflict / "frequency.db"
        )

        self.assertEqual(
            response,
            {
                "ok": False,
                "error": {
                    "type": "database_error",
                    "message": "database operation failed",
                },
            },
        )


if __name__ == "__main__":
    unittest.main()
