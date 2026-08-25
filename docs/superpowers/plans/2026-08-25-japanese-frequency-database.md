# Local Japanese Frequency Database Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standard-library Python package backed by SQLite that imports pinned JPDB v2.2 and optional BCCWJ1 LUW data, performs fast offline frequency lookups, and safely tracks private vocabulary state.

**Architecture:** Focused package modules share connection and normalization helpers. Importers stream into connection-scoped TEMP tables, validate fully, then atomically replace one live source. Query and mutation APIs return domain dictionaries; agent wrappers and CLI convert them into stable JSON envelopes.

**Tech Stack:** Python 3.11+, `sqlite3`, `csv`, `zipfile`, `urllib`, `hashlib`, `unittest`; no third-party runtime dependencies.

## Global Constraints

- Default database: `data/japanese_frequency.db`.
- Normal operation is offline and never reads source files after import.
- Word normalization is NFC plus trim only; reading normalization also converts standard katakana to hiragana.
- Every successful tool response uses keys `ok` and `result`; every expected failure uses keys `ok` and `error`, with `error.type` and `error.message`.
- Word-only results order by JPDB rank ascending NULL last, BCCWJ rank ascending NULL last, then reading ascending.
- Imports never modify `user_words` or unrelated source rows.
- Default `python -m unittest` suite stays offline, fast, and deterministic.
- All timestamps use injected UTC clocks and RFC 3339 `Z` output.

---

### Task 1: Core Database, Errors, And Normalization

**Files:**
- Create: `config.py`
- Create: `japanese_frequency/__init__.py`
- Create: `japanese_frequency/database.py`
- Create: `japanese_frequency/errors.py`
- Create: `japanese_frequency/normalization.py`
- Create: `tests/__init__.py`
- Create: `tests/test_database.py`
- Create: `tests/test_normalization.py`

**Interfaces:**
- Produces: `get_connection(db_path=None) -> sqlite3.Connection`
- Produces: `initialize_database(db_path=None) -> pathlib.Path`
- Produces: `integrity_check(db_path=None) -> str`
- Produces: `normalize_word(text: str) -> str`
- Produces: `normalize_reading(text: str | None) -> str | None`
- Produces: typed `JapaneseFrequencyError` subclasses with stable `.code`

- [ ] **Step 1: Write failing normalization and schema tests**

```python
class NormalizationTests(unittest.TestCase):
    def test_word_preserves_script_and_normalizes_nfc(self):
        self.assertEqual(normalize_word("  読む  "), "読む")
        self.assertNotEqual(normalize_word("よむ"), normalize_word("ヨム"))

    def test_reading_converts_katakana(self):
        self.assertEqual(normalize_reading(" ヨム "), "よむ")

class DatabaseTests(unittest.TestCase):
    def test_initialize_creates_schema_and_indexes(self):
        initialize_database(self.db_path)
        with get_connection(self.db_path) as connection:
            names = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'index')"
            )}
        self.assertTrue({"frequency", "user_words", "source_metadata"} <= names)
        self.assertTrue({"idx_frequency_word", "idx_frequency_word_reading", "idx_frequency_source_rank"} <= names)

    def test_busy_timeout_is_configured(self):
        with get_connection(self.db_path) as connection:
            timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        self.assertEqual(timeout, config.SQLITE_BUSY_TIMEOUT_MS)
```

- [ ] **Step 2: Run tests and verify import failures**

Run: `python -m unittest tests.test_normalization tests.test_database -v`

Expected: FAIL because package modules do not exist.

- [ ] **Step 3: Implement constants, errors, normalization, connections, and schema**

```python
# config.py
SQLITE_BUSY_TIMEOUT_MS = 1000
DEFAULT_DATABASE_PATH = Path(__file__).resolve().parent / "data" / "japanese_frequency.db"
JPDB_COMMONNESS_THRESHOLDS = ((1000, "extremely_common"), (3000, "very_common"), (10000, "common"), (20000, "moderately_common"), (40000, "uncommon"), (70000, "rare"))

# normalization.py
def normalize_word(text: str) -> str:
    if not isinstance(text, str) or not unicodedata.normalize("NFC", text).strip():
        raise InvalidInputError("word must be a non-empty string")
    return unicodedata.normalize("NFC", text).strip()

def normalize_reading(text: str | None) -> str | None:
    if text is None:
        return None
    value = unicodedata.normalize("NFC", text).strip()
    return "".join(chr(ord(char) - 0x60) if "ァ" <= char <= "ヶ" else char for char in value)

# database.py
def get_connection(db_path=None):
    path = Path(db_path or config.DEFAULT_DATABASE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=config.SQLITE_BUSY_TIMEOUT_MS / 1000)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {config.SQLITE_BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA journal_mode = WAL")
    return connection
```

