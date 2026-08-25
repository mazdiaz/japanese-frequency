# Repository Agent Guide

## Purpose And Non-Goals

This repository provides offline Japanese frequency lookup, private vocabulary
state, Migaku known-spelling import, media vocabulary import, bulk mining
analysis, and contextual recommendations. Use it to present evidence for user
decisions. It does not replace language judgment, choose cards autonomously, or
sync with Anki or Migaku.

## Data And Privacy

- Keep corpus sources, Migaku exports, media exports, SQLite databases, reports,
  and integration outputs local.
- Never upload personal files or data.
- Never dump the full database into model context. Query only needed records.
- Never use frequency as the sole decision.
- Use report files for large output rather than returning every candidate in
  chat or tool context.
- Ignored local locations include `data/sources/`, `data/imports/`, `data/*.db*`,
  `reports/`, and integration output directories. Confirm `git status` before
  committing.
- Do not commit generated databases, reports, personal exports, SQLite sidecars,
  or temporary `.part` files.

## Readiness

Python 3.11+ is required; runtime uses standard library only. Default database
is `data/japanese_frequency.db`, defined by `config.DEFAULT_DATABASE_PATH`.
Pass `db_path=...` to Python calls or place CLI-global `--db PATH` before command
to use another database.

Database setup is both source import and database mutation. Obtain explicit user
authorization before any download or database mutation, including first-time
setup and refresh. Prefer offline setup with already-downloaded local sources:

```console
python setup_database.py --jpdb-source C:\local\corpora\jpdb_v2.2.csv --db C:\local\data\japanese_frequency.db
python setup_database.py --jpdb-source C:\local\corpora\jpdb_v2.2.csv --with-bccwj --bccwj-source C:\local\corpora\BCCWJ_frequencylist_luw_ver1_0.zip --db C:\local\data\japanese_frequency.db
```

`--jpdb-source PATH` supplies mandatory local JPDB input. To include local
BCCWJ, pass both `--with-bccwj` and `--bccwj-source PATH`; without
`--with-bccwj`, BCCWJ is neither imported nor refreshed. `--db PATH` selects
target database.

Running python setup_database.py without --jpdb-source downloads pinned JPDB
source from URL recorded in `japanese_frequency.setup` into database-adjacent
`sources/`. Adding `--with-bccwj` without `--bccwj-source` also downloads pinned
BCCWJ source. Never use either network path without explicit approval. Both
downloaded and explicit local setup sources undergo pinned SHA-256 validation;
checksum mismatch is fatal and does not publish staged corpus changes. Setup
accepts only pinned source versions. Use standalone import scripts for an
intentionally different source version after separate user approval.

Successful setup mutates requested corpus rows and metadata while preserving
personal `user_words`. Output must report `integrity_check: "ok"`. Imports can
initialize missing schema, but media analysis without corpus data has less
identity and frequency evidence.

## Refresh Migaku Known Spellings

Migaku input is UTF-8 text containing one spelling per line. Import replaces
only prior `migaku` known-spelling snapshot after complete validation;
duplicates collapse after NFC normalization. It preserves corpus rows, media
sources, and `user_words` identity state.

```console
python -m japanese_frequency import-known C:\local\imports\migaku_known_words_DATE.txt
```

Direct API: `import_migaku_known_words(path, *, db_path=None) -> dict`.
Agent wrapper: `import_migaku_known_vocabulary(path, *, db_path=None)`.
Inspect returned `filename`, `source_row_count`, `entry_count`, `sha256`, and
`imported_at` before claiming refresh success.

## Import Media

Use stable, descriptive source keys such as `series-name-s01` or
`shuukura-v1`. Display names may change and need not be unique; source keys are
update identities.

```console
python -m japanese_frequency import-media C:\local\imports\media\Volume-1.txt --source-key shuukura-v1 --name "Volume 1"
```

Direct API:
`import_media_vocabulary(path, source_key, display_name=None, *, db_path=None) -> dict`.
Agent wrapper:
`import_japanese_media_vocabulary(path, source_key, display_name=None, *, db_path=None)`.

Importer accepts UTF-8 `.txt` and `.csv`. Requested TXT uses same-stem CSV when
that file exists. CSV requires `Word`, `ReadingKana`, and misspelled upstream
header `Occurences`; extra columns are accepted. TXT is unordered
spelling-membership data and invents no reading or rank. Confirm
`requested_filename`, `selected_filename`, `format`, `sha256`, counts, and
`notes` in result. Reimporting same source key atomically replaces only that
media snapshot after complete validation.

