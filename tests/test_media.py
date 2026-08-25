import csv
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
    InvalidInputError,
    MediaNotFoundError,
    SourceFormatError,
)
from japanese_frequency.media import (
    get_media_source,
    import_media_vocabulary,
    iter_media_candidates,
)


HEADERS = [
    "Word",
    "ReadingKana",
    "Occurences",
    "Definitions",
    "ExampleSentence",
    "JmDictWordId",
]


def row(word, reading, occurrences, definition="", example="", dictionary_id=""):
    return [word, reading, occurrences, definition, example, dictionary_id]


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.db_path = self.directory / "frequency.db"
        self.clock = lambda: datetime(2026, 8, 25, 3, 10, tzinfo=timezone.utc)
        self.fixture_directory = Path(__file__).parent / "fixtures"

    def tearDown(self):
        self.temporary_directory.cleanup()

    def write(self, name, text):
        path = self.directory / name
        path.write_text(text, encoding="utf-8")
        return path

    def write_bytes(self, name, data):
        path = self.directory / name
        path.write_bytes(data)
        return path

    def write_csv(self, name, rows, headers=HEADERS):
        path = self.directory / name
        with path.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(headers)
            writer.writerows(rows)
        return path

    def source_id(self, source_key):
        return get_media_source(source_key, db_path=self.db_path)["id"]

    def media_words(self, source_key):
        with closing(get_connection(self.db_path)) as connection:
            return [
                dict(record)
                for record in connection.execute(
                    "SELECT * FROM media_words WHERE media_id=? ORDER BY word, reading",
                    (self.source_id(source_key),),
                )
            ]

    def media_spellings(self, source_key):
        with closing(get_connection(self.db_path)) as connection:
            return [
                dict(record)
                for record in connection.execute(
                    "SELECT * FROM media_spellings WHERE media_id=? ORDER BY word",
                    (self.source_id(source_key),),
                )
            ]

    def full_state_snapshot(self):
        with closing(get_connection(self.db_path)) as connection:
            return tuple(
                tuple(tuple(record) for record in connection.execute(query))
                for query in (
                    "SELECT * FROM media_sources ORDER BY id",
                    "SELECT * FROM media_words ORDER BY media_id, word, reading",
                    "SELECT * FROM media_spellings ORDER BY media_id, word",
                    "SELECT * FROM user_words ORDER BY word, reading",
                    "SELECT * FROM frequency ORDER BY source, word, reading",
                    "SELECT * FROM known_spellings ORDER BY source, word",
                )
            )

    def find(self, records, word, reading=None):
        return next(
            record
            for record in records
            if record["word"] == word
            and (reading is None or record.get("reading") == reading)
        )

    def test_requested_txt_prefers_same_stem_csv_before_snapshot(self):
        txt = self.write("Volume 1.txt", "読む\n語\n")
        csv_path = self.write_csv("Volume 1.csv", [row("見る", "みる", 4)])

        result = import_media_vocabulary(
            txt, "series-v1", "Volume 1", db_path=self.db_path, now=self.clock
        )

        self.assertEqual(result["requested_filename"], "Volume 1.txt")
        self.assertEqual(result["selected_filename"], "Volume 1.csv")
        self.assertEqual(result["format"], "csv")
        self.assertEqual(result["sha256"], hashlib.sha256(csv_path.read_bytes()).hexdigest())
        self.assertIn("superseded", result["notes"])
        self.assertEqual([record["word"] for record in self.media_words("series-v1")], ["見る"])

    def test_unordered_txt_creates_spelling_membership_without_rank(self):
        result = import_media_vocabulary(
            self.write("words.txt", "読む\n語\n読む\n"),
            "plain-list",
            db_path=self.db_path,
            now=self.clock,
        )

        self.assertEqual(result["source_row_count"], 3)
        self.assertEqual(result["entry_count"], 2)
        records = self.media_spellings("plain-list")
        self.assertEqual(
            [(record["word"], record["occurrences"], record["media_rank"]) for record in records],
            [("語", None, None), ("読む", None, None)],
        )
        self.assertEqual(self.media_words("plain-list"), [])

    def test_csv_aggregates_canonical_readings_and_combines_ranks(self):
        result = import_media_vocabulary(
            self.fixture_directory / "media_words.csv",
            "media",
            db_path=self.db_path,
            now=self.clock,
        )

        exact = self.media_words("media")
        spellings = self.media_spellings("media")
        reading = self.find(exact, "読む", "よむ")
        self.assertEqual(result["source_row_count"], 4)
        self.assertEqual(result["entry_count"], 3)
        self.assertEqual(result["exact_entry_count"], 2)
        self.assertEqual(result["spelling_entry_count"], 1)
        self.assertEqual(reading["occurrences"], 7)
        self.assertEqual(reading["definitions"], "read")
        self.assertEqual(reading["example_sentence"], "本を読む。")
        self.assertEqual(reading["dictionary_id"], "1")
        self.assertEqual(self.find(spellings, "固有名")["media_rank"], 1)
        self.assertEqual(self.find(exact, "見る", "みる")["media_rank"], 2)
        self.assertEqual(reading["media_rank"], 3)

    def test_csv_first_nonempty_optional_values_are_retained(self):
        source = self.write_csv(
            "optional.csv",
            [
                row("読む", "よむ", 1, "", "", ""),
                row("読む", "ヨム", 2, "read", "example", "42"),
                row("読む", "よむ", 3, "later", "later", "99"),
            ],
        )

        import_media_vocabulary(source, "optional", db_path=self.db_path)

        record = self.media_words("optional")[0]
        self.assertEqual(record["occurrences"], 6)
        self.assertEqual(record["definitions"], "read")
        self.assertEqual(record["example_sentence"], "example")
        self.assertEqual(record["dictionary_id"], "42")

    def test_csv_accepts_additional_columns_and_optional_columns_may_be_absent(self):
        source = self.write_csv(
            "minimal.csv",
            [["語", "ご", "2", "ignored"]],
            headers=["Word", "ReadingKana", "Occurences", "Extra"],
        )

        import_media_vocabulary(source, "minimal", db_path=self.db_path)

        record = self.media_words("minimal")[0]
        self.assertEqual(record["occurrences"], 2)
        self.assertIsNone(record["definitions"])

    def test_csv_requires_exact_named_columns(self):
        for missing in ("Word", "ReadingKana", "Occurences"):
            with self.subTest(missing=missing):
                headers = [header for header in HEADERS if header != missing]
                source = self.write_csv(
                    f"missing-{missing}.csv",
                    [["x"] * len(headers)],
                    headers=headers,
                )
                with self.assertRaises(SourceFormatError) as error:
                    import_media_vocabulary(source, f"missing-{missing}", db_path=self.db_path)
                self.assertIn(missing, str(error.exception))

    def test_csv_rejects_bad_occurrences_blank_word_and_invalid_utf8(self):
        cases = (
            (self.write_csv("zero.csv", [row("語", "ご", 0)]), "positive integer"),
            (self.write_csv("decimal.csv", [row("語", "ご", "1.5")]), "positive integer"),
            (self.write_csv("blank.csv", [row(" ", "ご", 1)]), "row 2"),
            (self.write_bytes("invalid.csv", b"Word,ReadingKana,Occurences\n\xe8,\xff,1\n"), "parsed"),
        )
        for source, message in cases:
            with self.subTest(source=source.name):
                with self.assertRaises(SourceFormatError) as error:
                    import_media_vocabulary(source, source.stem, db_path=self.db_path)
                self.assertIn(message, str(error.exception))

    def test_csv_rejects_invalid_reading_encoding(self):
        source = self.write_csv("reading.csv", [row("語", "go", 1)])

        with self.assertRaises(SourceFormatError) as error:
            import_media_vocabulary(source, "reading", db_path=self.db_path)

        self.assertIn("ReadingKana", str(error.exception))

    def test_duplicate_display_names_allowed_and_source_key_stable(self):
        first = import_media_vocabulary(
            self.write_csv("a.csv", [row("語", "ご", 1)]),
            "series-a-v1",
            "Volume 1",
            db_path=self.db_path,
        )
        second = import_media_vocabulary(
            self.write_csv("b.csv", [row("見る", "みる", 2)]),
            "series-b-v1",
            "Volume 1",
            db_path=self.db_path,
        )
        updated = import_media_vocabulary(
            self.write_csv("c.csv", [row("読む", "よむ", 3)]),
            "series-a-v1",
            "Renamed",
            db_path=self.db_path,
        )

        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(updated["id"], first["id"])
        self.assertEqual(updated["display_name"], "Renamed")
        self.assertEqual([record["word"] for record in self.media_words("series-a-v1")], ["読む"])
        self.assertEqual([record["word"] for record in self.media_words("series-b-v1")], ["見る"])

    def test_failed_reimport_preserves_all_live_state(self):
        valid = self.write_csv("valid.csv", [row("語", "ご", 1)])
        import_media_vocabulary(valid, "media", db_path=self.db_path, now=self.clock)
        with closing(get_connection(self.db_path)) as connection:
            connection.execute("INSERT INTO user_words(word, reading, known) VALUES ('語', 'ご', 1)")
            connection.execute("INSERT INTO frequency(word, reading, source, rank) VALUES ('語', 'ご', 'manual', 1)")
            connection.execute("INSERT INTO known_spellings VALUES ('語', 'manual')")
            connection.commit()
        before = self.full_state_snapshot()

        with self.assertRaises(SourceFormatError):
            import_media_vocabulary(
                self.write_csv("bad.csv", [row("語", "ご", "bad")]),
                "media",
                db_path=self.db_path,
            )

        self.assertEqual(self.full_state_snapshot(), before)

    def test_source_key_and_display_name_are_validated(self):
        source = self.write("words.txt", "語\n")
        for source_key in (None, "", " ", 3):
            with self.subTest(source_key=source_key):
                with self.assertRaises(InvalidInputError):
                    import_media_vocabulary(source, source_key, db_path=self.db_path)
        with self.assertRaises(InvalidInputError):
            import_media_vocabulary(source, "valid", " ", db_path=self.db_path)

    def test_missing_unsupported_and_directory_sources_are_typed(self):
        cases = (
            self.directory / "missing.txt",
            self.write("words.json", "[]"),
            self.directory,
        )
        for source in cases:
            with self.subTest(source=source):
                with self.assertRaises(SourceFormatError):
                    import_media_vocabulary(source, "source", db_path=self.db_path)

    def test_empty_sources_are_rejected(self):
        for source in (
            self.write("empty.txt", ""),
            self.write_csv("empty.csv", []),
        ):
            with self.subTest(source=source.name):
                with self.assertRaises(SourceFormatError):
                    import_media_vocabulary(source, source.stem, db_path=self.db_path)

    def test_busy_lock_is_typed_and_preserves_existing_source(self):
        import_media_vocabulary(
            self.write_csv("before.csv", [row("語", "ご", 1)]),
            "media",
            db_path=self.db_path,
        )
        before = self.full_state_snapshot()
        with closing(sqlite3.connect(self.db_path)) as lock:
            lock.execute("BEGIN IMMEDIATE")
            with patch("config.SQLITE_BUSY_TIMEOUT_MS", 1):
                with self.assertRaises(DatabaseBusyError):
                    import_media_vocabulary(
                        self.write_csv("after.csv", [row("見る", "みる", 2)]),
                        "media",
                        db_path=self.db_path,
                    )
        self.assertEqual(self.full_state_snapshot(), before)

    def test_child_foreign_keys_and_other_source_isolation(self):
        first = import_media_vocabulary(
            self.write_csv("first.csv", [row("語", "ご", 1)]),
            "first",
            db_path=self.db_path,
        )
        import_media_vocabulary(
            self.write("second.txt", "読む\n"), "second", db_path=self.db_path
        )
        before_second = self.media_spellings("second")

        import_media_vocabulary(
            self.write_csv("changed.csv", [row("見る", "みる", 2)]),
            "first",
            db_path=self.db_path,
        )

        self.assertEqual(self.media_spellings("second"), before_second)
        with closing(get_connection(self.db_path)) as connection:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO media_words VALUES (?, 'x', 'x', 1, 1, NULL, NULL, NULL)",
                    (first["id"] + 999,),
                )

    def test_get_media_source_supports_owned_and_borrowed_connections(self):
        imported = import_media_vocabulary(
            self.write("words.txt", "語\n"),
            "media",
            "Words",
            db_path=self.db_path,
            now=self.clock,
        )

        self.assertEqual(get_media_source("media", db_path=self.db_path), imported)
        with closing(get_connection(self.db_path)) as connection:
            self.assertEqual(get_media_source("media", connection=connection), imported)
            connection.execute("SELECT 1")
        with self.assertRaises(MediaNotFoundError):
            get_media_source("missing", db_path=self.db_path)

    def test_iter_media_candidates_returns_exact_then_spelling_rows(self):
        imported = import_media_vocabulary(
            self.fixture_directory / "media_words.csv", "media", db_path=self.db_path
        )

        with closing(get_connection(self.db_path)) as connection:
            candidates = iter_media_candidates(connection, imported["id"])

        self.assertEqual(
            [(item["word"], item["reading"], item["identity_type"]) for item in candidates],
            [("固有名", None, "spelling"), ("見る", "みる", "exact"), ("読む", "よむ", "exact")],
        )
        self.assertEqual([item["media_rank"] for item in candidates], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
