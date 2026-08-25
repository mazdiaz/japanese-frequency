# Migaku Media Mining Workflow Design

## Objective

Extend the local Japanese frequency database into the intended vocabulary-mining
workflow:

1. Import the current Migaku known-words export as spelling-level knowledge.
2. Import vocabulary exported from a novel, anime, visual novel, or other media.
3. Prefer rich CSV media exports over redundant spelling-only TXT exports.
4. Combine media frequency, JPDB/BCCWJ frequency, spelling knowledge,
   identity-specific knowledge, Anki state, encounters, and optional contextual
   evidence.
5. Return explainable `mine`, `review`, or `skip` recommendations for AI review.

The system remains local and offline after source files are available. It does
not create Anki cards through the Migaku extension; it produces a filtered,
ranked mining report that the user or AI can act on separately.

## Inspected Inputs

### Migaku Known Words

Inspected file:

`C:\Users\diazh\OneDrive\文档\Workstation\JAP LEARNING\MIGAKU KNOWN WORDS\migaku_known_words_8-25-2026.txt`

The file contains 7,038 lines. Each line is one spelling with no reading,
dictionary identifier, or status metadata. It therefore proves only
spelling-level familiarity. It cannot prove that every reading or sense of an
ambiguous spelling is known.

### Media TXT

Inspected `Volume 1.txt` and `Hanahira!.txt` contain one dictionary-form spelling
per line. Their format does not document line order as media frequency. Such
files are treated as unordered membership sets. Line position is never converted
into occurrence count or media rank.

### Media CSV

Inspected `Volume 1.csv` contains named columns:

```text
Word, ReadingFurigana, ReadingKana, Occurences, ReadingFrequency,
PitchPositions, Definitions, ExampleSentence, JmDictWordId
```

The importer consumes columns by header name rather than fixed position.
`Occurences` is retained despite the source's spelling. Explicit occurrence
counts are the primary media-specific signal. Readings, definitions, examples,
and dictionary identifiers are retained when usable.

## Architecture

The existing SQLite database remains the single local store. New tables separate
replaceable Migaku snapshots, media imports, and durable identity-level personal
state. Existing `frequency` rows remain static corpus evidence. Existing
`user_words` remains durable personal state.

New focused modules:

- `knowledge.py`: Migaku snapshot import and spelling-knowledge queries.
- `media.py`: media source selection, parsing, staging, and atomic replacement.
- `mining.py`: evidence collection, scoring, tiers, ambiguity handling, and CSV
  report export.
- Existing `tools.py` and `__main__.py`: expose wrappers and CLI commands.

No web server, tokenizer, morphological analyzer, or third-party package is
added. Inputs are already dictionary-form vocabulary exports.

## Schema

### Spelling Knowledge

```sql
CREATE TABLE known_spellings (
    word TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (word, source)
);

CREATE INDEX idx_known_spellings_word
    ON known_spellings(word);

CREATE TABLE personal_source_metadata (
    source TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    notes TEXT
);
```

Initial spelling source is `migaku`. Schema permits future spelling-level sources
without changing lookup APIs.

### Media Sources

```sql
CREATE TABLE media_sources (
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
```

`source_key` is a stable caller-selected identifier such as `shuukura-v1`.
Display names are not unique because names such as `Volume 1` may repeat across
series. Reimport updates the existing row identified by `source_key`; it never
uses `INSERT OR REPLACE`.

The requested and selected filenames are both recorded. When `Volume 1.txt` is
requested and same-directory `Volume 1.csv` is selected, metadata and notes make
that supersession explicit.

### Exact Media Identities

```sql
CREATE TABLE media_words (
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

CREATE INDEX idx_media_words_media_rank
    ON media_words(media_id, media_rank);
```

`media_words` contains only rows with a usable canonical reading. It never uses
an empty reading as a substitute for missing identity information.

### Spelling-Only Media Entries

```sql
CREATE TABLE media_spellings (
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

CREATE INDEX idx_media_spellings_media_rank
    ON media_spellings(media_id, media_rank);
```

TXT entries belong here with nullable media values. CSV rows without usable
readings also belong here while retaining occurrence and descriptive fields.
Spelling-only membership never creates an exact `(word, "")` identity.

## Tri-State Personal State

Both exact knowledge and Anki state become tri-state:

- `true`: explicitly confirmed.
- `false`: explicitly confirmed absent/unknown.
- `null`: no identity-level decision exists.

