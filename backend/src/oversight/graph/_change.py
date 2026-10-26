"""A change's overlay: its version's graph against its base's, both parsed from file sets."""

from oversight.graph._version import CodeVersion
from oversight_contracts.graph import ChangeGraph


def change_graph(base: CodeVersion, head: CodeVersion) -> ChangeGraph:
    """The nodes the change added, modified (own source differs, nested nodes excluded) and
    removed, and the edges its version adds or drops, each list sorted by id. The added edges are
    every edge of `head` missing from `base`: the added nodes' calls, and new calls or imports in
    modified code."""
    base_ids = {n.id for n in base.graph.nodes}
    head_nodes = {n.id: n for n in head.graph.nodes}
    base_edges = {e.id for e in base.graph.edges}
    head_edges = {e.id: e for e in head.graph.edges}
    return ChangeGraph(
        nodes_added=[head_nodes[i] for i in sorted(head_nodes.keys() - base_ids)],
        nodes_modified=sorted(
            i
            for i in head_nodes.keys() & base_ids
            if head.own_hashes.get(i) != base.own_hashes.get(i)
        ),
        nodes_removed=sorted(base_ids - head_nodes.keys()),
        edges_added=[head_edges[i] for i in sorted(head_edges.keys() - base_edges)],
        edges_removed=sorted(base_edges - head_edges.keys()),
    )
