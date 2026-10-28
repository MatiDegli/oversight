"""Byte ceilings shared by independent backend modules; checked before decoding."""

from pathlib import Path
from typing import BinaryIO

MAX_BODY_BYTES = 1024 * 1024
MAX_FILE_BYTES = 256 * 1024
MAX_EVENT_LINE_BYTES = 1024 * 1024
MAX_GRAPH_EVENT_LINE_BYTES = 8 * 1024 * 1024
MAX_RANGE_FILE_BYTES = 64 * 1024 * 1024


class SizeLimitError(ValueError):
    """The actual bytes exceed the server's limit."""


def bounded_read(handle: BinaryIO, limit: int = MAX_FILE_BYTES, *, name: str = "file") -> bytes:
    data = handle.read(limit + 1)
    if len(data) > limit:
        raise SizeLimitError(f"{name} exceeds the {limit}-byte size limit")
    return data


def bounded_text(path: Path, *, errors: str = "strict") -> str:
    with path.open("rb") as handle:
        return bounded_read(handle, name=path.name).decode("utf-8", errors=errors)
