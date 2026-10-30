"""Partition feasibility requires complete evidence and conservative, unbounded calls."""

from dataclasses import FrozenInstanceError
from itertools import pairwise, permutations
from typing import get_args
from unittest.mock import patch

import pytest

from oversight.changes import _partition as partition
from oversight.changes._partition import CannotSplit, Split, partition_targets
from oversight.changes._zones import PlanProblem, check_plan, expand_zone
from oversight.graph import parse_version
from oversight_contracts.graph import (
    Confidence,
    EdgeKind,
    GraphEdge,
    GraphSnapshot,
    NodeKind,
    ParseError,
    PossibleReason,
)
from oversight_contracts.plans import PlanZone, SubtaskDraft


def graph(*names: str, edges: tuple[GraphEdge, ...] = ()) -> GraphSnapshot:
    result = parse_version({f"{name}.py": b"def f(): pass\n" for name in names}, "head").graph
    return result.model_copy(update={"edges": list(edges)})


def call(src: str, dst: str, confidence: Confidence = "exact") -> GraphEdge:
    return GraphEdge(
        id=f"calls:{src}->{dst}",
        kind="calls",
        src=src,
        dst=dst,
        confidence=confidence,
        reason="dynamic_dispatch" if confidence == "possible" else None,
    )


def split(snapshot: GraphSnapshot, *targets: str) -> Split | CannotSplit:
    return partition_targets(snapshot, targets, completeness="complete")


def refuses(snapshot: GraphSnapshot, targets: tuple[str, ...], code: str) -> CannotSplit:
    before = snapshot.model_copy(deep=True)
    result = split(snapshot, *targets)
    assert isinstance(result, CannotSplit)
    assert result.status == "cannot_split"
    assert code in {diagnostic.code for diagnostic in result.diagnostics}
    assert not hasattr(result, "zones")
    assert snapshot == before
    return result


def assert_zones(snapshot: GraphSnapshot, result: Split | CannotSplit, *expected: set[str]) -> None:
    assert isinstance(result, Split)
    assert result.status == "split"
    assert result.zones == tuple(tuple(sorted(zone)) for zone in expected)
    drafts = [
        SubtaskDraft(task="Implement subtask", zone=PlanZone(node_ids=list(zone), paths=[]))
        for zone in result.zones
    ]
    assert check_plan(drafts, snapshot) == []
    for draft in drafts:
        assert expand_zone(draft.zone, snapshot).node_ids == set(draft.zone.node_ids)
    paths = {node.id: node.path for node in snapshot.nodes}
    assert {paths[n] for n in result.zones[0]}.isdisjoint(paths[n] for n in result.zones[1])


def test_independent_targets_are_canonical_and_inputs_immutable():
    snapshot = graph("a", "b", "c", edges=(call("a:f", "a:f"), call("c:f", "c:f")))
    targets = ["c:f", "a:f", "b:f", "a:f"]
    before = snapshot.model_copy(deep=True), targets.copy()
    expected = split(snapshot, *targets)
    assert_zones(snapshot, expected, {"a:f"}, {"b:f", "c:f"})
    for nodes in (snapshot.nodes, snapshot.nodes[::-1]):
        for edges in permutations(snapshot.edges):
            for selected in permutations(targets):
                reordered = snapshot.model_copy(update={"nodes": nodes, "edges": list(edges)})
                assert split(reordered, *selected) == expected
    assert (snapshot, targets) == before
    with pytest.raises(FrozenInstanceError):
        expected.status = "changed"  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(TypeError):
        expected.zones[0][0] = "changed"  # pyright: ignore[reportIndexIssue,reportAttributeAccessIssue]


@pytest.mark.parametrize("evidence", [None, "unknown", "incomplete", "future", True])
def test_missing_or_unknown_completeness_refuses(evidence):
    snapshot = graph("a", "b")
    assert isinstance(partition_targets(snapshot, ["a:f", "b:f"]), CannotSplit)
    result = partition_targets(snapshot, ["a:f", "b:f"], completeness=evidence)
    assert isinstance(result, CannotSplit)
    assert [d.code for d in result.diagnostics] == ["incomplete_evidence"]


