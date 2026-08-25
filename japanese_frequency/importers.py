import csv
import hashlib
import io
import math
import sqlite3
import tempfile
import zipfile
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from japanese_frequency.database import (
    _database_error,
    get_connection,
    initialize_database,
)
from japanese_frequency.errors import InvalidInputError, SourceFormatError
from japanese_frequency.normalization import normalize_reading, normalize_word


JPDB_HEADER = ("term", "reading", "frequency", "kana_frequency")
BCCWJ_HEADER = (
    "rank", "lForm", "lemma", "pos", "subLemma", "wType", "frequency", "pmw",
    "PB_rank", "PB_frequency", "PB_pmw", "PM_rank", "PM_frequency", "PM_pmw",
    "PN_rank", "PN_frequency", "PN_pmw", "LB_rank", "LB_frequency", "LB_pmw",
    "OW_rank", "OW_frequency", "OW_pmw", "OT_rank", "OT_frequency", "OT_pmw",
    "OP_rank", "OP_frequency", "OP_pmw", "OB_rank", "OB_frequency", "OB_pmw",
    "OC_rank", "OC_frequency", "OC_pmw", "OY_rank", "OY_frequency", "OY_pmw",
    "OV_rank", "OV_frequency", "OV_pmw", "OL_rank", "OL_frequency", "OL_pmw",
    "OM_rank", "OM_frequency", "OM_pmw", "PB_fixed_rank", "PB_fixed_frequency",
    "PB_fixed_pmw", "PB_variable_rank", "PB_variable_frequency", "PB_variable_pmw",
    "PM_fixed_rank", "PM_fixed_frequency", "PM_fixed_pmw", "PM_variable_rank",
    "PM_variable_frequency", "PM_variable_pmw", "PN_fixed_rank", "PN_fixed_frequency",
    "PN_fixed_pmw", "PN_variable_rank", "PN_variable_frequency", "PN_variable_pmw",
    "LB_fixed_rank", "LB_fixed_frequency", "LB_fixed_pmw", "LB_variable_rank",
    "LB_variable_frequency", "LB_variable_pmw", "OW_fixed_rank", "OW_fixed_frequency",
    "OW_fixed_pmw", "OW_variable_rank", "OW_variable_frequency", "OW_variable_pmw",
    "core_rank", "core_frequency", "core_pmw",
)
BCCWJ_ZIP_MEMBER = "BCCWJ_frequencylist_luw_ver1_0.tsv"

_STAGE_FREQUENCY_SQL = """
CREATE TEMP TABLE {table} (
    word TEXT NOT NULL,
    reading TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    rank INTEGER,
    frequency REAL,
    frequency_per_million REAL,
    kana_rank INTEGER,
    PRIMARY KEY (word, reading, source)
)
"""

_JPDB_STAGE = "stage_jpdb_frequency"
_BCCWJ_STAGE = "stage_bccwj_frequency"
_BCCWJ_AGGREGATE_STAGE = "stage_bccwj_aggregate"


def validate_header(actual, expected, source) -> None:
    actual = tuple(actual or ())
    expected = tuple(expected)
    if actual == expected:
        return

    missing = [name for name in expected if name not in actual]
    unexpected = [name for name in actual if name not in expected]
    duplicates = []
    seen = set()
    for name in actual:
        if name in seen and name not in duplicates:
            duplicates.append(name)
        seen.add(name)
    expected_common = [name for name in expected if name in actual]
    actual_common = []
    for name in actual:
        if name in expected and name not in actual_common:
            actual_common.append(name)
    reordered = [
        name
        for index, name in enumerate(expected_common)
        if actual_common[index] != name
    ]
    reordered.extend(
        name
        for index, name in enumerate(actual_common)
        if expected_common[index] != name and name not in reordered
    )
    raise SourceFormatError(
        f"{source} header mismatch: missing={missing}; "
        f"unexpected={unexpected}; reordered={reordered}; duplicates={duplicates}"
    )


