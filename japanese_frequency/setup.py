import hashlib
import shutil
import urllib.request
from pathlib import Path

import config
from japanese_frequency.database import initialize_database, integrity_check
from japanese_frequency.errors import DownloadError
from japanese_frequency.importers import import_bccwj, import_jpdb


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
    part = Path(f"{destination}.part")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with (opener or urllib.request.urlopen)(url) as response, part.open(
            "wb"
        ) as output:
            shutil.copyfileobj(response, output)
        actual_sha256 = sha256_file(part)
        if expected_sha256 and actual_sha256.lower() != expected_sha256.lower():
            raise DownloadError("download checksum mismatch")
        part.replace(destination)
        return destination
    except Exception as error:
        part.unlink(missing_ok=True)
        if isinstance(error, DownloadError):
            raise
        raise DownloadError("source download failed") from error


def setup_database(
    *, db_path=None, jpdb_source=None, with_bccwj=False, bccwj_source=None, now=None
) -> dict:
    database_path = Path(db_path or config.DEFAULT_DATABASE_PATH).expanduser().resolve()
    sources_path = database_path.parent / "sources"
    sources_path.mkdir(parents=True, exist_ok=True)
    initialize_database(database_path)

    if jpdb_source is None:
        jpdb_source = download_source(
            JPDB_URL,
            sources_path / JPDB_FILENAME,
            expected_sha256=JPDB_SHA256,
        )
    jpdb_metadata = import_jpdb(
        jpdb_source, db_path=database_path, version=JPDB_VERSION, now=now
    )

    bccwj_entries = 0
    if with_bccwj:
        if bccwj_source is None:
            bccwj_source = download_source(
                BCCWJ_URL,
                sources_path / BCCWJ_FILENAME,
                expected_sha256=BCCWJ_SHA256,
            )
        bccwj_metadata = import_bccwj(
            bccwj_source,
            db_path=database_path,
            version=BCCWJ_VERSION,
            now=now,
        )
        bccwj_entries = bccwj_metadata["entry_count"]

    return {
        "jpdb_entries": jpdb_metadata["entry_count"],
        "bccwj_entries": bccwj_entries,
        "database_path": str(database_path),
        "database_size_bytes": database_path.stat().st_size,
        "integrity_check": integrity_check(database_path),
    }
