# Migaku Media Mining Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import replaceable Migaku spelling knowledge and media vocabulary, then produce explainable offline `mine`, `review`, and `skip` recommendations from media, corpus, and personal evidence.

**Architecture:** Extend existing SQLite schema through transactional migration. New `knowledge.py`, `media.py`, and `mining.py` modules own spelling snapshots, media ingestion, and recommendation policy. Existing profiles, agent wrappers, CLI, setup, and documentation gain compatible interfaces while preserving source-scoped atomicity and local privacy.

**Tech Stack:** Python 3.11+, standard-library `sqlite3`, `csv`, `hashlib`, `json`, `argparse`, and `unittest`; no third-party runtime dependencies.

## Global Constraints

- Word spellings use NFC plus trim only; readings also canonicalize standard katakana to hiragana.
- Migaku knowledge is spelling-level and never implies exact reading/sense knowledge.
- Exact `known` and `in_anki` states are tri-state: true, false, or null.
- Media rows without usable readings remain spelling-level; never invent `reading=""` identities.
- Same-stem CSV supersedes requested TXT and both requested/selected paths are recorded.
- `media_sources.source_key` is stable/unique; display names may collide; reimport keeps source ID and never uses `INSERT OR REPLACE`.
- Bulk results deduplicate spelling and exact evidence, prefer richer exact identities, retain reason codes/components, and order NULL ranks explicitly last.
- Numeric score is a ranking heuristic, never a probability or linguistic truth.
- Omitted-reading recommendation resolves exactly one identity or returns sorted ambiguity matches.
- Frequency is supporting evidence; no frequency threshold alone forces irreversible skip.
- All imports fully stage before short live transactions; failures preserve previous snapshots and durable personal/corpus state.
- Every successful agent tool response has exactly `ok` and `result`; expected failures have exactly `ok` and `error`, with `error.type` and `error.message`.
- Normal operation and default tests are offline; personal exports, reports, database, notes, and states remain Git-ignored.
- Root `AGENTS.md` must let future agents run the complete workflow safely without prior conversation context.

---

### Task 1: Schema Migration And Tri-State Profiles

**Files:**
- Modify: `japanese_frequency/database.py`
- Modify: `japanese_frequency/user_words.py`
- Modify: `japanese_frequency/errors.py`
- Create: `japanese_frequency/timestamps.py`
- Modify: `tests/test_database.py`
- Modify: `tests/test_user_words.py`

**Interfaces:**
- Produces schema versioning and idempotent migration in `initialize_database(db_path=None)`.
- Produces nullable `user.known` and `user.in_anki` profile fields.
- Produces profile fields `known_spelling`, `known_spelling_sources`, and `known_identity`.
- Produces `SourceNotFoundError(code="source_not_found")` and `MediaNotFoundError(code="media_not_found")`.
- Produces `format_utc_timestamp(now=None) -> str` for all snapshot metadata and encounters.

- [ ] **Step 1: Write failing migration and schema tests**

```python
def test_existing_boolean_user_state_migrates_conservatively(self):
    self.create_legacy_database()
    with sqlite3.connect(self.db_path) as connection:
        connection.execute(
            "INSERT INTO user_words VALUES (?,?,?,?,?,?,?,?)",
            ("既知", "きち", 1, 1, 4, "first", "last", "note",),
        )
        connection.execute(
            "INSERT INTO user_words VALUES (?,?,?,?,?,?,?,?)",
            ("不明", "ふめい", 0, 0, 2, "first2", "last2", "note2",),
        )
    initialize_database(self.db_path)
    with get_connection(self.db_path) as connection:
        rows = connection.execute(
            "SELECT word, known, in_anki, encounter_count, notes FROM user_words ORDER BY word"
        ).fetchall()
    self.assertEqual(tuple(rows[0]), ("不明", None, None, 2, "note2"))
    self.assertEqual(tuple(rows[1]), ("既知", 1, 1, 4, "note"))

def test_new_schema_contains_knowledge_and_media_tables(self):
    initialize_database(self.db_path)
    with get_connection(self.db_path) as connection:
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
    self.assertTrue({
        "known_spellings", "personal_source_metadata", "media_sources",
        "media_words", "media_spellings"
    } <= tables)
```

Build the legacy fixture using the exact current schema before calling the new initializer. Assert migration idempotency by running initialization twice.

- [ ] **Step 2: Write failing tri-state profile/mutation tests**