## Mining Workflow

Run bulk analysis first, inspect compact summary, then write CSV when candidate
output is large:

```console
python -m japanese_frequency analyze-media shuukura-v1 --limit 25
python -m japanese_frequency analyze-media shuukura-v1 --output reports\shuukura-v1.csv
```

Direct APIs:

- `analyze_media(source_key, *, limit=None, db_path=None) -> dict`
- `export_media_analysis_csv(analysis, output_path) -> dict`
- `recommend_media_word(source_key, word, reading=None, *, failed_recall=False, successful_inference=False, transparent_composition=False, personally_useful=False, db_path=None) -> dict`

Use contextual recommendation only after user supplies relevant context:

```console
python -m japanese_frequency recommend-media shuukura-v1 WORD READING --failed-recall true
```

Context flags are exactly `failed_recall`, `successful_inference`,
`transparent_composition`, and `personally_useful` in Python, with hyphenated
CLI forms. CLI values must be lowercase `true` or `false`.

## Public Interfaces

Exact existing lookup/state API signatures exported from `japanese_frequency`:

- `lookup_frequency(word: str, reading: str | None = None, *, db_path=None) -> dict`
- `classify_jpdb_rank(rank: int) -> dict`
- `get_word_profile(word, reading=None, *, db_path=None) -> dict`
- `record_encounter(word, reading=None, *, db_path=None, now=None) -> dict`
- `mark_known(word, reading=None, known=True, *, db_path=None) -> dict`
- `set_in_anki(word, reading=None, in_anki=True, *, db_path=None) -> dict`

Exact mining domain API signatures exported from `japanese_frequency`:

- `get_known_spelling(word, *, connection=None, db_path=None) -> dict`
- `get_media_source(source_key, *, db_path=None, connection=None) -> dict`
- `import_migaku_known_words(path, *, db_path=None, now=None) -> dict`
- `import_media_vocabulary(path, source_key, display_name=None, *, db_path=None, now=None) -> dict`
- `analyze_media(source_key, *, limit=None, db_path=None) -> dict`
- `export_media_analysis_csv(analysis, output_path) -> dict`
- `recommend_media_word(source_key, word, reading=None, *, failed_recall=False, successful_inference=False, transparent_composition=False, personally_useful=False, db_path=None) -> dict`

`now` is a deterministic timestamp injection point intended for tests. Normal
callers omit it. `connection` permits a caller-owned database connection;
otherwise function opens connection from `db_path` or default path.

Exact mining agent wrapper signatures in `japanese_frequency.tools`:

- `import_migaku_known_vocabulary(path, *, db_path=None)`
- `import_japanese_media_vocabulary(path, source_key, display_name=None, *, db_path=None)`
- `analyze_japanese_media(source_key, *, limit=None, db_path=None)`
- `recommend_japanese_media_word(source_key, word, reading=None, *, failed_recall=False, successful_inference=False, transparent_composition=False, personally_useful=False, db_path=None)`

Exact stable lookup/state wrapper signatures in `japanese_frequency.tools`:

- `lookup_japanese_frequency(word, reading=None, *, db_path=None)`
- `get_japanese_word_profile(word, reading=None, *, db_path=None)`
- `record_japanese_encounter(word, reading=None, *, db_path=None)`
- `mark_japanese_word_known(word, reading=None, known=True, *, db_path=None)`
- `set_japanese_word_anki_status(word, reading=None, in_anki=True, *, db_path=None)`

## CLI Contract

`--db` is global and must appear before command. Brackets below mean optional;
braces show accepted literal values.

```console
python -m japanese_frequency [--db DB_PATH] lookup WORD [READING]
python -m japanese_frequency [--db DB_PATH] profile WORD [READING]
python -m japanese_frequency [--db DB_PATH] encounter WORD [READING]
python -m japanese_frequency [--db DB_PATH] known WORD [READING] [--value {true,false}]
python -m japanese_frequency [--db DB_PATH] anki WORD [READING] [--value {true,false}]
python -m japanese_frequency [--db DB_PATH] import-known PATH
python -m japanese_frequency [--db DB_PATH] import-media PATH --source-key SOURCE_KEY [--name DISPLAY_NAME]
python -m japanese_frequency [--db DB_PATH] analyze-media SOURCE_KEY [--limit LIMIT] [--output PATH]
python -m japanese_frequency [--db DB_PATH] recommend-media SOURCE_KEY WORD [READING] [--failed-recall {true,false}] [--successful-inference {true,false}] [--transparent-composition {true,false}] [--personally-useful {true,false}]
```

