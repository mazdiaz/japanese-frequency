import importlib
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from japanese_frequency.database import get_connection, initialize_database
from japanese_frequency.errors import (
    AmbiguousReadingError,
    DatabaseBusyError,
    InvalidInputError,
)
from japanese_frequency.importers import import_jpdb
from japanese_frequency.user_words import (
    get_word_profile,
    mark_known,
    record_encounter,
    set_in_anki,
)


class UserWordTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary_directory.name) / "frequency.db"
        self.clock = lambda: datetime(2026, 8, 25, 2, 55, tzinfo=timezone.utc)
        initialize_database(self.db_path)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def insert_frequency(self, word, reading, source, rank=None):
        with closing(get_connection(self.db_path)) as connection:
            connection.execute(
                "INSERT INTO frequency(word, reading, source, rank) VALUES (?, ?, ?, ?)",
                (word, reading, source, rank),
            )
            connection.commit()

    def dump_user_words(self, connection=None):
        owns_connection = connection is None
        connection = connection or get_connection(self.db_path)
        try:
            return [
                tuple(row)
                for row in connection.execute(
                    "SELECT word, reading, known, in_anki, encounter_count, "
                    "first_seen, last_seen, notes FROM user_words "
                    "ORDER BY word, reading"
                )
            ]
        finally:
            if owns_connection:
                connection.close()

    def insert_user_word(
        self,
        word,
        reading,
        *,
        known=0,
        in_anki=0,
        encounter_count=0,
        first_seen=None,
        last_seen=None,
        notes=None,
    ):
        with closing(get_connection(self.db_path)) as connection:
            connection.execute(
                "INSERT INTO user_words "
                "(word, reading, known, in_anki, encounter_count, first_seen, "
                "last_seen, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    word,
                    reading,
                    known,
                    in_anki,
                    encounter_count,
                    first_seen,
                    last_seen,
                    notes,
                ),
            )
            connection.commit()

    def insert_known_spelling(self, word, source):
        with closing(get_connection(self.db_path)) as connection:
            connection.execute(
                "INSERT INTO known_spellings(word, source) VALUES (?, ?)",
                (word, source),
            )
            connection.commit()

    def test_encounter_defaults_identity_and_anki_states_to_null(self):
        result = record_encounter(
            "読む", "よむ", db_path=self.db_path, now=self.clock
        )

        self.assertIsNone(result["user"]["known"])
        self.assertIsNone(result["user"]["in_anki"])

    def test_explicit_false_states_remain_false(self):
        self.assertFalse(
            mark_known("読む", "よむ", False, db_path=self.db_path)["user"]["known"]
        )
        self.assertFalse(
            set_in_anki("読む", "よむ", False, db_path=self.db_path)["user"][
                "in_anki"
            ]
        )

    def test_profile_distinguishes_spelling_and_identity_knowledge(self):
        self.insert_known_spelling("開く", "migaku")

        result = get_word_profile("開く", "あく", db_path=self.db_path)

        self.assertTrue(result["found"])
        self.assertTrue(result["known_spelling"])
        self.assertEqual(result["known_spelling_sources"], ["migaku"])
        self.assertIsNone(result["known_identity"])
        self.assertIn("in_anki", result)
        self.assertIsNone(result["in_anki"])

    def test_format_utc_timestamp_preserves_clock_and_utc_z_behavior(self):
        spec = importlib.util.find_spec("japanese_frequency.timestamps")
        self.assertIsNotNone(spec)
        timestamps = importlib.import_module("japanese_frequency.timestamps")
        offset_clock = lambda: datetime(
            2026, 8, 25, 12, 55, 42, 987654, tzinfo=timezone(timedelta(hours=10))
        )

        self.assertEqual(
            timestamps.format_utc_timestamp(offset_clock), "2026-08-25T02:55:42Z"
        )

    def test_omitted_reading_resolves_single_corpus_identity(self):
        self.insert_frequency("読む", "よむ", "jpdb", 312)

        result = record_encounter("読む", db_path=self.db_path, now=self.clock)

        self.assertEqual(result["reading"], "よむ")
        self.assertEqual(result["user"]["encounter_count"], 1)
        self.assertEqual(result["user"]["first_seen"], "2026-08-25T02:55:00Z")
        self.assertEqual(result["user"]["last_seen"], "2026-08-25T02:55:00Z")

    def test_every_ambiguous_mutation_leaves_table_unchanged(self):
        self.insert_frequency("開く", "あく", "jpdb", 1)
        self.insert_frequency("開く", "ひらく", "jpdb", 2)
        self.insert_user_word("既存", "きそん", known=1, notes="keep")
        before = self.dump_user_words()
        calls = (
            lambda: record_encounter("開く", db_path=self.db_path, now=self.clock),
            lambda: mark_known("開く", db_path=self.db_path),
            lambda: set_in_anki("開く", db_path=self.db_path),
        )

        for call in calls:
            with self.subTest(call=call):
                with self.assertRaises(AmbiguousReadingError) as error:
                    call()
                self.assertEqual(error.exception.code, "ambiguous_reading")
                self.assertEqual(self.dump_user_words(), before)

    def test_no_corpus_entry_uses_empty_reading(self):
        result = mark_known("造語", db_path=self.db_path)

        self.assertEqual(result["reading"], "")
        self.assertTrue(result["user"]["known"])

    def test_record_encounter_increments_and_preserves_unrelated_fields(self):
        self.insert_user_word(
            "読む",
            "よむ",
            known=1,
            in_anki=1,
            encounter_count=2,
            first_seen="2026-08-24T00:00:00Z",
            last_seen="2026-08-24T01:00:00Z",
            notes="retain",
        )

        result = record_encounter("読む", "ヨム", db_path=self.db_path, now=self.clock)

        self.assertEqual(
            result["user"],
            {
                "known": True,
                "in_anki": True,
                "encounter_count": 3,
                "first_seen": "2026-08-24T00:00:00Z",
                "last_seen": "2026-08-25T02:55:00Z",
                "notes": "retain",
            },
        )

    def test_record_encounter_converts_injected_clock_to_utc_z(self):
        offset_clock = lambda: datetime(
            2026, 8, 25, 12, 55, 42, 987654, tzinfo=timezone(timedelta(hours=10))
        )

        result = record_encounter(
            "読む", "よむ", db_path=self.db_path, now=offset_clock
        )

        self.assertEqual(result["user"]["first_seen"], "2026-08-25T02:55:42Z")
        self.assertEqual(result["user"]["last_seen"], "2026-08-25T02:55:42Z")

    def test_known_and_anki_upserts_toggle_only_requested_field(self):
        self.insert_user_word(
            "読む",
            "よむ",
            known=1,
            in_anki=1,
            encounter_count=4,
            first_seen="2026-08-24T00:00:00Z",
            last_seen="2026-08-25T00:00:00Z",
            notes="retain",
        )

        known_result = mark_known("読む", "よむ", False, db_path=self.db_path)
        anki_result = set_in_anki("読む", "よむ", False, db_path=self.db_path)

        expected = {
            "known": False,
            "in_anki": False,
            "encounter_count": 4,
            "first_seen": "2026-08-24T00:00:00Z",
            "last_seen": "2026-08-25T00:00:00Z",
            "notes": "retain",
        }
        self.assertTrue(known_result["user"]["in_anki"])
        self.assertEqual(anki_result["user"], expected)

    def test_known_and_anki_reject_non_boolean_values_without_mutation(self):
        before = self.dump_user_words()
        calls = (mark_known, set_in_anki)
        invalid_values = (0, 1, "", "false", [], {}, None)

        for call in calls:
            for value in invalid_values:
                with self.subTest(call=call.__name__, value=value):
                    with self.assertRaises(InvalidInputError) as error:
                        call("読む", "よむ", value, db_path=self.db_path)
                    self.assertEqual(error.exception.code, "invalid_input")
                    self.assertEqual(self.dump_user_words(), before)

    def test_post_upsert_result_failure_rolls_back_mutation(self):
        before = self.dump_user_words()
        caught = None

        with patch(
            "japanese_frequency.user_words._user_from_row",
            side_effect=sqlite3.OperationalError("database is locked"),
        ):
            try:
                mark_known("読む", "よむ", db_path=self.db_path)
            except Exception as error:
                caught = error

        self.assertEqual(self.dump_user_words(), before)
        self.assertIsInstance(caught, DatabaseBusyError)
        self.assertEqual(caught.code, "database_busy")

    def test_user_state_persists_across_module_reload(self):
        import japanese_frequency.user_words as user_words

        mark_known("読む", "よむ", db_path=self.db_path)
        reloaded = importlib.reload(user_words)

        result = reloaded.get_word_profile("読む", "よむ", db_path=self.db_path)
        self.assertTrue(result["user"]["known"])

    def test_profile_combines_frequency_and_user_state(self):
        self.insert_frequency("読む", "よむ", "jpdb", 312)
        record_encounter("読む", "よむ", db_path=self.db_path, now=self.clock)

        profile = get_word_profile("読む", "ヨム", db_path=self.db_path)

        self.assertTrue(profile["found"])
        self.assertEqual(profile["reading"], "よむ")
        self.assertEqual(profile["frequency"]["jpdb"]["rank"], 312)
        self.assertEqual(profile["user"]["encounter_count"], 1)

    def test_profile_uses_one_connection_and_explicit_read_transaction(self):
        self.insert_frequency("読む", "よむ", "jpdb", 312)
        self.insert_user_word("読む", "よむ", known=1)

        for reading in ("よむ", None):
            connections = []
            statements = []

            def tracked_connection(db_path):
                connection = get_connection(db_path)
                connection.set_trace_callback(statements.append)
                connections.append(connection)
                return connection

            with self.subTest(reading=reading):
                with patch(
                    "japanese_frequency.user_words.get_connection",
                    side_effect=tracked_connection,
                ), patch(
                    "japanese_frequency.lookup.get_connection",
                    side_effect=AssertionError("separate lookup connection"),
                ):
                    result = get_word_profile(
                        "読む", reading, db_path=self.db_path
                    )

                self.assertTrue(result["found"])
                self.assertEqual(len(connections), 1)
                self.assertIn("BEGIN", statements)

    def test_precise_unknown_profile_has_stable_empty_shape(self):
        self.assertEqual(
            get_word_profile("不存在", "ふそんざい", db_path=self.db_path),
            {
                "found": False,
                "word": "不存在",
                "reading": "ふそんざい",
                "frequency": {},
                "user": {},
                "known_spelling": False,
                "known_spelling_sources": [],
                "known_identity": None,
                "in_anki": None,
            },
        )

    def test_word_only_profile_includes_corpus_and_user_only_identities(self):
        self.insert_frequency("開く", "あく", "jpdb", 20)
        self.insert_frequency("開く", "ひらく", "jpdb", 10)
        self.insert_user_word("開く", "あく", known=1)
        self.insert_user_word("開く", "しらく", in_anki=1)

        result = get_word_profile("開く", db_path=self.db_path)

        self.assertTrue(result["found"])
        self.assertEqual(
            [match["reading"] for match in result["matches"]],
            ["ひらく", "あく", "しらく"],
        )
        by_reading = {match["reading"]: match for match in result["matches"]}
        self.assertEqual(by_reading["ひらく"]["user"], {})
        self.assertTrue(by_reading["あく"]["user"]["known"])
        self.assertEqual(by_reading["しらく"]["frequency"], {})
        self.assertTrue(by_reading["しらく"]["user"]["in_anki"])

    def test_word_only_profile_returns_empty_matches_for_unknown_word(self):
        self.assertEqual(
            get_word_profile("不存在", db_path=self.db_path),
            {
                "found": False,
                "word": "不存在",
                "matches": [],
                "known_spelling": False,
                "known_spelling_sources": [],
            },
        )

    def test_locked_mutation_translates_busy_error_without_partial_write(self):
        first = get_connection(self.db_path)
        first.execute("BEGIN IMMEDIATE")
        before = self.dump_user_words(connection=first)
        try:
            with self.assertRaises(DatabaseBusyError) as error:
                mark_known("読む", "よむ", db_path=self.db_path)
            self.assertEqual(error.exception.code, "database_busy")
            self.assertEqual(self.dump_user_words(connection=first), before)
        finally:
            first.rollback()
            first.close()

    def test_omitted_reading_mutation_blocks_source_swap_until_commit(self):
        self.insert_frequency("読む", "よむ", "jpdb", 312)
        replacement = Path(self.temporary_directory.name) / "replacement.tsv"
        replacement.write_text(
            "term\treading\tfrequency\tkana_frequency\n読む\tどくむ\t1\t\n",
            encoding="utf-8",
        )
        resolved = threading.Event()
        release = threading.Event()
        swap_attempted = threading.Event()
        mutation_results = []
        mutation_errors = []
        import_errors = []

        import japanese_frequency.user_words as user_words

        original_resolve = user_words._resolve_mutation_reading

        def pausing_resolve(connection, word, reading):
            result = original_resolve(connection, word, reading)
            resolved.set()
            if not release.wait(2):
                raise AssertionError("mutation release timed out")
            return result

        def mutate():
            try:
                mutation_results.append(mark_known("読む", db_path=self.db_path))
            except Exception as error:
                mutation_errors.append(error)

        def replace_source():
            swap_attempted.set()
            try:
                import_jpdb(replacement, db_path=self.db_path, now=self.clock)
            except Exception as error:
                import_errors.append(error)

        with patch.object(
            user_words, "_resolve_mutation_reading", side_effect=pausing_resolve
        ):
            mutation_thread = threading.Thread(target=mutate)
            import_thread = threading.Thread(target=replace_source)
            mutation_thread.start()
            self.assertTrue(resolved.wait(2))
            import_thread.start()
            try:
                self.assertTrue(swap_attempted.wait(2))
                time.sleep(0.05)
                self.assertTrue(import_thread.is_alive())
            finally:
                release.set()
                mutation_thread.join(2)
                import_thread.join(2)

        self.assertFalse(mutation_thread.is_alive())
        self.assertFalse(import_thread.is_alive())
        self.assertEqual(mutation_errors, [])
        self.assertEqual(import_errors, [])
        self.assertEqual(mutation_results[0]["reading"], "よむ")
        with closing(get_connection(self.db_path)) as connection:
            user_reading = connection.execute(
                "SELECT reading FROM user_words WHERE word='読む'"
            ).fetchone()[0]
            corpus_reading = connection.execute(
                "SELECT reading FROM frequency WHERE word='読む' AND source='jpdb'"
            ).fetchone()[0]
        self.assertEqual(user_reading, "よむ")
        self.assertEqual(corpus_reading, "どくむ")


if __name__ == "__main__":
    unittest.main()
