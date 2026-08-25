import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from japanese_frequency.database import get_connection
from japanese_frequency.errors import DatabaseError, SourceFormatError
from japanese_frequency.importers import import_bccwj, import_jpdb, validate_header


JPDB_HEADER = ("term", "reading", "frequency", "kana_frequency")
BCCWJ_HEADER = (
    "rank", "lForm", "lemma", "pos", "subLemma", "wType", "frequency", "pmw",
    "PB_rank", "PB_frequency", "PB_pmw", "PM_rank", "PM_frequency", "PM_pmw",
    "PN_rank", "PN_frequency", "PN_pmw", "LB_rank", "LB_frequency", "LB_pmw",
    "OW_rank", "OW_frequency", "OW_pmw", "OT_rank", "OT_frequency", "OT_pmw",
    "OP_rank", "OP_frequency", "OP_pmw", "OB_rank", "OB_frequency", "OB_pmw",
    "OC_rank", "OC_frequency", "OC_pmw", "OY_rank", "OY_frequency", "OY_pmw",
    "OV_rank", "OV_frequency", "OV_pmw", "OL_rank", "OL_frequency", "OL_pmw",
    "OM_rank", "OM_frequency", "OM_pmw", "PB_fixed_rank", "PB_fixed_frequency",
    "PB_fixed_pmw", "PB_variable_rank", "PB_variable_frequency", "PB_variable_pmw",
    "PM_fixed_rank", "PM_fixed_frequency", "PM_fixed_pmw", "PM_variable_rank",
    "PM_variable_frequency", "PM_variable_pmw", "PN_fixed_rank", "PN_fixed_frequency",
    "PN_fixed_pmw", "PN_variable_rank", "PN_variable_frequency", "PN_variable_pmw",
    "LB_fixed_rank", "LB_fixed_frequency", "LB_fixed_pmw", "LB_variable_rank",
    "LB_variable_frequency", "LB_variable_pmw", "OW_fixed_rank", "OW_fixed_frequency",
    "OW_fixed_pmw", "OW_variable_rank", "OW_variable_frequency", "OW_variable_pmw",
    "core_rank", "core_frequency", "core_pmw",
)


class ImporterTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary_directory.name)
        self.db_path = self.directory / "frequency.db"
        self.clock = lambda: datetime(2026, 8, 25, 2, 55, tzinfo=timezone.utc)
        fixtures = Path(__file__).parent / "fixtures"
        self.valid_jpdb = fixtures / "jpdb.tsv"
        fixture_header = (fixtures / "bccwj.tsv").read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(tuple(fixture_header.split("\t")), BCCWJ_HEADER)
        self.valid_bccwj = self.write_bccwj(
            [self.bccwj_row("ヨム", "読む", 5, "0.5")], name="valid-bccwj.tsv"
        )

    def tearDown(self):
        self.temporary_directory.cleanup()

    def write(self, text, name="source.tsv", encoding="utf-8"):
        path = self.directory / name
        path.write_text(text, encoding=encoding)
        return path

    def bccwj_row(self, reading, word, frequency, pmw, rank="1"):
        values = {name: "" for name in BCCWJ_HEADER}
        values.update(
            rank=str(rank),
            lForm=reading,
            lemma=word,
            pos="動詞",
            wType="和",
            frequency=str(frequency),
            pmw=str(pmw),
        )
        return "\t".join(values[name] for name in BCCWJ_HEADER)

    def write_bccwj(self, rows, name="bccwj.tsv"):
        return self.write("\t".join(BCCWJ_HEADER) + "\n" + "\n".join(rows) + "\n", name)

    def test_validate_header_accepts_only_exact_order_and_details_mismatches(self):
        validate_header(JPDB_HEADER, JPDB_HEADER, "jpdb")

        with self.assertRaises(SourceFormatError) as context:
            validate_header(
                ("reading", "term", "frequency", "extra"), JPDB_HEADER, "jpdb"
            )

        message = str(context.exception)
        self.assertIn("jpdb header mismatch", message)
        self.assertIn("missing=['kana_frequency']", message)
        self.assertIn("unexpected=['extra']", message)
        self.assertIn("reordered=['term', 'reading']", message)

    def test_validate_header_reports_duplicate_columns_as_format_error(self):
        with self.assertRaises(SourceFormatError) as context:
            validate_header(
                ("term", "term", "reading", "frequency"), JPDB_HEADER, "jpdb"
            )

        self.assertIn("missing=['kana_frequency']", str(context.exception))
        self.assertIn("duplicates=['term']", str(context.exception))

    def test_jpdb_collapses_duplicate_senses_to_minimum_ranks(self):
        source = self.write(
            "term\treading\tfrequency\tkana_frequency\n"
            "読む\tよむ\t312\t20000\n"
            "読む\tヨム\t900\t19896\n"
        )

        result = import_jpdb(source, db_path=self.db_path, now=self.clock)

        self.assertEqual(result["source_row_count"], 2)
        self.assertEqual(result["entry_count"], 1)
        with closing(get_connection(self.db_path)) as connection:
            row = connection.execute(
                "SELECT rank, kana_rank FROM frequency WHERE source='jpdb'"
            ).fetchone()
        self.assertEqual((row["rank"], row["kana_rank"]), (312, 19896))

    def test_jpdb_records_actual_file_hash_and_metadata(self):
        source = self.write(
            "\ufeffterm\treading\tfrequency\tkana_frequency\n読む\tヨム\t312\t\n",
            name="jpdb-source.csv",
        )
        digest = hashlib.sha256(source.read_bytes()).hexdigest()

        result = import_jpdb(
            source, db_path=self.db_path, version="2.2-test", now=self.clock
        )

        self.assertEqual(result["sha256"], digest)
        with closing(get_connection(self.db_path)) as connection:
            metadata = connection.execute(
                "SELECT * FROM source_metadata WHERE source='jpdb'"
            ).fetchone()
        self.assertEqual(metadata["version"], "2.2-test")
        self.assertEqual(metadata["filename"], "jpdb-source.csv")
        self.assertEqual(metadata["imported_at"], "2026-08-25T02:55:00Z")
        self.assertEqual(metadata["source_row_count"], 1)
        self.assertEqual(metadata["entry_count"], 1)
        self.assertEqual(metadata["sha256"], digest)
        self.assertIn("minimum ranks", metadata["notes"])

    def test_jpdb_hash_matches_parsed_snapshot_when_source_changes(self):
        original = (
            "term\treading\tfrequency\tkana_frequency\n読む\tよむ\t312\t\n"
        ).encode()
        replacement = (
            "term\treading\tfrequency\tkana_frequency\n見る\tみる\t1\t\n"
        ).encode()
        source = self.directory / "changing.tsv"
        source.write_bytes(original)
        original_open = Path.open
        mutated = False

        class MutatingReader:
            def __init__(self, wrapped):
                self.wrapped = wrapped

            def __enter__(self):
                self.wrapped.__enter__()
                return self

            def __exit__(self, *arguments):
                return self.wrapped.__exit__(*arguments)

            def __getattr__(self, name):
                return getattr(self.wrapped, name)

            def read(self, size=-1):
                nonlocal mutated
                chunk = self.wrapped.read(size)
                if chunk == b"" and not mutated:
                    with original_open(source, "wb") as output:
                        output.write(replacement)
                    mutated = True
                return chunk

        def open_with_mutation(path, mode="r", *arguments, **keywords):
            opened = original_open(path, mode, *arguments, **keywords)
            if path == source and mode == "rb":
                return MutatingReader(opened)
            return opened

        with patch.object(Path, "open", new=open_with_mutation):
            result = import_jpdb(source, db_path=self.db_path, now=self.clock)

        self.assertTrue(mutated)
        self.assertEqual(source.read_bytes(), replacement)
        self.assertEqual(result["sha256"], hashlib.sha256(original).hexdigest())
        with closing(get_connection(self.db_path)) as connection:
            words = connection.execute(
                "SELECT word FROM frequency WHERE source='jpdb'"
            ).fetchall()
        self.assertEqual([row["word"] for row in words], ["読む"])

    def test_malformed_jpdb_reimport_preserves_live_source_and_user_words(self):
        import_jpdb(self.valid_jpdb, db_path=self.db_path, now=self.clock)
        with closing(get_connection(self.db_path)) as connection:
            connection.execute(
                "INSERT INTO user_words(word, reading, known) VALUES ('読む','よむ',1)"
            )
            connection.commit()
        malformed = self.write(
            "reading\tterm\tfrequency\tkana_frequency\nよむ\t読む\t1\t\n"
        )

        with self.assertRaises(SourceFormatError) as context:
            import_jpdb(malformed, db_path=self.db_path, now=self.clock)

        self.assertEqual(context.exception.code, "source_format_error")
        with closing(get_connection(self.db_path)) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM frequency WHERE source='jpdb'"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(
                connection.execute("SELECT known FROM user_words").fetchone()[0], 1
            )

    def test_jpdb_rejects_bad_width_numbers_empty_values_and_no_rows(self):
        cases = {
            "width": "term\treading\tfrequency\tkana_frequency\n読む\tよむ\t1\n",
            "rank": "term\treading\tfrequency\tkana_frequency\n読む\tよむ\t0\t\n",
            "kana_rank": "term\treading\tfrequency\tkana_frequency\n読む\tよむ\t1\tx\n",
            "word": "term\treading\tfrequency\tkana_frequency\n \tよむ\t1\t\n",
            "reading": "term\treading\tfrequency\tkana_frequency\n読む\t \t1\t\n",
            "empty": "term\treading\tfrequency\tkana_frequency\n",
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(SourceFormatError):
                    import_jpdb(
                        self.write(text, name=f"{name}.tsv"),
                        db_path=self.db_path,
                        now=self.clock,
                    )

    def test_jpdb_live_swap_rolls_back_data_and_metadata(self):
        import_jpdb(self.valid_jpdb, db_path=self.db_path, now=self.clock)
        with closing(get_connection(self.db_path)) as connection:
            connection.execute(
                "CREATE TRIGGER reject_jpdb BEFORE INSERT ON frequency "
                "WHEN NEW.source='jpdb' BEGIN SELECT RAISE(ABORT, 'rejected'); END"
            )
            connection.commit()
        replacement = self.write(
            "term\treading\tfrequency\tkana_frequency\n新しい\tあたらしい\t1\t\n"
        )

        with self.assertRaises(DatabaseError):
            import_jpdb(replacement, db_path=self.db_path, now=self.clock)

        with closing(get_connection(self.db_path)) as connection:
            words = connection.execute(
                "SELECT word FROM frequency WHERE source='jpdb' ORDER BY word"
            ).fetchall()
            metadata = connection.execute(
                "SELECT filename FROM source_metadata WHERE source='jpdb'"
            ).fetchone()
        self.assertEqual([row["word"] for row in words], ["見る", "読む"])
        self.assertEqual(metadata["filename"], "jpdb.tsv")

    def test_bccwj_aggregates_and_assigns_deterministic_sequential_rank(self):
        source = self.write_bccwj(
            [
                self.bccwj_row("ヨム", "読む", 5, "0.5"),
                self.bccwj_row("ヨム", "読む", 7, "0.7"),
                self.bccwj_row("ミル", "見る", 12, "1.2"),
            ]
        )

        result = import_bccwj(source, db_path=self.db_path, now=self.clock)

        self.assertEqual(result["source_row_count"], 3)
        self.assertEqual(result["entry_count"], 2)
        with closing(get_connection(self.db_path)) as connection:
            rows = connection.execute(
                "SELECT word, reading, rank, frequency, frequency_per_million "
                "FROM frequency WHERE source='bccwj_luw' ORDER BY rank"
            ).fetchall()
        self.assertEqual(
            [
                (
                    row["word"],
                    row["reading"],
                    row["rank"],
                    row["frequency"],
                    row["frequency_per_million"],
                )
                for row in rows
            ],
            [("見る", "みる", 1, 12.0, 1.2), ("読む", "よむ", 2, 12.0, 1.2)],
        )

    def test_replacing_each_source_preserves_other_source_and_user_words(self):
        import_jpdb(self.valid_jpdb, db_path=self.db_path, now=self.clock)
        import_bccwj(self.valid_bccwj, db_path=self.db_path, now=self.clock)
        with closing(get_connection(self.db_path)) as connection:
            connection.execute(
                "INSERT INTO user_words(word, reading, known) VALUES ('読む','よむ',1)"
            )
            connection.commit()

        import_jpdb(self.valid_jpdb, db_path=self.db_path, now=self.clock)
        import_bccwj(self.valid_bccwj, db_path=self.db_path, now=self.clock)

        with closing(get_connection(self.db_path)) as connection:
            counts = {
                row["source"]: row["count"]
                for row in connection.execute(
                    "SELECT source, COUNT(*) AS count FROM frequency GROUP BY source"
                )
            }
            known = connection.execute("SELECT known FROM user_words").fetchone()[0]
        self.assertEqual(counts, {"bccwj_luw": 1, "jpdb": 2})
        self.assertEqual(known, 1)

    def test_bccwj_rejects_wrong_header_width_numbers_empty_values_and_no_rows(self):
        wrong_header = list(BCCWJ_HEADER)
        wrong_header[0], wrong_header[1] = wrong_header[1], wrong_header[0]
        cases = {
            "header": "\t".join(wrong_header) + "\n" + self.bccwj_row("ヨム", "読む", 1, 0.1) + "\n",
            "width": "\t".join(BCCWJ_HEADER) + "\n1\tヨム\t読む\n",
            "rank": "\t".join(BCCWJ_HEADER) + "\n" + self.bccwj_row("ヨム", "読む", 1, 0.1, rank="x") + "\n",
            "frequency": "\t".join(BCCWJ_HEADER) + "\n" + self.bccwj_row("ヨム", "読む", -1, 0.1) + "\n",
            "pmw": "\t".join(BCCWJ_HEADER) + "\n" + self.bccwj_row("ヨム", "読む", 1, "x") + "\n",
            "word": "\t".join(BCCWJ_HEADER) + "\n" + self.bccwj_row("ヨム", " ", 1, 0.1) + "\n",
            "reading": "\t".join(BCCWJ_HEADER) + "\n" + self.bccwj_row(" ", "読む", 1, 0.1) + "\n",
            "empty": "\t".join(BCCWJ_HEADER) + "\n",
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(SourceFormatError):
                    import_bccwj(
                        self.write(text, name=f"{name}.tsv"),
                        db_path=self.db_path,
                        now=self.clock,
                    )

    def test_bccwj_streams_expected_zip_member_and_hashes_archive(self):
        archive = self.directory / "BCCWJ_frequencylist_luw_ver1_0.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as output:
            output.write(self.valid_bccwj, "BCCWJ_frequencylist_luw_ver1_0.tsv")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()

        result = import_bccwj(archive, db_path=self.db_path, now=self.clock)

        self.assertEqual(result["sha256"], digest)
        self.assertEqual(result["entry_count"], 1)
        with closing(get_connection(self.db_path)) as connection:
            metadata = connection.execute(
                "SELECT * FROM source_metadata WHERE source='bccwj_luw'"
            ).fetchone()
        self.assertEqual(metadata["filename"], archive.name)
        self.assertEqual(metadata["sha256"], digest)
        self.assertIn("project-computed", metadata["notes"])

    def test_bccwj_zip_requires_exactly_expected_member(self):
        for name, members in (
            ("wrong", {"other.tsv": "content"}),
            (
                "extra",
                {
                    "BCCWJ_frequencylist_luw_ver1_0.tsv": self.valid_bccwj.read_text(encoding="utf-8"),
                    "extra.txt": "content",
                },
            ),
        ):
            with self.subTest(name=name):
                archive = self.directory / f"{name}.zip"
                with zipfile.ZipFile(archive, "w") as output:
                    for member, content in members.items():
                        output.writestr(member, content)
                with self.assertRaises(SourceFormatError):
                    import_bccwj(archive, db_path=self.db_path, now=self.clock)

    def test_bccwj_zip_member_errors_are_source_format_errors(self):
        archive = self.directory / "BCCWJ_frequencylist_luw_ver1_0.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.write(self.valid_bccwj, "BCCWJ_frequencylist_luw_ver1_0.tsv")

        for error in (
            RuntimeError("encrypted member"),
            NotImplementedError("unsupported compression"),
            zipfile.BadZipFile("corrupt member"),
        ):
            with self.subTest(error=type(error).__name__):
                with patch.object(zipfile.ZipFile, "open", side_effect=error):
                    with self.assertRaises(SourceFormatError) as context:
                        import_bccwj(archive, db_path=self.db_path, now=self.clock)
                self.assertEqual(context.exception.code, "source_format_error")

    def test_bccwj_corrupt_zip_member_read_is_source_format_error(self):
        archive = self.directory / "BCCWJ_frequencylist_luw_ver1_0.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as output:
            output.write(self.valid_bccwj, "BCCWJ_frequencylist_luw_ver1_0.tsv")
            member = output.infolist()[0]
        data_offset = member.header_offset + 30 + len(member.filename) + len(member.extra)
        with archive.open("r+b") as output:
            output.seek(data_offset)
            first_byte = output.read(1)
            output.seek(data_offset)
            output.write(bytes([first_byte[0] ^ 0xFF]))

        with self.assertRaises(SourceFormatError) as context:
            import_bccwj(archive, db_path=self.db_path, now=self.clock)

        self.assertEqual(context.exception.code, "source_format_error")

    def test_malformed_bccwj_reimport_preserves_all_live_state(self):
        import_jpdb(self.valid_jpdb, db_path=self.db_path, now=self.clock)
        import_bccwj(self.valid_bccwj, db_path=self.db_path, now=self.clock)
        with closing(get_connection(self.db_path)) as connection:
            connection.execute(
                "INSERT INTO user_words(word, reading, known, notes) "
                "VALUES ('読む', 'よむ', 1, 'keep')"
            )
            connection.commit()

            def state():
                return tuple(
                    tuple(row)
                    for table, order in (
                        ("frequency", "source, word, reading"),
                        ("source_metadata", "source"),
                        ("user_words", "word, reading"),
                    )
                    for row in connection.execute(
                        f"SELECT * FROM {table} ORDER BY {order}"
                    )
                )

            before = state()

        malformed = self.write(
            "\t".join(reversed(BCCWJ_HEADER)) + "\n", name="malformed-bccwj.tsv"
        )
        with self.assertRaises(SourceFormatError):
            import_bccwj(malformed, db_path=self.db_path, now=self.clock)

        with closing(get_connection(self.db_path)) as connection:
            after = tuple(
                tuple(row)
                for table, order in (
                    ("frequency", "source, word, reading"),
                    ("source_metadata", "source"),
                    ("user_words", "word, reading"),
                )
                for row in connection.execute(
                    f"SELECT * FROM {table} ORDER BY {order}"
                )
            )
        self.assertEqual(after, before)

    def test_import_scripts_write_json_results(self):
        project = Path(__file__).parents[1]
        cases = (
            ("import_jpdb.py", self.valid_jpdb, "jpdb"),
            ("import_bccwj.py", self.valid_bccwj, "bccwj_luw"),
        )
        for script, source, expected_source in cases:
            with self.subTest(script=script):
                completed = subprocess.run(
                    [
                        sys.executable,
                        str(project / "scripts" / script),
                        str(source),
                        "--db-path",
                        str(self.directory / f"{script}.db"),
                    ],
                    cwd=project,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(json.loads(completed.stdout)["source"], expected_source)


if __name__ == "__main__":
    unittest.main()
