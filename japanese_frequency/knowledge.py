import sqlite3
from contextlib import closing
from pathlib import Path

from japanese_frequency.database import (
    _database_error,
    get_connection,
    initialize_database,
)
from japanese_frequency.errors import InvalidInputError, SourceFormatError
from japanese_frequency.normalization import normalize_word
from japanese_frequency.source_files import snapshot_source
from japanese_frequency.timestamps import format_utc_timestamp


_STAGE_TABLE = "stage_known_spellings"
_NOTES = "Duplicate spellings collapsed after NFC normalization."


def import_migaku_known_words(path, *, db_path=None, now=None) -> dict:
    path = Path(path)
    initialize_database(db_path)
    try:
        with closing(get_connection(db_path)) as connection:
            metadata = _stage_migaku(connection, path, now=now)
            _publish_migaku(connection, metadata)
            return metadata
    except sqlite3.Error as error:
        raise _database_error(error) from error


def _stage_migaku(connection, path, *, now):
    with snapshot_source(path) as (snapshot, digest):
        connection.execute(
            f"CREATE TEMP TABLE {_STAGE_TABLE} (word TEXT PRIMARY KEY)"
        )
        source_rows = 0
        try:
            with snapshot.open("r", encoding="utf-8-sig") as source_file:
                for line_number, line in enumerate(source_file, start=1):
                    record = line.rstrip("\r\n")
                    try:
                        word = normalize_word(record)
                    except InvalidInputError as error:
                        raise SourceFormatError(
                            f"migaku line {line_number}: spelling must be nonempty"
                        ) from error
                    connection.execute(
                        f"INSERT OR IGNORE INTO {_STAGE_TABLE}(word) VALUES (?)",
                        (word,),
                    )
                    source_rows += 1
        except UnicodeError as error:
            raise SourceFormatError(
                f"migaku source could not be parsed: {error}"
            ) from error
        if source_rows == 0:
            raise SourceFormatError("migaku must contain at least one spelling")
        entry_count = connection.execute(
            f"SELECT COUNT(*) FROM {_STAGE_TABLE}"
        ).fetchone()[0]
        return {
            "source": "migaku",
            "filename": path.name,
            "imported_at": format_utc_timestamp(now),
            "source_row_count": source_rows,
            "entry_count": entry_count,
            "sha256": digest,
            "notes": _NOTES,
        }


def _publish_migaku(connection, metadata):
    connection.commit()
    try:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM known_spellings WHERE source='migaku'")
        connection.execute(
            f"INSERT INTO known_spellings(word, source) "
            f"SELECT word, 'migaku' FROM {_STAGE_TABLE}"
        )
        connection.execute(
            """
            INSERT INTO personal_source_metadata(
                source, filename, imported_at, source_row_count,
                entry_count, sha256, notes
            ) VALUES ('migaku', ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source) DO UPDATE SET
                filename = excluded.filename,
                imported_at = excluded.imported_at,
                source_row_count = excluded.source_row_count,
                entry_count = excluded.entry_count,
                sha256 = excluded.sha256,
                notes = excluded.notes
            """,
            (
                metadata["filename"],
                metadata["imported_at"],
                metadata["source_row_count"],
                metadata["entry_count"],
                metadata["sha256"],
                metadata["notes"],
            ),
        )
        connection.commit()
    except BaseException:
        try:
            connection.rollback()
        except BaseException:
            pass
        raise


def get_known_spelling(word, *, connection=None, db_path=None) -> dict:
    normalized_word = normalize_word(word)
    if connection is not None:
        return _read_known_spelling(connection, normalized_word)
    initialize_database(db_path)
    try:
        with closing(get_connection(db_path)) as owned_connection:
            return _read_known_spelling(owned_connection, normalized_word)
    except sqlite3.Error as error:
        raise _database_error(error) from error


def _read_known_spelling(connection, word):
    try:
        rows = connection.execute(
            "SELECT source FROM known_spellings WHERE word = ? ORDER BY source",
            (word,),
        ).fetchall()
    except sqlite3.Error as error:
        raise _database_error(error) from error
    sources = [row["source"] for row in rows]
    return {"known": bool(sources), "word": word, "sources": sources}
