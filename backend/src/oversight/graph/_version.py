"""A graph built from a set of files: main's working tree, or a change's version read from git.

Each file is parsed on its own and cached by the hash of its bytes; the linker then resolves the
calls across all of them. A file that fails to parse keeps its last good parse, if it had one.
"""

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from oversight.graph._extract import module_name, parse_file
from oversight.graph._facts import FileParse
from oversight.graph._layout import positions_of
from oversight.graph._link import link
from oversight_contracts.common import NodeId
from oversight_contracts.graph import GraphSnapshot, ParseError, Point


@dataclass(frozen=True, slots=True)
class FileEntry:
    digest: str
    """The hash of the bytes last read."""
    good: FileParse | None
    """The last parse that succeeded, possibly of older bytes."""
    error: ParseError | None


def select_python_paths(paths: Iterable[str]) -> list[str]:
    """The importable `.py` files among workspace paths, sorted: no segment hidden or
    `__pycache__`, every segment an identifier. A `pkg.py` beside `pkg/__init__.py` is dropped,
    because Python imports the package, so each module has one path."""
    found = [
        p
        for p in paths
        if p.endswith(".py")
        and not any(s.startswith(".") or s == "__pycache__" for s in p.split("/"))
        and module_name(p) is not None
    ]
    packages = {p.removesuffix("/__init__.py") for p in found if p.endswith("/__init__.py")}
    return sorted({p for p in found if p.removesuffix(".py") not in packages})


def parse_entries(
    files: Mapping[str, bytes], previous: Mapping[str, FileEntry]
) -> tuple[dict[str, FileEntry], tuple[str, ...]]:
    """Each Python file's entry, reusing `previous` where the bytes are the same, and the paths
    parsed now because they were new or changed."""
    entries: dict[str, FileEntry] = {}
    parsed: list[str] = []
    for path in select_python_paths(files):
        data = files[path]
        digest = hashlib.sha256(data).hexdigest()
        old = previous.get(path)
        if old is not None and old.digest == digest:
            entries[path] = old
            continue
        parsed.append(path)
        result = parse_file(path, data)
        if isinstance(result, ParseError):
            entries[path] = FileEntry(digest, old.good if old else None, result)
        else:
            entries[path] = FileEntry(digest, result, None)
    return entries, tuple(parsed)


def assemble(
    entries: Mapping[str, FileEntry],
    commit: str,
    layout: Mapping[NodeId, Point] | None = None,
) -> tuple[GraphSnapshot, dict[str, str]]:
    """The linked graph of the entries, and each node's own-source hash. The snapshot carries the
    layout's position for each of its nodes that has one."""
    errors = [e.error for e in entries.values() if e.error is not None]
    files = [e.good for _, e in sorted(entries.items()) if e.good is not None]
    nodes, edges = link(files)
    own = {nid: h for f in files for nid, h in f.own_hashes.items()}
    version = hashlib.sha256(
        "".join(f"{path}\0{e.digest}\n" for path, e in sorted(entries.items())).encode()
    ).hexdigest()
    snapshot = GraphSnapshot(
        version=version,
        commit=commit,
        nodes=nodes,
        edges=edges,
        parse_errors=sorted(errors, key=lambda e: (e.path, e.line or 0)),
        positions=positions_of(layout or {}, [n.id for n in nodes]),
    )
    return snapshot, own


@dataclass(frozen=True, slots=True)
class CodeVersion:
    """One version of the code, parsed: its graph and each node's own-source hash. Build it with
    `parse_version`."""

    graph: GraphSnapshot
    own_hashes: Mapping[str, str]
    """Per node id: the hash of its own source, without the nodes nested in it."""
    entries: Mapping[str, FileEntry] = field(repr=False, compare=False)


def parse_version(
    files: Mapping[str, bytes],
    commit: str,
    previous: CodeVersion | None = None,
    layout: Mapping[NodeId, Point] | None = None,
) -> CodeVersion:
    """Parse a set of files, given as workspace path to bytes; paths that are not importable
    Python files are ignored. With `previous`, files whose bytes did not change are not parsed
    again, and a file that fails to parse keeps its parse from `previous`. With `layout`, the graph
    carries the positions of its nodes; `previous`'s layout is not inherited."""
    entries, _ = parse_entries(files, previous.entries if previous else {})
    graph, own = assemble(entries, commit, layout)
    return CodeVersion(graph, own, entries)
