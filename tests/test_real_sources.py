import os
import tempfile
import unittest
from pathlib import Path

from japanese_frequency.importers import import_bccwj, import_jpdb
from japanese_frequency.lookup import lookup_frequency


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
        metadata = import_jpdb(os.environ["JPDB_SOURCE"], db_path=self.db_path)
        result = lookup_frequency("読む", "よむ", db_path=self.db_path)

        self.assertGreater(metadata["source_row_count"], 0)
        self.assertGreater(metadata["entry_count"], 0)
        self.assertTrue(result["found"])
        self.assertIsInstance(result["frequency"]["jpdb"]["rank"], int)

    @unittest.skipUnless(
        os.environ.get("BCCWJ_SOURCE"),
        "set BCCWJ_SOURCE for real-source smoke test",
    )
    def test_real_bccwj_import_and_lookup(self):
        metadata = import_bccwj(os.environ["BCCWJ_SOURCE"], db_path=self.db_path)
        result = lookup_frequency("読む", "よむ", db_path=self.db_path)

        self.assertEqual(metadata["source_row_count"], 2_434_619)
        self.assertGreater(metadata["entry_count"], 0)
        self.assertTrue(result["found"])
        self.assertIn("bccwj_luw", result["frequency"])


if __name__ == "__main__":
    unittest.main()
