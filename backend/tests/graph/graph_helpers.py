"""Build a workspace of small files and scan it."""

from pathlib import Path
from textwrap import dedent

from oversight.graph import CodeVersion, WorkspaceGraph, parse_version
from oversight_contracts.graph import GraphEdge, GraphNode, GraphSnapshot

COMMIT = "0" * 40


def write(root: Path, files: dict[str, str]) -> None:
    for path, source in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(dedent(source).lstrip("\n"))


def scan(root: Path, files: dict[str, str]) -> tuple[WorkspaceGraph, GraphSnapshot]:
    write(root, files)
    graph = WorkspaceGraph(root)
    return graph, graph.scan(COMMIT)


def node(snapshot: GraphSnapshot, node_id: str) -> GraphNode:
    found = [n for n in snapshot.nodes if n.id == node_id]
    assert found, f"no node {node_id}; nodes: {[n.id for n in snapshot.nodes]}"
    return found[0]


def calls(snapshot: GraphSnapshot, src: str) -> dict[str, GraphEdge]:
    """The call edges out of `src`, by target."""
    return {e.dst: e for e in snapshot.edges if e.kind == "calls" and e.src == src}


def summary(snapshot: GraphSnapshot, src: str) -> dict[str, str]:
    """Each call target of `src` with "exact" or the possible reason."""
    return {dst: e.reason or "exact" for dst, e in calls(snapshot, src).items()}


def version(files: dict[str, str], previous: CodeVersion | None = None) -> CodeVersion:
    """Parse a file set given as dedented sources, as a change's version is parsed."""
    data = {path: dedent(source).lstrip("\n").encode() for path, source in files.items()}
    return parse_version(data, COMMIT, previous)
