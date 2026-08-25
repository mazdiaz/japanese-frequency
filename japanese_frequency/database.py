import sqlite3
from contextlib import closing
from pathlib import Path

import config
from japanese_frequency.errors import DatabaseBusyError, DatabaseError


SCHEMA = """
CREATE TABLE IF NOT EXISTS frequency (
    word TEXT NOT NULL,
    reading TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    rank INTEGER,
    frequency REAL,
    frequency_per_million REAL,
    kana_rank INTEGER,
    PRIMARY KEY (word, reading, source)
);

CREATE TABLE IF NOT EXISTS user_words (
    word TEXT NOT NULL,
    reading TEXT NOT NULL DEFAULT '',
    known INTEGER NOT NULL DEFAULT 0 CHECK (known IN (0, 1)),
    in_anki INTEGER NOT NULL DEFAULT 0 CHECK (in_anki IN (0, 1)),
    encounter_count INTEGER NOT NULL DEFAULT 0 CHECK (encounter_count >= 0),
    first_seen TEXT,
    last_seen TEXT,
    notes TEXT,
    PRIMARY KEY (word, reading)
);

CREATE TABLE IF NOT EXISTS source_metadata (
    source TEXT PRIMARY KEY,
    version TEXT,
    filename TEXT,
    imported_at TEXT,
    source_row_count INTEGER,
    entry_count INTEGER,
    sha256 TEXT,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_frequency_word ON frequency(word);
CREATE INDEX IF NOT EXISTS idx_frequency_word_reading ON frequency(word, reading);
CREATE INDEX IF NOT EXISTS idx_frequency_source_rank ON frequency(source, rank);
"""


def _database_error(error) -> DatabaseError:
    message = str(error)
    if isinstance(error, sqlite3.OperationalError) and (
        "locked" in message.lower() or "busy" in message.lower()
    ):
        return DatabaseBusyError(message)
    return DatabaseError(message)


def get_connection(db_path=None) -> sqlite3.Connection:
    path = Path(db_path or config.DEFAULT_DATABASE_PATH)
    connection = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            path, timeout=config.SQLITE_BUSY_TIMEOUT_MS / 1000
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {config.SQLITE_BUSY_TIMEOUT_MS}")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection
    except (OSError, sqlite3.Error) as error:
        if connection is not None:
            connection.close()
        raise _database_error(error) from error


def initialize_database(db_path=None) -> Path:
    path = Path(db_path or config.DEFAULT_DATABASE_PATH)
    try:
        with closing(get_connection(path)) as connection:
            with connection:
                connection.executescript(SCHEMA)
    except sqlite3.Error as error:
        raise _database_error(error) from error
    return path


def integrity_check(db_path=None) -> str:
    try:
        with closing(get_connection(db_path)) as connection:
            return connection.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.Error as error:
        raise _database_error(error) from error