Implement exact schema and indexes from design spec. Map locked operational errors to `DatabaseBusyError(code="database_busy")`; other SQLite errors use `DatabaseError(code="database_error")` at API boundaries.

- [ ] **Step 4: Run core tests**

Run: `python -m unittest tests.test_normalization tests.test_database -v`

Expected: PASS.

- [ ] **Step 5: Commit core**

```bash
git add config.py japanese_frequency tests/test_database.py tests/test_normalization.py tests/__init__.py
git commit -m "feat(core): add SQLite schema and normalization"
```

### Task 2: Transactional Frequency Importers

**Files:**
- Create: `japanese_frequency/importers.py`
- Create: `scripts/import_jpdb.py`
- Create: `scripts/import_bccwj.py`
- Create: `tests/test_importers.py`
- Create: `tests/fixtures/jpdb.tsv`
- Create: `tests/fixtures/bccwj.tsv`

**Interfaces:**
- Consumes: `get_connection`, `initialize_database`, `normalize_word`, `normalize_reading`
- Produces: `import_jpdb(path, *, db_path=None, version="2.2", now=None) -> dict`
- Produces: `import_bccwj(path, *, db_path=None, version="1.0", now=None) -> dict`
- Produces: `validate_header(actual, expected, source) -> None`

- [ ] **Step 1: Write failing JPDB import and safety tests**

```python
def test_jpdb_collapses_duplicate_senses_to_minimum_ranks(self):
    source = self.write("term\treading\tfrequency\tkana_frequency\n読む\tよむ\t312\t19896\n読む\tヨム\t900\t20000\n")
    result = import_jpdb(source, db_path=self.db_path, now=self.clock)
    self.assertEqual(result["source_row_count"], 2)
    self.assertEqual(result["entry_count"], 1)
    with get_connection(self.db_path) as connection:
        row = connection.execute("SELECT rank, kana_rank FROM frequency WHERE source='jpdb'").fetchone()
    self.assertEqual((row["rank"], row["kana_rank"]), (312, 19896))

def test_malformed_reimport_preserves_live_source_and_user_words(self):
    import_jpdb(self.valid_source, db_path=self.db_path, now=self.clock)
    with get_connection(self.db_path) as connection:
        connection.execute("INSERT INTO user_words(word, reading, known) VALUES ('読む','よむ',1)")
        connection.commit()
    malformed = self.write("reading\tterm\tfrequency\tkana_frequency\nよむ\t読む\t1\t\n")
    with self.assertRaises(SourceFormatError) as error:
        import_jpdb(malformed, db_path=self.db_path, now=self.clock)
    self.assertEqual(error.exception.code, "source_format_error")
    with get_connection(self.db_path) as connection:
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM frequency WHERE source='jpdb'").fetchone()[0], 2)
        self.assertEqual(connection.execute("SELECT known FROM user_words").fetchone()[0], 1)
```

- [ ] **Step 2: Write failing BCCWJ aggregation and source-isolation tests**