The current `user_words` booleans cannot distinguish default false from explicit
false. Transactional schema migration rebuilds `user_words` with nullable
`known` and `in_anki` columns:

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
```

Migration rules are conservative:

- Existing `known=1` becomes `known=1`.
- Existing `known=0` becomes `known=NULL` because explicit intent is unknown.
- Existing `in_anki=1` becomes `in_anki=1`.
- Existing `in_anki=0` becomes `in_anki=NULL`.
- Encounters, timestamps, notes, and identities are copied unchanged.

New encounter rows default both states to NULL. `mark_known(..., False)` and
`set_in_anki(..., False)` explicitly store zero. Existing public output may keep
the `known` and `in_anki` names, but values are now JSON booleans or null.

## Migaku Snapshot Import

```python
import_migaku_known_words(path, *, db_path=None, now=None) -> dict
```

Import behavior:

1. Stream the local UTF-8/UTF-8-BOM text file.
2. Apply word NFC normalization and surrounding-whitespace removal.
3. Reject blank records and malformed/non-text input with a line-aware error.
4. Deduplicate repeated spellings deterministically.
5. Hash the exact imported snapshot bytes.
6. Populate a connection-scoped TEMP staging table completely.
7. In a short transaction, replace only `known_spellings.source='migaku'` and
   its personal metadata.

Every successful import is a replaceable snapshot. A spelling absent from the
new file loses only Migaku-derived spelling knowledge. The importer never changes
`user_words`, encounter history, Anki state, notes, corpus data, media data, or
other spelling sources. Any failure preserves the previous Migaku snapshot.

## Media Import

```python
import_media_vocabulary(
    path,
    source_key,
    display_name=None,
    *,
    db_path=None,
    now=None,
) -> dict
```

### Source Selection

- A requested CSV is selected directly.
- For a requested TXT, a same-directory same-stem CSV is preferred when present.
- When CSV supersedes TXT, TXT is not imported as duplicate membership.
- Metadata reports requested path, selected path, selected format, and an
  explanatory note.
- A missing or unsupported source returns a typed error.

### CSV Parsing

The required columns are `Word`, `ReadingKana`, and `Occurences`. Optional known
columns are retained when present: `Definitions`, `ExampleSentence`, and
`JmDictWordId`. Additional columns do not affect import.

- Word uses existing word normalization.
- A nonempty reading uses existing reading canonicalization and creates an exact
  `media_words` identity.
- Missing/blank reading creates a `media_spellings` row, never an empty-reading
  identity.
- Occurrences must be a positive integer.
- Duplicate exact identities sum occurrence counts.
- Duplicate spelling-only entries sum occurrence counts.
- First nonempty definition, example, and dictionary ID by source row order are
  retained deterministically.
- Exact media ranks and spelling-only media ranks are sequential within one
  combined media inventory, ordered by occurrences descending, then word and
  reading ascending. Ties never receive competition ranks.

### TXT Parsing

- Each nonempty line is one normalized spelling.
- Duplicate spellings collapse.
- All rows go to `media_spellings`.
- Occurrences and media rank remain NULL because input does not document count or
  ordering semantics.

### Atomic Reimport

Parsing and validation complete in TEMP staging before live mutation. A short
transaction finds or creates `media_sources` by `source_key`, updates metadata,
deletes that source's old child rows, and inserts staged children. Reimport never
uses `INSERT OR REPLACE`, which could change source IDs or trigger unintended
cascades. Other media sources and all personal/corpus data remain untouched.

## Profile Knowledge Semantics

Frequency and word profiles expose:

```json
{
  "known_spelling": true,
  "known_spelling_sources": ["migaku"],
  "known_identity": null,
  "in_anki": null
}
```

- `known_spelling` is true when any spelling source contains the exact word.
- `known_spelling_sources` is sorted and identifies evidence provenance.
- `known_identity` is the nullable exact `user_words.known` state.
- `in_anki` is the nullable exact `user_words.in_anki` state.
- Migaku spelling knowledge never sets exact identity knowledge.
- A known spelling is evidence of familiarity, not proof every reading/sense is
  known.

Word-only profile results retain separate reading identities and spelling-level
evidence. They do not merge or arbitrarily select readings.

## Media Analysis

```python
analyze_media(source_key, *, limit=None, db_path=None) -> dict
```

Output includes source metadata, summary counts, and candidates grouped into
`mine`, `review`, and `skip`.

### Candidate Deduplication

Exact media identities are richer than spelling-only records. When an imported or
joined inventory contains both:

- Exact `(word, reading)` candidates are emitted separately.
- An overlapping spelling-only candidate is suppressed when it contributes no
  evidence beyond exact rows.
- Spelling-level occurrence/context evidence absent from exact rows is attached
  to exact candidates rather than emitted as a duplicate.
- A genuinely spelling-only word remains one spelling-level candidate with
  nullable identity and Anki state.

### Evidence Shape

Every candidate retains component evidence:

```json
{
  "word": "気まぐれ",
  "reading": "きまぐれ",
  "identity_type": "exact",
  "tier": "mine",
  "score": 7,
  "score_kind": "ranking_heuristic",
  "score_components": {},
  "reasons": ["unknown_spelling", "repeated_in_media", "common_jpdb"],
  "media": {},
  "frequency": {},
  "personal": {}
}
```

The numeric score is an ordering heuristic, not a probability, language level,
or linguistic truth. Raw evidence and machine-readable reasons are always
available.

### Default Tier Policy

Policy is configurable and advisory:

- Exact identity known true: normally `skip` with `known_identity`.
- Exact Anki state true: normally `skip` with `already_in_anki`.
- Migaku spelling known with exact identity false/null: at least `review`; never
  automatically suppressed solely by spelling knowledge.
- Exact identity explicitly false raises priority.
- Unknown spelling/identity receives positive evidence from repeated media
  occurrences, high media rank, broadly common JPDB/BCCWJ rank, and repeated
  prior personal encounters.
- One-off, corpus-rare, or corpus-absent words normally remain `review`; corpus
  rarity alone never forces `skip`.
- No single frequency threshold makes an irreversible decision.

Candidates sort by tier priority, score descending, media rank NULL last, JPDB
rank NULL last, BCCWJ rank NULL last, word, then reading. All NULL ordering is
explicit.

## Contextual Recommendation

```python
recommend_media_word(
    source_key,
    word,
    reading=None,
    *,
    failed_recall=False,
    successful_inference=False,
    transparent_composition=False,
    personally_useful=False,
    db_path=None,
) -> dict
```

Resolution rules:

- Supplied reading addresses one exact identity.
- Omitted reading resolves only when exactly one exact media/corpus identity is
  available.
- Multiple identities return `ambiguous_reading` with sorted matches; they are
  never merged or arbitrarily selected.
- A spelling-only media entry stays spelling-level and does not become an empty
  reading identity.

Context modifiers are explainable and advisory:

- Failed recall strongly raises priority.
- Personal usefulness raises priority.
- Successful inference lowers priority.
- Transparent composition lowers priority.
- When failed recall conflicts with successful inference or transparency, failed
  recall normally dominates because it is direct evidence of a retrieval gap.

Output retains the default tier, contextual tier, score components, context
flags, and reason codes. An AI may override the recommendation while preserving
the generated evidence.

## Agent Tools

Existing tool envelope remains unchanged:

```json
{"ok": true, "result": {}}
```

or:

```json
{"ok": false, "error": {"type": "invalid_input", "message": "..."}}
```

New wrappers:

- `import_migaku_known_vocabulary`
- `import_japanese_media_vocabulary`
- `analyze_japanese_media`
- `recommend_japanese_media_word`

Wrappers accept simple strings, booleans, and integers; return JSON-serializable
data; print nothing; and perform no network access.

## CLI And Reports

```powershell
python -m japanese_frequency import-known `
  "C:\path\migaku_known_words.txt"

python -m japanese_frequency import-media `
  "C:\path\Volume 1.txt" `
  --source-key "shuukura-v1" `
  --name "Story About Buying My Classmate Once a Week, Volume 1"

