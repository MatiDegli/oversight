"""Plain-text review context from the agent's graph, without source or runtime access."""

from collections import defaultdict
from collections.abc import Iterable, Mapping

from oversight_contracts.graph import GraphNode, GraphSnapshot

HEADING = "Functions one call away from your change; check whether the comment applies to them too"
LIMIT = 8


def _listed(nodes: Mapping[str, GraphNode], related: Mapping[str, int]) -> str:
    ordered = sorted(related, key=lambda i: (related[i], nodes[i].qualname, nodes[i].path, i))
    labels = ("exact calls", "same class", "possible calls")
    items = [
        f"{nodes[i].qualname} ({nodes[i].path}; {labels[related[i]]})" for i in ordered[:LIMIT]
    ]
    if len(ordered) > LIMIT:
        items.append(f"+{len(ordered) - LIMIT} more")
    return ", ".join(items) or "none"


def graph_context(graph: GraphSnapshot, edited: Iterable[str]) -> str:
    """List direct callers and siblings, deduplicated, exact paths before possible ones.

    A shared caller establishes an exact relationship only if both calls are exact. Class
    membership establishes a sibling without a call. Module/class calls may establish siblings,
    but only functions and methods appear in the lists. Missing (removed) seeds are skipped.
    """
    nodes = {n.id: n for n in graph.nodes if n.kind in ("function", "method")}
    incoming: defaultdict[str, dict[str, int]] = defaultdict(dict)
    outgoing: defaultdict[str, dict[str, int]] = defaultdict(dict)
    classes: defaultdict[str, set[str]] = defaultdict(set)
    for node in nodes.values():
        if node.kind == "method" and node.parent_id is not None:
            classes[node.parent_id].add(node.id)
    for edge in graph.edges:
        if edge.kind != "calls" or edge.dst not in nodes:
            continue
        rank = 0 if edge.confidence == "exact" else 2
        incoming[edge.dst][edge.src] = min(incoming[edge.dst].get(edge.src, rank), rank)
        outgoing[edge.src][edge.dst] = min(outgoing[edge.src].get(edge.dst, rank), rank)
    blocks: list[str] = []
    seeds = sorted(set(edited) & nodes.keys(), key=lambda i: (nodes[i].qualname, nodes[i].path, i))
    for seed in seeds:
        node = nodes[seed]
        callers = {i: rank for i, rank in incoming[seed].items() if i in nodes}
        siblings: dict[str, int] = {}
        for caller, seed_rank in incoming[seed].items():
            for sibling, rank in outgoing[caller].items():
                confidence = max(seed_rank, rank)
                siblings[sibling] = min(siblings.get(sibling, confidence), confidence)
        if node.kind == "method" and node.parent_id is not None:
            for sibling in classes[node.parent_id]:
                siblings[sibling] = min(siblings.get(sibling, 1), 1)
        siblings.pop(seed, None)
        blocks.append(
            f"{node.qualname} ({node.path})\n"
            f"Callers: {_listed(nodes, callers)}\n"
            f"Siblings: {_listed(nodes, siblings)}"
        )
    return HEADING + "\n" + "\n\n".join(blocks) if blocks else ""
