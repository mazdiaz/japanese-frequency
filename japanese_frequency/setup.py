import hashlib
import os
import shutil
import sqlite3
import tempfile
import urllib.request
from contextlib import closing
from pathlib import Path

import config
from japanese_frequency.database import (
    _database_error,
    get_connection,
    initialize_database,
    integrity_check,
)
from japanese_frequency.errors import DatabaseError, DownloadError, SourceFormatError
from japanese_frequency.importers import (
    _BCCWJ_STAGE,
    _JPDB_STAGE,
    _publish_staged_sources,
    _stage_bccwj,
    _stage_jpdb,
)


JPDB_VERSION = "2.2"
JPDB_FILENAME = "jpdb_v2.2_freq_list_2024-10-13.csv"
JPDB_URL = (
    "https://raw.githubusercontent.com/Kuuuube/yomitan-dictionaries/"
    "b1114cc014f4343d04c387c7dcd3b1171dd31782/data/"
    "jpdb_v2.2_freq_list_2024-10-13.csv"
)
JPDB_SHA256 = "5cf103c1538a2189cda2fe0d462ebfe08eb09f211a48b727f533d387b371c5eb"

BCCWJ_VERSION = "1.0"
BCCWJ_FILENAME = "BCCWJ_frequencylist_luw_ver1_0.zip"
BCCWJ_URL = (
    "https://repository.ninjal.ac.jp/record/3228/files/"
    "BCCWJ_frequencylist_luw_ver1_0.zip"
)
BCCWJ_SHA256 = "0a23e56283187ed4f65d70b89919e05652470474855e1d35566d4039ecb7973a"


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_source(url, destination, *, expected_sha256=None, opener=None) -> Path:
    destination = Path(destination)
    part = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with (opener or urllib.request.urlopen)(url) as response:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f"{destination.name}.",
                suffix=".part",
                delete=False,
            ) as output:
                part = Path(output.name)
                shutil.copyfileobj(response, output)
                output.flush()
        actual_sha256 = sha256_file(part)
        if expected_sha256 and actual_sha256.lower() != expected_sha256.lower():
            raise DownloadError("download checksum mismatch")
        os.replace(part, destination)
        return destination
    except Exception as error:
        if part is not None:
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
        if isinstance(error, DownloadError):
            raise
        raise DownloadError("source download failed") from error


def _verify_pinned_source(path, expected_sha256, source) -> Path:
    path = Path(path)
    try:
        actual_sha256 = sha256_file(path)
    except OSError as error:
        raise SourceFormatError(f"{source} source could not be read: {error}") from error
    if actual_sha256.lower() != expected_sha256.lower():
        raise SourceFormatError(f"{source} source checksum mismatch")
    return path


def setup_database(
    *,
    db_path=None,
    jpdb_source=None,
    with_bccwj=False,
    bccwj_source=None,
    now=None,
    jpdb_sha256=None,
    bccwj_sha256=None,
) -> dict:
    try:
        database_path = Path(db_path or config.DEFAULT_DATABASE_PATH).expanduser().resolve()
    except OSError as error:
        raise DatabaseError(str(error)) from error
    sources_path = database_path.parent / "sources"
    expected_jpdb_sha256 = jpdb_sha256 or JPDB_SHA256
    expected_bccwj_sha256 = bccwj_sha256 or BCCWJ_SHA256

    explicit_jpdb = jpdb_source is not None
    if jpdb_source is None:
        jpdb_source = download_source(
            JPDB_URL,
            sources_path / JPDB_FILENAME,
            expected_sha256=expected_jpdb_sha256,
        )
    if explicit_jpdb:
        jpdb_source = _verify_pinned_source(
            jpdb_source, expected_jpdb_sha256, "jpdb"
        )

    explicit_bccwj = bccwj_source is not None
    if with_bccwj and bccwj_source is None:
        bccwj_source = download_source(
            BCCWJ_URL,
            sources_path / BCCWJ_FILENAME,
            expected_sha256=expected_bccwj_sha256,
        )
    if with_bccwj and explicit_bccwj:
        bccwj_source = _verify_pinned_source(
            bccwj_source, expected_bccwj_sha256, "bccwj"
        )

    initialize_database(database_path)
    try:
        with closing(get_connection(database_path)) as connection:
            jpdb_metadata = _stage_jpdb(
                connection,
                Path(jpdb_source),
                version=JPDB_VERSION,
                now=now,
                expected_sha256=expected_jpdb_sha256,
            )
            staged_sources = [("jpdb", _JPDB_STAGE, jpdb_metadata)]
            if with_bccwj:
                bccwj_metadata = _stage_bccwj(
                    connection,
                    Path(bccwj_source),
                    version=BCCWJ_VERSION,
                    now=now,
                    expected_sha256=expected_bccwj_sha256,
                )
                staged_sources.append(
                    ("bccwj_luw", _BCCWJ_STAGE, bccwj_metadata)
                )
            _publish_staged_sources(connection, staged_sources)
            if with_bccwj:
                bccwj_entries = bccwj_metadata["entry_count"]
            else:
                bccwj_entries = connection.execute(
                    "SELECT COUNT(*) FROM frequency WHERE source = 'bccwj_luw'"
                ).fetchone()[0]
    except sqlite3.Error as error:
        raise _database_error(error) from error

    try:
        database_size = database_path.stat().st_size
    except OSError as error:
        raise DatabaseError(str(error)) from error

    return {
        "jpdb_entries": jpdb_metadata["entry_count"],
        "bccwj_entries": bccwj_entries,
        "database_path": str(database_path),
        "database_size_bytes": database_size,
        "integrity_check": integrity_check(database_path),
    }
