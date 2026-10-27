"""Parsed versions of a change's files, the nodes of one file, and main's graph after a merge."""

import asyncio
from collections.abc import Mapping

from oversight.graph import CodeVersion, DeltaCause, graph_delta, parse_version
from oversight_contracts.graph import ChangeGraph, GraphDelta, GraphSnapshot


async def parse(files: Mapping[str, str], commit: str, previous: CodeVersion | None) -> CodeVersion:
    """Parse a file set in a thread; with `previous`, only files whose bytes changed."""
    data = {path: text.encode("utf-8") for path, text in files.items()}
    return await asyncio.to_thread(parse_version, data, commit, previous)


def nodes_in_path(
    change: ChangeGraph, base: GraphSnapshot, head: GraphSnapshot, path: str
) -> list[str]:
    """The change's added, modified and removed nodes that live in `path`."""
    head_paths = {node.id: node.path for node in head.nodes}
    base_paths = {node.id: node.path for node in base.nodes}
    found = [node.id for node in change.nodes_added if node.path == path]
    found += [i for i in change.nodes_modified if head_paths.get(i) == path]
    found += [i for i in change.nodes_removed if base_paths.get(i) == path]
    return sorted(found)


def main_delta(
    old: GraphSnapshot, new: GraphSnapshot, commit: str, cause: DeltaCause
) -> GraphDelta:
    """Exactly one delta per commit on main, a merge or a save, so `from_version` always chains:
    `graph_delta`'s, or an empty one from `old.version` to `new.version` (equal when no Python file
    changed) when no node, edge or parse error changed, still carrying the new `commit`."""
    return graph_delta(old, new, cause, commit) or GraphDelta(
        from_version=old.version,
        to_version=new.version,
        commit=commit,
        cause=cause,
        nodes_added=[],
        nodes_changed=[],
        nodes_removed=[],
        edges_added=[],
        edges_changed=[],
        edges_removed=[],
        parse_errors=list(new.parse_errors),
    )


def with_positions(new: GraphSnapshot, old: GraphSnapshot) -> GraphSnapshot:
    """`new` with the preset layout of `old`, less the nodes that are gone, as the fold keeps it."""
    ids = {node.id for node in new.nodes}
    kept = {node_id: point for node_id, point in old.positions.items() if node_id in ids}
    return new.model_copy(update={"positions": kept})
