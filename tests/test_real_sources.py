import os
import tempfile
import unittest
from pathlib import Path

from japanese_frequency.importers import import_bccwj, import_jpdb
from japanese_frequency.knowledge import import_migaku_known_words
from japanese_frequency.lookup import lookup_frequency
from japanese_frequency.media import import_media_vocabulary
from japanese_frequency.mining import analyze_media
from japanese_frequency.setup import BCCWJ_SHA256, JPDB_SHA256, sha256_file


MIGAKU_KNOWN_SHA256 = "adc4e189f3565fcf8b8c6e5297832366a29abc348766976d96a728e71be9f6c1"
MEDIA_VOCAB_SHA256 = "d10c5dada703c581be9b4f526ff8c23c67c6c2de111ccbcce4bec7f3e308a13e"


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

    @unittest.skipUnless(
        os.environ.get("MIGAKU_KNOWN_SOURCE")
        and os.environ.get("MEDIA_VOCAB_SOURCE"),
        "set MIGAKU_KNOWN_SOURCE and MEDIA_VOCAB_SOURCE",
    )
    def test_real_migaku_and_media_workflow(self):
        known_source = Path(os.environ["MIGAKU_KNOWN_SOURCE"])
        requested_media = Path(os.environ["MEDIA_VOCAB_SOURCE"])
        selected_media = requested_media.with_suffix(".csv")
        if requested_media.suffix.lower() != ".txt" or not selected_media.is_file():
            selected_media = requested_media

        self.assertEqual(sha256_file(known_source), MIGAKU_KNOWN_SHA256)
        self.assertEqual(sha256_file(selected_media), MEDIA_VOCAB_SHA256)

        known = import_migaku_known_words(known_source, db_path=self.db_path)
        media = import_media_vocabulary(
            requested_media, "real-media", db_path=self.db_path
        )
        analysis = analyze_media("real-media", limit=100, db_path=self.db_path)

        self.assertEqual(known["sha256"], MIGAKU_KNOWN_SHA256)
        self.assertEqual(known["source_row_count"], 7038)
        self.assertEqual(media["sha256"], MEDIA_VOCAB_SHA256)
        self.assertEqual(media["selected_filename"], "Volume 1.csv")
        self.assertGreater(
            sum(len(rows) for rows in analysis["candidates"].values()), 0
        )


if __name__ == "__main__":
    unittest.main()