```python
def test_bccwj_aggregates_and_assigns_sequential_rank(self):
    source = self.write_bccwj([
        self.bccwj_row("ヨム", "読む", 5, "0.5"),
        self.bccwj_row("ヨム", "読む", 7, "0.7"),
        self.bccwj_row("ミル", "見る", 12, "1.2"),
    ])
    result = import_bccwj(source, db_path=self.db_path, now=self.clock)
    self.assertEqual(result["source_row_count"], 3)
    self.assertEqual(result["entry_count"], 2)
    with get_connection(self.db_path) as connection:
        rows = connection.execute("SELECT word, reading, rank, frequency, frequency_per_million FROM frequency WHERE source='bccwj_luw' ORDER BY rank").fetchall()
    self.assertEqual([(r["word"], r["reading"], r["rank"]) for r in rows], [("見る", "みる", 1), ("読む", "よむ", 2)])

def test_replacing_bccwj_preserves_jpdb(self):
    import_jpdb(self.valid_jpdb, db_path=self.db_path, now=self.clock)
    import_bccwj(self.valid_bccwj, db_path=self.db_path, now=self.clock)
    with get_connection(self.db_path) as connection:
        self.assertGreater(connection.execute("SELECT COUNT(*) FROM frequency WHERE source='jpdb'").fetchone()[0], 0)
```

- [ ] **Step 3: Run importer tests and verify failures**

Run: `python -m unittest tests.test_importers -v`

Expected: FAIL because importers are missing.

- [ ] **Step 4: Implement streaming TEMP-table importers**

```python
def import_jpdb(path, *, db_path=None, version="2.2", now=None):
    initialize_database(db_path)
    digest = sha256_file(path)
    with get_connection(db_path) as connection:
        connection.execute("CREATE TEMP TABLE stage_frequency AS SELECT * FROM frequency WHERE 0")
        # Stream DictReader(delimiter="\t"), validate width/types, then upsert:
        connection.execute(
            """INSERT INTO stage_frequency(word,reading,source,rank,kana_rank)
               VALUES (?,?,'jpdb',?,?)
               ON CONFLICT(word,reading,source) DO UPDATE SET
                 rank=min(rank,excluded.rank),
                 kana_rank=CASE
                   WHEN kana_rank IS NULL THEN excluded.kana_rank
                   WHEN excluded.kana_rank IS NULL THEN kana_rank
                   ELSE min(kana_rank,excluded.kana_rank) END""",
            values,
        )
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("DELETE FROM frequency WHERE source='jpdb'")
        connection.execute("INSERT INTO frequency SELECT * FROM stage_frequency")
        _upsert_metadata(
            connection,
            source="jpdb",
            version=version,
            filename=Path(path).name,
            imported_at=_timestamp(now),
            source_row_count=source_rows,
            entry_count=entry_count,
            sha256=digest,
            notes="Duplicate term/reading senses collapsed to minimum ranks.",
        )
        connection.commit()
```

Create TEMP tables with explicit constraints because `CREATE TABLE AS` omits them. For BCCWJ, use an aggregate TEMP table keyed by word/reading, upsert summed values, then populate ranked TEMP output using `ROW_NUMBER() OVER (ORDER BY frequency DESC, word ASC, reading ASC)`. Accept ZIP only when it contains exactly expected TSV member; stream with `io.TextIOWrapper`. Detailed header comparison reports missing, unexpected, and reordered names. On all errors, close connection before translating exceptions so TEMP state disappears.

- [ ] **Step 5: Run importer tests**

Run: `python -m unittest tests.test_importers -v`

Expected: PASS, including rollback and metadata hash assertions.

- [ ] **Step 6: Commit importers**

```bash
git add japanese_frequency/importers.py scripts tests/test_importers.py tests/fixtures
git commit -m "feat(import): add atomic corpus importers"
```

### Task 3: Frequency Lookup And Commonness

**Files:**
- Create: `japanese_frequency/lookup.py`
- Create: `tests/test_lookup.py`
- Modify: `japanese_frequency/__init__.py`

**Interfaces:**
- Produces: `classify_jpdb_rank(rank: int) -> dict`
- Produces: `lookup_frequency(word: str, reading: str | None = None, *, db_path=None) -> dict`

- [ ] **Step 1: Write failing precise, ambiguous, unknown, and ordering tests**

```python
def test_word_only_lookup_orders_null_ranks_last(self):
    self.insert_frequency("語", "ご", "bccwj_luw", rank=1)
    self.insert_frequency("語", "かたり", "jpdb", rank=50)
    self.insert_frequency("語", "ことば", "jpdb", rank=10)
    result = lookup_frequency("語", db_path=self.db_path)
    self.assertEqual([match["reading"] for match in result["matches"]], ["ことば", "かたり", "ご"])

def test_precise_result_omits_missing_kana_rank(self):
    result = lookup_frequency("読む", "よむ", db_path=self.db_path)
    self.assertTrue(result["found"])
    self.assertNotIn("kana_rank", result["frequency"]["jpdb"])

def test_unknown_word_returns_found_false(self):
    self.assertEqual(lookup_frequency("不存在", db_path=self.db_path), {"found": False, "word": "不存在", "matches": []})
```