```python
def test_encounter_defaults_identity_and_anki_states_to_null(self):
    result = record_encounter("読む", "よむ", db_path=self.db_path, now=self.clock)
    self.assertIsNone(result["user"]["known"])
    self.assertIsNone(result["user"]["in_anki"])

def test_explicit_false_states_remain_false(self):
    self.assertFalse(mark_known("読む", "よむ", False, db_path=self.db_path)["user"]["known"])
    self.assertFalse(set_in_anki("読む", "よむ", False, db_path=self.db_path)["user"]["in_anki"])

def test_profile_distinguishes_spelling_and_identity_knowledge(self):
    self.insert_known_spelling("開く", "migaku")
    result = get_word_profile("開く", "あく", db_path=self.db_path)
    self.assertTrue(result["known_spelling"])
    self.assertEqual(result["known_spelling_sources"], ["migaku"])
    self.assertIsNone(result["known_identity"])
```

- [ ] **Step 3: Run focused tests and verify failure**

Run: `python -m unittest tests.test_database tests.test_user_words -v`

Expected: FAIL because schema is not migrated and booleans are not nullable.

- [ ] **Step 4: Implement versioned transactional migration**

Add `PRAGMA user_version` with current version `2`. For version zero, detect whether `user_words` exists and whether `known` is nullable via `PRAGMA table_info`. Create all current/new tables. When legacy table exists:

```sql
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
```

Run migration in one explicit transaction, restore indexes, set `user_version=2`, and preserve primary exceptions if rollback fails.

Create exact tables/indexes from design spec. Add new typed errors. Move existing
timestamp formatting into `timestamps.format_utc_timestamp`, preserving injected
clock and UTC `Z` behavior, and make `user_words` consume it. Update
`_user_from_row` to preserve NULL rather than `bool(None)`, and add spelling
provenance query inside the existing profile read transaction.

- [ ] **Step 5: Run focused and full tests**

Run: `python -m unittest tests.test_database tests.test_user_words -v`

Expected: PASS.

Run: `python -m unittest discover -v`

Expected: PASS with only opt-in real-source skips.

- [ ] **Step 6: Commit migration**

```bash
git add japanese_frequency/database.py japanese_frequency/user_words.py japanese_frequency/errors.py japanese_frequency/timestamps.py tests/test_database.py tests/test_user_words.py
git commit -m "feat(schema): add mining state and tri-state migration"
```

### Task 2: Migaku Spelling Snapshot Import

**Files:**
- Create: `japanese_frequency/knowledge.py`
- Create: `japanese_frequency/source_files.py`
- Modify: `japanese_frequency/importers.py`
- Create: `tests/test_knowledge.py`
- Create: `tests/fixtures/migaku_known.txt`
- Modify: `japanese_frequency/__init__.py`

**Interfaces:**
- Consumes: `get_connection`, `initialize_database`, `normalize_word`, `format_utc_timestamp`.
- Produces: `import_migaku_known_words(path, *, db_path=None, now=None) -> dict`.
- Produces: `get_known_spelling(word, *, connection=None, db_path=None) -> dict`.
- Produces reusable `snapshot_source(path)` context manager yielding immutable path and SHA-256.

- [ ] **Step 1: Write failing snapshot tests**

```python
def test_import_normalizes_deduplicates_and_records_metadata(self):
    source = self.write_bytes("読む\n 読む \nヨム\n".encode("utf-8-sig"))
    result = import_migaku_known_words(source, db_path=self.db_path, now=self.clock)
    self.assertEqual(result["source_row_count"], 3)
    self.assertEqual(result["entry_count"], 2)
    self.assertEqual(result["source"], "migaku")
    self.assertRegex(result["sha256"], r"^[0-9a-f]{64}$")

def test_reimport_replaces_only_migaku_snapshot(self):
    import_migaku_known_words(self.write("読む\n古い\n"), db_path=self.db_path, now=self.clock)
    self.seed_identity_and_other_spelling_source()
    import_migaku_known_words(self.write("読む\n新しい\n"), db_path=self.db_path, now=self.clock)
    self.assertEqual(self.migaku_words(), ["新しい", "読む"])
    self.assertEqual(self.other_source_words(), ["古い"])
    self.assertEqual(self.identity_snapshot(), self.expected_identity_snapshot)

def test_malformed_snapshot_preserves_previous_snapshot(self):
    import_migaku_known_words(self.valid_source, db_path=self.db_path, now=self.clock)
    before = self.full_state_snapshot()
    with self.assertRaises(SourceFormatError) as error:
        import_migaku_known_words(self.write("読む\n\n語\n"), db_path=self.db_path)
    self.assertEqual(error.exception.code, "source_format_error")
    self.assertEqual(self.full_state_snapshot(), before)
```

