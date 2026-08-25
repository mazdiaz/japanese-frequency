from pathlib import Path


SQLITE_BUSY_TIMEOUT_MS = 1000
DEFAULT_DATABASE_PATH = Path(__file__).resolve().parent / "data" / "japanese_frequency.db"
JPDB_COMMONNESS_THRESHOLDS = (
    (1000, "extremely_common"),
    (3000, "very_common"),
    (10000, "common"),
    (20000, "moderately_common"),
    (40000, "uncommon"),
    (70000, "rare"),
)

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