- [ ] **Step 2: Run lookup tests and verify failure**

Run: `python -m unittest tests.test_lookup -v`

Expected: FAIL because lookup module is missing.

- [ ] **Step 3: Implement grouped lookup and heuristic classification**

```python
def classify_jpdb_rank(rank):
    if not isinstance(rank, int) or isinstance(rank, bool) or rank < 1:
        raise InvalidInputError("rank must be a positive integer")
    for maximum, category in config.JPDB_COMMONNESS_THRESHOLDS:
        if rank <= maximum:
            return {"rank": rank, "category": category}
    return {"rank": rank, "category": "very_rare"}

ORDER_SQL = """
ORDER BY (jpdb_rank IS NULL), jpdb_rank,
         (bccwj_rank IS NULL), bccwj_rank,
         reading
"""
```

Use conditional aggregation to produce one row per reading. Shape precise output as `{found, word, reading, frequency}` and ambiguous output as `{found, word, matches}`. Add commonness only to JPDB records and omit nullable source keys rather than returning null.

- [ ] **Step 4: Run lookup tests**

Run: `python -m unittest tests.test_lookup -v`

Expected: PASS.

- [ ] **Step 5: Commit lookup API**

```bash
git add japanese_frequency/lookup.py japanese_frequency/__init__.py tests/test_lookup.py
git commit -m "feat(lookup): add ranked frequency queries"
```

### Task 4: Personal State And Profiles

**Files:**
- Create: `japanese_frequency/user_words.py`
- Create: `tests/test_user_words.py`
- Modify: `japanese_frequency/__init__.py`

**Interfaces:**
- Produces: `record_encounter(word, reading=None, *, db_path=None, now=None) -> dict`
- Produces: `mark_known(word, reading=None, known=True, *, db_path=None) -> dict`
- Produces: `set_in_anki(word, reading=None, in_anki=True, *, db_path=None) -> dict`
- Produces: `get_word_profile(word, reading=None, *, db_path=None) -> dict`

- [ ] **Step 1: Write failing mutation resolution, timestamp, and persistence tests**

```python
def test_omitted_reading_resolves_single_corpus_identity(self):
    self.insert_frequency("読む", "よむ", "jpdb", 312)
    result = record_encounter("読む", db_path=self.db_path, now=self.clock)
    self.assertEqual(result["reading"], "よむ")
    self.assertEqual(result["user"]["encounter_count"], 1)
    self.assertEqual(result["user"]["first_seen"], "2026-08-25T02:55:00Z")

def test_every_ambiguous_mutation_leaves_table_unchanged(self):
    self.insert_frequency("開く", "あく", "jpdb", 1)
    self.insert_frequency("開く", "ひらく", "jpdb", 2)
    before = self.dump_user_words()
    calls = (lambda: record_encounter("開く", db_path=self.db_path, now=self.clock), lambda: mark_known("開く", db_path=self.db_path), lambda: set_in_anki("開く", db_path=self.db_path))
    for call in calls:
        with self.assertRaises(AmbiguousReadingError) as error:
            call()
        self.assertEqual(error.exception.code, "ambiguous_reading")
        self.assertEqual(self.dump_user_words(), before)

def test_no_corpus_entry_uses_empty_reading(self):
    result = mark_known("造語", db_path=self.db_path)
    self.assertEqual(result["reading"], "")
```

- [ ] **Step 2: Write failing profile tests**

```python
def test_profile_combines_frequency_and_user_state(self):
    self.insert_frequency("読む", "よむ", "jpdb", 312)
    record_encounter("読む", "よむ", db_path=self.db_path, now=self.clock)
    profile = get_word_profile("読む", "よむ", db_path=self.db_path)
    self.assertEqual(profile["frequency"]["jpdb"]["rank"], 312)
    self.assertEqual(profile["user"]["encounter_count"], 1)

def test_word_only_profile_returns_all_corpus_and_user_identities(self):
    result = get_word_profile("開く", db_path=self.db_path)
    self.assertIn("matches", result)
    self.assertGreaterEqual(len(result["matches"]), 2)
```

