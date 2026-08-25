import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import config
import japanese_frequency.database as database
import japanese_frequency.errors as errors
from japanese_frequency.database import get_connection, initialize_database, integrity_check
from japanese_frequency.errors import (
    AmbiguousReadingError,
    DatabaseBusyError,
    DatabaseError,
    DownloadError,
    InvalidInputError,
    JapaneseFrequencyError,
    NotFoundError,
    SourceFormatError,
)
from japanese_frequency.user_words import mark_known


class ErrorTests(unittest.TestCase):
    def test_error_subclasses_have_stable_codes(self):
        expected_codes = {
            InvalidInputError: "invalid_input",
            NotFoundError: "not_found",
            AmbiguousReadingError: "ambiguous_reading",
            SourceFormatError: "source_format_error",
            DownloadError: "download_error",
            DatabaseError: "database_error",
            DatabaseBusyError: "database_busy",
        }
        for error_type, code in expected_codes.items():
            with self.subTest(error_type=error_type):
                error = error_type("message")
                self.assertIsInstance(error, JapaneseFrequencyError)
                self.assertEqual(error.code, code)
                self.assertEqual(str(error), "message")

    def test_new_not_found_errors_have_stable_codes(self):
        self.assertTrue(hasattr(errors, "SourceNotFoundError"))
        self.assertTrue(hasattr(errors, "MediaNotFoundError"))
        self.assertEqual(errors.SourceNotFoundError.code, "source_not_found")
        self.assertEqual(errors.MediaNotFoundError.code, "media_not_found")


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary_directory.name) / "nested" / "frequency.db"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def create_legacy_database(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.db_path)) as connection:
            with connection:
                connection.executescript(
                    """
                CREATE TABLE frequency (
                    word TEXT NOT NULL,
                    reading TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL,
                    rank INTEGER,
                    frequency REAL,
                    frequency_per_million REAL,
                    kana_rank INTEGER,
                    PRIMARY KEY (word, reading, source)
                );
                CREATE TABLE user_words (
                    word TEXT NOT NULL,
                    reading TEXT NOT NULL DEFAULT '',
                    known INTEGER NOT NULL DEFAULT 0 CHECK (known IN (0, 1)),
                    in_anki INTEGER NOT NULL DEFAULT 0 CHECK (in_anki IN (0, 1)),
                    encounter_count INTEGER NOT NULL DEFAULT 0 CHECK (encounter_count >= 0),
                    first_seen TEXT,
                    last_seen TEXT,
                    notes TEXT,
                    PRIMARY KEY (word, reading)
                );
                CREATE TABLE source_metadata (
                    source TEXT PRIMARY KEY,
                    version TEXT,
                    filename TEXT,
                    imported_at TEXT,
                    source_row_count INTEGER,
                    entry_count INTEGER,
                    sha256 TEXT,
                    notes TEXT
                );
                CREATE INDEX idx_frequency_word ON frequency(word);
                CREATE INDEX idx_frequency_word_reading ON frequency(word, reading);
                CREATE INDEX idx_frequency_source_rank ON frequency(source, rank);
                """
                )

    def test_existing_boolean_user_state_migrates_conservatively(self):
        self.create_legacy_database()
        with closing(sqlite3.connect(self.db_path)) as connection:
            with connection:
                connection.execute(
                    "INSERT INTO user_words VALUES (?,?,?,?,?,?,?,?)",
                    ("既知", "きち", 1, 1, 4, "first", "last", "note"),
                )
                connection.execute(
                    "INSERT INTO user_words VALUES (?,?,?,?,?,?,?,?)",
                    ("不明", "ふめい", 0, 0, 2, "first2", "last2", "note2"),
                )

        initialize_database(self.db_path)
        initialize_database(self.db_path)

        with closing(get_connection(self.db_path)) as connection:
            rows = connection.execute(
                "SELECT word, known, in_anki, encounter_count, notes "
                "FROM user_words ORDER BY word"
            ).fetchall()
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(tuple(rows[0]), ("不明", None, None, 2, "note2"))
        self.assertEqual(tuple(rows[1]), ("既知", 1, 1, 4, "note"))
        self.assertEqual(version, 2)

    def test_migration_inspects_version_and_schema_under_write_lock(self):
        self.create_legacy_database()
        statements = []

        def tracked_connection(db_path):
            connection = get_connection(db_path)
            connection.set_trace_callback(statements.append)
            return connection

        with patch.object(database, "get_connection", side_effect=tracked_connection):
            initialize_database(self.db_path)

        normalized = [statement.strip().upper() for statement in statements]
        begin_index = next(
            index
            for index, statement in enumerate(normalized)
            if statement.startswith("BEGIN IMMEDIATE")
        )
        version_index = normalized.index("PRAGMA USER_VERSION")
        schema_index = next(
            index
            for index, statement in enumerate(normalized)
            if statement.startswith("PRAGMA TABLE_INFO(USER_WORDS)")
        )
        self.assertLess(begin_index, version_index)
        self.assertLess(begin_index, schema_index)

    def test_new_schema_contains_knowledge_and_media_tables(self):
        initialize_database(self.db_path)
        with closing(get_connection(self.db_path)) as connection:
            objects = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
                )
            }
        self.assertTrue(
            {
                "known_spellings",
                "personal_source_metadata",
                "media_sources",
                "media_words",
                "media_spellings",
                "idx_known_spellings_word",
                "idx_media_words_media_rank",
                "idx_media_spellings_media_rank",
            }
            <= objects
        )

    def test_initialize_creates_exact_schema_and_indexes(self):
        result = initialize_database(self.db_path)

        self.assertEqual(result, self.db_path)
        with closing(get_connection(self.db_path)) as connection:
            objects = {
                row["name"]: row["sql"]
                for row in connection.execute(
                    "SELECT name, sql FROM sqlite_master "
                    "WHERE type IN ('table', 'index') AND sql IS NOT NULL"
                )
            }
            columns = {
                table: [
                    (
                        row["name"],
                        row["type"],
                        row["notnull"],
                        row["dflt_value"],
                        row["pk"],
                    )
                    for row in connection.execute(f"PRAGMA table_info({table})")
                ]
                for table in ("frequency", "user_words", "source_metadata")
            }
            indexes = {
                index: [
                    row["name"]
                    for row in connection.execute(f"PRAGMA index_info({index})")
                ]
                for index in (
                    "idx_frequency_word",
                    "idx_frequency_word_reading",
                    "idx_frequency_source_rank",
                )
            }

        self.assertTrue({"frequency", "user_words", "source_metadata"} <= objects.keys())
        self.assertTrue(
            {
                "idx_frequency_word",
                "idx_frequency_word_reading",
                "idx_frequency_source_rank",
            }
            <= objects.keys()
        )
        self.assertEqual(
            columns["frequency"],
            [
                ("word", "TEXT", 1, None, 1),
                ("reading", "TEXT", 1, "''", 2),
                ("source", "TEXT", 1, None, 3),
                ("rank", "INTEGER", 0, None, 0),
                ("frequency", "REAL", 0, None, 0),
                ("frequency_per_million", "REAL", 0, None, 0),
                ("kana_rank", "INTEGER", 0, None, 0),
            ],
        )
        self.assertEqual(
            columns["user_words"],
            [
                ("word", "TEXT", 1, None, 1),
                ("reading", "TEXT", 1, "''", 2),
                ("known", "INTEGER", 0, None, 0),
                ("in_anki", "INTEGER", 0, None, 0),
                ("encounter_count", "INTEGER", 1, "0", 0),
                ("first_seen", "TEXT", 0, None, 0),
                ("last_seen", "TEXT", 0, None, 0),
                ("notes", "TEXT", 0, None, 0),
            ],
        )
        self.assertEqual(
            columns["source_metadata"],
            [
                ("source", "TEXT", 0, None, 1),
                ("version", "TEXT", 0, None, 0),
                ("filename", "TEXT", 0, None, 0),
                ("imported_at", "TEXT", 0, None, 0),
                ("source_row_count", "INTEGER", 0, None, 0),
                ("entry_count", "INTEGER", 0, None, 0),
                ("sha256", "TEXT", 0, None, 0),
                ("notes", "TEXT", 0, None, 0),
            ],
        )
        self.assertIn("CHECK (known IN (0, 1))", objects["user_words"])
        self.assertIn("CHECK (in_anki IN (0, 1))", objects["user_words"])
        self.assertIn("CHECK (encounter_count >= 0)", objects["user_words"])
        self.assertEqual(indexes["idx_frequency_word"], ["word"])
        self.assertEqual(
            indexes["idx_frequency_word_reading"], ["word", "reading"]
        )
        self.assertEqual(
            indexes["idx_frequency_source_rank"], ["source", "rank"]
        )

    def test_initialize_is_idempotent(self):
        initialize_database(self.db_path)
        initialize_database(self.db_path)

        with closing(get_connection(self.db_path)) as connection:
            table_count = connection.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type = 'table' AND name IN ('frequency', 'user_words', 'source_metadata')"
            ).fetchone()[0]
        self.assertEqual(table_count, 3)

    def test_connection_configures_row_factory_and_pragmas(self):
        with closing(get_connection(self.db_path)) as connection:
            row = connection.execute("SELECT 1 AS value").fetchone()
            timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
            foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]

        self.assertIsInstance(row, sqlite3.Row)
        self.assertEqual(row["value"], 1)
        self.assertEqual(timeout, config.SQLITE_BUSY_TIMEOUT_MS)
        self.assertEqual(foreign_keys, 1)
        self.assertEqual(journal_mode, "wal")

    def test_integrity_check_returns_sqlite_result(self):
        initialize_database(self.db_path)
        self.assertEqual(integrity_check(self.db_path), "ok")

    def test_database_api_translates_sqlite_errors(self):
        directory_path = Path(self.temporary_directory.name)
        with self.assertRaises(DatabaseError) as context:
            get_connection(directory_path)
        self.assertEqual(context.exception.code, "database_error")

    def test_database_api_translates_parent_path_conflicts(self):
        conflict = Path(self.temporary_directory.name) / "conflict"
        conflict.write_text("not a directory", encoding="utf-8")
        database_path = conflict / "frequency.db"

        for operation in (get_connection, initialize_database):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(DatabaseError) as context:
                    operation(database_path)
                self.assertEqual(context.exception.code, "database_error")

    def test_database_api_translates_parent_mkdir_oserror(self):
        for operation in (get_connection, initialize_database):
            with self.subTest(operation=operation.__name__):
                with patch.object(Path, "mkdir", side_effect=OSError("denied")):
                    with self.assertRaises(DatabaseError) as context:
                        operation(self.db_path)
                self.assertEqual(context.exception.code, "database_error")

    def test_database_api_translates_locked_errors(self):
        with patch(
            "japanese_frequency.database.sqlite3.connect",
            side_effect=sqlite3.OperationalError("database is locked"),
        ):
            with self.assertRaises(DatabaseBusyError) as context:
                get_connection(self.db_path)
        self.assertEqual(context.exception.code, "database_busy")

    def test_locked_mutation_times_out_without_partial_write(self):
        initialize_database(self.db_path)
        with closing(get_connection(self.db_path)) as connection:
            connection.execute(
                "INSERT INTO frequency(word, reading, source, rank) "
                "VALUES ('読む', 'よむ', 'jpdb', 1)"
            )
            connection.commit()

        first = get_connection(self.db_path)
        first.execute("BEGIN IMMEDIATE")
        before = [
            tuple(row)
            for row in first.execute("SELECT * FROM user_words ORDER BY word, reading")
        ]
        started = time.monotonic()
        try:
            with self.assertRaises(DatabaseBusyError) as error:
                mark_known("読む", "よむ", db_path=self.db_path)
            elapsed = time.monotonic() - started
            self.assertEqual(error.exception.code, "database_busy")
            self.assertGreaterEqual(elapsed, config.SQLITE_BUSY_TIMEOUT_MS / 1000 * 0.8)
            self.assertLess(elapsed, config.SQLITE_BUSY_TIMEOUT_MS / 1000 + 1.0)
            after = [
                tuple(row)
                for row in first.execute(
                    "SELECT * FROM user_words ORDER BY word, reading"
                )
            ]
            self.assertEqual(after, before)
        finally:
            first.rollback()
            first.close()


if __name__ == "__main__":
    unittest.main()
