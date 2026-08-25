# Japanese Frequency

Local, standard-library Python package for Japanese frequency lookup and private
vocabulary tracking. SQLite stores replaceable corpus data and personal state in
separate tables. Normal lookup and mutation operations use no network and do not
read source files after setup.

## Requirements

- Python 3.10 or newer
- No third-party runtime packages

`requirements.txt` is intentionally empty.

## Setup

Create the default `data/japanese_frequency.db` with mandatory JPDB data:

```console
python setup_database.py
```

Include optional BCCWJ long-unit-word data:

```console
python setup_database.py --with-bccwj
```

Use already-downloaded sources or another database path:

```console
python setup_database.py --jpdb-source C:\corpora\jpdb.tsv --db C:\data\frequency.db
python setup_database.py --jpdb-source C:\corpora\jpdb.tsv --with-bccwj --bccwj-source C:\corpora\BCCWJ_frequencylist_luw_ver1_0.zip
```

Setup prints JSON containing `jpdb_entries`, `bccwj_entries`, resolved
`database_path`, `database_size_bytes`, and explicit `integrity_check` result.
JPDB is mandatory. BCCWJ is imported only with `--with-bccwj`. Download or
import failure for any requested source is fatal and returns a nonzero exit
status. Downloads use a same-directory `.part` file, verify pinned SHA-256
checksums, then atomically replace the final source file.

## Pinned Sources

### JPDB v2.2

- Attribution: [Kuuuube/yomitan-dictionaries](https://github.com/Kuuuube/yomitan-dictionaries), data described there as scraped from [JPDB](https://jpdb.io/)
- Version: JPDB v2.2 export dated 2024-10-13
- File: `jpdb_v2.2_freq_list_2024-10-13.csv` (UTF-8 tab-separated despite extension)
- Pinned URL: <https://raw.githubusercontent.com/Kuuuube/yomitan-dictionaries/b1114cc014f4343d04c387c7dcd3b1171dd31782/data/jpdb_v2.2_freq_list_2024-10-13.csv>
- SHA-256: `5cf103c1538a2189cda2fe0d462ebfe08eb09f211a48b727f533d387b371c5eb`

JPDB ranks separate senses, but export does not identify senses beyond
`(term, reading)`. Import therefore collapses duplicates to minimum term rank
and minimum populated kana rank.

### BCCWJ1 LUW Version 1.0

- Source: National Institute for Japanese Language and Linguistics (NINJAL), Center for Corpus Development
- Dataset: [Balanced Corpus of Contemporary Written Japanese Long Unit Word Frequency List, Version 1.0](https://doi.org/10.15084/00003212)
- File: `BCCWJ_frequencylist_luw_ver1_0.zip`
- Pinned URL: <https://repository.ninjal.ac.jp/record/3228/files/BCCWJ_frequencylist_luw_ver1_0.zip>
- Project-recorded SHA-256 of the inspected pinned file (not an
  upstream-published checksum):
  `0a23e56283187ed4f65d70b89919e05652470474855e1d35566d4039ecb7973a`
- Upstream manual: [BCCWJ frequency-list manual ver.1.0](https://clrd.ninjal.ac.jp/bccwj/data-files/frequency-list/BCCWJ_frequencylist_manual_ver1_0b.pdf)

Manual section 6 states these terms:

- Research and educational use is free, with no application required.
- Redistribution is not permitted.
- Commercial use (use for profit) requires consultation.
- Papers and other citations must identify source and version. For this source,
  cite `『現代日本語書き言葉均衡コーパス』長単位語彙表 ver1.0`.
- Copyright, specifically compilation copyright, belongs to NINJAL.

This project does not redistribute BCCWJ source data or generated databases.
Each user obtains source from NINJAL and builds a local database. Consult
NINJAL before commercial use; README summary does not replace upstream manual.

BCCWJ can contain several rows for same normalized lemma and reading because
part of speech and word type differ. Import sums frequency and frequency per
million for those rows. Stored BCCWJ rank is project-computed, not upstream
rank: sequential order by summed frequency descending, then word and reading
ascending.

The pinned BCCWJ LUW file has exactly 14 rows whose raw `lForm` is empty. The
importer stores those rows with canonical reading `""` only when raw `lForm` is
exactly empty. Whitespace-only `lForm` and empty or whitespace-only `lemma`
remain invalid. Empty-reading corpus entries are distinct identities: they
appear in word-only lookup results and participate in omitted-reading
resolution, which can make a lemma ambiguous when populated readings also
exist.

## Python API

```python
from japanese_frequency import (
    get_word_profile,
    lookup_frequency,
    mark_known,
    record_encounter,
    set_in_anki,
)

lookup_frequency("読む", "よむ")
lookup_frequency("開く")  # Returns all matching readings.
record_encounter("読む", "よむ")
mark_known("読む", "よむ", True)
set_in_anki("読む", "よむ", True)
get_word_profile("読む", "よむ")
```

Pass `db_path=...` to any call to use a non-default database. Omitted readings
resolve only when one corpus identity exists. Ambiguous mutations fail without
changing personal state.

## Agent Tools

`japanese_frequency.tools` provides stable JSON-style envelopes:

```python
from japanese_frequency.tools import lookup_japanese_frequency

lookup_japanese_frequency("読む", "よむ")
# {"ok": True, "result": {"found": True, ...}}

lookup_japanese_frequency("   ")
# {"ok": False, "error": {"type": "invalid_input", "message": "..."}}
```

Other wrappers are `get_japanese_word_profile`,
`record_japanese_encounter`, `mark_japanese_word_known`, and
`set_japanese_word_anki_status`.

## CLI

Commands emit UTF-8 JSON directly:

```console
python -m japanese_frequency lookup 読む よむ
python -m japanese_frequency profile 読む
python -m japanese_frequency encounter 読む よむ
python -m japanese_frequency known 読む よむ --value true
python -m japanese_frequency anki 読む よむ --value false
python -m japanese_frequency --db C:\data\frequency.db lookup 読む
```

Domain and database failures produce a JSON `error` object and nonzero status.

## Updates And Backups

Source imports validate complete input in connection-local staging tables, then
replace only requested corpus source in a short transaction. Failed validation
or lock acquisition preserves live corpus rows, unrelated sources, and
`user_words`. A configured SQLite busy timeout maps lock failures to stable
`database_busy` errors.

Before replacing or moving a database, stop writers and back up database plus
any `-wal` and `-shm` sidecars, or use SQLite backup API. Re-running setup or
import scripts replaces corpus rows while preserving personal history. Do not
copy only main `.db` file while active WAL connections exist.

Source archives live under `data/sources/`; generated database defaults to
`data/japanese_frequency.db`. Both are ignored by Git. Personal vocabulary data
stays in local SQLite database unless user explicitly copies it.

## Tests

```console
python -m unittest discover -v
```

Default suite is offline and deterministic. It uses temporary databases,
synthetic fixtures, mocked downloads, and injected clocks. Real-corpus
integration runs must use sources already downloaded by user and never download
implicitly.

Run opt-in smoke tests against local source files in PowerShell:

```powershell
$env:JPDB_SOURCE='C:\corpora\jpdb_v2.2.csv'
python -m unittest tests.test_real_sources.RealSourceTests.test_real_jpdb_import_and_lookup -v

$env:BCCWJ_SOURCE='C:\corpora\BCCWJ_frequencylist_luw_ver1_0.zip'
python -m unittest tests.test_real_sources.RealSourceTests.test_real_bccwj_import_and_lookup -v
```

Unset environment variables leave these tests skipped during default discovery.