Also cover missing path, invalid UTF-8, whitespace-only lines, source I/O failure, busy lock, exact byte hash, BOM, and rollback failure preserving primary error.

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m unittest tests.test_knowledge -v`

Expected: FAIL because `knowledge.py` does not exist.

- [ ] **Step 3: Implement streamed immutable snapshot and TEMP staging**

Move the existing tested importer snapshot logic into `source_files.snapshot_source`
and update JPDB/BCCWJ importers to consume it without behavior changes. Preserve
typed I/O failures, best-effort cleanup, and primary-error precedence. TEMP schema:

```sql
CREATE TEMP TABLE stage_known_spellings (
    word TEXT PRIMARY KEY
);
```

Count nonblank source records before deduplication. Reject blank records with source line. After complete staging and validation:

```sql
BEGIN IMMEDIATE;
DELETE FROM known_spellings WHERE source = 'migaku';
INSERT INTO known_spellings(word, source)
SELECT word, 'migaku' FROM stage_known_spellings;
INSERT INTO personal_source_metadata(
    source, filename, imported_at, source_row_count,
    entry_count, sha256, notes
)
VALUES ('migaku', ?, ?, ?, ?, ?, ?)
ON CONFLICT(source) DO UPDATE SET
    filename = excluded.filename,
    imported_at = excluded.imported_at,
    source_row_count = excluded.source_row_count,
    entry_count = excluded.entry_count,
    sha256 = excluded.sha256,
    notes = excluded.notes;
COMMIT;
```

Return source, filename, imported timestamp, raw row count, entry count, SHA-256, and notes. Preserve original errors through cleanup/rollback.

- [ ] **Step 4: Run knowledge and regression tests**

Run: `python -m unittest tests.test_knowledge tests.test_user_words tests.test_importers -v`

Expected: PASS.

- [ ] **Step 5: Commit Migaku importer**

```bash
git add japanese_frequency/knowledge.py japanese_frequency/source_files.py japanese_frequency/importers.py japanese_frequency/__init__.py tests/test_knowledge.py tests/fixtures/migaku_known.txt
git commit -m "feat(knowledge): import Migaku spelling snapshots"
```

### Task 3: Media Source Selection And Atomic Import

**Files:**
- Create: `japanese_frequency/media.py`
- Create: `tests/test_media.py`
- Create: `tests/fixtures/media_words.txt`
- Create: `tests/fixtures/media_words.csv`
- Modify: `japanese_frequency/__init__.py`

**Interfaces:**
- Produces: `import_media_vocabulary(path, source_key, display_name=None, *, db_path=None, now=None) -> dict`.
- Produces: `get_media_source(source_key, *, db_path=None, connection=None) -> dict`.
- Produces: `iter_media_candidates(connection, media_id) -> list[dict]` for mining task.

- [ ] **Step 1: Write failing TXT/CSV selection tests**

```python
def test_requested_txt_prefers_same_stem_csv(self):
    txt = self.write("Volume 1.txt", "読む\n語\n")
    csv_path = self.write_csv("Volume 1.csv", self.rich_rows)
    result = import_media_vocabulary(
        txt, "series-v1", "Volume 1", db_path=self.db_path, now=self.clock
    )
    self.assertEqual(result["requested_filename"], "Volume 1.txt")
    self.assertEqual(result["selected_filename"], "Volume 1.csv")
    self.assertEqual(result["format"], "csv")
    self.assertIn("superseded", result["notes"])

def test_unordered_txt_creates_spelling_membership_without_rank(self):
    result = import_media_vocabulary(
        self.write("words.txt", "読む\n語\n読む\n"),
        "plain-list", db_path=self.db_path
    )
    self.assertEqual(result["entry_count"], 2)
    rows = self.media_spellings("plain-list")
    self.assertEqual([(r["word"], r["occurrences"], r["media_rank"]) for r in rows], [("語", None, None), ("読む", None, None)])
    self.assertEqual(self.media_words("plain-list"), [])
```

- [ ] **Step 2: Write failing CSV aggregation/rank tests**

```python
def test_csv_uses_readings_and_keeps_missing_reading_spelling_level(self):
    source = self.write_csv("media.csv", [
        row("読む", "よむ", 5, "read", "例1", "1"),
        row("読む", "ヨム", 2, "read later", "例2", "1"),
        row("固有名", "", 8, "name", "例3", "2"),
        row("見る", "みる", 8, "see", "例4", "3"),
    ])
    import_media_vocabulary(source, "media", db_path=self.db_path)
    exact = self.media_words("media")
    spelling = self.media_spellings("media")
    self.assertEqual(self.find(exact, "読む", "よむ")["occurrences"], 7)
    self.assertEqual(self.find(exact, "読む", "よむ")["definitions"], "read")
    self.assertEqual(self.find(exact, "見る", "みる")["media_rank"], 2)
    self.assertEqual(self.find(spelling, "固有名")["media_rank"], 1)
