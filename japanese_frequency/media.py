import csv
import sqlite3
from contextlib import closing
from pathlib import Path

from japanese_frequency.database import (
    _database_error,
    get_connection,
    initialize_database,
)
from japanese_frequency.errors import (
    InvalidInputError,
    MediaNotFoundError,
    SourceFormatError,
    SourceNotFoundError,
)
from japanese_frequency.normalization import normalize_reading, normalize_word
from japanese_frequency.source_files import snapshot_source, validated_source_path
from japanese_frequency.timestamps import format_utc_timestamp


_EXACT_STAGE = "stage_media_words"
_SPELLING_STAGE = "stage_media_spellings"
_RANK_STAGE = "stage_media_ranks"
_REQUIRED_HEADERS = {"Word", "ReadingKana", "Occurences"}
_OPTIONAL_FIELDS = (
    ("Definitions", "definitions"),
    ("ExampleSentence", "example_sentence"),
    ("JmDictWordId", "dictionary_id"),
)


def import_media_vocabulary(
    path, source_key, display_name=None, *, db_path=None, now=None
) -> dict:
    requested_path = validated_source_path(path)
    source_key = _required_text(source_key, "source_key")
    if display_name is None:
        display_name = requested_path.stem
    else:
        display_name = _required_text(display_name, "display_name")
    selected_path, source_format, notes = _select_source(requested_path)

    initialize_database(db_path)
    connection = get_connection(db_path)
    try:
        with snapshot_source(selected_path) as (snapshot, digest):
            _create_staging_tables(connection)
            if source_format == "csv":
                source_row_count = _stage_csv(connection, snapshot)
                _rank_csv_entries(connection)
            else:
                source_row_count = _stage_txt(connection, snapshot)
            exact_count, spelling_count = _stage_counts(connection)
            if exact_count + spelling_count == 0:
                raise SourceFormatError("media source must contain at least one entry")
            metadata = {
                "source_key": source_key,
                "display_name": display_name,
                "requested_filename": requested_path.name,
                "selected_filename": selected_path.name,
                "format": source_format,
                "imported_at": format_utc_timestamp(now),
                "source_row_count": source_row_count,
                "entry_count": exact_count + spelling_count,
                "sha256": digest,
                "notes": notes,
            }
        media_id = _publish_media(connection, metadata)
        return {
            "id": media_id,
            **metadata,
            "exact_entry_count": exact_count,
            "spelling_entry_count": spelling_count,
        }
    except sqlite3.Error as error:
        raise _database_error(error) from error
    finally:
        try:
            connection.close()
        except BaseException:
            pass


def _required_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError(f"{name} must be a non-empty string")
    return value.strip()


def _select_source(requested_path):
    suffix = requested_path.suffix.lower()
    if suffix not in {".csv", ".txt"}:
        if not requested_path.exists():
            raise SourceNotFoundError(f"source file not found: {requested_path}")
        raise SourceFormatError("media source must be a CSV or TXT file")
    selected_path = requested_path
    notes = "Imported requested source directly."
    if suffix == ".txt":
        csv_path = requested_path.with_suffix(".csv")
        if csv_path.is_file():
            selected_path = csv_path
            suffix = ".csv"
            notes = "Same-stem CSV superseded requested TXT source."
        else:
            notes = "TXT imported as unordered spelling membership."
    if not selected_path.exists():
        raise SourceNotFoundError(f"source file not found: {selected_path}")
    if not selected_path.is_file():
        raise SourceFormatError(f"media source is not a readable file: {selected_path}")
    return selected_path, suffix[1:], notes


def _create_staging_tables(connection):
    connection.execute(
        f"""
        CREATE TEMP TABLE {_EXACT_STAGE} (
            word TEXT NOT NULL,
            reading TEXT NOT NULL,
            occurrences INTEGER NOT NULL CHECK (occurrences > 0),
            media_rank INTEGER,
            definitions TEXT,
            definitions_ordinal INTEGER,
            example_sentence TEXT,
            example_sentence_ordinal INTEGER,
            dictionary_id TEXT,
            dictionary_id_ordinal INTEGER,
            PRIMARY KEY (word, reading)
        )
        """
    )
    connection.execute(
        f"""
        CREATE TEMP TABLE {_SPELLING_STAGE} (
            word TEXT PRIMARY KEY,
            occurrences INTEGER CHECK (occurrences IS NULL OR occurrences > 0),
            media_rank INTEGER,
            definitions TEXT,
            definitions_ordinal INTEGER,
            example_sentence TEXT,
            example_sentence_ordinal INTEGER,
            dictionary_id TEXT,
            dictionary_id_ordinal INTEGER
        )
        """
    )


