"""Build a workspace of small files and scan it."""

from pathlib import Path
from textwrap import dedent

from oversight.graph import WorkspaceGraph
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
