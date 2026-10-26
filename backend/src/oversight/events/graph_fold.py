"""Main's graph as the log has reached it: the snapshot with every later delta applied.

The store folds each committed `graph.snapshot` and `graph.delta` into one `GraphSnapshot`, in the
same step that advances its last `seq`, so `GET /graph` reads the graph and the `seq` it reflects
together. Replay and live runs use the same fold, since both reach the client only through events.
"""

from oversight_contracts.graph import GraphDelta, GraphSnapshot


def apply_delta(graph: GraphSnapshot, delta: GraphDelta) -> GraphSnapshot:
    """Remove, then replace in place, then append, for nodes and edges alike.

    Positions of removed nodes are dropped; the others stay, since they are the preset layout.
    """
    removed_nodes = set(delta.nodes_removed)
    changed_nodes = {node.id: node for node in delta.nodes_changed}
    nodes = [
        changed_nodes.get(node.id, node) for node in graph.nodes if node.id not in removed_nodes
    ]
    nodes.extend(delta.nodes_added)

    removed_edges = set(delta.edges_removed)
    changed_edges = {edge.id: edge for edge in delta.edges_changed}
    edges = [
        changed_edges.get(edge.id, edge) for edge in graph.edges if edge.id not in removed_edges
    ]
    edges.extend(delta.edges_added)

    positions = {
        node_id: point for node_id, point in graph.positions.items() if node_id not in removed_nodes
    }
    return GraphSnapshot(
        version=delta.to_version,
        commit=delta.commit,
        nodes=nodes,
        edges=edges,
        parse_errors=list(delta.parse_errors),
        positions=positions,
    )