```

Ranks are combined across exact and spelling staging rows by occurrences DESC,
word ASC, reading ASC where spelling-only sorting uses empty sort key internally
without storing an empty identity.

- [ ] **Step 3: Write failing stable-key/reimport safety tests**

```python
def test_duplicate_display_names_allowed_and_source_key_stable(self):
    first = import_media_vocabulary(self.csv_a, "series-a-v1", "Volume 1", db_path=self.db_path)
    second = import_media_vocabulary(self.csv_b, "series-b-v1", "Volume 1", db_path=self.db_path)
    self.assertNotEqual(first["id"], second["id"])
    updated = import_media_vocabulary(self.csv_c, "series-a-v1", "Renamed", db_path=self.db_path)
    self.assertEqual(updated["id"], first["id"])
    self.assertEqual(updated["display_name"], "Renamed")

def test_failed_reimport_preserves_all_live_state(self):
    import_media_vocabulary(self.valid_csv, "media", db_path=self.db_path)
    before = self.full_state_snapshot()
    with self.assertRaises(SourceFormatError):
        import_media_vocabulary(self.bad_csv, "media", db_path=self.db_path)
    self.assertEqual(self.full_state_snapshot(), before)
```

Cover named required columns, additional columns, bad occurrence values, invalid
reading encoding, duplicate handling, source key validation, path errors, busy
locks, stable media ID, child foreign keys, and other-source isolation.

- [ ] **Step 4: Run tests and verify failure**

Run: `python -m unittest tests.test_media -v`

Expected: FAIL because media module does not exist.

- [ ] **Step 5: Implement media parser and atomic source update**

Use one immutable selected-file snapshot and hash. CSV required headers are exact
names `Word`, `ReadingKana`, and `Occurences`; optional retained headers are
`Definitions`, `ExampleSentence`, and `JmDictWordId`.

Stage exact and spelling rows in separate constrained TEMP tables. Aggregate with
UPSERT sums and conditional first-nonempty field updates based on first source row
ordinal. Build a combined TEMP ranking table using:

```sql
ROW_NUMBER() OVER (
    ORDER BY occurrences DESC, word ASC, reading_sort ASC
)
```

Publish by selecting media ID, updating or inserting parent without REPLACE,
deleting old children, inserting staged rows, and updating metadata in one
`BEGIN IMMEDIATE` transaction. Return full parent metadata and child counts.

- [ ] **Step 6: Run media and full tests**

Run: `python -m unittest tests.test_media -v`

Expected: PASS.

Run: `python -m unittest discover -v`

Expected: PASS with opt-in skips only.

- [ ] **Step 7: Commit media import**

```bash
git add japanese_frequency/media.py japanese_frequency/__init__.py tests/test_media.py tests/fixtures/media_words.txt tests/fixtures/media_words.csv
git commit -m "feat(media): import stable media vocabulary sources"
```

### Task 4: Mining Evidence, Scores, Tiers, And Deduplication

**Files:**
- Create: `japanese_frequency/mining.py`
- Create: `tests/test_mining.py`
- Modify: `config.py`

**Interfaces:**
- Consumes media iterators, frequency schema, known spellings, and tri-state user rows.
- Produces: `analyze_media(source_key, *, limit=None, db_path=None) -> dict`.
- Produces internal `_score_candidate(candidate, context=None) -> dict`.

- [ ] **Step 1: Define exact configurable scoring constants**

Add immutable config tuples/dicts:

```python
MINING_SCORE = {
    "known_spelling": -2,
    "known_identity_false": 3,
    "media_occurrences_10": 4,
    "media_occurrences_5": 3,
    "media_occurrences_2": 2,
    "media_occurrences_1": 1,
    "media_rank_100": 2,
    "media_rank_500": 1,
    "jpdb_rank_3000": 3,
    "jpdb_rank_10000": 2,
    "jpdb_rank_20000": 1,
    "jpdb_rank_70000": -1,
    "jpdb_rank_over_70000": -2,
    "bccwj_rank_3000": 2,
    "bccwj_rank_10000": 1,
    "bccwj_rank_over_50000": -1,
    "encounters_3": 2,
    "encounters_1": 1,
}
MINING_MINE_SCORE = 5
CONTEXT_SCORE = {
    "failed_recall": 6,
    "personally_useful": 3,
    "successful_inference": -2,
    "transparent_composition": -2,
}
```

Components use the single highest matching value in each category, not cumulative
threshold stacking. Hard skip rules are represented as reasons, not `-100` score.

- [ ] **Step 2: Write failing evidence and deduplication tests**

```python
def test_exact_media_identity_suppresses_redundant_spelling_candidate(self):
    self.seed_exact_and_overlapping_spelling("読む", "よむ", occurrences=5)
    result = analyze_media("media", db_path=self.db_path)
    candidates = self.all_candidates(result)
    self.assertEqual([(c["word"], c["reading"]) for c in candidates], [("読む", "よむ")])
    self.assertEqual(candidates[0]["media"]["occurrences"], 5)