- [ ] **Step 3: Run state tests and verify failure**

Run: `python -m unittest tests.test_user_words -v`

Expected: FAIL because user state module is missing.

- [ ] **Step 4: Implement reading resolution, atomic upserts, and profiles**

```python
def _resolve_mutation_reading(connection, word, reading):
    if reading is not None:
        return normalize_reading(reading)
    rows = connection.execute("SELECT DISTINCT reading FROM frequency WHERE word=? ORDER BY reading", (word,)).fetchall()
    if len(rows) == 1:
        return rows[0]["reading"]
    if len(rows) > 1:
        raise AmbiguousReadingError("multiple corpus readings", details={"readings": [row["reading"] for row in rows]})
    return ""

def _format_timestamp(now):
    value = (now or datetime.now(timezone.utc))()
    value = value.astimezone(timezone.utc)
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")
```

Resolve ambiguity before issuing any INSERT/UPDATE. Use SQLite UPSERT statements that update only requested fields. Catch lock errors, roll back, and raise `DatabaseBusyError`. Profile word-only query forms union of corpus and user identities, then calls shared row-formatting logic without arbitrary selection.

- [ ] **Step 5: Run state tests**

Run: `python -m unittest tests.test_user_words -v`

Expected: PASS, including toggles and persistence across test reimports.

- [ ] **Step 6: Commit state API**

```bash
git add japanese_frequency/user_words.py japanese_frequency/__init__.py tests/test_user_words.py
git commit -m "feat(state): add vocabulary tracking and profiles"
```

### Task 5: Agent Tool Wrappers And JSON CLI

**Files:**
- Create: `japanese_frequency/tools.py`
- Create: `japanese_frequency/__main__.py`
- Create: `tests/test_tools.py`
- Create: `tests/test_cli.py`

**Interfaces:**
- Produces five tool functions named in design spec, each returning stable envelope
- Produces: `python -m japanese_frequency {lookup,profile,encounter,known,anki}`

- [ ] **Step 1: Write failing envelope and stable-error tests**

```python
def test_tool_success_uses_ok_result_envelope(self):
    response = lookup_japanese_frequency("不存在", db_path=self.db_path)
    self.assertEqual(set(response), {"ok", "result"})
    self.assertTrue(response["ok"])

def test_tool_ambiguity_uses_stable_error_code(self):
    response = mark_japanese_word_known("開く", db_path=self.db_path)
    self.assertEqual(response["ok"], False)
    self.assertEqual(response["error"]["type"], "ambiguous_reading")
    self.assertIn("message", response["error"])
```

- [ ] **Step 2: Write failing CLI tests**

```python
def test_lookup_cli_outputs_unicode_json(self):
    result = subprocess.run([sys.executable, "-m", "japanese_frequency", "--db", str(self.db_path), "lookup", "読む", "よむ"], text=True, encoding="utf-8", capture_output=True)
    self.assertEqual(result.returncode, 0)
    payload = json.loads(result.stdout)
    self.assertEqual(payload["word"], "読む")

def test_ambiguous_cli_exits_nonzero_with_error_code(self):
    result = self.run_cli("known", "開く")
    self.assertNotEqual(result.returncode, 0)
    self.assertEqual(json.loads(result.stdout)["error"]["type"], "ambiguous_reading")
```

- [ ] **Step 3: Run interface tests and verify failure**

Run: `python -m unittest tests.test_tools tests.test_cli -v`

Expected: FAIL because tools and CLI are missing.

- [ ] **Step 4: Implement wrapper translator and argparse CLI**

```python
def _tool_call(function, *args, **kwargs):
    try:
        return {"ok": True, "result": function(*args, **kwargs)}
    except JapaneseFrequencyError as error:
        return {"ok": False, "error": error.to_dict()}
    except sqlite3.Error:
        return {"ok": False, "error": {"type": "database_error", "message": "database operation failed"}}

def lookup_japanese_frequency(word, reading=None, *, db_path=None):
    return _tool_call(lookup_frequency, word, reading, db_path=db_path)
```

