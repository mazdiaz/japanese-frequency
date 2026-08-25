import argparse
import json
import sqlite3
import sys

from japanese_frequency.database import _database_error
from japanese_frequency.errors import JapaneseFrequencyError
from japanese_frequency.lookup import lookup_frequency
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
    return {"type": error.code, "message": safe_messages.get(error.code, str(error))}


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
    return set_in_anki(
        arguments.word,
        arguments.reading,
        arguments.value,
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
