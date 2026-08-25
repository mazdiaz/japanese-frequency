import inspect
import unittest
from pathlib import Path

from japanese_frequency import (
    analyze_media,
    export_media_analysis_csv,
    get_known_spelling,
    get_media_source,
    import_media_vocabulary,
    import_migaku_known_words,
    recommend_media_word,
)
from japanese_frequency.tools import (
    analyze_japanese_media,
    import_japanese_media_vocabulary,
    import_migaku_known_vocabulary,
    recommend_japanese_media_word,
)


def _section(text, heading):
    start = text.index(heading) + len(heading)
    end = text.find("\n## ", start)
    return text[start:] if end == -1 else text[start:end]


class AgentDocumentationTests(unittest.TestCase):
    def test_agent_guide_documents_exact_mining_api_and_wrapper_signatures(self):
        text = Path("AGENTS.md").read_text(encoding="utf-8")
        section = _section(text, "## Public Interfaces")
        functions = (
            get_known_spelling,
            get_media_source,
            import_migaku_known_words,
            import_media_vocabulary,
            analyze_media,
            export_media_analysis_csv,
            recommend_media_word,
            import_migaku_known_vocabulary,
            import_japanese_media_vocabulary,
            analyze_japanese_media,
            recommend_japanese_media_word,
        )
        for function in functions:
            with self.subTest(function=function.__name__):
                self.assertIn(
                    f"`{function.__name__}{inspect.signature(function)}`", section
                )

    def test_agent_guide_documents_complete_cli_syntax(self):
        text = Path("AGENTS.md").read_text(encoding="utf-8")
        section = _section(text, "## CLI Contract")
        commands = (
            "python -m japanese_frequency [--db DB_PATH] lookup WORD [READING]",
            "python -m japanese_frequency [--db DB_PATH] profile WORD [READING]",
            "python -m japanese_frequency [--db DB_PATH] encounter WORD [READING]",
            "python -m japanese_frequency [--db DB_PATH] known WORD [READING] [--value {true,false}]",
            "python -m japanese_frequency [--db DB_PATH] anki WORD [READING] [--value {true,false}]",
            "python -m japanese_frequency [--db DB_PATH] import-known PATH",
            "python -m japanese_frequency [--db DB_PATH] import-media PATH --source-key SOURCE_KEY [--name DISPLAY_NAME]",
            "python -m japanese_frequency [--db DB_PATH] analyze-media SOURCE_KEY [--limit LIMIT] [--output PATH]",
            "python -m japanese_frequency [--db DB_PATH] recommend-media SOURCE_KEY WORD [READING] [--failed-recall {true,false}] [--successful-inference {true,false}] [--transparent-composition {true,false}] [--personally-useful {true,false}]",
        )
        for command in commands:
            with self.subTest(command=command):
                self.assertIn(command, section)

    def test_agent_guide_documents_complete_tool_and_cli_envelopes(self):
        text = Path("AGENTS.md").read_text(encoding="utf-8")
        section = _section(text, "## Envelope Contract")
        schemas = (
            '{"ok": true, "result": <object>}',
            '{"ok": false, "error": {"type": "<code>", "message": "<safe message>"}}',
            '{"ok": false, "error": {"type": "ambiguous_reading", "message": "<safe message>", "matches": ["<reading>"]}}',
            '{"error": {"type": "<code>", "message": "<safe message>"}}',
            '{"error": {"type": "ambiguous_reading", "message": "<safe message>", "matches": ["<reading>"]}}',
        )
        for schema in schemas:
            with self.subTest(schema=schema):
                self.assertIn(schema, section)

    def test_agent_guide_makes_setup_network_and_mutation_safety_explicit(self):
        text = " ".join(
            _section(
                Path("AGENTS.md").read_text(encoding="utf-8"), "## Readiness"
            )
            .lower()
            .split()
        )
        required = (
            "running python setup_database.py without --jpdb-source downloads",
            "prefer offline setup with already-downloaded local sources",
            "--jpdb-source",
            "--with-bccwj",
            "--bccwj-source",
            "pinned sha-256",
            "explicit user authorization before any download or database mutation",
        )
        for fragment in required:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)

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
