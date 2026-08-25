import json
import os
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
        result = self.run_cli("lookup", "読む", db_path=self.db_path.parent)

        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.payload(result)["error"]["type"], "database_error")


if __name__ == "__main__":
    unittest.main()
