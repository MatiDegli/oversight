"""Stream large files without ever allocating a whole file or an unbounded line."""

import codecs
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from oversight.limits import MAX_RANGE_FILE_BYTES
from oversight.workspace.errors import WorkspaceError
from oversight.workspace.files import open_file


@dataclass(frozen=True)
class FileRange:
    lines: list[str]
    lengths: list[int]
    total: int
    hash: str


def read_range(root: Path, path: str, first: int, last: int, chars: int) -> FileRange:
    """Keep only selected line prefixes, but stream all bytes for a coherent edit hash.

    A fixed-size read and incremental decoder handle long lines within the absolute file cap.
    Match str.splitlines(), including CRLF across chunk boundaries and Unicode separators.
    """
    digest = hashlib.sha256()
    decoder = codecs.getincrementaldecoder("utf-8")()
    lines: list[str] = []
    lengths: list[int] = []
    number, length = 1, 0
    prefix: list[str] = []
    previous_cr = False
    kept = 0

    def finish() -> None:
        nonlocal number, length, prefix, kept
        if first <= number <= last:
            text = "".join(prefix)
            lines.append(text)
            lengths.append(length)
            kept += len(text)
        number += 1
        length, prefix = 0, []

    try:
        with open_file(root, path) as handle:
            if os.fstat(handle.fileno()).st_size > MAX_RANGE_FILE_BYTES:
                raise WorkspaceError(f"{path} exceeds the {MAX_RANGE_FILE_BYTES}-byte size limit")
            consumed = 0
            while True:
                chunk = handle.read(min(8192, MAX_RANGE_FILE_BYTES - consumed + 1))
                consumed += len(chunk)
                if consumed > MAX_RANGE_FILE_BYTES:
                    raise WorkspaceError(
                        f"{path} exceeds the {MAX_RANGE_FILE_BYTES}-byte size limit"
                    )
                digest.update(chunk)
                text = decoder.decode(chunk, final=not chunk)
                for char in text:
                    if char == "\n" and previous_cr:
                        previous_cr = False
                        continue
                    previous_cr = char == "\r"
                    if char in "\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029":
                        finish()
                    else:
                        length += 1
                        if first <= number <= last and kept + len(prefix) < chars:
                            prefix.append(char)
                if not chunk:
                    break
    except UnicodeDecodeError as error:
        raise WorkspaceError(f"not a UTF-8 text file: {path}") from error
    if length:
        finish()
    return FileRange(lines, lengths, number - 1, digest.hexdigest())