Implement all five wrappers. CLI calls direct domain APIs, prints `json.dumps(payload, ensure_ascii=False, indent=2)`, supports optional reading positional argument, global `--db`, and `--value true|false` for known/Anki commands. Errors use an `error` object containing `type` and `message`, plus nonzero status.

- [ ] **Step 5: Run interface tests**

Run: `python -m unittest tests.test_tools tests.test_cli -v`

Expected: PASS.

- [ ] **Step 6: Commit interfaces**

```bash
git add japanese_frequency/tools.py japanese_frequency/__main__.py tests/test_tools.py tests/test_cli.py
git commit -m "feat(api): add agent tools and JSON CLI"
```

### Task 6: Atomic Setup, Downloads, Documentation, And Lock Safety

**Files:**
- Create: `setup_database.py`
- Create: `japanese_frequency/setup.py`
- Create: `tests/test_setup.py`
- Create: `README.md`
- Create: `requirements.txt`
- Create: `.gitignore`

**Interfaces:**
- Produces: `download_source(url, destination, *, expected_sha256=None, opener=None) -> Path`
- Produces: `setup_database(*, db_path=None, jpdb_source=None, with_bccwj=False, bccwj_source=None, now=None) -> dict`
- Produces setup CLI with `--jpdb-source`, `--with-bccwj`, `--bccwj-source`, and `--db`

- [ ] **Step 1: Write failing atomic-download and setup tests**

```python
def test_download_renames_part_only_after_success(self):
    destination = self.root / "source.tsv"
    result = download_source("https://example.invalid/source", destination, opener=self.fake_opener(b"data"))
    self.assertEqual(result, destination)
    self.assertEqual(destination.read_bytes(), b"data")
    self.assertFalse(destination.with_suffix(destination.suffix + ".part").exists())

def test_failed_download_leaves_no_valid_file(self):
    destination = self.root / "source.tsv"
    with self.assertRaises(DownloadError) as error:
        download_source("https://example.invalid/source", destination, opener=self.failing_opener)
    self.assertEqual(error.exception.code, "download_error")
    self.assertFalse(destination.exists())

def test_setup_reports_counts_size_and_integrity(self):
    report = setup_database(db_path=self.db_path, jpdb_source=self.valid_jpdb, now=self.clock)
    self.assertEqual(report["integrity_check"], "ok")
    self.assertGreater(report["jpdb_entries"], 0)
    self.assertGreater(report["database_size_bytes"], 0)
```

- [ ] **Step 2: Write failing multi-connection busy-timeout test**

```python
def test_locked_mutation_times_out_without_partial_write(self):
    first = get_connection(self.db_path)
    first.execute("BEGIN IMMEDIATE")
    before = self.dump_user_words()
    try:
        with self.assertRaises(DatabaseBusyError) as error:
            mark_known("読む", "よむ", db_path=self.db_path)
        self.assertEqual(error.exception.code, "database_busy")
        self.assertEqual(self.dump_user_words(connection=first), before)
    finally:
        first.rollback()
        first.close()
```

- [ ] **Step 3: Run setup and lock tests and verify failure**

Run: `python -m unittest tests.test_setup tests.test_database.DatabaseTests.test_locked_mutation_times_out_without_partial_write -v`

Expected: FAIL because setup functions are missing or lock errors are untranslated.

- [ ] **Step 4: Implement atomic downloads and setup orchestration**

```python
def download_source(url, destination, *, expected_sha256=None, opener=None):
    destination = Path(destination)
    part = destination.with_suffix(destination.suffix + ".part")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with (opener or urllib.request.urlopen)(url) as response, part.open("wb") as output:
            shutil.copyfileobj(response, output)
        actual = sha256_file(part)
        if expected_sha256 and actual.lower() != expected_sha256.lower():
            raise DownloadError("download checksum mismatch")
        part.replace(destination)
        return destination
    except Exception as error:
        part.unlink(missing_ok=True)
        if isinstance(error, DownloadError):
            raise
        raise DownloadError("source download failed") from error
```

