import sqlite3

from japanese_frequency.database import _database_error
from japanese_frequency.errors import JapaneseFrequencyError
from japanese_frequency.lookup import lookup_frequency
from japanese_frequency.user_words import (
    get_word_profile,
    mark_known,
    record_encounter,
    set_in_anki,
)


def _error_object(error):
    safe_messages = {
        "database_busy": "database is temporarily unavailable",
        "database_error": "database operation failed",
    }
    return {"type": error.code, "message": safe_messages.get(error.code, str(error))}


def _tool_call(function, *args, **kwargs):
    try:
        return {"ok": True, "result": function(*args, **kwargs)}
    except JapaneseFrequencyError as error:
        return {"ok": False, "error": _error_object(error)}
    except sqlite3.Error as error:
        return {"ok": False, "error": _error_object(_database_error(error))}


def lookup_japanese_frequency(word, reading=None, *, db_path=None):
    return _tool_call(lookup_frequency, word, reading, db_path=db_path)


def get_japanese_word_profile(word, reading=None, *, db_path=None):
    return _tool_call(get_word_profile, word, reading, db_path=db_path)


def record_japanese_encounter(word, reading=None, *, db_path=None):
    return _tool_call(record_encounter, word, reading, db_path=db_path)


def mark_japanese_word_known(word, reading=None, known=True, *, db_path=None):
    return _tool_call(mark_known, word, reading, known, db_path=db_path)


def set_japanese_word_anki_status(
    word, reading=None, in_anki=True, *, db_path=None
):
    return _tool_call(set_in_anki, word, reading, in_anki, db_path=db_path)
