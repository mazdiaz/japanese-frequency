import hashlib
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from japanese_frequency.errors import (
    InvalidInputError,
    SourceFormatError,
    SourceNotFoundError,
)


def validated_source_path(path):
    if not isinstance(path, (str, os.PathLike)):
        raise InvalidInputError("path must be a string or path-like object")
    try:
        value = os.fspath(path)
    except TypeError as error:
        raise InvalidInputError(
            "path must be a string or path-like object"
        ) from error
    if not isinstance(value, str):
        raise InvalidInputError("path must be a string or path-like object")
    return Path(value)


@contextmanager
def snapshot_source(path):
    path = validated_source_path(path)
    digest = hashlib.sha256()
    try:
        temporary_directory = tempfile.TemporaryDirectory(
            prefix="japanese-frequency-"
        )
    except OSError as error:
        raise SourceFormatError(
            f"source snapshot could not be created: {error}"
        ) from error
    try:
        snapshot = Path(temporary_directory.name) / f"source{path.suffix}"
        try:
            source = path.open("rb")
        except FileNotFoundError as error:
            raise SourceNotFoundError(f"source file not found: {path}") from error
        except OSError as error:
            raise SourceFormatError(
                f"source snapshot could not be read: {error}"
            ) from error
        try:
            with source, snapshot.open("wb") as output:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
                    output.write(chunk)
        except OSError as error:
            raise SourceFormatError(
                f"source snapshot could not be read: {error}"
            ) from error
        yield snapshot, digest.hexdigest()
    except BaseException:
        try:
            temporary_directory.cleanup()
        except BaseException:
            pass
        raise
    try:
        temporary_directory.cleanup()
    except OSError as error:
        raise SourceFormatError(
            f"source snapshot cleanup failed: {error}"
        ) from error