@contextmanager
def _snapshot_source(path):
    digest = hashlib.sha256()
    try:
        temporary_directory = tempfile.TemporaryDirectory(
            prefix="japanese-frequency-"
        )
    except OSError as error:
        raise SourceFormatError(f"source snapshot could not be created: {error}") from error
    with temporary_directory as directory:
        snapshot = Path(directory) / f"source{path.suffix}"
        try:
            with path.open("rb") as source, snapshot.open("wb") as output:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
                    output.write(chunk)
        except OSError as error:
            raise SourceFormatError(f"source snapshot could not be read: {error}") from error
        yield snapshot, digest.hexdigest()


def _timestamp(now) -> str:
    value = now() if now is not None else datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _positive_integer(value, source, row_number, field) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise SourceFormatError(
            f"{source} row {row_number}: {field} must be a positive integer"
        ) from error
    if parsed <= 0 or str(parsed) != value.strip():
        raise SourceFormatError(
            f"{source} row {row_number}: {field} must be a positive integer"
        )
    return parsed


def _nonnegative_number(value, source, row_number, field) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise SourceFormatError(
            f"{source} row {row_number}: {field} must be a nonnegative number"
        ) from error
    if not math.isfinite(parsed) or parsed < 0:
        raise SourceFormatError(
            f"{source} row {row_number}: {field} must be a nonnegative number"
        )
    return parsed


def _identity(word, reading, source, row_number, *, allow_empty_reading=False):
    try:
        normalized_word = normalize_word(word)
        normalized_reading = normalize_reading(reading)
    except InvalidInputError as error:
        raise SourceFormatError(f"{source} row {row_number}: {error}") from error
    if not normalized_reading and not (allow_empty_reading and reading == ""):
        raise SourceFormatError(f"{source} row {row_number}: reading must be nonempty")
    return normalized_word, normalized_reading


def _upsert_metadata(connection, metadata):
    connection.execute(
        """
        INSERT INTO source_metadata(
            source, version, filename, imported_at, source_row_count,
            entry_count, sha256, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source) DO UPDATE SET
            version=excluded.version,
            filename=excluded.filename,
            imported_at=excluded.imported_at,
            source_row_count=excluded.source_row_count,
            entry_count=excluded.entry_count,
            sha256=excluded.sha256,
            notes=excluded.notes
        """,
        (
            metadata["source"],
            metadata["version"],
            metadata["filename"],
            metadata["imported_at"],
            metadata["source_row_count"],
            metadata["entry_count"],
            metadata["sha256"],
            metadata["notes"],
        ),
    )


def _create_frequency_stage(connection, table):
    connection.execute(_STAGE_FREQUENCY_SQL.format(table=table))


def _publish_staged_sources(connection, staged_sources):
    connection.commit()
    try:
        connection.execute("BEGIN IMMEDIATE")
        for source, stage_table, metadata in staged_sources:
            connection.execute("DELETE FROM frequency WHERE source = ?", (source,))
            connection.execute(
                f"""
                INSERT INTO frequency(
                    word, reading, source, rank, frequency,
                    frequency_per_million, kana_rank
                )
                SELECT word, reading, source, rank, frequency,
                       frequency_per_million, kana_rank
                FROM {stage_table}
                """
            )
            _upsert_metadata(connection, metadata)
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def import_jpdb(
    path, *, db_path=None, version="2.2", now=None, expected_sha256=None
) -> dict:
    path = Path(path)
    initialize_database(db_path)
    try:
        with closing(get_connection(db_path)) as connection:
            metadata = _stage_jpdb(
                connection,
                path,
                version=version,
                now=now,
                expected_sha256=expected_sha256,
            )
            _publish_staged_sources(
                connection, [("jpdb", _JPDB_STAGE, metadata)]
            )
            return metadata
    except OSError as error:
        raise SourceFormatError(f"jpdb source could not be read: {error}") from error
    except sqlite3.Error as error:
        raise _database_error(error) from error