@pytest.mark.parametrize("confidence", ["exact", "possible"])
@pytest.mark.parametrize("intermediate", [False, True])
def test_call_scc_peers_stay_atomic(confidence, intermediate):
    edges = [call("a:f", "b:f", confidence), call("b:f", "a:f", confidence)]
    peers = {"a:f", "b:f"}
    if intermediate:
        edges = [call("a:f", "b:f", confidence), call("b:f", "x:f"), call("x:f", "a:f")]
        peers.add("x:f")
    snapshot = graph("a", "b", "c", "x", edges=tuple(edges))
    assert_zones(snapshot, split(snapshot, "a:f", "c:f"), peers, {"c:f"})
    refuses(snapshot, ("a:f", "b:f"), "coupled_targets")


@pytest.mark.parametrize("confidence", ["exact", "possible"])
@pytest.mark.parametrize("test_caller", [False, True])
@pytest.mark.parametrize("depth", [1, 40])
def test_shared_ancestors_include_possible_tests_and_long_chains(confidence, test_caller, depth):
    root = "test_root" if test_caller else "root"
    chain = [f"x{i}" for i in range(depth)]
    edges = [call(f"{root}:f", "a:f", confidence), call(f"{root}:f", f"{chain[0]}:f")]
    edges += [call(f"{a}:f", f"{b}:f") for a, b in zip(chain, [*chain[1:], "b"], strict=True)]
    snapshot = graph("a", "b", root, *chain, edges=tuple(edges))
    refuses(snapshot, ("a:f", "b:f"), "coupled_targets")


@pytest.mark.parametrize("constructor", ["__init__", "__new__", "__post_init__"])
@pytest.mark.parametrize("transitive", [False, True])
def test_constructor_class_influence_is_preserved(constructor, transitive):
    files = {
        "a.py": f"class A:\n def {constructor}(self): pass\n".encode(),
        "b.py": b"def f(): pass\n",
        "root.py": b"def f(): pass\n",
    }
    snapshot = parse_version(files, "head").graph
    edges = [call("root:f", "a:A", "possible"), call("root:f", "b:f")]
    target = f"a:A.{constructor}"
    if transitive:
        snapshot = snapshot.model_copy(update={"nodes": snapshot.nodes + graph("leaf").nodes})
        edges.append(call(target, "leaf:f"))
        target = "leaf:f"
    snapshot = snapshot.model_copy(update={"edges": edges})
    refuses(snapshot, (target, "b:f"), "coupled_targets")


def test_direct_connectivity_and_transitive_group_coalescing():
    snapshot = graph(
        "a",
        "b",
        "c",
        "d",
        "x",
        "y",
        edges=(call("x:f", "a:f"), call("x:f", "b:f"), call("y:f", "b:f"), call("y:f", "c:f")),
    )
    refuses(snapshot, ("a:f", "b:f", "c:f"), "coupled_targets")
    assert_zones(
        snapshot, split(snapshot, "a:f", "b:f", "c:f", "d:f"), {"a:f", "b:f", "c:f"}, {"d:f"}
    )
    refuses(graph("a", "b", edges=(call("a:f", "b:f"),)), ("a:f", "b:f"), "coupled_targets")


@pytest.mark.parametrize("container", ["a", "a:A"])
def test_container_expansion_and_scc_closure_reach_fixed_point(container):
    snapshot = parse_version(
        {
            "a.py": b"class A:\n def f(self): pass\n",
            "b.py": b"def f(): pass\n",
            "c.py": b"def f(): pass\n",
        },
        "head",
    ).graph
    # A method's SCC peer is a module: expanding that module must add its function too.
    snapshot = snapshot.model_copy(update={"edges": [call("a:A.f", "b"), call("b", "a:A.f")]})
    expected = {"a:A", "a:A.f", "b", "b:f"}
    if container == "a":
        expected.add("a")
    assert_zones(snapshot, split(snapshot, container, "c:f"), expected, {"c:f"})
    refuses(snapshot, (container, "b:f"), "coupled_targets")


