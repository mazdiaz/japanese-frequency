"""Local Japanese frequency database."""

from japanese_frequency.lookup import classify_jpdb_rank, lookup_frequency
from japanese_frequency.knowledge import get_known_spelling, import_migaku_known_words
from japanese_frequency.media import get_media_source, import_media_vocabulary
from japanese_frequency.mining import (
    analyze_media,
    export_media_analysis_csv,
    recommend_media_word,
)
from japanese_frequency.tools import (
    analyze_japanese_media,
    import_japanese_media_vocabulary,
    import_migaku_known_vocabulary,
    recommend_japanese_media_word,
)
from japanese_frequency.user_words import (
    get_word_profile,
    mark_known,
    record_encounter,
    set_in_anki,
)


__all__ = [
    "analyze_japanese_media",
    "analyze_media",
    "classify_jpdb_rank",
    "export_media_analysis_csv",
    "get_known_spelling",
    "get_media_source",
    "get_word_profile",
    "import_migaku_known_words",
    "import_media_vocabulary",
    "import_japanese_media_vocabulary",
    "import_migaku_known_vocabulary",
    "lookup_frequency",
    "mark_known",
    "record_encounter",
    "recommend_japanese_media_word",
    "recommend_media_word",
    "set_in_anki",
]
