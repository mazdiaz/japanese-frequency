import unicodedata

from japanese_frequency.errors import InvalidInputError


_KATAKANA_ITERATION_MARKS = {"ヽ": "ゝ", "ヾ": "ゞ"}


def normalize_word(text: str) -> str:
    if not isinstance(text, str):
        raise InvalidInputError("word must be a non-empty string")
    value = unicodedata.normalize("NFC", text).strip()
    if not value:
        raise InvalidInputError("word must be a non-empty string")
    return value


def normalize_reading(text: str | None) -> str | None:
    if text is None:
        return None
    if not isinstance(text, str):
        raise InvalidInputError("reading must be a string or None")
    value = unicodedata.normalize("NFC", text).strip()
    return "".join(
        chr(ord(character) - 0x60)
        if "ァ" <= character <= "ヶ"
        else _KATAKANA_ITERATION_MARKS.get(character, character)
        for character in value
    )