def test_spelling_only_candidate_keeps_nullable_identity_evidence(self):
    self.seed_media_spelling("固有名")
    candidate = self.one_candidate(analyze_media("media", db_path=self.db_path))
    self.assertEqual(candidate["identity_type"], "spelling")
    self.assertIsNone(candidate["reading"])
    self.assertIsNone(candidate["personal"]["known_identity"])
    self.assertIsNone(candidate["personal"]["in_anki"])
```

- [ ] **Step 3: Write failing score/tier/order tests**

```python
def test_known_identity_and_anki_are_hard_skip_reasons(self):
    known = self.analyze_seed(known=True, in_anki=None, occurrences=20)
    anki = self.analyze_seed(word="語", known=False, in_anki=True, occurrences=20)
    self.assertEqual(known["tier"], "skip")
    self.assertIn("known_identity", known["reasons"])
    self.assertEqual(anki["tier"], "skip")
    self.assertIn("already_in_anki", anki["reasons"])

def test_known_spelling_never_becomes_skip_by_itself(self):
    candidate = self.analyze_seed(known_spelling=True, known=None, occurrences=1, jpdb_rank=90000)
    self.assertEqual(candidate["tier"], "review")

def test_score_is_explainable_ranking_heuristic(self):
    candidate = self.analyze_seed(known=False, occurrences=5, media_rank=20, jpdb_rank=5000, bccwj_rank=9000, encounters=3)
    self.assertEqual(candidate["score_kind"], "ranking_heuristic")
    self.assertEqual(candidate["score"], 3 + 2 + 2 + 1 + 2 + 3)
    self.assertEqual(candidate["tier"], "mine")
    self.assertIn("media_occurrences_5", candidate["score_components"])
```

Also assert explicit NULL-last order, deterministic word/reading ties, limit after
global ranking, media/source summary counts, missing source typed `media_not_found`,
and raw frequency values retained.

- [ ] **Step 4: Run tests and verify failure**

Run: `python -m unittest tests.test_mining -v`

Expected: FAIL because mining module does not exist.

- [ ] **Step 5: Implement one-snapshot evidence query and scoring**

Use one connection and explicit read transaction. Gather media rows, known spelling
sources, exact user states, and frequency sources without N+1 connections. For a
spelling row, inspect available media/corpus exact identities:

- If exact rows exist, merge spelling-only evidence into each exact candidate and
  suppress duplicate spelling output.
- If no exact row exists, emit one spelling candidate.

Tier rules:

```python
if known_identity is True:
    tier = "skip"
elif in_anki is True:
    tier = "skip"
elif score >= config.MINING_MINE_SCORE:
    tier = "mine"
else:
    tier = "review"
```

Spelling knowledge contributes a negative component but never creates skip.
Return grouped lists, deterministic summaries, score components as name-to-points,
and sorted reason codes.

- [ ] **Step 6: Run mining and regression tests**

Run: `python -m unittest tests.test_mining tests.test_lookup tests.test_user_words -v`

Expected: PASS.

- [ ] **Step 7: Commit mining analysis**

```bash
git add config.py japanese_frequency/mining.py tests/test_mining.py
git commit -m "feat(mining): rank explainable media candidates"
```

### Task 5: Contextual Recommendation And CSV Reports

**Files:**
- Modify: `japanese_frequency/mining.py`
- Create: `tests/test_recommendation.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `recommend_media_word(source_key, word, reading=None, *, failed_recall=False, successful_inference=False, transparent_composition=False, personally_useful=False, db_path=None) -> dict`.
- Produces: `export_media_analysis_csv(analysis, output_path) -> dict`.

- [ ] **Step 1: Write failing identity-resolution tests**

