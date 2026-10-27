"""Small hand-built snapshots exercise pure zone expansion and plan diagnostics."""

from dataclasses import FrozenInstanceError
from itertools import permutations

import pytest

from oversight.changes._zones import ExpandedZone, PlanProblem, check_plan, expand_zone
from oversight_contracts.graph import GraphEdge, GraphNode, GraphSnapshot, NodeKind, ParseError
from oversight_contracts.plans import PlanSubtask, PlanZone, SubtaskDraft


def node(node_id: str, kind: NodeKind, path: str, parent: str | None = None) -> GraphNode:
    return GraphNode(
        id=node_id,
        kind=kind,
        name=node_id,
        qualname=node_id,
        path=path,
        start_line=1,
        end_line=20,
        parent_id=parent,
        is_test=False,
    )


@pytest.fixture
def snapshot() -> GraphSnapshot:
    return GraphSnapshot(
        version="graph-v1",
        commit="abc123",
        nodes=[
            node("shop", "module", "shop.py"),
            node("shop:Cart", "class", "shop.py", "shop"),
            node("shop:Cart.total", "method", "shop.py", "shop:Cart"),
            node("shop:Cart.total#2", "method", "shop.py", "shop:Cart"),
            node("shop:price", "function", "shop.py", "shop"),
            # Prefix similarity is not containment.
            node("shop.extra", "module", "shop/extra.py"),
            node("orders", "module", "orders.py"),
            node("orders:submit", "function", "orders.py", "orders"),
        ],
        edges=[
            GraphEdge(
                id="calls:orders:submit->shop:price",
                kind="calls",
                src="orders:submit",
                dst="shop:price",
                confidence="exact",
                reason=None,
            )
        ],
        parse_errors=[ParseError(path="broken.py", line=1, message="Invalid syntax")],
        positions={},
    )


def draft(
    *node_ids: str, paths: tuple[str, ...] = (), task: str = "Implement task"
) -> SubtaskDraft:
    return SubtaskDraft(task=task, zone=PlanZone(node_ids=list(node_ids), paths=list(paths)))


@pytest.mark.parametrize(
    ("selected", "expected"),
    [
        ("shop", {"shop", "shop:Cart", "shop:Cart.total", "shop:Cart.total#2", "shop:price"}),
        ("shop:Cart", {"shop:Cart", "shop:Cart.total", "shop:Cart.total#2"}),
        ("shop:Cart.total", {"shop:Cart.total"}),
    ],
)
def test_expands_containers_transitively_but_not_methods(
    snapshot: GraphSnapshot, selected: str, expected: set[str]
) -> None:
    assert expand_zone(draft(selected).zone, snapshot) == ExpandedZone(
        frozenset(expected), frozenset(), frozenset(), frozenset()
    )


def test_paths_stay_explicit_and_do_not_expand_into_nodes(snapshot: GraphSnapshot) -> None:
    assert expand_zone(draft(paths=("shop.py",)).zone, snapshot) == ExpandedZone(
        frozenset(), frozenset({"shop.py"}), frozenset(), frozenset()
    )


def test_reports_unknowns_without_dropping_them(snapshot: GraphSnapshot) -> None:
    expanded = expand_zone(draft("missing", paths=("missing.py", "shop")).zone, snapshot)
    assert isinstance(expanded.unknown_node_ids, frozenset)
    assert expanded == ExpandedZone(
        frozenset({"missing"}),
        frozenset({"missing.py", "shop"}),
        frozenset({"missing"}),
        frozenset({"missing.py", "shop"}),
    )
    assert check_plan([draft("missing", paths=("missing.py",)), draft("orders")], snapshot) == [
        PlanProblem("unknown_node", 0, node_id="missing"),
        PlanProblem("unknown_path", 0, path="missing.py"),
    ]


def test_files_with_parse_errors_are_known_paths(snapshot: GraphSnapshot) -> None:
    assert check_plan([draft(paths=("broken.py",)), draft("orders")], snapshot) == []


def test_container_selection_overlaps_a_descendant(snapshot: GraphSnapshot) -> None:
    assert check_plan([draft("shop"), draft("shop:Cart.total")], snapshot) == [
        PlanProblem("node_overlap", 0, node_id="shop:Cart.total"),
        PlanProblem("node_overlap", 1, node_id="shop:Cart.total"),
    ]


@pytest.mark.parametrize("swap", [False, True])
@pytest.mark.parametrize("selected", ["shop", "shop:Cart", "shop:Cart.total"])
def test_path_overlaps_any_node_of_its_file(
    snapshot: GraphSnapshot, swap: bool, selected: str
) -> None:
    tasks = [draft(paths=("shop.py",)), draft(selected)]
    assert check_plan(tasks[::-1] if swap else tasks, snapshot) == [
        PlanProblem("path_overlap", 0, path="shop.py"),
        PlanProblem("path_overlap", 1, path="shop.py"),
    ]