def test_same_file_coalesces_without_changing_check_plan_behavior():
    snapshot = parse_version(
        {"a.py": b"def f(): pass\ndef g(): pass\n", "b.py": b"def f(): pass\n"}, "head"
    ).graph
    drafts = [
        SubtaskDraft(task="Task", zone=PlanZone(node_ids=[n], paths=[])) for n in ("a:f", "a:g")
    ]
    assert check_plan(drafts, snapshot) == []
    refuses(snapshot, ("a:f", "a:g"), "coupled_targets")
    assert_zones(snapshot, split(snapshot, "a:f", "a:g", "b:f"), {"a:f", "a:g"}, {"b:f"})


@pytest.mark.parametrize("kind", ["imports", "inherits"])
def test_known_noncall_edges_do_not_couple(kind):
    edge = call("a:f", "b:f").model_copy(update={"kind": kind})
    snapshot = graph("a", "b", edges=(edge,))
    assert_zones(snapshot, split(snapshot, "a:f", "b:f"), {"a:f"}, {"b:f"})


@pytest.mark.parametrize("targets", [(), ("a:f",), ("a:f", "a:f")])
def test_too_few_targets_refuse(targets):
    refuses(graph("a", "b"), targets, "insufficient_groups")


def test_unknown_targets_are_sorted_and_refusal_is_immutable():
    result = refuses(graph("a", "b"), ("z", "a:f", "missing", "z"), "unknown_target")
    assert result.diagnostics[0].ids == ("missing", "z")
    with pytest.raises(FrozenInstanceError):
        result.diagnostics = ()  # pyright: ignore[reportAttributeAccessIssue]


def test_retained_nodes_with_parse_error_refuse():
    snapshot = graph("a", "b")
    snapshot = snapshot.model_copy(
        update={"parse_errors": [ParseError(path="a.py", line=1, message="bad")]}
    )
    refuses(snapshot, ("a:f", "b:f"), "parse_errors")


@pytest.mark.parametrize("identity", ["node", "edge"])
def test_duplicate_identities_refuse(identity):
    snapshot = graph("a", "b", edges=(call("a:f", "a:f"),))
    field = "nodes" if identity == "node" else "edges"
    values = getattr(snapshot, field)
    snapshot = snapshot.model_copy(update={field: [*values, values[0]]})
    refuses(snapshot, ("a:f", "b:f"), f"duplicate_{identity}_id")


@pytest.mark.parametrize("endpoint", ["src", "dst"])
@pytest.mark.parametrize("kind", ["calls", "imports", "inherits"])
def test_dangling_endpoints_are_checked_before_edge_filtering(endpoint, kind):
    edge = call("a:f", "b:f").model_copy(update={endpoint: "missing", "kind": kind})
    refuses(graph("a", "b", edges=(edge,)), ("a:f", "b:f"), "dangling_endpoint")


@pytest.mark.parametrize(
    "updates,code",
    [
        ({"parent_id": "missing"}, "dangling_parent"),
        ({"parent_id": None}, "malformed_containment"),
        ({"parent_id": "b"}, "malformed_containment"),
        ({"parent_id": "a:f"}, "malformed_containment"),
        ({"kind": "method"}, "malformed_containment"),
        ({"end_line": 0}, "malformed_containment"),
        ({"end_line": 100}, "malformed_containment"),
    ],
)
def test_malformed_containment_refuses(updates, code):
    snapshot = graph("a", "b")
    nodes = [n.model_copy(update=updates) if n.id == "a:f" else n for n in snapshot.nodes]
    refuses(snapshot.model_copy(update={"nodes": nodes}), ("a:f", "b:f"), code)


def test_containment_cycle_refuses_even_when_disconnected():
    snapshot = parse_version({"x.py": b"class A:\n class B: pass\n"}, "head").graph
    nodes = [
        n.model_copy(update={"parent_id": "x:A.B"}) if n.id == "x:A" else n for n in snapshot.nodes
    ]
    snapshot = graph("a", "b").model_copy(update={"nodes": graph("a", "b").nodes + nodes})
    refuses(snapshot, ("a:f", "b:f"), "containment_cycle")


