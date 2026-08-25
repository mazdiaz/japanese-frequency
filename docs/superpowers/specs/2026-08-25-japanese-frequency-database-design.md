# Local Japanese Frequency Database Design

## Objective

Build a local Python 3 and SQLite vocabulary service for fast, offline Japanese
frequency lookup and private learning-state tracking. JPDB v2.2 is the primary
frequency source. BCCWJ1 Long Unit Word (LUW) frequency data is an optional
secondary written-Japanese source. Updating either corpus source must never
alter personal vocabulary history or the other corpus source.

## Design Priorities

1. Correct, unambiguous lookup results.
2. Preservation of personal learning data.
3. Simple setup and updates.
4. Fast local queries.
5. Stable agent-facing tools.
6. Easy addition of future frequency sources.
7. Standard-library-only runtime.

## Source Findings

### JPDB v2.2

Pinned source:

`Kuuuube/yomitan-dictionaries`,
`data/jpdb_v2.2_freq_list_2024-10-13.csv`

Despite its `.csv` extension, the inspected file is UTF-8 tab-separated data
with this header:

```text
term	reading	frequency	kana_frequency
```

`frequency` is the term rank. `kana_frequency` is an optional rank for the kana
form and has a documented meaning in the upstream JPDB v2.2 Kana dictionary.
The importer stores it as `kana_rank` only when populated. The inspected file
contains 278,946 rows but 276,190 normalized `(term, reading)` identities;
2,370 identities repeat because JPDB ranks separate senses that this export does
not identify further.

### BCCWJ1 LUW

Pinned source:

`BCCWJ_frequencylist_luw_ver1_0.zip`, DOI `10.15084/00003212`

The archive contains `BCCWJ_frequencylist_luw_ver1_0.tsv`: UTF-8,
tab-separated, 2,434,620 lines including an 80-column header. Relevant columns
are `rank`, `lForm`, `lemma`, `frequency`, and `pmw`. `lForm` readings use
katakana. The same lemma and reading can occur under different part-of-speech
or word-type identities.

The configured BCCWJ SHA-256 is the project's recorded hash of the inspected
pinned ZIP, not an upstream-published checksum. It provides reproducible byte
identity for setup and opt-in integration tests.

Exactly 14 rows in the pinned TSV have an empty raw `lForm`. Those rows use
canonical reading `""`. Whitespace-only `lForm` and empty or whitespace-only
`lemma` are invalid.

The upstream manual states its exact use and redistribution terms. README text
will quote or accurately reference those terms and link to the upstream source,
without strengthening or weakening them. Source data and generated databases
will not be distributed by this project.

## Project Structure

```text
japanese-frequency/
├── data/
│   └── sources/
├── docs/
│   └── superpowers/specs/
├── japanese_frequency/
│   ├── __init__.py
│   ├── __main__.py
│   ├── database.py
│   ├── errors.py
│   ├── importers.py
│   ├── lookup.py
│   ├── normalization.py
│   ├── tools.py
│   └── user_words.py
├── scripts/
│   ├── import_bccwj.py
│   └── import_jpdb.py
├── tests/
│   ├── fixtures/
│   └── test_*.py
├── .gitignore
├── config.py
├── README.md
├── requirements.txt
└── setup_database.py
```

Python 3.11 or newer is the documented baseline. Runtime code uses only Python's
standard library, including `sqlite3`, `csv`, `zipfile`, `urllib`, `hashlib`,
and `unicodedata`. `requirements.txt` documents that no external runtime
dependencies are required.

## Components

- `database.py`: path resolution, connection setup, schema creation, and
  transaction helpers.
- `errors.py`: typed internal exceptions and stable public error codes.
- `normalization.py`: word and reading normalization.
- `importers.py`: streaming source validation, staging, aggregation, and
  source-scoped replacement.
- `lookup.py`: frequency lookup and commonness classification.
- `user_words.py`: personal-state mutations, omitted-reading resolution, and
  combined profiles.
- `tools.py`: JSON-serializable wrappers suitable for agent tool calling.
- `__main__.py`: JSON command-line interface.
- `setup_database.py`: directory creation, atomic downloads, imports, integrity
  check, and setup report.

