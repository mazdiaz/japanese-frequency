class JapaneseFrequencyError(Exception):
    code = "japanese_frequency_error"


class InvalidInputError(JapaneseFrequencyError):
    code = "invalid_input"


class NotFoundError(JapaneseFrequencyError):
    code = "not_found"


class SourceNotFoundError(NotFoundError):
    code = "source_not_found"


class MediaNotFoundError(NotFoundError):
    code = "media_not_found"


class AmbiguousReadingError(JapaneseFrequencyError):
    code = "ambiguous_reading"

    def __init__(self, message, *, matches=None):
        super().__init__(message)
        self.matches = sorted(matches) if matches is not None else []


class SourceFormatError(JapaneseFrequencyError):
    code = "source_format_error"


class DownloadError(JapaneseFrequencyError):
    code = "download_error"


class DatabaseError(JapaneseFrequencyError):
    code = "database_error"


class DatabaseBusyError(DatabaseError):
    code = "database_busy"
