import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from japanese_frequency.database import get_connection
from japanese_frequency.errors import (
    DatabaseBusyError,
    DatabaseError,
    SourceFormatError,
)
from japanese_frequency.knowledge import get_known_spelling, import_migaku_known_words


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.db_path = self.directory / "frequency.db"
        self.clock = lambda: datetime(2026, 8, 25, 2, 55, tzinfo=timezone.utc)
        self.valid_source = Path(__file__).parent / "fixtures" / "migaku_known.txt"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def write(self, text, name="known.txt"):
        path = self.directory / name
        path.write_text(text, encoding="utf-8")
        return path

    def write_bytes(self, data, name="known.txt"):
        path = self.directory / name
        path.write_bytes(data)
        return path

    def full_state_snapshot(self):
        with closing(get_connection(self.db_path)) as connection:
            return tuple(
                tuple(tuple(row) for row in connection.execute(query))
                for query in (
                    "SELECT * FROM known_spellings ORDER BY source, word",
                    "SELECT * FROM personal_source_metadata ORDER BY source",
                    "SELECT * FROM user_words ORDER BY word, reading",
                    "SELECT * FROM frequency ORDER BY source, word, reading",
                )
            )

    def test_import_normalizes_deduplicates_and_records_metadata(self):
        raw = "読む\n 読む \nヨム\n".encode("utf-8-sig")
        source = self.write_bytes(raw, name="migaku-export.txt")

        result = import_migaku_known_words(
            source, db_path=self.db_path, now=self.clock
        )

        self.assertEqual(result["source"], "migaku")
        self.assertEqual(result["filename"], "migaku-export.txt")
        self.assertEqual(result["imported_at"], "2026-08-25T02:55:00Z")
        self.assertEqual(result["source_row_count"], 3)
        self.assertEqual(result["entry_count"], 2)
        self.assertEqual(result["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertTrue(result["notes"])
        with closing(get_connection(self.db_path)) as connection:
            words = connection.execute(
                "SELECT word FROM known_spellings WHERE source='migaku' ORDER BY word"
            ).fetchall()
            metadata = connection.execute(
                "SELECT * FROM personal_source_metadata WHERE source='migaku'"
            ).fetchone()
        self.assertEqual([row["word"] for row in words], ["ヨム", "読む"])
        self.assertEqual(dict(metadata), result)

    def test_import_applies_nfc_normalization(self):
        source = self.write("か\u3099く\nがく\n")

        result = import_migaku_known_words(
            source, db_path=self.db_path, now=self.clock
        )

        self.assertEqual(result["source_row_count"], 2)
        self.assertEqual(result["entry_count"], 1)
        self.assertEqual(get_known_spelling("か\u3099く", db_path=self.db_path), {
            "known": True,
            "word": "がく",
            "sources": ["migaku"],
        })

    def test_reimport_replaces_only_migaku_snapshot(self):
        import_migaku_known_words(
            self.write("読む\n古い\n"), db_path=self.db_path, now=self.clock
        )
        with closing(get_connection(self.db_path)) as connection:
            connection.execute(
                "INSERT INTO known_spellings(word, source) VALUES ('古い', 'manual')"
            )
            connection.execute(
                "INSERT INTO personal_source_metadata VALUES "
                "('manual', 'manual.txt', '2026-01-01T00:00:00Z', 1, 1, 'hash', 'keep')"
            )
            connection.execute(
                "INSERT INTO user_words(word, reading, known, in_anki, notes) "
                "VALUES ('古い', 'ふるい', 0, 1, 'keep')"
            )
            connection.execute(
                "INSERT INTO frequency(word, reading, source, rank) "
                "VALUES ('古い', 'ふるい', 'manual', 1)"
            )
            connection.commit()

        import_migaku_known_words(
            self.write("読む\n新しい\n"), db_path=self.db_path, now=self.clock
        )

        with closing(get_connection(self.db_path)) as connection:
            migaku = connection.execute(
                "SELECT word FROM known_spellings WHERE source='migaku' ORDER BY word"
            ).fetchall()
            manual = connection.execute(
                "SELECT word FROM known_spellings WHERE source='manual'"
            ).fetchall()
            identity = tuple(connection.execute("SELECT * FROM user_words").fetchone())
            frequency = tuple(connection.execute("SELECT * FROM frequency").fetchone())
            manual_metadata = tuple(
                connection.execute(
                    "SELECT * FROM personal_source_metadata WHERE source='manual'"
                ).fetchone()
            )
        self.assertEqual([row["word"] for row in migaku], ["新しい", "読む"])
        self.assertEqual([row["word"] for row in manual], ["古い"])
        self.assertEqual(identity, ("古い", "ふるい", 0, 1, 0, None, None, "keep"))
        self.assertEqual(frequency[:4], ("古い", "ふるい", "manual", 1))
        self.assertEqual(manual_metadata[-1], "keep")

    def test_malformed_snapshot_preserves_previous_snapshot(self):
        import_migaku_known_words(
            self.valid_source, db_path=self.db_path, now=self.clock
        )
        before = self.full_state_snapshot()

        with self.assertRaises(SourceFormatError) as error:
            import_migaku_known_words(
                self.write("読む\n\n語\n"), db_path=self.db_path, now=self.clock
            )

        self.assertEqual(error.exception.code, "source_format_error")
        self.assertIn("line 2", str(error.exception))
        self.assertEqual(self.full_state_snapshot(), before)

    def test_rejects_whitespace_only_line_with_line_number(self):
        with self.assertRaises(SourceFormatError) as error:
            import_migaku_known_words(
                self.write("読む\n \t \n語\n"), db_path=self.db_path, now=self.clock
            )

        self.assertIn("line 2", str(error.exception))

    def test_missing_invalid_utf8_and_source_io_are_typed_format_errors(self):
        invalid = self.write_bytes(b"\xff\n", name="invalid.txt")
        cases = (
            self.directory / "missing.txt",
            invalid,
        )
        for source in cases:
            with self.subTest(source=source.name):
                with self.assertRaises(SourceFormatError) as error:
                    import_migaku_known_words(
                        source, db_path=self.db_path, now=self.clock
                    )
                self.assertEqual(error.exception.code, "source_format_error")

        with patch.object(Path, "open", side_effect=OSError("read denied")):
            with self.assertRaises(SourceFormatError) as error:
                import_migaku_known_words(
                    self.valid_source, db_path=self.db_path, now=self.clock
                )
        self.assertEqual(error.exception.code, "source_format_error")

    def test_busy_lock_is_typed_and_preserves_snapshot(self):
        import_migaku_known_words(
            self.valid_source, db_path=self.db_path, now=self.clock
        )
        before = self.full_state_snapshot()
        with closing(sqlite3.connect(self.db_path)) as lock:
            lock.execute("BEGIN IMMEDIATE")
            with patch("config.SQLITE_BUSY_TIMEOUT_MS", 1):
                with self.assertRaises(DatabaseBusyError):
                    import_migaku_known_words(
                        self.write("新しい\n"), db_path=self.db_path, now=self.clock
                    )
        self.assertEqual(self.full_state_snapshot(), before)

    def test_publish_rollback_failure_preserves_primary_error(self):
        import_migaku_known_words(
            self.valid_source, db_path=self.db_path, now=self.clock
        )
        with closing(get_connection(self.db_path)) as connection:
            connection.execute(
                "CREATE TRIGGER reject_migaku BEFORE INSERT ON known_spellings "
                "WHEN NEW.source='migaku' BEGIN SELECT RAISE(ABORT, 'rejected'); END"
            )
            connection.commit()
        before = self.full_state_snapshot()
        real_connection = get_connection(self.db_path)

        class RollbackFailure:
            def __getattr__(self, name):
                return getattr(real_connection, name)

            def rollback(self):
                raise sqlite3.OperationalError("rollback failed")

            def close(self):
                real_connection.close()

        with patch(
            "japanese_frequency.knowledge.get_connection",
            return_value=RollbackFailure(),
        ):
            with self.assertRaises(DatabaseError) as error:
                import_migaku_known_words(
                    self.write("新しい\n"), db_path=self.db_path, now=self.clock
                )

        self.assertEqual(type(error.exception), DatabaseError)
        self.assertEqual(str(error.exception), "rejected")
        self.assertEqual(self.full_state_snapshot(), before)

    def test_get_known_spelling_supports_owned_and_borrowed_connections(self):
        import_migaku_known_words(
            self.valid_source, db_path=self.db_path, now=self.clock
        )
        self.assertEqual(get_known_spelling(" 読む ", db_path=self.db_path), {
            "known": True,
            "word": "読む",
            "sources": ["migaku"],
        })
        with closing(get_connection(self.db_path)) as connection:
            connection.execute(
                "INSERT INTO known_spellings VALUES ('読む', 'manual')"
            )
            self.assertEqual(get_known_spelling("読む", connection=connection), {
                "known": True,
                "word": "読む",
                "sources": ["manual", "migaku"],
            })
            connection.execute("SELECT 1")
        self.assertEqual(get_known_spelling("未知", db_path=self.db_path), {
            "known": False,
            "word": "未知",
            "sources": [],
        })


if __name__ == "__main__":
    unittest.main()
