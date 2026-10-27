"""Pure plan checks against one graph snapshot; no assignment or workspace effects."""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from oversight_contracts.graph import GraphSnapshot
from oversight_contracts.plans import PlanZone, SubtaskDraft


@dataclass(frozen=True)
class ExpandedZone:
    """Deduplicated selections, including unknown inputs, with unknowns also reported.

    Paths cover whole files but do not add nodes to node_ids. Snapshot files are those
    represented by nodes or parse errors, including files with no successful parse yet.
    """

    node_ids: frozenset[str]
    paths: frozenset[str]
    unknown_node_ids: frozenset[str]
    unknown_paths: frozenset[str]


@dataclass(frozen=True)
class PlanProblem:
    """A stable code and location; indices are zero-based, None means the whole plan.

    Codes: subtask_count, empty_task, empty_zone, unknown_node, unknown_path,
    node_overlap and path_overlap. Overlaps are reported for both involved subtasks.
    Only node problems set node_id; only path problems set path.
    """

    code: str
    subtask_index: int | None
    node_id: str | None = None
    path: str | None = None


def expand_zone(zone: PlanZone, snapshot: GraphSnapshot) -> ExpandedZone:
    """Include all descendants of selected modules/classes using parent_id, not names.

    The visited set also bounds traversal if a snapshot contains a containment cycle.
    Explicit unknown ids and paths stay in the result so they cannot silently disappear.
    """
    nodes = {node.id: node for node in snapshot.nodes}
    children: dict[str, set[str]] = defaultdict(set)
    for node in snapshot.nodes:
        if node.parent_id is not None:
            children[node.parent_id].add(node.id)

    selected = set(zone.node_ids)
    pending = [
        node_id
        for node_id in selected
        if node_id in nodes and nodes[node_id].kind in {"module", "class"}
    ]
    visited: set[str] = set()
    while pending:
        parent = pending.pop()
        if parent in visited:
            continue
        visited.add(parent)
        descendants = children[parent]
        selected.update(descendants)
        pending.extend(descendants - visited)

    paths = frozenset(zone.paths)
    files = {node.path for node in snapshot.nodes} | {error.path for error in snapshot.parse_errors}
    return ExpandedZone(
        node_ids=frozenset(selected),
        paths=paths,
        unknown_node_ids=frozenset(zone.node_ids).difference(nodes),
        unknown_paths=paths - files,
    )


def check_plan(subtasks: Sequence[SubtaskDraft], snapshot: GraphSnapshot) -> list[PlanProblem]:
    """Check two draft or assigned subtasks without mutating either input.

    Unknown selections are invalid, but do not produce secondary overlap problems.
    Two distinct nodes in one file are disjoint unless a zone explicitly selects that
    file. Problems sort by index (plan-wide first), code, node and path, independent of
    selection, node and edge order. Subtask order still defines the A/B indices.
    """
    problems: list[PlanProblem] = []
    if len(subtasks) != 2:
        problems.append(PlanProblem("subtask_count", None))
    zones = [expand_zone(subtask.zone, snapshot) for subtask in subtasks]
    for index, (subtask, zone) in enumerate(zip(subtasks, zones, strict=True)):
        if not subtask.task.strip():
            problems.append(PlanProblem("empty_task", index))
        if not zone.node_ids and not zone.paths:
            problems.append(PlanProblem("empty_zone", index))
        problems.extend(
            PlanProblem("unknown_node", index, node_id=n) for n in zone.unknown_node_ids
        )
        problems.extend(PlanProblem("unknown_path", index, path=p) for p in zone.unknown_paths)

    if len(zones) == 2:
        problems.extend(_overlaps(zones[0], zones[1], snapshot))
    return sorted(
        problems,
        key=lambda p: (
            -1 if p.subtask_index is None else p.subtask_index,
            p.code,
            p.node_id or "",
            p.path or "",
        ),
    )


def _overlaps(a: ExpandedZone, b: ExpandedZone, snapshot: GraphSnapshot) -> list[PlanProblem]:
    nodes = {node.id: node for node in snapshot.nodes}
    a_nodes = a.node_ids - a.unknown_node_ids
    b_nodes = b.node_ids - b.unknown_node_ids
    a_paths = a.paths - a.unknown_paths
    b_paths = b.paths - b.unknown_paths
    shared_nodes = a_nodes & b_nodes
    shared_paths = (
        (a_paths & b_paths)
        | (a_paths & {nodes[n].path for n in b_nodes})
        | (b_paths & {nodes[n].path for n in a_nodes})
    )
    return [
        problem
        for index in (0, 1)
        for problem in (
            [PlanProblem("node_overlap", index, node_id=n) for n in shared_nodes]
            + [PlanProblem("path_overlap", index, path=p) for p in shared_paths]
        )
    ]
