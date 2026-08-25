import sqlite3
from contextlib import closing
from datetime import datetime, timezone

from japanese_frequency.database import _database_error, get_connection
from japanese_frequency.errors import AmbiguousReadingError, InvalidInputError
from japanese_frequency.lookup import _lookup_frequency
from japanese_frequency.normalization import normalize_reading, normalize_word


_USER_SELECT = """
SELECT known, in_anki, encounter_count, first_seen, last_seen, notes
FROM user_words
WHERE word = ? AND reading = ?
"""


def _user_from_row(row) -> dict:
    if row is None:
        return {}
    return {
        "known": bool(row["known"]),
        "in_anki": bool(row["in_anki"]),
        "encounter_count": row["encounter_count"],
        "first_seen": row["first_seen"],
        "last_seen": row["last_seen"],
        "notes": row["notes"],
    }


def _resolve_mutation_reading(connection, word, reading) -> str:
    if reading is not None:
        return normalize_reading(reading)
    rows = connection.execute(
        "SELECT DISTINCT reading FROM frequency WHERE word = ? ORDER BY reading",
        (word,),
    ).fetchall()
    if len(rows) == 1:
        return rows[0]["reading"]
    if len(rows) > 1:
        readings = ", ".join(row["reading"] for row in rows)
        raise AmbiguousReadingError(f"multiple corpus readings: {readings}")
    return ""


def _format_timestamp(now) -> str:
    value = now() if now is not None else datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _mutate(word, reading, sql, parameters, *, db_path) -> dict:
    normalized_word = normalize_word(word)
    try:
        with closing(get_connection(db_path)) as connection:
            with connection:
                normalized_reading = _resolve_mutation_reading(
                    connection, normalized_word, reading
                )
                connection.execute(
                    sql, (normalized_word, normalized_reading, *parameters)
                )
                user_row = connection.execute(
                    _USER_SELECT, (normalized_word, normalized_reading)
                ).fetchone()
                frequency = _lookup_frequency(
                    connection, normalized_word, normalized_reading, True
                )["frequency"]
                result = {
                    "found": True,
                    "word": normalized_word,
                    "reading": normalized_reading,
                    "frequency": frequency,
                    "user": _user_from_row(user_row),
                }
    except sqlite3.Error as error:
        raise _database_error(error) from error
    return result


def _require_bool(value, name) -> None:
    if type(value) is not bool:
        raise InvalidInputError(f"{name} must be a boolean")


def record_encounter(word, reading=None, *, db_path=None, now=None) -> dict:
    timestamp = _format_timestamp(now)
    return _mutate(
        word,
        reading,
        """
        INSERT INTO user_words
            (word, reading, encounter_count, first_seen, last_seen)
        VALUES (?, ?, 1, ?, ?)
        ON CONFLICT(word, reading) DO UPDATE SET
            encounter_count = user_words.encounter_count + 1,
            first_seen = COALESCE(user_words.first_seen, excluded.first_seen),
            last_seen = excluded.last_seen
        """,
        (timestamp, timestamp),
        db_path=db_path,
    )


def mark_known(word, reading=None, known=True, *, db_path=None) -> dict:
    _require_bool(known, "known")
    return _mutate(
        word,
        reading,
        """
        INSERT INTO user_words (word, reading, known)
        VALUES (?, ?, ?)
        ON CONFLICT(word, reading) DO UPDATE SET known = excluded.known
        """,
        (int(known),),
        db_path=db_path,
    )


def set_in_anki(word, reading=None, in_anki=True, *, db_path=None) -> dict:
    _require_bool(in_anki, "in_anki")
    return _mutate(
        word,
        reading,
        """
        INSERT INTO user_words (word, reading, in_anki)
        VALUES (?, ?, ?)
        ON CONFLICT(word, reading) DO UPDATE SET in_anki = excluded.in_anki
        """,
        (int(in_anki),),
        db_path=db_path,
    )


def _read_user_rows(connection, word) -> dict:
    rows = connection.execute(
        "SELECT reading, known, in_anki, encounter_count, first_seen, "
        "last_seen, notes FROM user_words WHERE word = ? ORDER BY reading",
        (word,),
    ).fetchall()
    return {row["reading"]: _user_from_row(row) for row in rows}


def get_word_profile(word, reading=None, *, db_path=None) -> dict:
    normalized_word = normalize_word(word)
    normalized_reading = normalize_reading(reading)
    precise = reading is not None
    try:
        with closing(get_connection(db_path)) as connection:
            with connection:
                connection.execute("BEGIN")
                frequency_result = _lookup_frequency(
                    connection, normalized_word, normalized_reading, precise
                )
                users = _read_user_rows(connection, normalized_word)
                result = _format_profile(
                    normalized_word,
                    normalized_reading,
                    precise,
                    frequency_result,
                    users,
                )
    except sqlite3.Error as error:
        raise _database_error(error) from error
    return result


def _format_profile(word, reading, precise, frequency_result, users) -> dict:
    if precise:
        user = users.get(reading, {})
        return {
            "found": frequency_result["found"] or bool(user),
            "word": word,
            "reading": reading,
            "frequency": frequency_result["frequency"],
            "user": user,
        }

    matches = []
    corpus_readings = set()
    for frequency_match in frequency_result["matches"]:
        match_reading = frequency_match["reading"]
        corpus_readings.add(match_reading)
        matches.append(
            {
                "reading": match_reading,
                "frequency": frequency_match["frequency"],
                "user": users.get(match_reading, {}),
            }
        )
    for user_reading in sorted(users.keys() - corpus_readings):
        matches.append(
            {
                "reading": user_reading,
                "frequency": {},
                "user": users[user_reading],
            }
        )
    return {"found": bool(matches), "word": word, "matches": matches}