## Normalization And Identity

All text receives NFC normalization and surrounding-whitespace removal. Word
spellings receive no other transformation, so `読む`, `よむ`, and `ヨム`
remain distinct words.

Readings additionally receive katakana-to-hiragana canonicalization. This lets
the BCCWJ `ヨム` reading match API input `よむ` while leaving exact word spelling
untouched. Reading conversion covers standard katakana code points whose
hiragana equivalents are defined; it does not perform morphological or spelling
normalization.

The canonical vocabulary identity is `(word, reading)`. An empty reading is a
valid identity when reading is unavailable, including user-entered words and
the 14 BCCWJ rows with exactly empty raw `lForm`. Empty-reading corpus entries
remain distinct from populated readings, appear in word-only results, and
participate in omitted-reading resolution.

## Database Schema

Database location defaults to `data/japanese_frequency.db`.

```sql
CREATE TABLE frequency (
    word TEXT NOT NULL,
    reading TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    rank INTEGER,
    frequency REAL,
    frequency_per_million REAL,
    kana_rank INTEGER,
    PRIMARY KEY (word, reading, source)
);

CREATE TABLE user_words (
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

CREATE TABLE source_metadata (
    source TEXT PRIMARY KEY,
    version TEXT,
    filename TEXT,
    imported_at TEXT,
    source_row_count INTEGER,
    entry_count INTEGER,
    sha256 TEXT,
    notes TEXT
);

CREATE INDEX idx_frequency_word ON frequency(word);
CREATE INDEX idx_frequency_word_reading ON frequency(word, reading);
CREATE INDEX idx_frequency_source_rank ON frequency(source, rank);
```

`frequency` and `source_metadata` contain replaceable corpus data.
`user_words` contains durable personal state. Import code never deletes,
recreates, or updates `user_words`.

Connections enable foreign keys, a configured `busy_timeout`, and WAL where
appropriate. Normal lookups do not run `PRAGMA integrity_check`; setup and
explicit maintenance paths do.

## JPDB Import

The importer accepts the pinned source file and uses `utf-8-sig` with tab
delimiting. It requires the exact ordered header because the URL and source
version are pinned. Header failures identify missing, unexpected, and reordered
columns.

Rows are streamed into a connection-scoped SQLite TEMP staging table. Repeated
normalized identities are collapsed using the lowest term rank, representing
the most common exported sense, and the lowest populated kana rank. Metadata
records raw source row count, normalized entry count, and this transformation.
Validation requires:

- Correct row width.
- Nonempty normalized term and reading.
- Positive integer `frequency` rank.
- Empty or positive integer `kana_frequency` rank.
- At least one valid data row.

The actual imported file's SHA-256 is calculated and stored. An upstream
expected checksum is also verified when one is available in configuration.

Only after complete staging and validation does the importer start a short
`BEGIN IMMEDIATE` transaction. It deletes live `source = 'jpdb'` rows, copies
staged rows, updates JPDB metadata, and commits. Any live-swap failure rolls back
the deletion and insertion together. Parsing or staging failure occurs before
the swap and leaves live data untouched.

## BCCWJ Import

The importer accepts the official ZIP or extracted TSV and streams content
without materializing the 498 MB TSV in Python memory. It requires the exact
documented 80-column ordered header with detailed mismatch diagnostics. Every
row must have the correct width and valid required numeric fields.

Rows are normalized and aggregated in a connection-scoped TEMP table keyed by
exact lemma and canonical reading. For duplicate BCCWJ lemma/reading identities,
`frequency` and `pmw` are summed. After complete parsing, the importer assigns a
deterministic sequential computed rank ordered by:

1. Aggregate frequency descending.
2. Word ascending.
3. Reading ascending.

Ranks are always `1, 2, 3, ...`; tied frequencies do not receive competition
ranks. Metadata identifies this rank as project-computed and records raw input
row count, aggregate entry count, source version, filename, imported-file
SHA-256, and transformation details.