def _stage_jpdb(connection, path, *, version, now, expected_sha256=None):
    try:
        with _snapshot_source(path) as (snapshot, digest):
            if expected_sha256 and digest.lower() != expected_sha256.lower():
                raise SourceFormatError("jpdb source checksum mismatch")
            return _stage_jpdb_snapshot(
                connection,
                snapshot,
                filename=path.name,
                digest=digest,
                version=version,
                now=now,
            )
    except OSError as error:
        raise SourceFormatError(f"jpdb source could not be read: {error}") from error


def _stage_jpdb_snapshot(connection, path, *, filename, digest, version, now):
    source_rows = 0
    _create_frequency_stage(connection, _JPDB_STAGE)
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as source_file:
            reader = csv.reader(source_file, delimiter="\t")
            validate_header(next(reader, ()), JPDB_HEADER, "jpdb")
            for row_number, row in enumerate(reader, start=2):
                if len(row) != len(JPDB_HEADER):
                    raise SourceFormatError(
                        f"jpdb row {row_number}: expected {len(JPDB_HEADER)} "
                        f"columns, got {len(row)}"
                    )
                word, reading = _identity(row[0], row[1], "jpdb", row_number)
                rank = _positive_integer(row[2], "jpdb", row_number, "frequency")
                kana_rank = (
                    _positive_integer(row[3], "jpdb", row_number, "kana_frequency")
                    if row[3].strip()
                    else None
                )
                connection.execute(
                    f"""
                    INSERT INTO {_JPDB_STAGE}(
                        word, reading, source, rank, kana_rank
                    ) VALUES (?, ?, 'jpdb', ?, ?)
                    ON CONFLICT(word, reading, source) DO UPDATE SET
                        rank = min(rank, excluded.rank),
                        kana_rank = CASE
                            WHEN kana_rank IS NULL THEN excluded.kana_rank
                            WHEN excluded.kana_rank IS NULL THEN kana_rank
                            ELSE min(kana_rank, excluded.kana_rank)
                        END
                    """,
                    (word, reading, rank, kana_rank),
                )
                source_rows += 1
    except (UnicodeError, csv.Error) as error:
        raise SourceFormatError(f"jpdb could not be parsed: {error}") from error

    if source_rows == 0:
        raise SourceFormatError("jpdb must contain at least one data row")
    entry_count = connection.execute(
        f"SELECT COUNT(*) FROM {_JPDB_STAGE}"
    ).fetchone()[0]
    return {
        "source": "jpdb",
        "version": version,
        "filename": filename,
        "imported_at": _timestamp(now),
        "source_row_count": source_rows,
        "entry_count": entry_count,
        "sha256": digest,
        "notes": "Duplicate term/reading senses collapsed to minimum ranks.",
    }


@contextmanager
def _open_bccwj(path):
    if path.suffix.lower() != ".zip":
        with path.open("r", encoding="utf-8-sig", newline="") as source_file:
            yield source_file
        return

    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if (
                len(members) != 1
                or members[0].is_dir()
                or members[0].filename != BCCWJ_ZIP_MEMBER
            ):
                names = [member.filename for member in members]
                raise SourceFormatError(
                    f"bccwj ZIP must contain only {BCCWJ_ZIP_MEMBER!r}; got {names}"
                )
            with archive.open(members[0], "r") as binary_file:
                with io.TextIOWrapper(
                    binary_file, encoding="utf-8-sig", newline=""
                ) as source_file:
                    yield source_file
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as error:
        raise SourceFormatError(f"bccwj ZIP could not be read: {error}") from error


def import_bccwj(
    path, *, db_path=None, version="1.0", now=None, expected_sha256=None
) -> dict:
    path = Path(path)
    initialize_database(db_path)
    try:
        with closing(get_connection(db_path)) as connection:
            metadata = _stage_bccwj(
                connection,
                path,
                version=version,
                now=now,
                expected_sha256=expected_sha256,
            )
            _publish_staged_sources(
                connection, [("bccwj_luw", _BCCWJ_STAGE, metadata)]
            )
            return metadata
    except OSError as error:
        raise SourceFormatError(f"bccwj source could not be read: {error}") from error
    except sqlite3.Error as error:
        raise _database_error(error) from error


