"""Read-only evidence for offline measurements; never changes the scanner's policy."""

import ast
import hashlib
import io
import os
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from oversight.graph._extract import module_name
from oversight.graph._workspace import python_files
from oversight.limits import SizeLimitError, bounded_read

type FileStatus = Literal["parsed", "size", "syntax", "encoding", "unimportable"]


@dataclass(frozen=True)
class FileCompleteness:
    path: str
    status: FileStatus
    sha256: str
    detail: str = ""


def graph_completeness(root: Path) -> tuple[FileCompleteness, ...]:
    """Audit current bytes using the scan's discovery and parsing rules.

    Call after scanning an unchanged root. This rereads the files, so a cached last-good
    parse cannot hide a gap. Unimportable paths are separate evidence, not a policy decision.
    I/O errors propagate: unreadable evidence must never attest completeness.
    """
    paths = set(python_files(root))
    # python_files drops unimportable paths before reading them. Retain those separately,
    # applying the very same traversal exclusions (including hidden file names).
    for directory, dirs, files in os.walk(root):
        dirs[:] = [
            d
            for d in dirs
            if not d.startswith(".")
            and d != "__pycache__"
            and not (Path(directory) / d).is_symlink()
        ]
        for name in files:
            full = Path(directory) / name
            path = full.relative_to(root).as_posix()
            if (
                name.endswith(".py")
                and not name.startswith(".")
                and not full.is_symlink()
                and full.is_file()
                and module_name(path) is None
            ):
                paths.add(path)
    return tuple(_file_status(root, path) for path in sorted(paths))


def _file_status(root: Path, path: str) -> FileCompleteness:
    digest = hashlib.sha256()
    with (root / path).open("rb") as handle:
        while chunk := handle.read(65536):
            digest.update(chunk)
        if module_name(path) is None:
            return FileCompleteness(path, "unimportable", digest.hexdigest())
        handle.seek(0)
        try:
            data = bounded_read(handle)
        except SizeLimitError as error:
            return FileCompleteness(path, "size", digest.hexdigest(), str(error))
    hashed = digest.hexdigest()
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
        data.decode(encoding)
    except (SyntaxError, UnicodeError, LookupError) as error:
        return FileCompleteness(path, "encoding", hashed, str(error))
    try:
        ast.parse(data, filename=path)
    except (SyntaxError, ValueError) as error:
        return FileCompleteness(path, "syntax", hashed, str(error))
    return FileCompleteness(path, "parsed", hashed)
