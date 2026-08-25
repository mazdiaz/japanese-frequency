import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from japanese_frequency.errors import DownloadError, SourceFormatError
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
        self.clock = lambda: datetime(2026, 8, 25, 2, 55, tzinfo=timezone.utc)

    def tearDown(self):
        self.temporary_directory.cleanup()

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

    def test_setup_reports_counts_resolved_path_size_and_integrity(self):
        report = setup_database(
            db_path=self.db_path, jpdb_source=self.valid_jpdb, now=self.clock
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
            now=self.clock,
        )
        self.assertGreater(report["bccwj_entries"], 0)

        with patch("japanese_frequency.setup.import_bccwj") as importer:
            setup_database(
                db_path=self.root / "jpdb-only.db",
                jpdb_source=self.valid_jpdb,
                bccwj_source=self.valid_bccwj,
                now=self.clock,
            )
        importer.assert_not_called()

    def test_requested_bccwj_failure_is_fatal(self):
        malformed = self.root / "bad-bccwj.tsv"
        malformed.write_text("wrong\n", encoding="utf-8")

        with self.assertRaises(SourceFormatError):
            setup_database(
                db_path=self.db_path,
                jpdb_source=self.valid_jpdb,
                with_bccwj=True,
                bccwj_source=malformed,
                now=self.clock,
            )

    def test_default_sources_use_pinned_urls_and_checksums_without_network(self):
        calls = []

        def fake_download(url, destination, *, expected_sha256=None, opener=None):
            calls.append((url, Path(destination).name, expected_sha256))
            source = self.valid_jpdb if url == JPDB_URL else self.valid_bccwj
            return source

        with patch("japanese_frequency.setup.download_source", side_effect=fake_download):
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


if __name__ == "__main__":
    unittest.main()