@pytest.mark.parametrize("construction", ["copy", "construct"])
@pytest.mark.parametrize("kind", ["calls", "imports", "inherits", "future"])
@pytest.mark.parametrize("updates", [{"confidence": "future"}, {"reason": "future"}])
def test_unknown_edge_values_on_disconnected_edges_refuse(construction, kind, updates):
    edge = call("x:f", "y:f", "possible").model_copy(update={"kind": kind, **updates})
    if construction == "construct":
        edge = GraphEdge.model_construct(**edge.model_dump())
    refuses(graph("a", "b", "x", "y", edges=(edge,)), ("a:f", "b:f"), "unhandled_graph_enum")


def test_unknown_edge_kind_with_known_confidence_refuses():
    edge = call("x:f", "y:f").model_copy(update={"kind": "future"})
    refuses(graph("a", "b", "x", "y", edges=(edge,)), ("a:f", "b:f"), "unhandled_graph_enum")


def test_unknown_disconnected_node_kind_refuses():
    snapshot = graph("a", "b", "x")
    nodes = [
        n.model_copy(update={"kind": "future"}) if n.id == "x:f" else n for n in snapshot.nodes
    ]
    refuses(snapshot.model_copy(update={"nodes": nodes}), ("a:f", "b:f"), "unhandled_graph_enum")


@pytest.mark.parametrize("updates", [{"reason": "dynamic_dispatch"}, {"confidence": "possible"}])
def test_malformed_confidence_reason_pair_refuses(updates):
    edge = call("a:f", "a:f").model_copy(update=updates)
    refuses(graph("a", "b", edges=(edge,)), ("a:f", "b:f"), "malformed_edge")


@pytest.mark.parametrize(
    "table,enum",
    [
        ("_NODE_PARENTS", NodeKind),
        ("_CALL_KINDS", EdgeKind),
        ("_CALL_CONFIDENCE", Confidence),
        ("_POSSIBLE_REASONS", PossibleReason),
    ],
)
def test_enum_tables_are_exhaustive(table, enum):
    assert set(getattr(partition, table)) == set(get_args(enum.__value__))


def test_multiple_diagnostics_are_stable_under_snapshot_and_target_order():
    snapshot = graph("a", "b", edges=(call("missing-z", "missing-a"),))
    snapshot = snapshot.model_copy(update={"nodes": snapshot.nodes * 2})
    expected = refuses(snapshot, ("z", "missing", "z"), "duplicate_node_id")
    reordered = snapshot.model_copy(
        update={"nodes": snapshot.nodes[::-1], "edges": snapshot.edges[::-1]}
    )
    assert split(reordered, "missing", "z") == expected
    assert expected.diagnostics == tuple(
        sorted(expected.diagnostics, key=lambda d: (d.code, d.ids))
    )
    assert all(d.ids == tuple(sorted(set(d.ids))) for d in expected.diagnostics)


def test_conflicting_duplicate_nodes_have_order_independent_diagnostics():
    snapshot = graph("a", "b")
    duplicate = snapshot.nodes[0].model_copy(update={"path": "other.py"})
    snapshot = snapshot.model_copy(update={"nodes": [*snapshot.nodes, duplicate]})
    expected = refuses(snapshot, ("a:f", "b:f"), "duplicate_node_id")
    reordered = snapshot.model_copy(update={"nodes": snapshot.nodes[::-1]})
    assert split(reordered, "b:f", "a:f") == expected


@pytest.mark.parametrize("problems", [None, [PlanProblem("future_check", None)]])
def test_incomplete_or_failed_final_plan_check_refuses(problems):
    snapshot = graph("a", "b")
    assert isinstance(split(snapshot, "a:f", "b:f"), Split)
    with patch.object(partition, "check_plan", return_value=problems):
        refuses(snapshot, ("a:f", "b:f"), "revalidation_failed")


def test_traversals_do_not_depend_on_python_recursion_depth():
    names = [f"n{i}" for i in range(1100)]
    edges = [call(f"{a}:f", f"{b}:f") for a, b in pairwise(names)]
    edges.append(call(f"{names[-1]}:f", f"{names[0]}:f", "possible"))
    snapshot = graph(*names, "z", edges=tuple(edges))
    assert_zones(snapshot, split(snapshot, "n0:f", "z:f"), {f"{n}:f" for n in names}, {"z:f"})