```python
def test_omitted_reading_resolves_one_identity(self):
    self.seed_exact("開く", "あく")
    result = recommend_media_word("media", "開く", db_path=self.db_path)
    self.assertEqual(result["reading"], "あく")

def test_omitted_reading_returns_sorted_ambiguity_matches(self):
    self.seed_exact("開く", "ひらく")
    self.seed_exact("開く", "あく")
    with self.assertRaises(AmbiguousReadingError) as error:
        recommend_media_word("media", "開く", db_path=self.db_path)
    self.assertEqual(error.exception.matches, ["あく", "ひらく"])

def test_spelling_only_media_stays_spelling_level(self):
    self.seed_spelling_only("固有名")
    result = recommend_media_word("media", "固有名", db_path=self.db_path)
    self.assertEqual(result["identity_type"], "spelling")
    self.assertIsNone(result["reading"])
```

Extend `AmbiguousReadingError` with JSON-safe sorted `matches` detail and ensure
existing tools/CLI retain type/message compatibility.

- [ ] **Step 2: Write failing context precedence tests**

```python
def test_failed_recall_outweighs_inference_and_transparency(self):
    result = recommend_media_word(
        "media", "宇宙飛行士", "うちゅうひこうし",
        failed_recall=True,
        successful_inference=True,
        transparent_composition=True,
        db_path=self.db_path,
    )
    self.assertEqual(result["context_score"], 2)
    self.assertIn("failed_recall", result["context_reasons"])
    self.assertGreater(result["contextual_score"], result["default_score"])

def test_failed_recall_moves_known_identity_to_review_but_not_anki_skip(self):
    known = self.seed_and_recommend(known=True, failed_recall=True)
    anki = self.seed_and_recommend(word="語", in_anki=True, failed_recall=True)
    self.assertEqual(known["default_tier"], "skip")
    self.assertEqual(known["contextual_tier"], "review")
    self.assertEqual(anki["contextual_tier"], "skip")
```

All context flags require actual bool. Personal usefulness cannot suppress hard
Anki skip. Return both default and contextual tiers/scores.

- [ ] **Step 3: Write failing CSV report tests**

```python
def test_report_contains_stable_review_columns_and_unicode(self):
    analysis = analyze_media("media", db_path=self.db_path)
    report = export_media_analysis_csv(analysis, self.output)
    with self.output.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    self.assertEqual(report["row_count"], len(rows))
    self.assertEqual(rows[0]["score_kind"], "ranking_heuristic")
    self.assertIn("理由", rows[0]["word"] + rows[0]["reasons"])
```

CSV uses UTF-8 BOM for spreadsheet compatibility. Serialize components/reasons as
compact JSON strings. Write to unique `.part`, flush, then atomic replace; typed
I/O errors and best-effort cleanup preserve primary errors.

- [ ] **Step 4: Run tests and verify failure**

Run: `python -m unittest tests.test_recommendation -v`

Expected: FAIL because recommendation/report functions are absent.

- [ ] **Step 5: Implement context and report APIs**

Context score uses exact config values. Contextual tier recomputes mine/review for
non-Anki candidates. Exact-known plus failed recall becomes review. Anki true stays
skip because card already exists. Preserve complete default evidence and add:

```json
{
  "default_tier": "review",
  "contextual_tier": "mine",
  "default_score": 3,
  "context_score": 6,
  "contextual_score": 9,
  "context": {"failed_recall": true},
  "context_reasons": ["failed_recall"]
}
```

Add `reports/` to `.gitignore`.

- [ ] **Step 6: Run recommendation/full tests**

Run: `python -m unittest tests.test_recommendation -v`

Expected: PASS.

Run: `python -m unittest discover -v`

Expected: PASS with opt-in skips only.

- [ ] **Step 7: Commit recommendations**

```bash
git add japanese_frequency/mining.py tests/test_recommendation.py .gitignore
git commit -m "feat(mining): add contextual recommendations and reports"
```

### Task 6: Agent Tools And CLI

**Files:**
- Modify: `japanese_frequency/tools.py`
- Modify: `japanese_frequency/__main__.py`
- Modify: `japanese_frequency/__init__.py`
- Modify: `tests/test_tools.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces wrappers `import_migaku_known_vocabulary`, `import_japanese_media_vocabulary`, `analyze_japanese_media`, and `recommend_japanese_media_word`.
- Produces CLI commands `import-known`, `import-media`, `analyze-media`, and `recommend-media`.

- [ ] **Step 1: Write failing exact-envelope/no-network tool tests**

```python
def test_new_tools_use_exact_success_envelope(self):
    responses = [
        import_migaku_known_vocabulary(self.migaku_path, db_path=self.db_path),
        import_japanese_media_vocabulary(self.media_path, "media", db_path=self.db_path),
        analyze_japanese_media("media", db_path=self.db_path),
        recommend_japanese_media_word("media", "読む", "よむ", db_path=self.db_path),
    ]
    for response in responses:
        self.assertEqual(set(response), {"ok", "result"})
        self.assertTrue(response["ok"])

