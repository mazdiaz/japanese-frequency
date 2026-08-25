import hashlib
import tempfile
from contextlib import contextmanager
from pathlib import Path

from japanese_frequency.errors import SourceFormatError


@contextmanager
def snapshot_source(path):
    path = Path(path)
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
            with path.open("rb") as source, snapshot.open("wb") as output:
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