Setup treats JPDB as mandatory. BCCWJ imports only when requested and any requested BCCWJ failure exits nonzero. Report source entry counts from metadata, resolved path, file size, and explicit integrity result.

- [ ] **Step 5: Write README, empty requirements, and data ignores**

README must include exact pinned URLs, source attribution, BCCWJ manual wording/links, non-redistribution handling, setup commands, API examples, tool envelope examples, CLI, safe updates, backups, duplicate JPDB sense collapse, BCCWJ aggregation/ranking, and offline guarantees. `.gitignore` includes `data/sources/*`, `data/*.db*`, `*.part`, `__pycache__/`, and integration outputs. Keep `data/sources/.gitkeep` trackable if created.

- [ ] **Step 6: Run setup and lock tests**

Run: `python -m unittest tests.test_setup tests.test_database -v`

Expected: PASS with lock test taking approximately configured timeout and no partial row.

- [ ] **Step 7: Commit setup and docs**

```bash
git add setup_database.py japanese_frequency/setup.py tests/test_setup.py README.md requirements.txt .gitignore
git commit -m "feat(setup): add atomic source initialization"
```

### Task 7: Offline And Real-Source Verification

**Files:**
- Create: `tests/test_real_sources.py`
- Modify: `README.md`

**Interfaces:**
- Consumes all prior public APIs and local downloaded source paths
- Produces opt-in environment-driven smoke tests only

- [ ] **Step 1: Add opt-in real-source tests**

```python
@unittest.skipUnless(os.environ.get("JPDB_SOURCE"), "set JPDB_SOURCE for real-source smoke test")
def test_real_jpdb_import_and_lookup(self):
    import_jpdb(os.environ["JPDB_SOURCE"], db_path=self.db_path)
    result = lookup_frequency("読む", "よむ", db_path=self.db_path)
    self.assertTrue(result["found"])
    self.assertIsInstance(result["frequency"]["jpdb"]["rank"], int)

@unittest.skipUnless(os.environ.get("BCCWJ_SOURCE"), "set BCCWJ_SOURCE for real-source smoke test")
def test_real_bccwj_import_and_lookup(self):
    import_bccwj(os.environ["BCCWJ_SOURCE"], db_path=self.db_path)
    self.assertTrue(lookup_frequency("読む", "よむ", db_path=self.db_path)["found"])
```

- [ ] **Step 2: Run complete default offline suite**

Run: `python -m unittest discover -v`

Expected: PASS; two real-source tests SKIPPED unless environment variables are set; no network requests.

- [ ] **Step 3: Run pinned JPDB smoke test**

PowerShell:

```powershell
$env:JPDB_SOURCE='C:\Users\diazh\AppData\Local\Temp\opencode\jpdb_v2.2.csv'
python -m unittest tests.test_real_sources.RealSourceTests.test_real_jpdb_import_and_lookup -v
```

Expected: PASS without hard-coded rank.

- [ ] **Step 4: Run pinned BCCWJ smoke test**

PowerShell:

```powershell
$env:BCCWJ_SOURCE='C:\Users\diazh\AppData\Local\Temp\opencode\bccwj_luw.zip'
python -m unittest tests.test_real_sources.RealSourceTests.test_real_bccwj_import_and_lookup -v
```

Expected: PASS; import processes all 2,434,619 source rows.

- [ ] **Step 5: Run functional CLI checks**

```powershell
python setup_database.py --jpdb-source "$env:JPDB_SOURCE"
python -m japanese_frequency lookup 読む よむ
python -m japanese_frequency encounter 読む よむ
python -m japanese_frequency known 読む よむ --value true
python -m japanese_frequency anki 読む よむ --value true
python -m japanese_frequency profile 読む よむ
```

Expected: each command outputs valid JSON; profile contains local frequency, commonness, known/Anki state, encounter count, and UTC timestamps.

- [ ] **Step 6: Inspect final status and diff**

Run: `git status --short`, `git diff --check`, and `git log --oneline -10`.

Expected: no unintended files, no whitespace errors, commits match tasks.

- [ ] **Step 7: Commit smoke tests**

```bash
git add tests/test_real_sources.py README.md
git commit -m "test: add opt-in corpus smoke coverage"
```
