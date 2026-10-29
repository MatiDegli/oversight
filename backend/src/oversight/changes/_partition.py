"""Pure, unwired feasibility under explicitly complete server-owned graph evidence.

This is a call-only policy: imports/inherits are validated but excluded. No production
completeness producer exists; parsing success and absent edges never establish completeness.
Every traversal is iterative and bounded by the finite snapshot, without a display-cone cap.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from oversight.changes._zones import check_plan, expand_zone
from oversight_contracts.graph import (
    Confidence,
    EdgeKind,
    GraphNode,
    GraphSnapshot,
    NodeKind,
    PossibleReason,
)
from oversight_contracts.plans import PlanZone, SubtaskDraft

_NODE_PARENTS: dict[NodeKind, frozenset[NodeKind | None]] = {
    "module": frozenset({None}),
    "class": frozenset({"module", "class"}),
    "function": frozenset({"module"}),
    "method": frozenset({"class"}),
}
_CALL_KINDS: dict[EdgeKind, bool] = {"calls": True, "imports": False, "inherits": False}
_CALL_CONFIDENCE: dict[Confidence, bool] = {"exact": True, "possible": True}
_POSSIBLE_REASONS: frozenset[PossibleReason] = frozenset(
    {"unknown_receiver", "several_candidates", "rebound_name", "star_import", "dynamic_dispatch"}
)
_CONSTRUCTORS = frozenset({"__init__", "__new__", "__post_init__"})
type Adjacency = dict[str, set[str]]
type Components = dict[str, frozenset[str]]


@dataclass(frozen=True)
class Diagnostic:
    """Stable code with sorted relevant node/edge identities or parse-error paths."""

    code: str
    ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Split:
    """Exactly two canonical, expanded node selections; immutable at every level."""

    zones: tuple[tuple[str, ...], tuple[str, ...]]
    status: Literal["split"] = "split"


@dataclass(frozen=True)
class CannotSplit:
    """No candidate partition is exposed when any evidence or check fails."""

    diagnostics: tuple[Diagnostic, ...]
    status: Literal["cannot_split"] = "cannot_split"


def partition_targets(
    snapshot: GraphSnapshot,
    target_ids: Iterable[str],
    *,
    completeness: Literal["unknown", "incomplete", "complete"] = "unknown",
) -> Split | CannotSplit:
    """Determine feasibility, preserving all inputs and requiring an explicit attestation.

    `completeness` is internal server evidence about this entire snapshot, never model or
    request input. Unknown/future values refuse. Success is conditional on that evidence
    and this conservative policy, not a claim about unresolved calls absent from the graph.
    """
    targets = frozenset(target_ids)
    problems = _validate(snapshot, targets)
    if completeness != "complete":
        problems.append(Diagnostic("incomplete_evidence"))
    if problems:
        return _refuse(problems)
    if len(targets) < 2:
        return _refuse([Diagnostic("insufficient_groups", tuple(sorted(targets)))])

    calls, reverse = _adjacency(snapshot)
    components = _components(calls)
    groups = [_close(frozenset({target}), snapshot, components) for target in sorted(targets)]
    nodes = {node.id: node for node in snapshot.nodes}
    groups = _coalesce(groups, nodes, reverse)
    if len(groups) < 2:
        return _refuse([Diagnostic("coupled_targets", tuple(sorted(targets)))])
    groups.sort(key=lambda group: tuple(sorted(group)))
    zones = (groups[0], frozenset[str]().union(*groups[1:]))
    if not _revalidate(zones, targets, snapshot, nodes, components, reverse):
        return _refuse([Diagnostic("revalidation_failed", tuple(sorted(targets)))])
    return Split((tuple(sorted(zones[0])), tuple(sorted(zones[1]))))


def _refuse(problems: Iterable[Diagnostic]) -> CannotSplit:
    return CannotSplit(tuple(sorted(set(problems), key=lambda p: (p.code, p.ids))))


def _validate(snapshot: GraphSnapshot, targets: frozenset[str]) -> list[Diagnostic]:
    """Validate the whole snapshot before discarding any known non-call edges."""
    problems: list[Diagnostic] = []
    nodes = {node.id: node for node in snapshot.nodes}
    for kind, identities in (
        ("node", [node.id for node in snapshot.nodes]),
        ("edge", [edge.id for edge in snapshot.edges]),
    ):
        duplicates = tuple(sorted(n for n, count in Counter(identities).items() if count > 1))
        if duplicates:
            problems.append(Diagnostic(f"duplicate_{kind}_id", duplicates))
    missing = targets - nodes.keys()
    if missing:
        problems.append(Diagnostic("unknown_target", tuple(sorted(missing))))
    if snapshot.parse_errors:
        problems.append(
            Diagnostic("parse_errors", tuple(sorted({e.path for e in snapshot.parse_errors})))
        )
    # Never choose an arbitrary definition of a duplicate id to interpret containment.
    # Still check every enum and edge; contextual containment requires unique identities.
    unique_nodes = len(nodes) == len(snapshot.nodes)
    for node in snapshot.nodes:
        problems.extend(_node_problems(node, nodes if unique_nodes else None))
    if unique_nodes:
        problems.extend(_containment_cycles(nodes))
    for edge in snapshot.edges:
        if (
            edge.kind not in _CALL_KINDS
            or edge.confidence not in _CALL_CONFIDENCE
            or (edge.reason is not None and edge.reason not in _POSSIBLE_REASONS)
        ):
            problems.append(Diagnostic("unhandled_graph_enum", (edge.id,)))
        if (edge.confidence == "possible") != (edge.reason is not None):
            problems.append(Diagnostic("malformed_edge", (edge.id,)))
        dangling = {edge.src, edge.dst} - nodes.keys()
        if dangling:
            problems.append(Diagnostic("dangling_endpoint", tuple(sorted({edge.id, *dangling}))))
    return problems


def _node_problems(node: GraphNode, nodes: dict[str, GraphNode] | None) -> list[Diagnostic]:
    problems: list[Diagnostic] = []
    allowed = _NODE_PARENTS.get(node.kind)
    if allowed is None:
        problems.append(Diagnostic("unhandled_graph_enum", (node.id,)))
    if nodes is None:
        return problems
    parent = nodes.get(node.parent_id) if node.parent_id is not None else None
    if node.parent_id is not None and parent is None:
        problems.append(Diagnostic("dangling_parent", tuple(sorted({node.id, node.parent_id}))))
    malformed = node.start_line < 1 or node.end_line < node.start_line
    if allowed is not None and (parent.kind if parent else None) not in allowed:
        malformed = True
    if parent is not None:
        malformed |= (
            parent.path != node.path
            or parent.start_line > node.start_line
            or parent.end_line < node.end_line
            or parent.is_test != node.is_test
        )
    if malformed:
        problems.append(Diagnostic("malformed_containment", (node.id,)))
    return problems


def _containment_cycles(nodes: dict[str, GraphNode]) -> list[Diagnostic]:
    done: set[str] = set()
    problems: list[Diagnostic] = []
    for start in sorted(nodes):
        path: list[str] = []
        offsets: dict[str, int] = {}
        current: str | None = start
        while current in nodes and current not in done:
            if current in offsets:
                problems.append(
                    Diagnostic("containment_cycle", tuple(sorted(path[offsets[current] :])))
                )
                break
            offsets[current] = len(path)
            path.append(current)
            current = nodes[current].parent_id
        done.update(path)
    return problems


def _adjacency(snapshot: GraphSnapshot) -> tuple[Adjacency, Adjacency]:
    calls: Adjacency = {node.id: set() for node in snapshot.nodes}
    reverse: Adjacency = {node.id: set() for node in snapshot.nodes}
    for edge in snapshot.edges:
        if _CALL_KINDS[edge.kind] and _CALL_CONFIDENCE[edge.confidence]:
            calls[edge.src].add(edge.dst)
            reverse[edge.dst].add(edge.src)
    for node in snapshot.nodes:
        if node.kind == "method" and node.name in _CONSTRUCTORS and node.parent_id is not None:
            # Changing a constructor can affect callers of its class, even without a
            # literal class->constructor call edge. Also applies to reached constructors.
            reverse[node.id].add(node.parent_id)
    return calls, reverse


def _reachable(seeds: Iterable[str], adjacency: Adjacency) -> frozenset[str]:
    reached: set[str] = set()
    pending = sorted(seeds, reverse=True)
    while pending:
        node = pending.pop()
        if node in reached:
            continue
        reached.add(node)
        pending.extend(sorted(adjacency[node] - reached, reverse=True))
    return frozenset(reached)


def _components(calls: Adjacency) -> Components:
    """Iterative Kosaraju over all call nodes, including non-target intermediates."""
    visited: set[str] = set()
    finished: list[str] = []
    for start in sorted(calls):
        pending = [(start, False)]
        while pending:
            node, exiting = pending.pop()
            if exiting:
                finished.append(node)
            elif node not in visited:
                visited.add(node)
                pending.append((node, True))
                pending.extend((n, False) for n in sorted(calls[node] - visited, reverse=True))
    reverse: Adjacency = {node: set() for node in calls}
    for src, destinations in calls.items():
        for dst in destinations:
            reverse[dst].add(src)
    components: Components = {}
    for start in reversed(finished):
        if start in components:
            continue
        # Assigned components must be excluded from this pass's reachability.
        members: set[str] = set()
        pending = [start]
        while pending:
            node = pending.pop()
            if node in components or node in members:
                continue
            members.add(node)
            pending.extend(sorted(reverse[node] - members, reverse=True))
        component = frozenset(members)
        components.update((node, component) for node in members)
    return components


def _close(
    selected: frozenset[str], snapshot: GraphSnapshot, components: Components
) -> frozenset[str]:
    while True:
        expanded = expand_zone(PlanZone(node_ids=sorted(selected), paths=[]), snapshot).node_ids
        closed = expanded.union(*(components[node] for node in expanded))
        if closed == selected:
            return closed
        selected = closed


def _coalesce(
    groups: list[frozenset[str]], nodes: dict[str, GraphNode], reverse: Adjacency
) -> list[frozenset[str]]:
    """Union footprints distribute over seeds, so unioning closed groups stays closed."""
    records = [
        (group, {nodes[n].path for n in group}, _reachable(group, reverse)) for group in groups
    ]
    while True:
        pair = next(
            (
                (i, j)
                for i, a in enumerate(records)
                for j, b in enumerate(records)
                if i < j and (a[0] & b[0] or a[1] & b[1] or a[2] & b[2])
            ),
            None,
        )
        if pair is None:
            return [record[0] for record in records]
        i, j = pair
        a, b = records[i], records.pop(j)
        records[i] = (a[0] | b[0], a[1] | b[1], a[2] | b[2])


def _revalidate(
    zones: tuple[frozenset[str], frozenset[str]],
    targets: frozenset[str],
    snapshot: GraphSnapshot,
    nodes: dict[str, GraphNode],
    components: Components,
    reverse: Adjacency,
) -> bool:
    if not all(zones) or not targets <= zones[0] | zones[1] or zones[0] & zones[1]:
        return False
    if any(_close(zone, snapshot, components) != zone for zone in zones):
        return False
    if {nodes[n].path for n in zones[0]} & {nodes[n].path for n in zones[1]}:
        return False
    if _reachable(zones[0], reverse) & _reachable(zones[1], reverse):
        return False
    drafts = [
        SubtaskDraft(
            task="Internal partition check", zone=PlanZone(node_ids=sorted(zone), paths=[])
        )
        for zone in zones
    ]
    return check_plan(drafts, snapshot) == []
