"""The review's possible callers: sources outside the exact cone, counted once."""

import pytest

from oversight.graph import possible_callers
from oversight_contracts.graph import GraphEdge, GraphNode, GraphSnapshot, ImpactCone, ImpactEntry


def snapshot(edges: list[tuple[str, str, str, str]]) -> GraphSnapshot:
    return GraphSnapshot(
        version="v1",
        commit="head",
        parse_errors=[],
        positions={},
        nodes=[
            GraphNode(
                id=name,
                kind="function",
                name=name,
                qualname=name,
                path="lib.py",
                start_line=1,
                end_line=2,
                parent_id=None,
                is_test=name == "test",
            )
            for name in ["seed", "entry", "a", "z", "test", "outside"]
        ],
        edges=[
            GraphEdge.model_validate(
                dict(
                    id=f"edge-{i}",
                    src=src,
                    dst=dst,
                    kind=kind,
                    confidence=confidence,
                    reason="unknown_receiver" if confidence == "possible" else None,
                )
            )
            for i, (src, dst, kind, confidence) in enumerate(edges)
        ],
    )


@pytest.mark.parametrize(
    ("edges", "expected"),
    [
        ([("z", "seed", "calls", "possible")], ["z"]),
        ([("a", "entry", "calls", "possible")], ["a"]),
        ([("a", "seed", "calls", "possible"), ("a", "entry", "calls", "exact")], []),
        ([("a", "seed", "calls", "exact"), ("a", "seed", "calls", "possible")], []),
        ([("a", "seed", "calls", "possible"), ("a", "outside", "calls", "exact")], ["a"]),
        ([("test", "seed", "calls", "possible")], []),
        ([("seed", "entry", "calls", "possible"), ("entry", "seed", "calls", "possible")], []),
        ([("a", "seed", "imports", "possible")], []),
        ([("missing", "seed", "calls", "possible")], []),
        ([("a", "missing", "calls", "possible")], []),
        ([("a", "outside", "calls", "possible")], []),
        (
            [
                ("z", "seed", "calls", "possible"),
                ("a", "entry", "calls", "possible"),
                ("z", "seed", "calls", "possible"),
                ("z", "entry", "calls", "possible"),
            ],
            ["a", "z"],
        ),
        ([], []),
    ],
)
def test_only_possible_sources_outside_targets_count_once(edges, expected):
    graph = snapshot(edges)
    cone = ImpactCone(
        seeds=["seed", "missing"],
        entries=[ImpactEntry(node_id="entry", distance=1)],
        edges_followed="exact",
        max_depth=5,
        max_nodes=10,
        omitted_by_depth=0,
        omitted_by_limit=0,
        tests_reaching=0,
    )
    before = (graph.model_dump_json(), cone.model_dump_json())
    assert possible_callers(graph, cone) == expected
    assert possible_callers(graph, cone) == expected
    assert (graph.model_dump_json(), cone.model_dump_json()) == before
