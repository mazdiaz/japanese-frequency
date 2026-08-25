# Task 2 Report

## Status

DONE

## Commits

- `908baad` (`feat(import): add atomic corpus importers`)
- Report: committed separately after implementation verification.

## Files Changed

- `japanese_frequency/importers.py`: exact-header JPDB and BCCWJ importers,
  explicit TEMP staging tables, validation, aggregation, deterministic ranking,
  metadata, actual-file SHA-256, streamed ZIP input, and atomic live swaps.
- `scripts/import_jpdb.py`: JSON-emitting JPDB import CLI.
- `scripts/import_bccwj.py`: JSON-emitting BCCWJ TSV/ZIP import CLI.
- `tests/test_importers.py`: importer, safety, rollback, metadata, hash, ZIP,
  source-isolation, header-diagnostic, and CLI coverage.
- `tests/fixtures/jpdb.tsv`: representative JPDB fixture.
- `tests/fixtures/bccwj.tsv`: official ordered 80-column BCCWJ LUW header fixture.

No Task 1 interfaces or contracts were changed.

## TDD Evidence

### Initial RED

Command:

```text
$env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest tests.test_importers -v
```

Result: expected import error because `japanese_frequency.importers` did not
exist.

### Initial GREEN

Command:

```text
$env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest tests.test_importers -v
```

Result after closing test-owned SQLite handles correctly: 12 tests passed, 0
failures, 0 errors.

### Header Regression RED

Command:

```text
$env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest tests.test_importers.ImporterTests.test_validate_header_reports_duplicate_columns_as_format_error -v
```

Result: expected `IndexError` reproduced for a duplicate known header name.
Root cause was unequal filtered header-list lengths in reorder diagnostics.

### Header Regression GREEN

Command:

```text
$env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest tests.test_importers.ImporterTests.test_validate_header_reports_duplicate_columns_as_format_error -v
```

Result: 1 test passed. Duplicate names now produce detailed
`SourceFormatError` output.

## Final Verification

Importer suite:

```text
$env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest tests.test_importers -v
```

Result: 13 tests passed in 0.450 seconds, 0 failures, 0 errors.

Full suite:

```text
$env:PYTHONDONTWRITEBYTECODE='1'; python -m unittest -v
```

Result: 26 tests passed in 0.498 seconds, 0 failures, 0 errors.

Syntax compilation:

```text
$env:PYTHONDONTWRITEBYTECODE='1'; python -m py_compile japanese_frequency/importers.py scripts/import_jpdb.py scripts/import_bccwj.py tests/test_importers.py
```

Result: completed with no output or errors.

Git validation:

```text
git diff --cached --check
```

Result: completed with no output or errors before implementation commit.

The canonical BCCWJ header constant was also checked at 80 columns.

## Self-Review

- Confirmed JPDB and BCCWJ parse and validate all rows before any live-table
  deletion.
- Confirmed explicit constrained TEMP tables are used rather than
  `CREATE TABLE AS`.
- Confirmed the live replacement is a short `BEGIN IMMEDIATE` transaction
  containing source-specific deletion, staged insertion, and metadata upsert.
- Confirmed forced insertion failure rolls back both data and metadata.
- Confirmed JPDB duplicate normalized identities independently retain minimum
  term rank and minimum populated kana rank.
- Confirmed BCCWJ duplicate normalized identities sum frequency and pmw, then
  receive contiguous ranks ordered by frequency DESC, word ASC, reading ASC.
- Confirmed imports preserve `user_words`, unrelated frequency sources, and
  unrelated metadata.
- Confirmed JPDB exact four-column and BCCWJ official exact 80-column ordered
  headers, including missing, unexpected, reordered, and duplicate diagnostics.
- Confirmed row-width, required identity, positive integer, finite pmw, and
  nonempty-source validation.
- Confirmed UTF-8 BOM handling and katakana-to-hiragana reading normalization.
- Confirmed BCCWJ ZIP input requires exactly
  `BCCWJ_frequencylist_luw_ver1_0.tsv` and streams it through
  `io.TextIOWrapper` without extraction or whole-file loading.
- Confirmed SHA-256 covers the supplied file itself, including ZIP container
  bytes, and metadata records the supplied filename.
- Confirmed SQLite connections close before SQLite exceptions are translated,
  so TEMP state disappears on every failure path.
- Confirmed both CLI scripts invoke public importer interfaces and emit valid
  JSON results.
- Reviewed staged diff for unrelated changes and whitespace errors.

## Concerns

- Full 57 MB compressed / approximately 498 MB extracted BCCWJ corpus import
  was not run in this task. Tests exercise streamed ZIP behavior with a small
  archive; opt-in real-source smoke testing remains assigned to the later
  integration task.