def test_explicit_paths_overlap_each_other(snapshot: GraphSnapshot) -> None:
    assert check_plan([draft(paths=("shop.py",)), draft(paths=("shop.py",))], snapshot) == [
        PlanProblem("path_overlap", 0, path="shop.py"),
        PlanProblem("path_overlap", 1, path="shop.py"),
    ]


@pytest.mark.parametrize(
    "tasks",
    [
        [draft("shop"), draft("orders")],
        [draft("shop:price"), draft("shop:Cart.total")],
        [draft(paths=("shop.py",)), draft("orders:submit")],
        [draft(paths=("shop.py",)), draft(paths=("orders.py",))],
    ],
)
def test_disjoint_zones_pass_despite_call_edges(
    snapshot: GraphSnapshot, tasks: list[SubtaskDraft]
) -> None:
    assert check_plan(tasks, snapshot) == []


def test_accepts_assigned_subtasks(snapshot: GraphSnapshot) -> None:
    tasks = [
        PlanSubtask(**draft("shop").model_dump(), agent_id="agent-1"),
        PlanSubtask(**draft("orders").model_dump(), agent_id="agent-2"),
    ]
    assert check_plan(tasks, snapshot) == []


@pytest.mark.parametrize("count", [0, 1, 3])
def test_requires_exactly_two_subtasks(snapshot: GraphSnapshot, count: int) -> None:
    assert check_plan([draft("shop")] * count, snapshot) == [PlanProblem("subtask_count", None)]


@pytest.mark.parametrize("text", ["", " ", "\n\t"])
def test_requires_nonblank_tasks_and_nonempty_zones(snapshot: GraphSnapshot, text: str) -> None:
    # Empty strings fail the wire contract first; construct one to exercise this pure check.
    invalid = SubtaskDraft.model_construct(task=text, zone=PlanZone(node_ids=[], paths=[]))
    assert check_plan([invalid, draft("orders")], snapshot) == [
        PlanProblem("empty_task", 0),
        PlanProblem("empty_zone", 0),
    ]


def test_unknown_selections_do_not_produce_secondary_overlap_problems(
    snapshot: GraphSnapshot,
) -> None:
    tasks = [draft("missing", paths=("missing.py",))] * 2
    assert check_plan(tasks, snapshot) == [
        PlanProblem("unknown_node", 0, node_id="missing"),
        PlanProblem("unknown_path", 0, path="missing.py"),
        PlanProblem("unknown_node", 1, node_id="missing"),
        PlanProblem("unknown_path", 1, path="missing.py"),
    ]


def test_duplicate_selections_do_not_duplicate_nodes_or_problems(snapshot: GraphSnapshot) -> None:
    tasks = [draft("shop:Cart", "shop:Cart.total", "missing", paths=("shop.py", "missing.py"))] * 2
    duplicated = [
        SubtaskDraft(
            task=t.task, zone=PlanZone(node_ids=t.zone.node_ids * 3, paths=t.zone.paths * 3)
        )
        for t in tasks
    ]
    assert expand_zone(duplicated[0].zone, snapshot) == expand_zone(tasks[0].zone, snapshot)
    problems = check_plan(tasks, snapshot)
    assert problems
    assert len(problems) == len(set(problems))
    assert check_plan(duplicated, snapshot) == problems


def test_problem_order_is_independent_of_selections_and_snapshot_order(
    snapshot: GraphSnapshot,
) -> None:
    ids = ("shop:Cart", "unknown-b", "unknown-a")
    paths = ("shop.py", "unknown-b.py", "unknown-a.py")
    other = draft("shop:Cart.total", "unknown-a", paths=("shop.py",))
    expected = check_plan([draft(*ids, paths=paths), other], snapshot)
    reordered = snapshot.model_copy(
        update={"nodes": snapshot.nodes[::-1], "edges": snapshot.edges[::-1]}
    )
    for ordered_ids in permutations(ids):
        for ordered_paths in permutations(paths):
            assert (
                check_plan([draft(*ordered_ids, paths=ordered_paths), other], reordered) == expected
            )


def test_checks_preserve_inputs_and_problem_is_frozen(snapshot: GraphSnapshot) -> None:
    tasks = [draft("shop"), draft("shop:Cart.total")]
    before = snapshot.model_dump(), [t.model_dump() for t in tasks]
    problem = check_plan(tasks, snapshot)[0]
    assert (snapshot.model_dump(), [t.model_dump() for t in tasks]) == before
    with pytest.raises(FrozenInstanceError):
        problem.code = "changed"  # pyright: ignore[reportAttributeAccessIssue]


def test_expansion_terminates_on_containment_cycles(snapshot: GraphSnapshot) -> None:
    cyclic = snapshot.model_copy(
        update={
            "nodes": [
                node("a", "class", "cycle.py", "b"),
                node("b", "class", "cycle.py", "a"),
            ]
        }
    )
    assert expand_zone(draft("a").zone, cyclic).node_ids == {"a", "b"}
