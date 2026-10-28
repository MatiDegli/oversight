"""Byte ceilings shared by independent backend modules; checked before decoding."""

from pathlib import Path
from typing import BinaryIO

MAX_BODY_BYTES = 1024 * 1024
MAX_FILE_BYTES = 256 * 1024
# Real plugin measurement (test_report_limits): 3,000 long-id parametrized cases,
# 9,001 records, 3,399,018 bytes total, largest record 380 bytes (including LF).
# Budget 10,000 cases * 3 phases * 380 bytes * 2 margin = 22,800,000 bytes;
# round up to 32 MiB. Per line, round 380 * 8 margin up to 4 KiB, including LF.
MAX_REPORT_BYTES = 32 * 1024 * 1024
MAX_REPORT_LINE_BYTES = 4 * 1024
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
