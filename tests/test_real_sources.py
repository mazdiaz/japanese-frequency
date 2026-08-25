import os
import tempfile
import unittest
from pathlib import Path

from japanese_frequency.importers import import_bccwj, import_jpdb
from japanese_frequency.lookup import lookup_frequency
from japanese_frequency.setup import BCCWJ_SHA256, JPDB_SHA256, sha256_file


class RealSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary_directory.name) / "frequency.db"

    def tearDown(self):
        self.temporary_directory.cleanup()

    @unittest.skipUnless(
        os.environ.get("JPDB_SOURCE"),
        "set JPDB_SOURCE for real-source smoke test",
    )
    def test_real_jpdb_import_and_lookup(self):
        source = Path(os.environ["JPDB_SOURCE"])
        self.assertEqual(sha256_file(source), JPDB_SHA256)

        metadata = import_jpdb(source, db_path=self.db_path)
        result = lookup_frequency("読む", "よむ", db_path=self.db_path)

        self.assertEqual(metadata["sha256"], JPDB_SHA256)
        self.assertEqual(metadata["source_row_count"], 278_946)
        self.assertEqual(metadata["entry_count"], 276_190)
        self.assertTrue(result["found"])
        self.assertIsInstance(result["frequency"]["jpdb"]["rank"], int)

    @unittest.skipUnless(
        os.environ.get("BCCWJ_SOURCE"),
        "set BCCWJ_SOURCE for real-source smoke test",
    )
    def test_real_bccwj_import_and_lookup(self):
        source = Path(os.environ["BCCWJ_SOURCE"])
        self.assertEqual(sha256_file(source), BCCWJ_SHA256)

        metadata = import_bccwj(source, db_path=self.db_path)
        result = lookup_frequency("読む", "よむ", db_path=self.db_path)

        self.assertEqual(metadata["sha256"], BCCWJ_SHA256)
        self.assertEqual(metadata["source_row_count"], 2_434_619)
        self.assertEqual(metadata["entry_count"], 2_391_203)
        self.assertTrue(result["found"])
        self.assertIn("bccwj_luw", result["frequency"])


if __name__ == "__main__":
    unittest.main()
