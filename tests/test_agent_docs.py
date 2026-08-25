import unittest
from pathlib import Path


class AgentDocumentationTests(unittest.TestCase):
    def test_agent_guide_names_complete_workflow_and_tool_contracts(self):
        text = Path("AGENTS.md").read_text(encoding="utf-8")
        required = {
            "import_migaku_known_vocabulary",
            "import_japanese_media_vocabulary",
            "analyze_japanese_media",
            "recommend_japanese_media_word",
            "known_spelling",
            "known_identity",
            "in_anki",
            "ambiguous_reading",
            '"ok": true',
            '"ok": false',
        }
        self.assertEqual(
            required - set(filter(lambda item: item in text, required)), set()
        )

    def test_agent_guide_states_privacy_and_mutation_rules(self):
        text = " ".join(
            Path("AGENTS.md").read_text(encoding="utf-8").lower().split()
        )
        required = {
            "never upload personal files or data",
            "never dump the full database into model context",
            "never use frequency as the sole decision",
            "use report files for large output",
            "explicit user instruction or explicit evidence",
        }
        self.assertEqual(
            required - set(filter(lambda item: item in text, required)), set()
        )

    def test_readme_links_agent_guide(self):
        self.assertIn(
            "[AGENTS.md](AGENTS.md)", Path("README.md").read_text(encoding="utf-8")
        )

    def test_ignore_rules_scope_personal_import_patterns(self):
        lines = Path(".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("data/imports/migaku_known_words_*.txt", lines)
        self.assertIn("data/imports/media/*.txt", lines)
        self.assertIn("data/imports/media/*.csv", lines)
        self.assertNotIn("*.txt", lines)
        self.assertNotIn("*.csv", lines)


if __name__ == "__main__":
    unittest.main()
