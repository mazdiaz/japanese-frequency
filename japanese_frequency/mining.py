import csv
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import config
from japanese_frequency.database import _database_error, get_connection
from japanese_frequency.errors import (
    AmbiguousReadingError,
    InvalidInputError,
    MediaNotFoundError,
    SourceFormatError,
)
from japanese_frequency.media import get_media_source, iter_media_candidates
from japanese_frequency.normalization import normalize_reading, normalize_word


_MEDIA_FIELDS = (
    "occurrences",
    "media_rank",
    "definitions",
    "example_sentence",
    "dictionary_id",
)
_TIER_ORDER = {"mine": 0, "review": 1, "skip": 2}
_REPORT_FIELDS = (
    "tier",
    "score",
    "score_kind",
    "score_components",
    "reasons",
    "word",
    "reading",
    "identity_type",
    "occurrences",
    "media_rank",
    "known_spelling",
    "known_identity",
    "in_anki",
    "encounters",
    "jpdb_rank",
    "bccwj_rank",
)
_MEDIA_WORDS_CTE = """
WITH media_vocabulary(word) AS (
    SELECT word FROM media_words WHERE media_id = ?
    UNION
    SELECT word FROM media_spellings WHERE media_id = ?
)
"""


def _media_evidence(row):
    return {field: row[field] for field in _MEDIA_FIELDS}


def _optional_component(components, value, thresholds):
    if value is None:
        return
    for matches, name in thresholds:
        if matches(value):
            components[name] = config.MINING_SCORE[name]
            return


def _score_candidate(candidate, context=None) -> dict:
    components = {}
    reasons = []
    personal = candidate["personal"]
    media = candidate["media"]
    frequency = candidate["frequency"]

    if personal["known_spelling"]:
        components["known_spelling"] = config.MINING_SCORE["known_spelling"]
        reasons.append("known_spelling")
    else:
        reasons.append("unknown_spelling")
    if personal["known_identity"] is False:
        components["known_identity_false"] = config.MINING_SCORE[
            "known_identity_false"
        ]
        reasons.append("known_identity_false")

    occurrences = media["occurrences"]
    _optional_component(
        components,
        occurrences,
        (
            (lambda value: value >= 10, "media_occurrences_10"),
            (lambda value: value >= 5, "media_occurrences_5"),
            (lambda value: value >= 2, "media_occurrences_2"),
            (lambda value: value >= 1, "media_occurrences_1"),
        ),
    )
    if occurrences is not None:
        reasons.append("repeated_in_media" if occurrences >= 2 else "seen_in_media")
    _optional_component(
        components,
        media["media_rank"],
        (
            (lambda value: value <= 100, "media_rank_100"),
            (lambda value: value <= 500, "media_rank_500"),
        ),
    )

    jpdb_rank = frequency.get("jpdb", {}).get("rank")
    _optional_component(
        components,
        jpdb_rank,
        (
            (lambda value: value <= 3000, "jpdb_rank_3000"),
            (lambda value: value <= 10000, "jpdb_rank_10000"),
            (lambda value: value <= 20000, "jpdb_rank_20000"),
            (lambda value: value <= 70000, "jpdb_rank_70000"),
            (lambda value: value > 70000, "jpdb_rank_over_70000"),
        ),
    )
    if jpdb_rank is not None:
        reasons.append("common_jpdb" if jpdb_rank <= 20000 else "rare_jpdb")

    bccwj_rank = frequency.get("bccwj_luw", {}).get("rank")
    _optional_component(
        components,
        bccwj_rank,
        (
            (lambda value: value <= 3000, "bccwj_rank_3000"),
            (lambda value: value <= 10000, "bccwj_rank_10000"),
            (lambda value: value > 50000, "bccwj_rank_over_50000"),
        ),
    )
    if bccwj_rank is not None:
        reasons.append("common_bccwj" if bccwj_rank <= 10000 else "rare_bccwj")

    encounters = personal["encounter_count"]
    _optional_component(
        components,
        encounters,
        (
            (lambda value: value >= 3, "encounters_3"),
            (lambda value: value >= 1, "encounters_1"),
        ),
    )
    if encounters:
        reasons.append("prior_encounters")

    if context is not None:
        for name, points in config.CONTEXT_SCORE.items():
            if context.get(name):
                components[name] = points
                reasons.append(name)

    score = sum(components.values())
    if personal["known_identity"] is True or personal["in_anki"] is True:
        tier = "skip"
    elif score >= config.MINING_MINE_SCORE:
        tier = "mine"
    else:
        tier = "review"
    if personal["known_identity"] is True:
        reasons.append("known_identity")
    if personal["in_anki"] is True:
        reasons.append("already_in_anki")
    return {
        "tier": tier,
        "score": score,
        "score_kind": "ranking_heuristic",
        "score_components": dict(sorted(components.items())),
        "reasons": sorted(set(reasons)),
    }


