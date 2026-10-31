"""What changed in main's graph between two snapshots, as a `graph.delta` payload."""

from typing import Literal

from oversight_contracts.graph import GraphDelta, GraphSnapshot

type DeltaCause = Literal["merge", "user_edit", "rescan", "revert"]


def graph_delta(
    old: GraphSnapshot, new: GraphSnapshot, cause: DeltaCause, commit: str
) -> GraphDelta | None:
    """The nodes and edges added, changed (same id, other fields) and removed from `old` to `new`,
    each list sorted by id, with `new`'s full list of parse errors. None when no node, edge or
    parse error changed, so there is nothing to send. A node that only moved, such as one below an
    added line, is changed: its lines differ."""
    old_nodes = {n.id: n for n in old.nodes}
    new_nodes = {n.id: n for n in new.nodes}
    old_edges = {e.id: e for e in old.edges}
    new_edges = {e.id: e for e in new.edges}
    delta = GraphDelta(
        from_version=old.version,
        to_version=new.version,
        commit=commit,
        cause=cause,
        nodes_added=[new_nodes[i] for i in sorted(new_nodes.keys() - old_nodes.keys())],
        nodes_changed=[
            new_nodes[i]
            for i in sorted(new_nodes.keys() & old_nodes.keys())
            if new_nodes[i] != old_nodes[i]
        ],
        nodes_removed=sorted(old_nodes.keys() - new_nodes.keys()),
        edges_added=[new_edges[i] for i in sorted(new_edges.keys() - old_edges.keys())],
        edges_changed=[
            new_edges[i]
            for i in sorted(new_edges.keys() & old_edges.keys())
            if new_edges[i] != old_edges[i]
        ],
        edges_removed=sorted(old_edges.keys() - new_edges.keys()),
        parse_errors=new.parse_errors,
    )
    unchanged = (
        not delta.nodes_added
        and not delta.nodes_changed
        and not delta.nodes_removed
        and not delta.edges_added
        and not delta.edges_changed
        and not delta.edges_removed
        and new.parse_errors == old.parse_errors
    )
    return None if unchanged else delta
