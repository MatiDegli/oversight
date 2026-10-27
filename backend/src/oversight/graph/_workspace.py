"""Main's graph, read from a workspace directory and re-parsed only where a file's bytes changed."""

import os
from collections.abc import Mapping
from pathlib import Path

from oversight.graph._spans import SpanIndex
from oversight.graph._version import FileEntry, assemble, parse_entries, select_python_paths
from oversight.limits import MAX_FILE_BYTES, SizeLimitError, bounded_read
from oversight_contracts.common import NodeId
from oversight_contracts.graph import GraphSnapshot, ParseError, Point


def python_files(root: Path) -> list[str]:
    """Workspace paths of the importable `.py` files, skipping hidden directories, `__pycache__`
    and symbolic links. A `pkg.py` beside `pkg/__init__.py` is skipped too: Python imports the
    package, so each module has one path."""
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
                with (self._root / path).open("rb") as handle:
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