def _stage_txt(connection, snapshot):
    source_rows = 0
    try:
        with snapshot.open("r", encoding="utf-8-sig") as source_file:
            for line_number, line in enumerate(source_file, start=1):
                value = line.rstrip("\r\n")
                if not value.strip():
                    continue
                try:
                    word = normalize_word(value)
                except InvalidInputError as error:
                    raise SourceFormatError(
                        f"media TXT line {line_number}: Word must be nonempty"
                    ) from error
                connection.execute(
                    f"INSERT OR IGNORE INTO {_SPELLING_STAGE}(word) VALUES (?)",
                    (word,),
                )
                source_rows += 1
    except (OSError, UnicodeError) as error:
        raise SourceFormatError(f"media TXT could not be parsed: {error}") from error
    return source_rows


def _stage_csv(connection, snapshot):
    source_rows = 0
    try:
        with snapshot.open("r", encoding="utf-8-sig", newline="") as source_file:
            reader = csv.DictReader(source_file, strict=True)
            headers = set(reader.fieldnames or ())
            missing = sorted(_REQUIRED_HEADERS - headers)
            if missing:
                raise SourceFormatError(
                    f"media CSV missing required columns: {', '.join(missing)}"
                )
            for row_number, record in enumerate(reader, start=2):
                source_rows += 1
                _stage_csv_record(connection, record, row_number)
    except SourceFormatError:
        raise
    except (OSError, UnicodeError, csv.Error) as error:
        raise SourceFormatError(f"media CSV could not be parsed: {error}") from error
    return source_rows


def _stage_csv_record(connection, record, row_number):
    try:
        word = normalize_word(record["Word"])
    except InvalidInputError as error:
        raise SourceFormatError(f"media CSV row {row_number}: Word must be nonempty") from error
    occurrence_text = (record["Occurences"] or "").strip()
    try:
        occurrences = int(occurrence_text)
        if occurrences <= 0 or str(occurrences) != occurrence_text:
            raise ValueError
    except ValueError as error:
        raise SourceFormatError(
            f"media CSV row {row_number}: Occurences must be a positive integer"
        ) from error

    reading_text = record["ReadingKana"] or ""
    reading = normalize_reading(reading_text)
    if reading and not _is_kana_reading(reading):
        raise SourceFormatError(
            f"media CSV row {row_number}: ReadingKana must contain kana"
        )
    optional = []
    for header, _ in _OPTIONAL_FIELDS:
        value = (record.get(header) or "").strip() or None
        optional.extend((value, row_number if value is not None else None))

    if reading:
        _upsert_csv_row(
            connection,
            _EXACT_STAGE,
            (word, reading, occurrences, *optional),
            "word, reading",
        )
    else:
        _upsert_csv_row(
            connection,
            _SPELLING_STAGE,
            (word, occurrences, *optional),
            "word",
        )


def _is_kana_reading(reading):
    return all(
        "ぁ" <= character <= "ゖ"
        or character in {"ゝ", "ゞ", "ー", "・"}
        for character in reading
    )


def _upsert_csv_row(connection, table, values, conflict_columns):
    identity_columns = conflict_columns.split(", ")
    columns = identity_columns + [
        "occurrences",
        "definitions",
        "definitions_ordinal",
        "example_sentence",
        "example_sentence_ordinal",
        "dictionary_id",
        "dictionary_id_ordinal",
    ]
    updates = ["occurrences = occurrences + excluded.occurrences"]
    for field in ("definitions", "example_sentence", "dictionary_id"):
        updates.extend(
            (
                f"{field} = CASE WHEN {field}_ordinal IS NULL "
                f"OR (excluded.{field}_ordinal IS NOT NULL "
                f"AND excluded.{field}_ordinal < {field}_ordinal) "
                f"THEN excluded.{field} ELSE {field} END",
                f"{field}_ordinal = CASE WHEN {field}_ordinal IS NULL "
                f"OR (excluded.{field}_ordinal IS NOT NULL "
                f"AND excluded.{field}_ordinal < {field}_ordinal) "
                f"THEN excluded.{field}_ordinal ELSE {field}_ordinal END",
            )
        )
    placeholders = ", ".join("?" for _ in columns)
    connection.execute(
        f"INSERT INTO {table}({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT({conflict_columns}) DO UPDATE SET {', '.join(updates)}",
        values,
    )


def _rank_csv_entries(connection):
    connection.execute(
        f"""
        CREATE TEMP TABLE {_RANK_STAGE} AS
        SELECT identity_type, word, reading_sort,
               ROW_NUMBER() OVER (
                   ORDER BY occurrences DESC, word ASC, reading_sort ASC
               ) AS media_rank
        FROM (
            SELECT 'exact' AS identity_type, word, reading AS reading_sort, occurrences
            FROM {_EXACT_STAGE}
            UNION ALL
            SELECT 'spelling', word, '' AS reading_sort, occurrences
            FROM {_SPELLING_STAGE}
        )
        """
    )
    connection.execute(
        f"""
        UPDATE {_EXACT_STAGE}
        SET media_rank = (
            SELECT media_rank FROM {_RANK_STAGE}
            WHERE identity_type='exact'
              AND {_RANK_STAGE}.word={_EXACT_STAGE}.word
              AND reading_sort={_EXACT_STAGE}.reading
        )
        """
    )
    connection.execute(
        f"""
        UPDATE {_SPELLING_STAGE}
        SET media_rank = (
            SELECT media_rank FROM {_RANK_STAGE}
            WHERE identity_type='spelling'
              AND {_RANK_STAGE}.word={_SPELLING_STAGE}.word
        )
        """
    )