python -m japanese_frequency analyze-media "shuukura-v1" --limit 100

python -m japanese_frequency analyze-media "shuukura-v1" `
  --output "reports\shuukura-v1.csv"

python -m japanese_frequency recommend-media `
  "shuukura-v1" 気まぐれ きまぐれ --failed-recall true
```

JSON is default. CSV report columns include tier, score, score components/reasons,
word, reading, identity type, occurrences, media rank, spelling knowledge,
identity knowledge, Anki state, encounters, JPDB rank, and BCCWJ rank.

Reports contain personal evidence and are ignored by Git under a project
`reports/` directory by default. Explicit paths outside the project remain the
user's responsibility.

## Errors And Transactions

Stable error types extend existing conventions:

- `invalid_input`
- `source_format_error`
- `source_not_found`
- `media_not_found`
- `ambiguous_reading`
- `database_busy`
- `database_error`

Human-readable wording may improve without changing codes. File, line, column,
and conflicting identity details are included where safe and useful.

No parsing operation holds the live replacement transaction. TEMP staging is
fully populated and validated first. Live swaps use short `BEGIN IMMEDIATE`
transactions. Rollback and cleanup failures never mask primary failures.

## Testing

Default `python -m unittest discover -v` remains offline and deterministic.
Tests use temporary databases, synthetic fixtures matching inspected formats,
injected clocks, and mocked failures.

Coverage includes:

- Transactional schema migration and all conservative tri-state conversions.
- Existing encounter/timestamp/note preservation through migration.
- Migaku 1-line-per-spelling parsing, normalization, deduplication, metadata,
  atomic snapshot replacement, rollback, and source isolation.
- TXT membership import with no invented rank or reading.
- CSV named-column validation, occurrences, canonical readings, missing-reading
  spelling rows, deterministic aggregation, metadata retention, and sequential
  combined rank.
- Same-stem CSV superseding requested TXT without duplicate import.
- Requested/selected path metadata and notes.
- Stable source keys, duplicate display names, source-key reimport with stable ID,
  and no `INSERT OR REPLACE` behavior.
- Spelling/identity profile distinction and sorted provenance.
- Tri-state exact knowledge and Anki mutations.
- Bulk media deduplication, evidence, explicit NULL-last ordering, deterministic
  scores, tiers, and reason codes.
- Recommendation single-identity resolution, multiple-reading ambiguity, and
  spelling-only behavior.
- Context modifier precedence, especially failed recall over inference and
  transparency.
- Score labeling as ranking heuristic rather than probability.
- CSV report export and Git ignore behavior.
- Typed file/database/lock errors and complete rollback guarantees.
- Agent tool envelopes, CLI Unicode JSON, and no-network guards.

Opt-in local integration imports the inspected 7,038-line Migaku file and
`Volume 1.csv`. It verifies exact source hashes/counts, CSV selection behavior,
representative evidence/recommendations, and absence of committed personal data.
It never downloads or commits these files.

## Documentation And Privacy

README gains a complete workflow from Migaku export through media analysis,
explanations of spelling-versus-identity knowledge, source-key naming guidance,
tier/score caveats, AI invocation examples, report handling, and safe snapshot
updates.

The repository also gains a root `AGENTS.md`. This is operational context for
any future AI agent using or maintaining the project, independent of machine-wide
agent configuration. It documents:

- Project purpose: compare Migaku spelling knowledge with media vocabulary and
  produce evidence-based mining recommendations.
- Database and default local-data locations, while making clear that user-specific
  paths may differ and must not be committed.
- Required workflow: verify database readiness, import or refresh Migaku snapshot,
  import media under a stable `source_key`, run bulk analysis, then use contextual
  recommendation for uncertain candidates.
- Exact Python APIs, agent wrapper names, CLI commands, success/error envelopes,
  and representative calls.
- Knowledge semantics: `known_spelling` is not `known_identity`; identity and
  Anki states are tri-state; missing readings must never be invented.
- Reading ambiguity rules and prohibition on arbitrary identity selection.
- Mining policy: frequency is supporting evidence, scores are ranking heuristics,
  reasons/components must remain visible, and context may override defaults.
- Mutation safety: importing snapshots must not alter durable identity history;
  agents must not mark known, unknown, or Anki state without user instruction or
  explicit evidence.
- Privacy/offline rules: never upload personal exports, database contents, reports,
  encounter history, notes, or Anki state; normal analysis must not use internet
  services.
- Output discipline: return small candidate/result payloads, never dump the full
  database into model context, and prefer CSV report paths for large analyses.
- Update, backup, testing, and troubleshooting procedures, including stable error
  codes and how to respond to ambiguity or missing imports.

`README.md` links prominently to `AGENTS.md` for agent-oriented operation. Tests
or documentation checks verify that all public mining tool names and the
`{ok, result}` / `{ok, error}` contract appear in the file, reducing drift as APIs
change.

GitHub continues to contain code and documentation only. Migaku exports, media
exports, generated reports, SQLite database, personal knowledge, encounters,
notes, and Anki state remain local and ignored.

## Completion Criteria

The feature is complete when the user or AI can:

1. Atomically import the current Migaku known-spelling snapshot.
2. Import `Volume 1.txt` while automatically selecting `Volume 1.csv`.
3. Analyze `shuukura-v1` into explainable `mine`, `review`, and `skip` groups.
4. Distinguish `known_spelling` from nullable exact `known_identity` and nullable
   exact `in_anki`.
5. Request one contextual recommendation without arbitrary reading selection.
6. Export a reviewable CSV report.
7. Repeat all normal analysis without network access.
8. Reimport Migaku/media sources without losing corpus data or durable personal
   history.
9. Give a future agent enough repository-local instructions in `AGENTS.md` to run
   the complete workflow safely without prior conversation context.