After staging validation, a short `BEGIN IMMEDIATE` transaction replaces only
`source = 'bccwj_luw'` rows and its metadata. JPDB and personal rows remain
untouched. TEMP staging disappears when its connection closes, including after
process failure.

## Public Python API

Public functions accept an optional keyword-only `db_path` for tests and custom
deployment. Normal callers omit it.

### Frequency Lookup

```python
lookup_frequency(word: str, reading: str | None = None) -> dict
```

With a reading, returns the precise normalized identity and available source
records. Without a reading, returns every matching reading identity. Multiple
readings are never collapsed or arbitrarily selected. Results are ordered by
JPDB rank ascending with NULL last, then BCCWJ rank ascending with NULL last,
then reading ascending. SQL ordering uses explicit nullness terms rather than
depending on SQLite's default NULL ordering. Unknown words return `found: false`.

Source values always come from imported data. `kana_rank` is omitted when the
JPDB row does not supply `kana_frequency`.

### Personal Mutations

```python
record_encounter(word, reading=None)
mark_known(word, reading=None, known=True)
set_in_anki(word, reading=None, in_anki=True)
```

When reading is supplied, mutations use that normalized identity. When reading
is omitted, all three functions resolve it as follows:

1. Exactly one distinct corpus reading for the exact word: use it.
2. Multiple corpus readings: return or raise `ambiguous_reading` and perform no
   mutation.
3. No corpus entry: use the empty-string reading identity.

Ambiguity is resolved before beginning mutation, and tests verify `user_words`
is completely unchanged after every ambiguous mutation attempt.

`record_encounter` atomically creates or updates the row, increments
`encounter_count`, sets `first_seen` once, and updates `last_seen`. Timestamps
come from an injectable clock and use UTC RFC 3339 form such as
`2026-08-25T02:55:00Z` or the equivalent with fractional seconds.

Known and Anki mutations are atomic upserts and preserve all unrelated fields.

### Combined Profile

```python
get_word_profile(word: str, reading: str | None = None) -> dict
```

With a reading, combines corpus data and personal state for one identity. With
no reading and multiple corpus or user identities, returns a `matches` list and
never chooses one arbitrarily. User-only identities remain visible.

### Commonness

```python
classify_jpdb_rank(rank: int) -> dict
```

Thresholds live in configuration:

```text
1-1,000       extremely_common
1,001-3,000   very_common
3,001-10,000  common
10,001-20,000 moderately_common
20,001-40,000 uncommon
40,001-70,000 rare
70,001+       very_rare
```

Output includes raw rank and category. Documentation labels categories as user
heuristics rather than official JPDB or linguistic classifications.

## Agent Tools And Errors

`tools.py` exposes:

- `lookup_japanese_frequency`
- `get_japanese_word_profile`
- `record_japanese_encounter`
- `mark_japanese_word_known`
- `set_japanese_word_anki_status`

Wrappers accept simple strings and booleans, return JSON-serializable objects,
perform no network calls, and print nothing. Every successful response uses
`{"ok": true, "result": {...}}`. Expected failures use
`{"ok": false, "error": {"type": "...", "message": "..."}}` with a stable
machine-readable type and human-readable message.

Stable error types include:

- `invalid_input`
- `not_found`
- `ambiguous_reading`
- `source_format_error`
- `download_error`
- `database_error`
- `database_busy`

Direct Python domain APIs raise typed exceptions. CLI and tool wrappers convert
them to stable error objects. Tests assert error codes rather than exact message
wording.

## CLI

The module CLI maps directly to public APIs and prints UTF-8 JSON:

```text
python -m japanese_frequency lookup 読む よむ
python -m japanese_frequency profile 読む よむ
python -m japanese_frequency encounter 読む よむ
python -m japanese_frequency known 読む よむ --value true
python -m japanese_frequency anki 読む よむ --value true
```

Errors are JSON and produce nonzero exit status. Boolean commands support both
setting and clearing state.

## Setup And Downloads

`setup_database.py`:

