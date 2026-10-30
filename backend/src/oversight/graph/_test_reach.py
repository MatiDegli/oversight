"""Static test call-graph reach, never coverage or proof that a test ran.

Use the parser's `is_test` path rule: test/ or tests/ directories, test_*.py,
*_test.py and conftest.py. Only functions/methods named test* are test roots;
helpers and fixtures need an exact caller chain from such a root.
An exhaustive miss supports "no test calls it" on exact edges, not a coverage badge.
"""

from collections import defaultdict, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from oversight_contracts.graph import GraphSnapshot


@dataclass(frozen=True, slots=True)
class TestReach:
    """Only exhaustively searched functions enter `unreached_nodes`."""

    unreached_nodes: tuple[str, ...]
    complete: bool


def call_graph_test_reach(
    graph: GraphSnapshot, changed: Iterable[str], *, max_depth: int, max_nodes: int
) -> TestReach:
    """Reverse exact calls per changed function/method, with independent walk budgets.

    Depth counts edges and the node budget includes the seed and test roots. A cut
    walk or missing seed stays unclassified. Containers are ignored. Parse errors
    make absence unknowable, so no negative classification is published for them.
    Cycles terminate through the visited set; ordering is deterministic.
    """
    if max_depth < 0 or max_nodes < 0:
        raise ValueError("max_depth and max_nodes must not be negative")
    if graph.parse_errors:
        return TestReach((), False)
    nodes = {node.id: node for node in graph.nodes}
    callers: defaultdict[str, set[str]] = defaultdict(set)
    for edge in graph.edges:
        if edge.kind == "calls" and edge.confidence == "exact":
            callers[edge.dst].add(edge.src)
    tests = {
        node.id
        for node in graph.nodes
        if node.is_test and node.kind in ("function", "method") and node.name.startswith("test")
    }
    complete = True
    unreached: list[str] = []
    for seed in sorted(set(changed)):
        if seed not in nodes:
            complete = False
            continue
        if nodes[seed].kind not in ("function", "method"):
            continue
        reached = _walk(callers, tests, seed, max_depth, max_nodes)
        if reached is None:
            complete = False
        elif not reached:
            unreached.append(seed)
    return TestReach(tuple(unreached), complete)


def _walk(
    callers: Mapping[str, set[str]], tests: set[str], seed: str, max_depth: int, max_nodes: int
) -> bool | None:
    if max_nodes == 0:
        return None
    seen = {seed}
    pending = deque([(seed, 0)])
    while pending:
        node_id, depth = pending.popleft()
        if node_id in tests:
            return True
        for caller in sorted(callers.get(node_id, set()) - seen):
            if depth == max_depth or len(seen) == max_nodes:
                return None
            seen.add(caller)
            pending.append((caller, depth + 1))
    return False
