class JapaneseFrequencyError(Exception):
    code = "japanese_frequency_error"


class InvalidInputError(JapaneseFrequencyError):
    code = "invalid_input"


class NotFoundError(JapaneseFrequencyError):
    code = "not_found"


class AmbiguousReadingError(JapaneseFrequencyError):
    code = "ambiguous_reading"


class SourceFormatError(JapaneseFrequencyError):
    code = "source_format_error"


class DownloadError(JapaneseFrequencyError):
    code = "download_error"


class DatabaseError(JapaneseFrequencyError):
    code = "database_error"


class DatabaseBusyError(DatabaseError):
    code = "database_busy"
