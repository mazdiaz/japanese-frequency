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
    get_japanese_word_profile,
    lookup_japanese_frequency,
    mark_japanese_word_known,
    record_japanese_encounter,
    set_japanese_word_anki_status,
)


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary_directory.name) / "frequency.db"
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
                self.assertIn("message", response["error"])
                self.assertTrue(response["error"]["message"])
                self.assertNotIn("private_frequency_schema", response["error"]["message"])


if __name__ == "__main__":
    unittest.main()