1. Creates `data/` and `data/sources/`.
2. Initializes schema and indexes.
3. Uses an explicit source path when provided or downloads pinned sources.
4. Downloads to a `.part` file in the destination directory.
5. Verifies successful completion and any configured expected checksum.
6. Atomically renames `.part` to the final filename.
7. Imports mandatory JPDB.
8. Imports BCCWJ only with `--with-bccwj`.
9. Runs `PRAGMA integrity_check`.
10. Reports JSON source counts, database path, database size, and integrity
    result.

A JPDB download or import failure is fatal. If `--with-bccwj` is passed, a
BCCWJ download or import failure is also fatal and setup exits nonzero. Setup
never silently continues after failure of an explicitly requested source.
Partial downloads never appear under valid source filenames and are cleaned up
after failures where possible.

Normal lookup and mutation operations use only SQLite and never access source
files or the network.

## Concurrency And Transaction Safety

Connection-scoped TEMP staging is preferred and used where SQLite permits.
Staging population may take substantial time but does not hold a write
transaction that deletes or replaces live source rows. Final source replacement
uses a short write transaction.

All data values use parameterized SQL. Fixed internal table and index names are
not derived from user input. A configured `busy_timeout` gives concurrent
connections predictable lock behavior. Lock failures map to `database_busy`
and cannot produce partial user mutations or source replacements.

## Testing Strategy

Default `python -m unittest` tests are entirely offline, fast, and deterministic.
They use temporary directories, temporary databases, small synthetic source
fixtures, mocked downloads, and injected clocks. Default tests never download
real source data.

Coverage includes:

- Existing, unknown, precise, and word-only lookups.
- Multiple-reading results and deterministic explicit NULL-last ordering.
- NFC, whitespace, and reading kana normalization.
- Preservation of exact word spellings.
- Conditional JPDB `kana_rank` output.
- Omitted-reading mutation resolution for one, many, and zero readings.
- Complete absence of user-state changes after ambiguous mutations.
- Encounter counts and injected UTC timestamps.
- Known and Anki status setting and clearing.
- Precise, ambiguous, user-only, and absent profiles.
- JPDB and BCCWJ header, row-width, and numeric failures.
- Header diagnostics for missing, unexpected, and reordered columns.
- Repeat imports without duplicate live rows.
- JPDB duplicate-sense collapse using minimum term and kana ranks.
- BCCWJ aggregation and deterministic sequential ranking.
- Staging failures leaving live data untouched.
- Atomic live-swap rollback.
- Personal-state persistence across JPDB and BCCWJ replacements.
- Source isolation in both update directions.
- Metadata version, source-row count, entry count, filename, SHA-256, and notes.
- Successful explicit `PRAGMA integrity_check`.
- CLI JSON and tool stable error codes.
- Atomic download rename and `.part` failure behavior.
- Configured `busy_timeout` across multiple connections, including no partial
  mutation after lock failure.

Opt-in integration tests import already-downloaded real JPDB or BCCWJ files.
They never download implicitly. JPDB smoke tests may query known terms such as
`読む`, but do not assert a rank unless explicitly tied to the pinned version.
Full BCCWJ import testing remains opt-in because of source size and processing
time.

## Documentation And Data Handling

README documents installation, pinned sources and attribution, exact upstream
terms, setup options, Python API, agent tools, CLI, database location, safe
source updates, backup guidance, BCCWJ aggregation, computed-rank policy, and
known source quirks.

`.gitignore` excludes source archives, extracted corpus files, generated SQLite
databases and journals, `.part` downloads, caches, and test artifacts. Personal
vocabulary data never leaves the local database.

## Future Sources

Future corpora use new `source` values and source-scoped importers that emit the
same normalized frequency contract. Raw source details remain importer concerns.
The core lookup and personal-state schema needs no change for ordinary rank,
frequency, and frequency-per-million sources.

## Completion Criteria

Version one is complete when:

- JPDB import, local lookup, all personal mutations, profiles, commonness, CLI,
  and tool wrappers work without network access after setup.
- Optional BCCWJ LUW import and lookup work with documented aggregation and
  computed sequential ranks.
- Reimports preserve personal data and unrelated frequency sources.
- Malformed imports and lock failures cannot partially mutate live state.
- Default offline test suite passes.
- Setup reports source counts, database path and size, and successful integrity
  check.
