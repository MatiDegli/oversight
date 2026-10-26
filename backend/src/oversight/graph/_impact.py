"""The impact cone: what possibly calls the changed code, directly or through other callers.

A bounded reverse breadth-first search over exact call edges. Every node kind can be an entry:
a module whose top-level code calls into the cone, a class whose body does, or a class whose
constructor is in the cone. Calling a class runs its `__init__`, `__new__` or `__post_init__`, so
when one of those enters the cone (or is a seed), its class enters at the same distance and the
class's callers follow. Test nodes are never entries and the search does not pass through them;
`tests_reaching` counts the test functions that reach a seed instead.
"""

from collections import defaultdict
from collections.abc import Iterable

from oversight_contracts.graph import ChangeGraph, GraphNode, GraphSnapshot, ImpactCone, ImpactEntry

_CONSTRUCTORS = frozenset({"__init__", "__new__", "__post_init__"})


class NodeNotFoundError(LookupError):
    """A seed that is not a node of the graph the cone is computed on."""


class _Calls:
    """The exact call edges of one graph, indexed both ways."""

    def __init__(self, graph: GraphSnapshot) -> None:
        self.nodes: dict[str, GraphNode] = {n.id: n for n in graph.nodes}
        self.callers: defaultdict[str, set[str]] = defaultdict(set)
        self.callees: defaultdict[str, set[str]] = defaultdict(set)
        for edge in graph.edges:
            if edge.kind == "calls" and edge.confidence == "exact":
                self.callers[edge.dst].add(edge.src)
                self.callees[edge.src].add(edge.dst)

    def constructed_class(self, node_id: str) -> str | None:
        """The class a constructor method belongs to; None for any other node."""
        node = self.nodes[node_id]
        if node.kind != "method" or node.name not in _CONSTRUCTORS or node.parent_id is None:
            return None
        parent = self.nodes.get(node.parent_id)
        return parent.id if parent is not None and parent.kind == "class" else None

    def distances(self, seeds: Iterable[str], through_tests: bool) -> dict[str, int]:
        """Each node that reaches a seed through exact calls, with its distance; seeds are 0.
        Layer by layer, so a cycle ends when it meets a node already placed."""
        distance: dict[str, int] = {}
        layer: list[str] = []
        for seed in seeds:
            distance[seed] = 0
            layer.append(seed)
        depth = 0
        while layer:
            depth += 1
            found: list[str] = []
            for callee in layer:
                for caller in sorted(self.callers[callee]):
                    if caller in distance:
                        continue
                    if not through_tests and self.nodes[caller].is_test:
                        continue
                    distance[caller] = depth
                    found.append(caller)
            for node_id in list(found):
                # A constructor and its class share a file, so they are both tests or neither.
                cls = self.constructed_class(node_id)
                if cls is not None and cls not in distance:
                    distance[cls] = depth
                    found.append(cls)
            layer = found
        return distance


def impact_cone(
    graph: GraphSnapshot, seeds: Iterable[str], *, max_depth: int, max_nodes: int
) -> ImpactCone:
    """The cone of `seeds` on `graph`: main's graph, or a change's version. Entries are sorted by
    distance, then id; the first `max_nodes` within `max_depth` are kept. Every node reachable
    beyond `max_depth` counts in `omitted_by_depth`, every one within it that the node limit cut
    in `omitted_by_limit`. Raises `NodeNotFoundError` for a seed not in the graph."""
    if max_depth < 0 or max_nodes < 0:
        raise ValueError("max_depth and max_nodes must not be negative")
    calls = _Calls(graph)
    seed_set = set(seeds)
    missing = sorted(seed_set - calls.nodes.keys())
    if missing:
        raise NodeNotFoundError(", ".join(missing))
    for seed in list(seed_set):
        cls = calls.constructed_class(seed)
        if cls is not None:
            seed_set.add(cls)
    ordered_seeds = sorted(seed_set)

    distance = calls.distances(ordered_seeds, through_tests=False)
    reached = sorted((d, n) for n, d in distance.items() if n not in seed_set)
    within = [(d, n) for d, n in reached if d <= max_depth]
    kept = within[:max_nodes]

    reaching = calls.distances(ordered_seeds, through_tests=True)
    tests = sum(
        1
        for node in calls.nodes.values()
        if node.is_test
        and node.kind in ("function", "method")
        and node.name.startswith("test")
        and any(callee in reaching for callee in calls.callees[node.id])
    )
    return ImpactCone(
        seeds=ordered_seeds,
        entries=[ImpactEntry(node_id=n, distance=d) for d, n in kept],
        edges_followed="exact",
        max_depth=max_depth,
        max_nodes=max_nodes,
        omitted_by_depth=len(reached) - len(within),
        omitted_by_limit=len(within) - len(kept),
        tests_reaching=tests,
    )


def change_cone(
    change: ChangeGraph, graph: GraphSnapshot, *, max_depth: int, max_nodes: int
) -> ImpactCone:
    """The cone of a change, seeded by its added and modified nodes, on the change's version."""
    seeds = [n.id for n in change.nodes_added] + change.nodes_modified
    return impact_cone(graph, seeds, max_depth=max_depth, max_nodes=max_nodes)


def node_cone(graph: GraphSnapshot, node_id: str, *, max_depth: int, max_nodes: int) -> ImpactCone:
    """The cone of one node, as `GET /impact?node=&depth=` answers it."""
    return impact_cone(graph, [node_id], max_depth=max_depth, max_nodes=max_nodes)
