"""Main's graph, read from a workspace directory and re-parsed only where a file's bytes changed."""

import os
import stat
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import BinaryIO

from oversight.graph._spans import SpanIndex
from oversight.graph._version import FileEntry, assemble, parse_entries, select_python_paths
from oversight.limits import MAX_FILE_BYTES, SizeLimitError, bounded_read
from oversight_contracts.common import NodeId
from oversight_contracts.graph import GraphSnapshot, ParseError, Point


def python_files(root: Path, *, nt: bool | None = None) -> list[str]:
    """Workspace paths of the importable `.py` files, skipping hidden directories, `__pycache__`
    and symbolic links. A `pkg.py` beside `pkg/__init__.py` is skipped too: Python imports the
    package, so each module has one path."""
    if sys.platform == "win32" if nt is None else nt:
        return select_python_paths(python_paths_nt(root))
    found: list[str] = []
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
            if not name.endswith(".py") or full.is_symlink() or not full.is_file():
                continue
            found.append(full.relative_to(root).as_posix())
    return select_python_paths(found)


def plain_nt(path: Path, *, directory: bool) -> bool:
    info = os.lstat(path)
    return not (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400) and (
        stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    )


def python_paths_nt(root: Path) -> list[str]:
    if not all(plain_nt(p, directory=True) for p in (root, *root.parents)):
        return []
    found: list[str] = []
    for directory, dirs, files in os.walk(root):
        dirs[:] = [
            d
            for d in dirs
            if not d.startswith(".")
            and d.casefold() != "__pycache__"
            and plain_nt(Path(directory) / d, directory=True)
        ]
        for name in files:
            full = Path(directory) / name
            if (
                name.endswith(".py")
                and not name.startswith(".")
                and plain_nt(full, directory=False)
            ):
                found.append(full.relative_to(root).as_posix())
    return sorted(found)


def open_source(root: Path, path: str, *, nt: bool | None = None) -> BinaryIO:
    target = root / path
    if not (sys.platform == "win32" if nt is None else nt):
        return target.open("rb")
    if not all(plain_nt(p, directory=True) for p in target.parents) or not plain_nt(
        target, directory=False
    ):
        raise OSError("linked graph source")
    fd = os.open(target, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        fresh, opened = os.lstat(target), os.fstat(fd)
        if (
            stat.S_ISLNK(fresh.st_mode)
            or getattr(fresh, "st_file_attributes", 0) & 0x400
            or not stat.S_ISREG(fresh.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or (fresh.st_dev, fresh.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise OSError("graph source identity changed")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


class WorkspaceGraph:
    """The graph of one directory. `scan` is synchronous: callers run it in a thread. With a
    `layout`, every snapshot carries the positions of its nodes."""

    def __init__(self, root: Path, layout: Mapping[NodeId, Point] | None = None) -> None:
        self._root = root
        self._layout = layout
        self._entries: dict[str, FileEntry] = {}
        self._spans = SpanIndex(())
        self._own: dict[str, str] = {}
        self.last_parsed: tuple[str, ...] = ()
        """The paths the last scan parsed, because they were new or their bytes changed."""

    def scan(self, commit: str) -> GraphSnapshot:
        files: dict[str, bytes] = {}
        oversized: list[str] = []
        for path in python_files(self._root):
            try:
                # Decode only in the parser, which handles invalid UTF-8 as a parse error.
                with open_source(self._root, path) as handle:
                    files[path] = bounded_read(handle)
            except SizeLimitError:
                oversized.append(path)
        self._entries, self.last_parsed = parse_entries(files, self._entries)
        for path in oversized:
            self._entries[path] = FileEntry(
                "too_large",
                None,
                ParseError(
                    path=path,
                    line=None,
                    message=f"source exceeds the {MAX_FILE_BYTES}-byte size limit; skipped",
                ),
            )
        snapshot, self._own = assemble(self._entries, commit, self._layout)
        self._spans = SpanIndex(snapshot.nodes)
        return snapshot

    def node_at(self, path: str, line: int) -> str | None:
        """The innermost node at a line of a file, as of the last scan."""
        return self._spans.node_at(path, line)

    def own_hash(self, node_id: str) -> str | None:
        """The hash of a node's own source, without the bodies of the nodes nested in it."""
        return self._own.get(node_id)