`LIMIT` must be nonnegative. `known` and `anki` default `--value` to `true`.
Recommendation context flags default to `false`. `analyze-media --output PATH`
writes UTF-8-with-BOM CSV atomically and prints source, summary, and report
metadata instead of candidate rows.

## Envelope Contract

Agent wrappers never print. Success always has exactly `ok` and `result`; result
is domain API object:

```text
{"ok": true, "result": <object>}
```

Ordinary wrapper failure always has exactly `ok` and `error`; error contains
safe type and message:

```text
{"ok": false, "error": {"type": "<code>", "message": "<safe message>"}}
```

For `ambiguous_reading`, sorted `matches` is inside `error`, alongside `type`
and `message`, never at top level:

```text
{"ok": false, "error": {"type": "ambiguous_reading", "message": "<safe message>", "matches": ["<reading>"]}}
```

CLI success prints direct UTF-8 domain result with no `ok` envelope. Ordinary
CLI failure exits nonzero and prints:

```text
{"error": {"type": "<code>", "message": "<safe message>"}}
```

CLI ambiguity also places sorted `matches` inside `error`:

```text
{"error": {"type": "ambiguous_reading", "message": "<safe message>", "matches": ["<reading>"]}}
```

## Identity And Tri-State Semantics

`known_spelling` means spelling appears in imported source such as Migaku. It
does not prove which reading or sense is known and never causes skip by itself.
`known_identity` applies to exact `(word, reading)` personal state. `in_anki`
also applies to exact identity.

`known_identity` and `in_anki` are tri-state: `true` means explicit positive,
`false` means explicit negative, and `null` means no assertion. Preserve `null`;
do not coerce it to false. `identity_type: "spelling"` means no reliable reading
identity was available.

## Ambiguity And Readings

Omitted reading resolves only when exactly one corpus/media identity exists.
Multiple identities produce `ambiguous_reading` with sorted readings. Ask user
or retain spelling-level evidence. Never invent, transliterate, or guess a
reading to bypass ambiguity. Empty-reading corpus identities and absent media
readings remain explicit data states.

## Scores, Tiers, And Evidence

Mining score is `ranking_heuristic`, not probability or linguistic truth.
Default `mine` threshold is 5; lower candidates are `review`. Explicit
`known_identity: true` or `in_anki: true` normally yields `skip`. Failed recall
can move known identity to contextual `review`, but never overrides Anki skip.

Never use frequency as the sole decision. Consider occurrences, media rank,
known spelling, exact identity state, Anki state, encounters, context, and user
goals. Preserve raw `score_components`, `reasons`, media evidence, personal
evidence, and source metadata when summarizing. Recommendation output retains
default and contextual score/tier separately.

## Mutation Authorization

Never mark known, unknown, or Anki without explicit user instruction or explicit
evidence authorized by user. Imports and refreshes also require user-selected
local source and target database. Analysis and recommendation are read-only;
CSV export writes only requested report. Do not infer mutations from score,
tier, failed recall, spelling membership, or frequency.

## Failures, Backup, And Tests

Treat `invalid_input`, `source_format_error`, `media_not_found`,
`ambiguous_reading`, `database_busy`, and `database_error` as recoverable typed
failures. Do not expose raw database details. On ambiguity, present safe matches.
On lock failure, stop competing writers and retry; do not delete database.

Imports stage and validate snapshots before atomic publication. Failed imports
preserve live state. Before database replacement or movement, stop writers and
back up database plus `-wal` and `-shm` sidecars, or use SQLite backup API.

Run offline checks:

```console
python -m unittest discover -v
python -m compileall -q japanese_frequency tests
git diff --check
git status --short --branch
```

Real-source tests are opt-in through `MIGAKU_KNOWN_SOURCE` and
`MEDIA_VOCAB_SOURCE`; they read local files only. Missing variables must leave
default suite skipped and offline. For format errors, inspect selected filename,
encoding, exact CSV headers, row number, and positive integer `Occurences`.
