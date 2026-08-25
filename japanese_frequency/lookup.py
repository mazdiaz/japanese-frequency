import sqlite3
from contextlib import closing

import config
from japanese_frequency.database import _database_error, get_connection
from japanese_frequency.errors import InvalidInputError
from japanese_frequency.normalization import normalize_reading, normalize_word


ORDER_SQL = """
ORDER BY (jpdb_rank IS NULL), jpdb_rank,
         (bccwj_rank IS NULL), bccwj_rank,
         reading
"""

_GROUPED_SELECT = """
SELECT reading,
       MAX(CASE WHEN source = 'jpdb' THEN 1 ELSE 0 END) AS jpdb_present,
       MAX(CASE WHEN source = 'jpdb' THEN rank END) AS jpdb_rank,
       MAX(CASE WHEN source = 'jpdb' THEN kana_rank END) AS jpdb_kana_rank,
       MAX(CASE WHEN source = 'bccwj_luw' THEN 1 ELSE 0 END) AS bccwj_present,
       MAX(CASE WHEN source = 'bccwj_luw' THEN rank END) AS bccwj_rank,
       MAX(CASE WHEN source = 'bccwj_luw' THEN frequency END) AS bccwj_frequency,
       MAX(CASE WHEN source = 'bccwj_luw' THEN frequency_per_million END)
           AS bccwj_frequency_per_million
FROM frequency
"""


def classify_jpdb_rank(rank: int) -> dict:
    if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
        raise InvalidInputError("rank must be a positive integer")
    for maximum, category in config.JPDB_COMMONNESS_THRESHOLDS:
        if rank <= maximum:
            return {"rank": rank, "category": category}
    return {"rank": rank, "category": "very_rare"}


def _frequency_from_row(row) -> dict:
    frequency = {}
    if row["jpdb_present"]:
        jpdb = {}
        if row["jpdb_rank"] is not None:
            jpdb["rank"] = row["jpdb_rank"]
            jpdb["commonness"] = classify_jpdb_rank(row["jpdb_rank"])
        if row["jpdb_kana_rank"] is not None:
            jpdb["kana_rank"] = row["jpdb_kana_rank"]
        frequency["jpdb"] = jpdb
    if row["bccwj_present"]:
        bccwj = {}
        for key, column in (
            ("rank", "bccwj_rank"),
            ("frequency", "bccwj_frequency"),
            ("frequency_per_million", "bccwj_frequency_per_million"),
        ):
            if row[column] is not None:
                bccwj[key] = row[column]
        frequency["bccwj_luw"] = bccwj
    return frequency


def lookup_frequency(word: str, reading: str | None = None, *, db_path=None) -> dict:
    normalized_word = normalize_word(word)
    normalized_reading = normalize_reading(reading)
    precise = reading is not None
    where_sql = "WHERE word = ?"
    parameters = [normalized_word]
    if precise:
        where_sql += " AND reading = ?"
        parameters.append(normalized_reading)
    query = f"{_GROUPED_SELECT} {where_sql} GROUP BY reading {ORDER_SQL}"

    try:
        with closing(get_connection(db_path)) as connection:
            rows = connection.execute(query, parameters).fetchall()
    except sqlite3.Error as error:
        raise _database_error(error) from error

    if precise:
        if not rows:
            return {
                "found": False,
                "word": normalized_word,
                "reading": normalized_reading,
                "frequency": {},
            }
        return {
            "found": True,
            "word": normalized_word,
            "reading": normalized_reading,
            "frequency": _frequency_from_row(rows[0]),
        }

    matches = [
        {"reading": row["reading"], "frequency": _frequency_from_row(row)}
        for row in rows
    ]
    return {"found": bool(matches), "word": normalized_word, "matches": matches}
