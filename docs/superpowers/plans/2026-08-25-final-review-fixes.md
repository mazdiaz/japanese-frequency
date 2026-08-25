# Final Review Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make multi-source setup publication atomic, normalize filesystem failures into stable domain errors, document rank labels, and prove behavior with offline and real-source verification.

**Architecture:** Importers expose connection-scoped TEMP staging helpers that parse outside live-write transactions. Standalone importers stage and publish one source; setup stages every requested source on one connection and publishes all requested sources plus metadata in one short `BEGIN IMMEDIATE` transaction.

**Tech Stack:** Python 3.11+, standard library, SQLite, `unittest`.

## Global Constraints

- Preserve public signatures and source-scoped behavior of `import_jpdb` and `import_bccwj`.
- Preserve `user_words` and unrequested corpus sources.
- No network in default test suite.
- Cleanup failures must never mask original download failures.

---

### Task 1: Atomic Multi-Source Publication

**Files:**
- Modify: `japanese_frequency/importers.py`
- Modify: `japanese_frequency/setup.py`
- Test: `tests/test_setup.py`
- Test: `tests/test_importers.py`

**Interfaces:**
- Produces connection-scoped staging helpers and a batch publication helper.
- Standalone import functions retain current signatures and return metadata dictionaries.

- [ ] Add regression tests that seed `frequency`, `source_metadata`, and `user_words`, then verify checksum-valid malformed second-source staging leaves every table unchanged.
- [ ] Run targeted tests and confirm current sequential publication fails the new regression.
- [ ] Refactor source parsing into helpers accepting an existing SQLite connection and unique TEMP table names.
- [ ] Add one publication helper that starts `BEGIN IMMEDIATE`, replaces only requested live sources, upserts matching metadata, and rolls back on any SQLite failure.
- [ ] Make setup initialize once, stage all requested sources, then call batch publication once.
- [ ] Add injected publication-failure test and standalone importer regression tests.
- [ ] Run setup/importer tests.

### Task 2: Filesystem Error Translation

**Files:**
- Modify: `japanese_frequency/database.py`
- Modify: `japanese_frequency/setup.py`
- Test: `tests/test_database.py`
- Test: `tests/test_setup.py`
- Test: `tests/test_tools.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Database path creation/open/stat failures raise `DatabaseError` with code `database_error`.
- Download directory/temp/write/flush/replace failures raise `DownloadError` with code `download_error`.

- [ ] Add direct database API tests for parent path conflicts and mocked `mkdir` failures.
- [ ] Add tool and CLI envelope tests proving `database_error` and no traceback.
- [ ] Move database parent creation inside translated exception boundary and translate `OSError` consistently.
- [ ] Add download tests for directory creation, temporary creation/write/flush/replace, and cleanup failures.
- [ ] Wrap setup source-directory and final-stat failures with appropriate domain errors; suppress cleanup-only failures.
- [ ] Run database/setup/tool/CLI tests.

### Task 3: Rank Documentation

**Files:**
- Modify: `README.md`

**Interfaces:**
- Documents exported `classify_jpdb_rank(rank)` and exact thresholds from `japanese_frequency/lookup.py`.

- [ ] Copy all threshold boundaries and labels from implementation into README.
- [ ] State labels are project/user heuristics, not official JPDB categories or linguistic classes.
- [ ] Check README against implementation.

### Task 4: Verification And Delivery

**Files:**
- Create ignored: `final-fix-2-report.md`

**Interfaces:**
- Report records exact commands, counts, durations, real-source paths/results, functional rollback evidence, compile result, and diff review.

- [ ] Run complete offline suite with count and duration.
- [ ] Run real JPDB and BCCWJ smoke tests using local pinned sources.
- [ ] Run both-source setup functional flow and failure rollback verification.
- [ ] Run `compileall`, inspect `git diff --check`, status, and final diff.
- [ ] Write ignored evidence report and verify Git ignores it.
- [ ] Commit intended tracked files with concise message.
