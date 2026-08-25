"""Local Japanese frequency database."""

from japanese_frequency.lookup import classify_jpdb_rank, lookup_frequency
from japanese_frequency.knowledge import get_known_spelling, import_migaku_known_words
from japanese_frequency.user_words import (
    get_word_profile,
    mark_known,
    record_encounter,
    set_in_anki,
)


__all__ = [
    "classify_jpdb_rank",
    "get_known_spelling",
    "get_word_profile",
    "import_migaku_known_words",
    "lookup_frequency",
    "mark_known",
    "record_encounter",
    "set_in_anki",
]
