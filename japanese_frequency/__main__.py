import argparse
import json
import sqlite3
import sys

from japanese_frequency.database import _database_error
from japanese_frequency.errors import JapaneseFrequencyError
from japanese_frequency.knowledge import import_migaku_known_words
from japanese_frequency.lookup import lookup_frequency
from japanese_frequency.media import import_media_vocabulary
from japanese_frequency.mining import (
    analyze_media,
    export_media_analysis_csv,
    recommend_media_word,
)
from japanese_frequency.user_words import (
    get_word_profile,
    mark_known,
    record_encounter,
    set_in_anki,
)


def _configure_utf8():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")


def _write_json(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _error_object(error):
    safe_messages = {
        "database_busy": "database is temporarily unavailable",
        "database_error": "database operation failed",
    }
    result = {"type": error.code, "message": safe_messages.get(error.code, str(error))}
    if error.code == "ambiguous_reading":
        result["matches"] = sorted(
            match for match in getattr(error, "matches", []) if isinstance(match, str)
        )
    return result


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        _write_json({"error": {"type": "invalid_input", "message": message}})
        raise SystemExit(2)


def _boolean(value):
    if value == "true":
        return True
    if value == "false":
        return False
    raise argparse.ArgumentTypeError("value must be true or false")


def _add_word_arguments(parser, *, boolean=False):
    parser.add_argument("word")
    parser.add_argument("reading", nargs="?")
    if boolean:
        parser.add_argument("--value", type=_boolean, default=True)


def _parser():
    parser = JsonArgumentParser(prog="python -m japanese_frequency")
    parser.add_argument("--db", dest="db_path")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("lookup", "profile", "encounter"):
        _add_word_arguments(subparsers.add_parser(command))
    for command in ("known", "anki"):
        _add_word_arguments(subparsers.add_parser(command), boolean=True)

    import_known = subparsers.add_parser("import-known")
    import_known.add_argument("path")

    import_media = subparsers.add_parser("import-media")
    import_media.add_argument("path")
    import_media.add_argument("--source-key", required=True)
    import_media.add_argument("--name")

    analyze = subparsers.add_parser("analyze-media")
    analyze.add_argument("source_key")
    analyze.add_argument("--limit", type=int)
    analyze.add_argument("--output")

    recommend = subparsers.add_parser("recommend-media")
    recommend.add_argument("source_key")
    _add_word_arguments(recommend)
    for flag in (
        "failed-recall",
        "successful-inference",
        "transparent-composition",
        "personally-useful",
    ):
        recommend.add_argument(f"--{flag}", type=_boolean, default=False)
    return parser


def _run(arguments):
    if arguments.command == "lookup":
        return lookup_frequency(
            arguments.word, arguments.reading, db_path=arguments.db_path
        )
    if arguments.command == "profile":
        return get_word_profile(
            arguments.word, arguments.reading, db_path=arguments.db_path
        )
    if arguments.command == "encounter":
        return record_encounter(
            arguments.word, arguments.reading, db_path=arguments.db_path
        )
    if arguments.command == "known":
        return mark_known(
            arguments.word,
            arguments.reading,
            arguments.value,
            db_path=arguments.db_path,
        )
    if arguments.command == "anki":
        return set_in_anki(
            arguments.word,
            arguments.reading,
            arguments.value,
            db_path=arguments.db_path,
        )
    if arguments.command == "import-known":
        return import_migaku_known_words(arguments.path, db_path=arguments.db_path)
    if arguments.command == "import-media":
        return import_media_vocabulary(
            arguments.path,
            arguments.source_key,
            arguments.name,
            db_path=arguments.db_path,
        )
    if arguments.command == "analyze-media":
        analysis = analyze_media(
            arguments.source_key,
            limit=arguments.limit,
            db_path=arguments.db_path,
        )
        if arguments.output is None:
            return analysis
        report = export_media_analysis_csv(analysis, arguments.output)
        return {
            "source": analysis["source"],
            "summary": analysis["summary"],
            "report": report,
        }
    return recommend_media_word(
        arguments.source_key,
        arguments.word,
        arguments.reading,
        failed_recall=arguments.failed_recall,
        successful_inference=arguments.successful_inference,
        transparent_composition=arguments.transparent_composition,
        personally_useful=arguments.personally_useful,
        db_path=arguments.db_path,
    )


def main(argv=None):
    _configure_utf8()
    arguments = _parser().parse_args(argv)
    try:
        payload = _run(arguments)
    except JapaneseFrequencyError as error:
        _write_json({"error": _error_object(error)})
        return 1
    except sqlite3.Error as error:
        _write_json({"error": _error_object(_database_error(error))})
        return 1
    _write_json(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
