import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from japanese_frequency.database import get_connection, initialize_database


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.db_path = self.root / "frequency.db"
        self.migaku_path = self.root / "既知語.txt"
        self.media_txt = self.root / "作品.txt"
        self.media_csv = self.root / "作品.csv"
        self.report = self.root / "報告" / "分析.csv"
        self.migaku_path.write_text("読む\n", encoding="utf-8")
        self.media_txt.write_text("fallback\n", encoding="utf-8")
        self.media_csv.write_text(
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

    def run_cli(self, *arguments, db_path=None):
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "cp1252"
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "japanese_frequency",
                "--db",
                str(db_path or self.db_path),
                *arguments,
            ],
            capture_output=True,
            env=environment,
            check=False,
        )

    def payload(self, result):
        return json.loads(result.stdout.decode("utf-8"))

    def test_lookup_outputs_direct_unicode_json_with_optional_reading(self):
        precise = self.run_cli("lookup", "読む", "よむ")
        broad = self.run_cli("lookup", "読む")

        self.assertEqual(precise.returncode, 0, precise.stderr)
        self.assertEqual(broad.returncode, 0, broad.stderr)
        self.assertIn("読む".encode("utf-8"), precise.stdout)
        self.assertEqual(self.payload(precise)["reading"], "よむ")
        self.assertEqual(self.payload(broad)["matches"][0]["reading"], "よむ")

    def test_profile_and_encounter_commands_return_direct_results(self):
        profile = self.run_cli("profile", "読む", "よむ")
        encounter = self.run_cli("encounter", "読む")

        self.assertEqual(profile.returncode, 0, profile.stderr)
        self.assertEqual(encounter.returncode, 0, encounter.stderr)
        self.assertNotIn("ok", self.payload(profile))
        self.assertEqual(self.payload(encounter)["user"]["encounter_count"], 1)

    def test_boolean_commands_support_setting_and_clearing(self):
        for command, field in (("known", "known"), ("anki", "in_anki")):
            with self.subTest(command=command):
                set_result = self.run_cli(command, "読む", "よむ", "--value", "true")
                clear_result = self.run_cli(command, "読む", "よむ", "--value", "false")

                self.assertEqual(set_result.returncode, 0, set_result.stderr)
                self.assertIs(self.payload(set_result)["user"][field], True)
                self.assertEqual(clear_result.returncode, 0, clear_result.stderr)
                self.assertIs(self.payload(clear_result)["user"][field], False)

    def test_import_and_analyze_cli_workflow(self):
        imported = self.run_cli("import-known", str(self.migaku_path))
        media = self.run_cli(
            "import-media",
            str(self.media_txt),
            "--source-key",
            "media",
            "--name",
            "Volume 1",
        )
        analyzed = self.run_cli("analyze-media", "media", "--limit", "2")

        self.assertEqual(imported.returncode, 0, imported.stderr)
        self.assertEqual(media.returncode, 0, media.stderr)
        self.assertEqual(self.payload(media)["selected_filename"], "作品.csv")
        self.assertEqual(analyzed.returncode, 0, analyzed.stderr)
        self.assertLessEqual(
            sum(len(value) for value in self.payload(analyzed)["candidates"].values()),
            2,
        )

    def test_analyze_cli_writes_unicode_csv_report_and_returns_metadata(self):
        self.run_cli(
            "import-media", str(self.media_csv), "--source-key", "media"
        )

        result = self.run_cli(
            "analyze-media", "media", "--output", str(self.report)
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.report.exists())
        payload = self.payload(result)
        self.assertEqual(set(payload), {"source", "summary", "report"})
        self.assertNotIn("candidates", payload)
        self.assertEqual(payload["report"]["output_path"], str(self.report))
        self.assertEqual(
            payload["report"]["row_count"], payload["summary"]["returned_candidates"]
        )

    def test_recommend_media_context_flags_parse_true_and_false(self):
        self.run_cli(
            "import-media", str(self.media_csv), "--source-key", "media"
        )
        flags = (
            "failed-recall",
            "successful-inference",
            "transparent-composition",
            "personally-useful",
        )

        for flag in flags:
            for value, expected in (("true", True), ("false", False)):
                with self.subTest(flag=flag, value=value):
                    result = self.run_cli(
                        "recommend-media",
                        "media",
                        "読む",
                        "よむ",
                        f"--{flag}",
                        value,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIs(
                        self.payload(result)["context"][flag.replace("-", "_")],
                        expected,
                    )

    def test_new_cli_errors_are_typed_json_and_nonzero(self):
        self.run_cli(
            "import-media", str(self.media_csv), "--source-key", "media"
        )
        malformed_db = self.root / "malformed.db"
        sqlite3.connect(malformed_db).close()
        results = (
            (self.run_cli("analyze-media", "missing"), "media_not_found"),
            (self.run_cli("recommend-media", "media", "開く"), "ambiguous_reading"),
            (self.run_cli("analyze-media", "media", "--limit", "invalid"), "invalid_input"),
            (
                self.run_cli("analyze-media", "media", db_path=malformed_db),
                "database_error",
            ),
        )

        for result, error_type in results:
            with self.subTest(error_type=error_type):
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.payload(result)["error"]["type"], error_type)
        self.assertEqual(
            self.payload(results[1][0])["error"]["matches"], ["あく", "ひらく"]
        )

    def test_domain_and_parser_errors_are_machine_readable_and_nonzero(self):
        results = (
            (self.run_cli("known", "開く"), "ambiguous_reading"),
            (self.run_cli("lookup", "   "), "invalid_input"),
            (self.run_cli("known", "読む", "--value", "yes"), "invalid_input"),
        )

        for result, error_type in results:
            with self.subTest(error_type=error_type):
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(set(self.payload(result)), {"error"})
                self.assertEqual(self.payload(result)["error"]["type"], error_type)
                self.assertIn("message", self.payload(result)["error"])

    def test_database_error_is_machine_readable_and_nonzero(self):
        malformed_db = self.db_path.parent / "private_frequency_schema.db"
        sqlite3.connect(malformed_db).close()
        result = self.run_cli("lookup", "読む", db_path=malformed_db)

        self.assertNotEqual(result.returncode, 0)
        error = self.payload(result)["error"]
        self.assertEqual(error["type"], "database_error")
        self.assertIn("message", error)
        self.assertTrue(error["message"])
        self.assertNotIn("frequency", error["message"])

    def test_database_parent_path_conflict_has_json_without_traceback(self):
        conflict = self.db_path.parent / "conflict"
        conflict.write_text("not a directory", encoding="utf-8")

        result = self.run_cli("lookup", "読む", db_path=conflict / "frequency.db")

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.payload(result)["error"]["type"], "database_error")
        self.assertNotIn(b"Traceback", result.stderr)

    def test_database_busy_error_does_not_expose_sqlite_message(self):
        connection = get_connection(self.db_path)
        connection.execute("BEGIN IMMEDIATE")
        try:
            result = self.run_cli("known", "読む", "よむ")
        finally:
            connection.rollback()
            connection.close()

        self.assertNotEqual(result.returncode, 0)
        error = self.payload(result)["error"]
        self.assertEqual(error["type"], "database_busy")
        self.assertIn("message", error)
        self.assertTrue(error["message"])
        self.assertNotIn("locked", error["message"].lower())


if __name__ == "__main__":
    unittest.main()
