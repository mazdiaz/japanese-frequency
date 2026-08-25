import sqlite3

from japanese_frequency.database import _database_error
from japanese_frequency.errors import JapaneseFrequencyError
from japanese_frequency.knowledge import import_migaku_known_words
from japanese_frequency.lookup import lookup_frequency
from japanese_frequency.media import import_media_vocabulary
from japanese_frequency.mining import analyze_media, recommend_media_word
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
    result = {"type": error.code, "message": safe_messages.get(error.code, str(error))}
    if error.code == "ambiguous_reading":
        result["matches"] = sorted(
            match for match in getattr(error, "matches", []) if isinstance(match, str)
        )
    return result


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


def import_migaku_known_vocabulary(path, *, db_path=None):
    return _tool_call(import_migaku_known_words, path, db_path=db_path)


def import_japanese_media_vocabulary(
    path, source_key, display_name=None, *, db_path=None
):
    return _tool_call(
        import_media_vocabulary,
        path,
        source_key,
        display_name,
        db_path=db_path,
    )


def analyze_japanese_media(source_key, *, limit=None, db_path=None):
    return _tool_call(analyze_media, source_key, limit=limit, db_path=db_path)


def recommend_japanese_media_word(
    source_key,
    word,
    reading=None,
    *,
    failed_recall=False,
    successful_inference=False,
    transparent_composition=False,
    personally_useful=False,
    db_path=None,
):
    return _tool_call(
        recommend_media_word,
        source_key,
        word,
        reading,
        failed_recall=failed_recall,
        successful_inference=successful_inference,
        transparent_composition=transparent_composition,
        personally_useful=personally_useful,
        db_path=db_path,
    )