def _stage_bccwj(connection, path, *, version, now, expected_sha256=None):
    try:
        with _snapshot_source(path) as (snapshot, digest):
            if expected_sha256 and digest.lower() != expected_sha256.lower():
                raise SourceFormatError("bccwj source checksum mismatch")
            return _stage_bccwj_snapshot(
                connection,
                snapshot,
                filename=path.name,
                digest=digest,
                version=version,
                now=now,
            )
    except OSError as error:
        raise SourceFormatError(f"bccwj source could not be read: {error}") from error


def _stage_bccwj_snapshot(connection, path, *, filename, digest, version, now):
    source_rows = 0
    connection.execute(
        f"""
                CREATE TEMP TABLE {_BCCWJ_AGGREGATE_STAGE} (
                    word TEXT NOT NULL,
                    reading TEXT NOT NULL,
                    frequency REAL NOT NULL,
                    frequency_per_million REAL NOT NULL,
                    PRIMARY KEY (word, reading)
                )
                """
    )
    _create_frequency_stage(connection, _BCCWJ_STAGE)
    try:
        with _open_bccwj(path) as source_file:
            reader = csv.reader(source_file, delimiter="\t")
            validate_header(next(reader, ()), BCCWJ_HEADER, "bccwj")
            for row_number, row in enumerate(reader, start=2):
                if len(row) != len(BCCWJ_HEADER):
                    raise SourceFormatError(
                        f"bccwj row {row_number}: expected {len(BCCWJ_HEADER)} "
                        f"columns, got {len(row)}"
                    )
                _positive_integer(row[0], "bccwj", row_number, "rank")
                word, reading = _identity(
                    row[2], row[1], "bccwj", row_number, allow_empty_reading=True
                )
                frequency = _positive_integer(
                    row[6], "bccwj", row_number, "frequency"
                )
                pmw = _nonnegative_number(row[7], "bccwj", row_number, "pmw")
                connection.execute(
                    f"""
                    INSERT INTO {_BCCWJ_AGGREGATE_STAGE}(
                        word, reading, frequency, frequency_per_million
                    ) VALUES (?, ?, ?, ?)
                    ON CONFLICT(word, reading) DO UPDATE SET
                        frequency = frequency + excluded.frequency,
                        frequency_per_million =
                            frequency_per_million + excluded.frequency_per_million
                    """,
                    (word, reading, frequency, pmw),
                )
                source_rows += 1
    except (UnicodeError, csv.Error) as error:
        raise SourceFormatError(f"bccwj could not be parsed: {error}") from error

    if source_rows == 0:
        raise SourceFormatError("bccwj must contain at least one data row")
    connection.execute(
        f"""
        INSERT INTO {_BCCWJ_STAGE}(
            word, reading, source, rank, frequency,
            frequency_per_million, kana_rank
        )
        SELECT word, reading, 'bccwj_luw',
               ROW_NUMBER() OVER (ORDER BY frequency DESC, word ASC, reading ASC),
               frequency, frequency_per_million, NULL
        FROM {_BCCWJ_AGGREGATE_STAGE}
        """
    )
    entry_count = connection.execute(
        f"SELECT COUNT(*) FROM {_BCCWJ_STAGE}"
    ).fetchone()[0]
    return {
        "source": "bccwj_luw",
        "version": version,
        "filename": filename,
        "imported_at": _timestamp(now),
        "source_row_count": source_rows,
        "entry_count": entry_count,
        "sha256": digest,
        "notes": (
            "Duplicate lemma/reading rows summed; rank is project-computed "
            "by frequency DESC, word ASC, reading ASC."
        ),
    }