def test_ambiguous_tool_error_contains_sorted_matches(self):
    response = recommend_japanese_media_word("media", "開く", db_path=self.db_path)
    self.assertEqual(response["error"]["type"], "ambiguous_reading")
    self.assertEqual(response["error"]["matches"], ["あく", "ひらく"])
```

Keep existing socket guards and silence assertions around all wrappers.

- [ ] **Step 2: Write failing CLI tests**

```python
def test_import_and_analyze_cli_workflow(self):
    imported = self.run_cli("import-known", str(self.migaku_path))
    media = self.run_cli(
        "import-media", str(self.media_txt), "--source-key", "media", "--name", "Volume 1"
    )
    analyzed = self.run_cli("analyze-media", "media", "--limit", "10")
    self.assertEqual(imported.returncode, 0)
    self.assertEqual(json.loads(media.stdout)["selected_filename"], "Volume 1.csv")
    self.assertLessEqual(sum(len(v) for v in json.loads(analyzed.stdout)["candidates"].values()), 10)

def test_analyze_cli_writes_csv_report(self):
    result = self.run_cli("analyze-media", "media", "--output", str(self.report))
    self.assertEqual(result.returncode, 0)
    self.assertTrue(self.report.exists())
```

Also test all context flags as `true|false`, Unicode paths/output, missing source,
ambiguous recommendation nonzero JSON, invalid limit, and database errors.

- [ ] **Step 3: Run tests and verify failure**

Run: `python -m unittest tests.test_tools tests.test_cli -v`

Expected: FAIL because new wrappers/commands are absent.

- [ ] **Step 4: Implement wrappers and parser commands**

Tool wrappers call domain APIs through `_tool_call`. Extend `_error_object` to
include optional safe `matches` from typed ambiguity errors without exposing raw
database text.

CLI syntax:

```text
import-known PATH
import-media PATH --source-key KEY [--name NAME]
analyze-media SOURCE_KEY [--limit N] [--output PATH]
recommend-media SOURCE_KEY WORD [READING]
  [--failed-recall true|false]
  [--successful-inference true|false]
  [--transparent-composition true|false]
  [--personally-useful true|false]
```

When `--output` is set, return analysis summary plus report metadata rather than
printing every candidate; report path contains full rows.

- [ ] **Step 5: Run interface/full tests**

Run: `python -m unittest tests.test_tools tests.test_cli -v`

Expected: PASS.

Run: `python -m unittest discover -v`

Expected: PASS with opt-in skips only.

- [ ] **Step 6: Commit interfaces**

```bash
git add japanese_frequency/tools.py japanese_frequency/__main__.py japanese_frequency/__init__.py tests/test_tools.py tests/test_cli.py
git commit -m "feat(api): expose media mining workflow"
```

### Task 7: Repository Agent Guide, README, And Real Local Workflow

**Files:**
- Create: `AGENTS.md`
- Modify: `README.md`
- Modify: `.gitignore`
- Create: `tests/test_agent_docs.py`
- Modify: `tests/test_real_sources.py`

**Interfaces:**
- Documents all public APIs, wrappers, CLI commands, state semantics, and safety rules.
- Produces opt-in environment variables `MIGAKU_KNOWN_SOURCE` and `MEDIA_VOCAB_SOURCE` for local integration tests.

- [ ] **Step 1: Write failing agent-document contract tests**

```python
def test_agent_guide_names_complete_workflow_and_tool_contracts(self):
    text = Path("AGENTS.md").read_text(encoding="utf-8")
    required = {
        "import_migaku_known_vocabulary",
        "import_japanese_media_vocabulary",
        "analyze_japanese_media",
        "recommend_japanese_media_word",
        "known_spelling",
        "known_identity",
        "in_anki",
        "ambiguous_reading",
        '"ok": true',
        '"ok": false',
    }
    self.assertEqual(required - set(filter(lambda item: item in text, required)), set())

def test_readme_links_agent_guide(self):
    self.assertIn("[AGENTS.md](AGENTS.md)", Path("README.md").read_text(encoding="utf-8"))