def _stage_counts(connection):
    exact_count = connection.execute(
        f"SELECT COUNT(*) FROM {_EXACT_STAGE}"
    ).fetchone()[0]
    spelling_count = connection.execute(
        f"SELECT COUNT(*) FROM {_SPELLING_STAGE}"
    ).fetchone()[0]
    return exact_count, spelling_count


def _publish_media(connection, metadata):
    connection.commit()
    try:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT id FROM media_sources WHERE source_key=?",
            (metadata["source_key"],),
        ).fetchone()
        parent_columns = (
            "source_key",
            "display_name",
            "requested_filename",
            "selected_filename",
            "format",
            "imported_at",
            "source_row_count",
            "entry_count",
            "sha256",
            "notes",
        )
        parent_values = tuple(metadata[column] for column in parent_columns)
        if existing is None:
            cursor = connection.execute(
                f"INSERT INTO media_sources({', '.join(parent_columns)}) "
                f"VALUES ({', '.join('?' for _ in parent_columns)})",
                parent_values,
            )
            media_id = cursor.lastrowid
        else:
            media_id = existing["id"]
            assignments = ", ".join(f"{column}=?" for column in parent_columns[1:])
            connection.execute(
                f"UPDATE media_sources SET {assignments} WHERE id=?",
                (*parent_values[1:], media_id),
            )
        connection.execute("DELETE FROM media_words WHERE media_id=?", (media_id,))
        connection.execute("DELETE FROM media_spellings WHERE media_id=?", (media_id,))
        connection.execute(
            f"""
            INSERT INTO media_words(
                media_id, word, reading, occurrences, media_rank,
                definitions, example_sentence, dictionary_id
            )
            SELECT ?, word, reading, occurrences, media_rank,
                   definitions, example_sentence, dictionary_id
            FROM {_EXACT_STAGE}
            """,
            (media_id,),
        )
        connection.execute(
            f"""
            INSERT INTO media_spellings(
                media_id, word, occurrences, media_rank,
                definitions, example_sentence, dictionary_id
            )
            SELECT ?, word, occurrences, media_rank,
                   definitions, example_sentence, dictionary_id
            FROM {_SPELLING_STAGE}
            """,
            (media_id,),
        )
        connection.commit()
        return media_id
    except BaseException:
        try:
            connection.rollback()
        except BaseException:
            pass
        raise


def get_media_source(source_key, *, db_path=None, connection=None) -> dict:
    source_key = _required_text(source_key, "source_key")
    if connection is not None:
        return _read_media_source(connection, source_key)
    initialize_database(db_path)
    try:
        with closing(get_connection(db_path)) as owned_connection:
            return _read_media_source(owned_connection, source_key)
    except sqlite3.Error as error:
        raise _database_error(error) from error


def _read_media_source(connection, source_key):
    try:
        record = connection.execute(
            "SELECT * FROM media_sources WHERE source_key=?", (source_key,)
        ).fetchone()
        if record is None:
            raise MediaNotFoundError(f"media source not found: {source_key}")
        result = dict(record)
        result["exact_entry_count"] = connection.execute(
            "SELECT COUNT(*) FROM media_words WHERE media_id=?", (record["id"],)
        ).fetchone()[0]
        result["spelling_entry_count"] = connection.execute(
            "SELECT COUNT(*) FROM media_spellings WHERE media_id=?", (record["id"],)
        ).fetchone()[0]
        return result
    except sqlite3.Error as error:
        raise _database_error(error) from error


def iter_media_candidates(connection, media_id) -> list[dict]:
    try:
        records = connection.execute(
            """
            SELECT * FROM (
                SELECT word, reading, 'exact' AS identity_type, occurrences, media_rank,
                       definitions, example_sentence, dictionary_id
                FROM media_words WHERE media_id=?
                UNION ALL
                SELECT word, NULL, 'spelling', occurrences, media_rank,
                       definitions, example_sentence, dictionary_id
                FROM media_spellings WHERE media_id=?
            )
            ORDER BY media_rank IS NULL, media_rank, word, reading
            """,
            (media_id, media_id),
        ).fetchall()
        return [dict(record) for record in records]
    except sqlite3.Error as error:
        raise _database_error(error) from error
