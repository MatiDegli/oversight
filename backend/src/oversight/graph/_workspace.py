"""Main's graph, read from a workspace directory and re-parsed only where a file's bytes changed."""

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from oversight.graph._extract import module_name, parse_file
from oversight.graph._facts import FileParse
from oversight.graph._link import link
from oversight.graph._spans import SpanIndex
from oversight_contracts.graph import GraphSnapshot, ParseError


@dataclass(frozen=True, slots=True)
class _Entry:
    digest: str
    """The hash of the bytes last read."""
    good: FileParse | None
    """The last parse that succeeded, possibly of older bytes."""
    error: ParseError | None


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
            path = full.relative_to(root).as_posix()
            if module_name(path) is not None:
                found.append(path)
    packages = {p.removesuffix("/__init__.py") for p in found if p.endswith("/__init__.py")}
    return sorted(p for p in found if p.removesuffix(".py") not in packages)


class WorkspaceGraph:
    """The graph of one directory. `scan` is synchronous: callers run it in a thread."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._entries: dict[str, _Entry] = {}
        self._spans = SpanIndex(())
        self._own: dict[str, str] = {}
        self.last_parsed: tuple[str, ...] = ()
        """The paths the last scan parsed, because they were new or their bytes changed."""

    def scan(self, commit: str) -> GraphSnapshot:
        entries: dict[str, _Entry] = {}
        parsed: list[str] = []
        for path in python_files(self._root):
            data = (self._root / path).read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            old = self._entries.get(path)
            if old is not None and old.digest == digest:
                entries[path] = old
                continue
            parsed.append(path)
            result = parse_file(path, data)
            if isinstance(result, ParseError):
                entries[path] = _Entry(digest, old.good if old else None, result)
            else:
                entries[path] = _Entry(digest, result, None)
        self._entries = entries
        self.last_parsed = tuple(parsed)

        errors = [e.error for e in entries.values() if e.error is not None]
        files = [e.good for _, e in sorted(entries.items()) if e.good is not None]
        nodes, edges = link(files)
        self._spans = SpanIndex(nodes)
        self._own = {nid: h for f in files for nid, h in f.own_hashes.items()}
        version = hashlib.sha256(
            "".join(f"{path}\0{e.digest}\n" for path, e in sorted(entries.items())).encode()
        ).hexdigest()
        return GraphSnapshot(
            version=version,
            commit=commit,
            nodes=nodes,
            edges=edges,
            parse_errors=sorted(errors, key=lambda e: (e.path, e.line or 0)),
            positions={},
        )

    def node_at(self, path: str, line: int) -> str | None:
        """The innermost node at a line of a file, as of the last scan."""
        return self._spans.node_at(path, line)

    def own_hash(self, node_id: str) -> str | None:
        """The hash of a node's own source, without the bodies of the nodes nested in it."""
        return self._own.get(node_id)
