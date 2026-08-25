import sqlite3
from contextlib import closing
from pathlib import Path

import config
from japanese_frequency.errors import DatabaseBusyError, DatabaseError


SCHEMA_VERSION = 2


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
    known INTEGER CHECK (known IN (0, 1)),
    in_anki INTEGER CHECK (in_anki IN (0, 1)),
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

CREATE TABLE IF NOT EXISTS known_spellings (
    word TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (word, source)
);

CREATE TABLE IF NOT EXISTS personal_source_metadata (
    source TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    source_row_count INTEGER NOT NULL,
    entry_count INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS media_sources (
    id INTEGER PRIMARY KEY,
    source_key TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    requested_filename TEXT NOT NULL,
    selected_filename TEXT NOT NULL,
    format TEXT NOT NULL CHECK (format IN ('csv', 'txt')),
    imported_at TEXT NOT NULL,
    source_row_count INTEGER NOT NULL,
    entry_count INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS media_words (
    media_id INTEGER NOT NULL,
    word TEXT NOT NULL,
    reading TEXT NOT NULL,
    occurrences INTEGER NOT NULL CHECK (occurrences > 0),
    media_rank INTEGER NOT NULL CHECK (media_rank > 0),
    definitions TEXT,
    example_sentence TEXT,
    dictionary_id TEXT,
    PRIMARY KEY (media_id, word, reading),
    FOREIGN KEY (media_id) REFERENCES media_sources(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS media_spellings (
    media_id INTEGER NOT NULL,
    word TEXT NOT NULL,
    occurrences INTEGER,
    media_rank INTEGER,
    definitions TEXT,
    example_sentence TEXT,
    dictionary_id TEXT,
    PRIMARY KEY (media_id, word),
    FOREIGN KEY (media_id) REFERENCES media_sources(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_frequency_word ON frequency(word);
CREATE INDEX IF NOT EXISTS idx_frequency_word_reading ON frequency(word, reading);
CREATE INDEX IF NOT EXISTS idx_frequency_source_rank ON frequency(source, rank);
CREATE INDEX IF NOT EXISTS idx_known_spellings_word ON known_spellings(word);
CREATE INDEX IF NOT EXISTS idx_media_words_media_rank
    ON media_words(media_id, media_rank);
CREATE INDEX IF NOT EXISTS idx_media_spellings_media_rank
    ON media_spellings(media_id, media_rank);
"""


LEGACY_USER_MIGRATION = """
CREATE TABLE user_words_new (
    word TEXT NOT NULL,
    reading TEXT NOT NULL DEFAULT '',
    known INTEGER CHECK (known IN (0, 1)),
    in_anki INTEGER CHECK (in_anki IN (0, 1)),
    encounter_count INTEGER NOT NULL DEFAULT 0 CHECK (encounter_count >= 0),
    first_seen TEXT,
    last_seen TEXT,
    notes TEXT,
    PRIMARY KEY (word, reading)
);

INSERT INTO user_words_new
SELECT word, reading,
       CASE WHEN known = 1 THEN 1 ELSE NULL END,
       CASE WHEN in_anki = 1 THEN 1 ELSE NULL END,
       encounter_count, first_seen, last_seen, notes
FROM user_words;
DROP TABLE user_words;
ALTER TABLE user_words_new RENAME TO user_words;
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
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version >= SCHEMA_VERSION:
                return path

            user_columns = connection.execute(
                "PRAGMA table_info(user_words)"
            ).fetchall()
            migrate_legacy_user_words = bool(user_columns) and any(
                row["name"] == "known" and row["notnull"]
                for row in user_columns
            )
            try:
                migration = "BEGIN IMMEDIATE;\n"
                if migrate_legacy_user_words:
                    migration += LEGACY_USER_MIGRATION
                migration += SCHEMA
                migration += f"PRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;"
                connection.executescript(migration)
            except sqlite3.Error:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
                raise
    except sqlite3.Error as error:
        raise _database_error(error) from error
    return path


def integrity_check(db_path=None) -> str:
    try:
        with closing(get_connection(db_path)) as connection:
            return connection.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.Error as error:
        raise _database_error(error) from error
