import hashlib
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from japanese_frequency.database import get_connection
from japanese_frequency.errors import DatabaseError, DownloadError, SourceFormatError
from japanese_frequency.setup import (
    BCCWJ_SHA256,
    BCCWJ_URL,
    JPDB_SHA256,
    JPDB_URL,
    download_source,
    setup_database,
)
import setup_database as setup_cli


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.db_path = self.root / "output" / "frequency.db"
        fixtures = Path(__file__).parent / "fixtures"
        self.valid_jpdb = fixtures / "jpdb.tsv"
        header = (fixtures / "bccwj.tsv").read_text(encoding="utf-8").rstrip("\n")
        values = [""] * len(header.split("\t"))
        values[0] = "1"
        values[1] = "ヨム"
        values[2] = "読む"
        values[3] = "動詞"
        values[5] = "和"
        values[6] = "5"
        values[7] = "0.5"
        self.valid_bccwj = self.root / "valid-bccwj.tsv"
        self.valid_bccwj.write_text(
            header + "\n" + "\t".join(values) + "\n", encoding="utf-8"
        )
        self.jpdb_hash = hashlib.sha256(self.valid_jpdb.read_bytes()).hexdigest()
        self.bccwj_hash = hashlib.sha256(self.valid_bccwj.read_bytes()).hexdigest()
        self.clock = lambda: datetime(2026, 8, 25, 2, 55, tzinfo=timezone.utc)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def database_state(self):
        with closing(sqlite3.connect(self.db_path)) as connection:
            return tuple(
                connection.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()
                for table, order in (
                    ("frequency", "source, word, reading"),
                    ("source_metadata", "source"),
                    ("user_words", "word, reading"),
                )
            )

    def seed_old_state(self):
        setup_database(
            db_path=self.db_path,
            jpdb_source=self.valid_jpdb,
            with_bccwj=True,
            bccwj_source=self.valid_bccwj,
            jpdb_sha256=self.jpdb_hash,
            bccwj_sha256=self.bccwj_hash,
            now=self.clock,
        )
        with closing(get_connection(self.db_path)) as connection:
            connection.execute(
                "INSERT INTO user_words(word, reading, known, notes) "
                "VALUES ('読む', 'よむ', 1, 'keep')"
            )
            connection.commit()

    def test_download_renames_part_only_after_success(self):
        destination = self.root / "source.tsv"
        result = download_source(
            "https://example.invalid/source",
            destination,
            expected_sha256=hashlib.sha256(b"data").hexdigest().upper(),
            opener=lambda url: Response(b"data"),
        )

        self.assertEqual(result, destination)
        self.assertEqual(destination.read_bytes(), b"data")
        self.assertFalse(Path(f"{destination}.part").exists())

    def test_download_uses_unique_same_directory_part_names(self):
        destination = self.root / "source.tsv"
        observed = []

        class ObservingResponse(Response):
            def read(self, size=-1):
                observed.extend(path.name for path in self_root.glob("*.part"))
                return super().read(size)

        self_root = self.root
        for payload in (b"first", b"second"):
            download_source(
                "https://example.invalid/source",
                destination,
                opener=lambda url, payload=payload: ObservingResponse(payload),
            )

        self.assertEqual(len(set(observed)), 2)
        self.assertTrue(all(name.startswith("source.tsv.") for name in observed))
        self.assertTrue(all(name.endswith(".part") for name in observed))
        self.assertEqual(list(self.root.glob("*.part")), [])

    def test_failed_download_cleans_part_without_replacing_existing_file(self):
        destination = self.root / "source.tsv"
        destination.write_bytes(b"existing")

        with self.assertRaises(DownloadError) as error:
            download_source(
                "https://example.invalid/source",
                destination,
                opener=lambda url: (_ for _ in ()).throw(OSError("offline")),
            )

        self.assertEqual(error.exception.code, "download_error")
        self.assertEqual(destination.read_bytes(), b"existing")
        self.assertFalse(Path(f"{destination}.part").exists())

    def test_checksum_mismatch_cleans_part_and_has_stable_error(self):
        destination = self.root / "source.tsv"

        with self.assertRaises(DownloadError) as error:
            download_source(
                "https://example.invalid/source",
                destination,
                expected_sha256="0" * 64,
                opener=lambda url: Response(b"data"),
            )

        self.assertEqual(str(error.exception), "download checksum mismatch")
        self.assertFalse(destination.exists())
        self.assertFalse(Path(f"{destination}.part").exists())

    def test_download_directory_creation_failure_is_download_error(self):
        destination = self.root / "conflict" / "source.tsv"
        destination.parent.write_text("not a directory", encoding="utf-8")

        with self.assertRaises(DownloadError) as error:
            download_source("https://example.invalid/source", destination)

        self.assertEqual(error.exception.code, "download_error")

    def test_download_temp_creation_and_replace_failures_are_download_errors(self):
        destination = self.root / "source.tsv"
        cases = (
            patch(
                "japanese_frequency.setup.tempfile.NamedTemporaryFile",
                side_effect=OSError("temp denied"),
            ),
            patch("japanese_frequency.setup.os.replace", side_effect=OSError("replace denied")),
        )
        for failure in cases:
            with self.subTest(failure=failure):
                with failure, self.assertRaises(DownloadError) as error:
                    download_source(
                        "https://example.invalid/source",
                        destination,
                        opener=lambda url: Response(b"data"),
                    )
                self.assertEqual(error.exception.code, "download_error")

    def test_download_write_and_flush_failures_are_download_errors(self):
        destination = self.root / "source.tsv"
        with patch(
            "japanese_frequency.setup.shutil.copyfileobj",
            side_effect=OSError("write denied"),
        ):
            with self.assertRaises(DownloadError) as write_error:
                download_source(
                    "https://example.invalid/source",
                    destination,
                    opener=lambda url: Response(b"data"),
                )

        partial = self.root / "flush.part"
        partial.touch()
        output = MagicMock()
        output.name = str(partial)
        output.flush.side_effect = OSError("flush denied")
        temporary = MagicMock()
        temporary.__enter__.return_value = output
        with patch(
            "japanese_frequency.setup.tempfile.NamedTemporaryFile",
            return_value=temporary,
        ):
            with self.assertRaises(DownloadError) as flush_error:
                download_source(
                    "https://example.invalid/source",
                    destination,
                    opener=lambda url: Response(b"data"),
                )

        self.assertEqual(write_error.exception.code, "download_error")
        self.assertEqual(flush_error.exception.code, "download_error")

    def test_download_cleanup_failure_does_not_mask_original_error(self):
        destination = self.root / "source.tsv"

        with patch.object(Path, "unlink", side_effect=OSError("cleanup denied")):
            with self.assertRaises(DownloadError) as error:
                download_source(
                    "https://example.invalid/source",
                    destination,
                    expected_sha256="0" * 64,
                    opener=lambda url: Response(b"data"),
                )

        self.assertEqual(str(error.exception), "download checksum mismatch")

    def test_setup_reports_counts_resolved_path_size_and_integrity(self):
        report = setup_database(
            db_path=self.db_path,
            jpdb_source=self.valid_jpdb,
            jpdb_sha256=self.jpdb_hash,
            now=self.clock,
        )

        self.assertEqual(report["integrity_check"], "ok")
        self.assertGreater(report["jpdb_entries"], 0)
        self.assertEqual(report["bccwj_entries"], 0)
        self.assertEqual(report["database_path"], str(self.db_path.resolve()))
        self.assertEqual(report["database_size_bytes"], self.db_path.stat().st_size)

    def test_setup_imports_bccwj_only_when_requested(self):
        report = setup_database(
            db_path=self.db_path,
            jpdb_source=self.valid_jpdb,
            with_bccwj=True,
            bccwj_source=self.valid_bccwj,
            jpdb_sha256=self.jpdb_hash,
            bccwj_sha256=self.bccwj_hash,
            now=self.clock,
        )
        self.assertGreater(report["bccwj_entries"], 0)

        with patch("japanese_frequency.setup._stage_bccwj") as importer:
            setup_database(
                db_path=self.root / "jpdb-only.db",
                jpdb_source=self.valid_jpdb,
                jpdb_sha256=self.jpdb_hash,
                bccwj_source=self.valid_bccwj,
                now=self.clock,
            )
        importer.assert_not_called()

    def test_jpdb_only_rerun_reports_preserved_bccwj_count(self):
        initial = setup_database(
            db_path=self.db_path,
            jpdb_source=self.valid_jpdb,
            with_bccwj=True,
            bccwj_source=self.valid_bccwj,
            jpdb_sha256=self.jpdb_hash,
            bccwj_sha256=self.bccwj_hash,
            now=self.clock,
        )

        rerun = setup_database(
            db_path=self.db_path,
            jpdb_source=self.valid_jpdb,
            jpdb_sha256=self.jpdb_hash,
            now=self.clock,
        )

        self.assertEqual(rerun["bccwj_entries"], initial["bccwj_entries"])
        self.assertGreater(rerun["bccwj_entries"], 0)

    def test_explicit_pinned_source_checksum_mismatch_preserves_live_data(self):
        setup_database(
            db_path=self.db_path,
            jpdb_source=self.valid_jpdb,
            with_bccwj=True,
            bccwj_source=self.valid_bccwj,
            jpdb_sha256=self.jpdb_hash,
            bccwj_sha256=self.bccwj_hash,
            now=self.clock,
        )

        def state():
            import sqlite3

            with closing(sqlite3.connect(self.db_path)) as connection:
                return tuple(
                    connection.execute(
                        f"SELECT * FROM {table} ORDER BY {order}"
                    ).fetchall()
                    for table, order in (
                        ("frequency", "source, word, reading"),
                        ("source_metadata", "source"),
                    )
                )

        before = state()
        replacement_jpdb = self.root / "replacement-jpdb.tsv"
        replacement_jpdb.write_text(
            "term\treading\tfrequency\tkana_frequency\n新しい\tあたらしい\t1\t\n",
            encoding="utf-8",
        )
        replacement_hash = hashlib.sha256(replacement_jpdb.read_bytes()).hexdigest()

        cases = (
            {
                "jpdb_source": replacement_jpdb,
                "jpdb_sha256": "0" * 64,
            },
            {
                "jpdb_source": replacement_jpdb,
                "jpdb_sha256": replacement_hash,
                "with_bccwj": True,
                "bccwj_source": self.valid_bccwj,
                "bccwj_sha256": "0" * 64,
            },
        )
        for arguments in cases:
            with self.subTest(arguments=arguments):
                with self.assertRaises(SourceFormatError) as error:
                    setup_database(db_path=self.db_path, now=self.clock, **arguments)
                self.assertEqual(error.exception.code, "source_format_error")
                self.assertIn("checksum mismatch", str(error.exception))
                self.assertEqual(state(), before)

    def test_malformed_second_source_preserves_all_existing_state(self):
        self.seed_old_state()
        before = self.database_state()
        replacement_jpdb = self.root / "replacement-jpdb.tsv"
        replacement_jpdb.write_text(
            "term\treading\tfrequency\tkana_frequency\n新しい\tあたらしい\t1\t\n",
            encoding="utf-8",
        )
        malformed_bccwj = self.root / "malformed-bccwj.tsv"
        malformed_bccwj.write_text("wrong\n", encoding="utf-8")

        with self.assertRaises(SourceFormatError):
            setup_database(
                db_path=self.db_path,
                jpdb_source=replacement_jpdb,
                with_bccwj=True,
                bccwj_source=malformed_bccwj,
                jpdb_sha256=hashlib.sha256(replacement_jpdb.read_bytes()).hexdigest(),
                bccwj_sha256=hashlib.sha256(malformed_bccwj.read_bytes()).hexdigest(),
                now=self.clock,
            )

        self.assertEqual(self.database_state(), before)

    def test_multi_source_publish_failure_rolls_back_all_existing_state(self):
        self.seed_old_state()
        before = self.database_state()
        replacement_jpdb = self.root / "replacement-jpdb.tsv"
        replacement_jpdb.write_text(
            "term\treading\tfrequency\tkana_frequency\n新しい\tあたらしい\t1\t\n",
            encoding="utf-8",
        )
        with closing(get_connection(self.db_path)) as connection:
            connection.execute(
                "CREATE TRIGGER reject_bccwj BEFORE INSERT ON frequency "
                "WHEN NEW.source='bccwj_luw' BEGIN SELECT RAISE(ABORT, 'rejected'); END"
            )
            connection.commit()

        with self.assertRaises(DatabaseError):
            setup_database(
                db_path=self.db_path,
                jpdb_source=replacement_jpdb,
                with_bccwj=True,
                bccwj_source=self.valid_bccwj,
                jpdb_sha256=hashlib.sha256(replacement_jpdb.read_bytes()).hexdigest(),
                bccwj_sha256=self.bccwj_hash,
                now=self.clock,
            )

        self.assertEqual(self.database_state(), before)

    def test_final_database_stat_failure_is_database_error(self):
        original_stat = Path.stat

        def fail_database_stat(path, *args, **kwargs):
            if path == self.db_path.resolve():
                raise OSError("stat denied")
            return original_stat(path, *args, **kwargs)

        with patch.object(Path, "stat", new=fail_database_stat):
            with self.assertRaises(DatabaseError) as error:
                setup_database(
                    db_path=self.db_path,
                    jpdb_source=self.valid_jpdb,
                    jpdb_sha256=self.jpdb_hash,
                    now=self.clock,
                )

        self.assertEqual(error.exception.code, "database_error")

    def test_requested_bccwj_failure_is_fatal(self):
        malformed = self.root / "bad-bccwj.tsv"
        malformed.write_text("wrong\n", encoding="utf-8")

        with self.assertRaises(SourceFormatError):
            setup_database(
                db_path=self.db_path,
                jpdb_source=self.valid_jpdb,
                with_bccwj=True,
                bccwj_source=malformed,
                jpdb_sha256=self.jpdb_hash,
                bccwj_sha256=hashlib.sha256(malformed.read_bytes()).hexdigest(),
                now=self.clock,
            )

    def test_default_sources_use_pinned_urls_and_checksums_without_network(self):
        calls = []

        def fake_download(url, destination, *, expected_sha256=None, opener=None):
            calls.append((url, Path(destination).name, expected_sha256))
            source = self.valid_jpdb if url == JPDB_URL else self.valid_bccwj
            return source

        with patch(
            "japanese_frequency.setup.download_source", side_effect=fake_download
        ), patch(
            "japanese_frequency.setup._stage_jpdb",
            return_value={"source": "jpdb", "entry_count": 2},
        ), patch(
            "japanese_frequency.setup._stage_bccwj",
            return_value={"source": "bccwj_luw", "entry_count": 1},
        ), patch(
            "japanese_frequency.setup._publish_staged_sources",
        ):
            setup_database(
                db_path=self.db_path, with_bccwj=True, now=self.clock
            )

        self.assertEqual(
            calls,
            [
                (JPDB_URL, "jpdb_v2.2_freq_list_2024-10-13.csv", JPDB_SHA256),
                (BCCWJ_URL, "BCCWJ_frequencylist_luw_ver1_0.zip", BCCWJ_SHA256),
            ],
        )

    def test_cli_prints_report_and_requested_source_failure_exits_nonzero(self):
        report = {"integrity_check": "ok"}
        stdout = io.StringIO()
        with patch("setup_database.setup_database", return_value=report) as setup:
            with redirect_stdout(stdout):
                result = setup_cli.main(
                    ["--db", str(self.db_path), "--jpdb-source", str(self.valid_jpdb)]
                )
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(stdout.getvalue()), report)
        setup.assert_called_once_with(
            db_path=str(self.db_path),
            jpdb_source=str(self.valid_jpdb),
            with_bccwj=False,
            bccwj_source=None,
        )

        stderr = io.StringIO()
        with patch(
            "setup_database.setup_database", side_effect=DownloadError("failed")
        ):
            with redirect_stderr(stderr):
                result = setup_cli.main(["--with-bccwj"])
        self.assertEqual(result, 1)
        self.assertEqual(
            json.loads(stderr.getvalue()),
            {"error": {"type": "download_error", "message": "failed"}},
        )

    def test_cli_missing_explicit_source_emits_typed_json(self):
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            result = setup_cli.main(
                [
                    "--db",
                    str(self.db_path),
                    "--jpdb-source",
                    str(self.root / "missing.tsv"),
                ]
            )

        self.assertEqual(result, 1)
        payload = json.loads(stderr.getvalue())
        self.assertEqual(
            payload,
            {
                "error": {
                    "type": "source_not_found",
                    "message": f"source file not found: {self.root / 'missing.tsv'}",
                }
            },
        )

    def test_setup_cli_database_path_conflict_emits_json_without_traceback(self):
        conflict = self.root / "conflict"
        conflict.write_text("not a directory", encoding="utf-8")
        stderr = io.StringIO()

        with patch("japanese_frequency.setup.JPDB_SHA256", self.jpdb_hash):
            with redirect_stderr(stderr):
                result = setup_cli.main(
                    [
                        "--db",
                        str(conflict / "frequency.db"),
                        "--jpdb-source",
                        str(self.valid_jpdb),
                    ]
                )

        self.assertEqual(result, 1)
        self.assertEqual(json.loads(stderr.getvalue())["error"]["type"], "database_error")
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_cli_reconfigures_stdout_for_unicode_report_paths(self):
        report = {"database_path": "C:\\文档\\frequency.db"}
        output = io.BytesIO()
        stdout = io.TextIOWrapper(output, encoding="cp1252")

        with patch("setup_database.setup_database", return_value=report):
            with redirect_stdout(stdout):
                result = setup_cli.main(["--jpdb-source", str(self.valid_jpdb)])

        stdout.flush()
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue().decode("utf-8")), report)

    def test_cli_writes_unicode_error_json_when_stderr_starts_as_cp1252(self):
        archive = self.root / "bad-bccwj.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("文档.tsv", "wrong")
        project = Path(__file__).parents[1]
        jpdb_hash = hashlib.sha256(self.valid_jpdb.read_bytes()).hexdigest()
        bccwj_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
        command = (
            "import sys; "
            "sys.stderr.reconfigure(encoding='cp1252', errors='strict'); "
            "import japanese_frequency.setup as frequency_setup; "
            f"frequency_setup.JPDB_SHA256={jpdb_hash!r}; "
            f"frequency_setup.BCCWJ_SHA256={bccwj_hash!r}; "
            "import setup_database; "
            "raise SystemExit(setup_database.main(sys.argv[1:]))"
        )

        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                command,
                "--jpdb-source",
                str(self.valid_jpdb),
                "--with-bccwj",
                "--bccwj-source",
                str(archive),
                "--db",
                str(self.db_path),
            ],
            cwd=project,
            capture_output=True,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertNotIn(b"UnicodeEncodeError", completed.stderr)
        self.assertEqual(
            json.loads(completed.stderr.decode("utf-8")),
            {
                "error": {
                    "type": "source_format_error",
                    "message": (
                        "bccwj ZIP must contain only "
                        "'BCCWJ_frequencylist_luw_ver1_0.tsv'; got ['文档.tsv']"
                    ),
                }
            },
        )


if __name__ == "__main__":
    unittest.main()