```

- [ ] **Step 2: Write `AGENTS.md` operational instructions**

Include concise sections:

1. Purpose and non-goals.
2. Data/privacy rules and ignored files.
3. Readiness check and default database path.
4. Import/refresh Migaku snapshot.
5. Import media with stable source-key naming.
6. Bulk analysis then contextual recommendation workflow.
7. Exact APIs, wrappers, CLI, and envelopes.
8. Spelling versus identity and tri-state semantics.
9. Ambiguity and no-invented-reading rules.
10. Score/tier interpretation and evidence retention.
11. Mutation authorization rules.
12. Error handling, backup/update, tests, and troubleshooting.

Explicitly state: never upload personal files/data; never mark known/unknown/Anki
without user instruction or explicit evidence; never dump full DB into model
context; never use frequency as sole decision; use report files for large output.

- [ ] **Step 3: Extend README and ignore rules**

README includes first-run commands with generic paths, inspected format behavior,
same-stem selection, source-key examples, JSON/CSV outputs, score caveat, context
flags, update semantics, and AGENTS link. Ignore `reports/`, Migaku/media source
patterns only under local data/import directories, and integration outputs; do not
glob-ignore arbitrary user `.txt`/`.csv` files repo-wide.

- [ ] **Step 4: Add opt-in real local workflow test**

```python
@unittest.skipUnless(
    os.environ.get("MIGAKU_KNOWN_SOURCE") and os.environ.get("MEDIA_VOCAB_SOURCE"),
    "set MIGAKU_KNOWN_SOURCE and MEDIA_VOCAB_SOURCE",
)
def test_real_migaku_and_media_workflow(self):
    known = import_migaku_known_words(os.environ["MIGAKU_KNOWN_SOURCE"], db_path=self.db_path)
    media = import_media_vocabulary(
        os.environ["MEDIA_VOCAB_SOURCE"], "real-media", db_path=self.db_path
    )
    analysis = analyze_media("real-media", limit=100, db_path=self.db_path)
    self.assertEqual(known["source_row_count"], 7038)
    self.assertEqual(media["selected_filename"], "Volume 1.csv")
    self.assertGreater(sum(len(rows) for rows in analysis["candidates"].values()), 0)
```

Compute and assert inspected source SHA-256 values in integration test after reading
actual files, then pin those expected values in the test to prevent accidental
fixture substitution. Do not assert recommendation tiers for words whose upstream
data may legitimately change unless database corpus version is also pinned.

- [ ] **Step 5: Run default offline suite**

Run: `python -m unittest discover -v`

Expected: PASS; real corpus/media tests skipped without environment variables; no
network access.

- [ ] **Step 6: Run real local workflow**

PowerShell:

```powershell
$env:MIGAKU_KNOWN_SOURCE='C:\Users\diazh\OneDrive\文档\Workstation\JAP LEARNING\MIGAKU KNOWN WORDS\migaku_known_words_8-25-2026.txt'
$env:MEDIA_VOCAB_SOURCE='C:\Users\diazh\OneDrive\文档\Workstation\JAP LEARNING\Volume 1.txt'
python -m unittest tests.test_real_sources.RealSourceTests.test_real_migaku_and_media_workflow -v
```

Expected: PASS, CSV selected, 7,038 raw Migaku rows, nonempty tiered candidates.

- [ ] **Step 7: Run functional CLI and report flow**

```powershell
python -m japanese_frequency import-known "$env:MIGAKU_KNOWN_SOURCE"
python -m japanese_frequency import-media "$env:MEDIA_VOCAB_SOURCE" --source-key shuukura-v1 --name "Volume 1"
python -m japanese_frequency analyze-media shuukura-v1 --limit 25
python -m japanese_frequency analyze-media shuukura-v1 --output reports\shuukura-v1.csv
python -m japanese_frequency recommend-media shuukura-v1 気まぐれ きまぐれ --failed-recall true
```

Expected: valid UTF-8 JSON, same-stem CSV metadata, nonempty report, explainable
candidate/recommendation evidence, no network.

- [ ] **Step 8: Run final repository checks**

Run: `python -m compileall -q japanese_frequency tests`, `git diff --check`, and
`git status --short --branch`.

Expected: compilation success, no whitespace errors, generated personal files and
reports ignored, only intended tracked changes committed.

- [ ] **Step 9: Commit docs and integration coverage**

```bash
git add AGENTS.md README.md .gitignore tests/test_agent_docs.py tests/test_real_sources.py
git commit -m "docs: add agent mining workflow guide"
```