def _read_frequency(connection, media_id):
    rows = connection.execute(
        _MEDIA_WORDS_CTE
        + """
        SELECT frequency.word, frequency.reading, frequency.source,
               frequency.rank, frequency.frequency,
               frequency.frequency_per_million, frequency.kana_rank
        FROM frequency
        JOIN media_vocabulary ON media_vocabulary.word = frequency.word
        WHERE frequency.reading <> ''
        ORDER BY frequency.word, frequency.reading, frequency.source
        """,
        (media_id, media_id),
    ).fetchall()
    result = {}
    for row in rows:
        values = {
            field: row[field]
            for field in ("rank", "frequency", "frequency_per_million", "kana_rank")
        }
        result.setdefault((row["word"], row["reading"]), {})[row["source"]] = values
    return result


def _read_users(connection, media_id):
    rows = connection.execute(
        _MEDIA_WORDS_CTE
        + """
        SELECT user_words.*
        FROM user_words
        JOIN media_vocabulary ON media_vocabulary.word = user_words.word
        ORDER BY user_words.word, user_words.reading
        """,
        (media_id, media_id),
    ).fetchall()
    return {
        (row["word"], row["reading"]): {
            "known_identity": None if row["known"] is None else bool(row["known"]),
            "in_anki": None if row["in_anki"] is None else bool(row["in_anki"]),
            "encounter_count": row["encounter_count"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "notes": row["notes"],
        }
        for row in rows
    }


def _read_known_spellings(connection, media_id):
    rows = connection.execute(
        _MEDIA_WORDS_CTE
        + """
        SELECT known_spellings.word, known_spellings.source
        FROM known_spellings
        JOIN media_vocabulary ON media_vocabulary.word = known_spellings.word
        ORDER BY known_spellings.word, known_spellings.source
        """,
        (media_id, media_id),
    ).fetchall()
    result = {}
    for row in rows:
        result.setdefault(row["word"], []).append(row["source"])
    return result


def _personal_evidence(identity, word, users, known_spellings):
    user = users.get(identity, {}) if identity is not None else {}
    sources = known_spellings.get(word, [])
    return {
        "known_spelling": bool(sources),
        "known_spelling_sources": sources,
        "known_identity": user.get("known_identity"),
        "in_anki": user.get("in_anki"),
        "encounter_count": user.get("encounter_count", 0),
        "first_seen": user.get("first_seen"),
        "last_seen": user.get("last_seen"),
        "notes": user.get("notes"),
    }


def _build_candidates(media_rows, frequencies, users, known_spellings):
    exact_media = {
        (row["word"], row["reading"]): row
        for row in media_rows
        if row["identity_type"] == "exact"
    }
    spelling_media = {
        row["word"]: row
        for row in media_rows
        if row["identity_type"] == "spelling"
    }
    exact_media_by_word = {}
    for identity, row in exact_media.items():
        exact_media_by_word.setdefault(identity[0], row)
    media_words = set(exact_media_by_word) | set(spelling_media)
    identities = set(exact_media)
    identities.update(
        identity for identity in frequencies if identity[0] in media_words
    )

    candidates = []
    for identity in sorted(identities):
        word, reading = identity
        exact_row = exact_media.get(identity)
        spelling_row = spelling_media.get(word)
        source_row = exact_row or spelling_row or exact_media_by_word[word]
        media = _media_evidence(source_row)
        if spelling_row is not None:
            media["spelling"] = _media_evidence(spelling_row)
        candidate = {
            "word": word,
            "reading": reading,
            "identity_type": "exact",
            "media": media,
            "frequency": frequencies.get(identity, {}),
            "personal": _personal_evidence(
                identity, word, users, known_spellings
            ),
        }
        candidate.update(_score_candidate(candidate))
        candidates.append(candidate)

    exact_words = {word for word, _ in identities}
    for word, spelling_row in sorted(spelling_media.items()):
        if word in exact_words:
            continue
        candidate = {
            "word": word,
            "reading": None,
            "identity_type": "spelling",
            "media": _media_evidence(spelling_row),
            "frequency": {},
            "personal": _personal_evidence(
                None, word, users, known_spellings
            ),
        }
        candidate.update(_score_candidate(candidate))
        candidates.append(candidate)
    return candidates


def _rank_key(candidate):
    media_rank = candidate["media"]["media_rank"]
    jpdb_rank = candidate["frequency"].get("jpdb", {}).get("rank")
    bccwj_rank = candidate["frequency"].get("bccwj_luw", {}).get("rank")
    return (
        _TIER_ORDER[candidate["tier"]],
        -candidate["score"],
        media_rank is None,
        media_rank if media_rank is not None else 0,
        jpdb_rank is None,
        jpdb_rank if jpdb_rank is not None else 0,
        bccwj_rank is None,
        bccwj_rank if bccwj_rank is not None else 0,
        candidate["word"],
        candidate["reading"] or "",
    )


def analyze_media(source_key, *, limit=None, db_path=None) -> dict:
    if limit is not None and (
        not isinstance(limit, int) or isinstance(limit, bool) or limit < 0
    ):
        raise InvalidInputError("limit must be a nonnegative integer or null")
    try:
        with closing(get_connection(db_path)) as connection:
            connection.execute("BEGIN")
            source = get_media_source(source_key, connection=connection)
            media_rows = iter_media_candidates(connection, source["id"])
            frequencies = _read_frequency(connection, source["id"])
            users = _read_users(connection, source["id"])
            known_spellings = _read_known_spellings(connection, source["id"])
            candidates = sorted(
                _build_candidates(media_rows, frequencies, users, known_spellings),
                key=_rank_key,
            )
            connection.commit()
    except sqlite3.Error as error:
        raise _database_error(error) from error

    available_candidates = len(candidates)
    if limit is not None:
        candidates = candidates[:limit]
    grouped = {tier: [] for tier in ("mine", "review", "skip")}
    for candidate in candidates:
        grouped[candidate["tier"]].append(candidate)
    return {
        "source": source,
        "summary": {
            "available_candidates": available_candidates,
            "returned_candidates": len(candidates),
            "mine": len(grouped["mine"]),
            "review": len(grouped["review"]),
            "skip": len(grouped["skip"]),
        },
        "candidates": grouped,
    }


def recommend_media_word(
    source_key,
    word,
    reading=None,
    *,
    failed_recall=False,
    successful_inference=False,
    transparent_composition=False,
    personally_useful=False,
    db_path=None,
) -> dict:
    word = normalize_word(word)
    if reading is not None and not isinstance(reading, str):
        raise InvalidInputError("reading must be a nonempty string or null")
    reading = normalize_reading(reading)
    if reading == "":
        raise InvalidInputError("reading must be a nonempty string or null")
    context = {
        "failed_recall": failed_recall,
        "successful_inference": successful_inference,
        "transparent_composition": transparent_composition,
        "personally_useful": personally_useful,
    }
    for name, value in context.items():
        if not isinstance(value, bool):
            raise InvalidInputError(f"{name} must be a boolean")

    analysis = analyze_media(source_key, db_path=db_path)
    candidates = [
        candidate
        for tier in ("mine", "review", "skip")
        for candidate in analysis["candidates"][tier]
        if candidate["word"] == word
    ]
    if reading is not None:
        matches = [
            candidate for candidate in candidates if candidate["reading"] == reading
        ]
    else:
        exact = [
            candidate for candidate in candidates if candidate["identity_type"] == "exact"
        ]
        if len(exact) > 1:
            matches = sorted(candidate["reading"] for candidate in exact)
            raise AmbiguousReadingError(
                f"multiple media readings: {matches}", matches=matches
            )
        matches = exact or [
            candidate
            for candidate in candidates
            if candidate["identity_type"] == "spelling"
        ]
    if len(matches) != 1:
        identity = word if reading is None else f"{word} [{reading}]"
        raise MediaNotFoundError(f"media word not found: {identity}")

    result = dict(matches[0])
    context_reasons = sorted(name for name, enabled in context.items() if enabled)
    context_score = sum(config.CONTEXT_SCORE[name] for name in context_reasons)
    contextual_score = result["score"] + context_score
    personal = result["personal"]
    if personal["in_anki"] is True:
        contextual_tier = "skip"
    elif personal["known_identity"] is True:
        contextual_tier = "review" if failed_recall else "skip"
    elif contextual_score >= config.MINING_MINE_SCORE:
        contextual_tier = "mine"
    else:
        contextual_tier = "review"
    result.update(
        {
            "default_tier": result["tier"],
            "contextual_tier": contextual_tier,
            "default_score": result["score"],
            "context_score": context_score,
            "contextual_score": contextual_score,
            "context": context,
            "context_reasons": context_reasons,
        }
    )
    return result


def _report_row(candidate):
    personal = candidate["personal"]
    frequency = candidate["frequency"]
    return {
        "tier": candidate["tier"],
        "score": candidate["score"],
        "score_kind": candidate["score_kind"],
        "score_components": json.dumps(
            candidate["score_components"], ensure_ascii=False, separators=(",", ":")
        ),
        "reasons": json.dumps(
            candidate["reasons"], ensure_ascii=False, separators=(",", ":")
        ),
        "word": candidate["word"],
        "reading": candidate["reading"],
        "identity_type": candidate["identity_type"],
        "occurrences": candidate["media"]["occurrences"],
        "media_rank": candidate["media"]["media_rank"],
        "known_spelling": personal["known_spelling"],
        "known_identity": personal["known_identity"],
        "in_anki": personal["in_anki"],
        "encounters": personal["encounter_count"],
        "jpdb_rank": frequency.get("jpdb", {}).get("rank"),
        "bccwj_rank": frequency.get("bccwj_luw", {}).get("rank"),
    }


def export_media_analysis_csv(analysis, output_path) -> dict:
    destination = Path(output_path)
    rows = [
        _report_row(candidate)
        for tier in ("mine", "review", "skip")
        for candidate in analysis["candidates"][tier]
    ]
    part = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="",
            dir=destination.parent,
            prefix=f"{destination.name}.",
            suffix=".part",
            delete=False,
        ) as output:
            part = Path(output.name)
            writer = csv.DictWriter(output, fieldnames=_REPORT_FIELDS)
            writer.writeheader()
            writer.writerows(rows)
            output.flush()
        os.replace(part, destination)
        return {"output_path": str(destination), "row_count": len(rows)}
    except (OSError, UnicodeError, csv.Error) as error:
        if part is not None:
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
        raise SourceFormatError(
            f"media analysis report could not be written: {error}"
        ) from error
