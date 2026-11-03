"""Pure committed-prefix projection and reference resolution, without runtime authority."""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import TypeAdapter, ValidationError

from oversight.events.graph_fold import apply_delta
from oversight_contracts.events import ServerEvent
from oversight_contracts.graph import GraphSnapshot
from oversight_contracts.views import LeadView, LeadViewKind, ViewPath

MOMENT_EVENT_TYPES = frozenset(
    {
        "change.proposed",
        "change.resolved",
        "change.reviewed",
        "tests.executed",
        "plan.updated",
        "revert.resolved",
        "lead.reply",
        "error",
    }
)
"""Committed milestones eligible as moments, shared with the TypeScript selector."""

_PATH = TypeAdapter(ViewPath)


@dataclass
class ViewProjection:
    """Reference identities at one displayed prefix; never model-authored facts."""

    agents: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    paths: dict[str, list[str]] = field(default_factory=dict[str, list[str]])
    files: set[tuple[str, str]] = field(default_factory=set[tuple[str, str]])
    behaviors: set[str] = field(default_factory=set[str])
    moments: dict[int, str] = field(default_factory=dict[int, str])

    def resolve(self, view: LeadViewKind) -> list[str | int | tuple[str, str]]:
        """Omit unavailable identities; callers retain the original request separately."""
        match view.kind:
            case "highlight":
                nodes: list[str] = []
                for ref in view.references:
                    found = (
                        self.agents.get(ref.agent_id, [])
                        if ref.kind == "agent"
                        else self.paths.get(ref.path, [])
                    )
                    nodes.extend(found)
                return list(dict.fromkeys(nodes))
            case "files":
                return [
                    (r.change_id, r.path)
                    for r in view.references
                    if (r.change_id, r.path) in self.files
                ]
            case "behavior":
                return [view.change_id] if view.change_id in self.behaviors else []
            case "preview":
                return []
            case "moment":
                return [view.event_seq] if view.event_seq in self.moments else []

    def accepts(self, view: LeadViewKind) -> bool:
        """Every requested identity must resolve at the observed prefix."""
        if view.kind == "highlight":
            return all(
                self.agents.get(r.agent_id) if r.kind == "agent" else self.paths.get(r.path)
                for r in view.references
            )
        if view.kind == "files":
            return len(self.resolve(view)) == len(view.references)
        if view.kind == "moment":
            return view.event_seq in list(self.moments)[-32:]
        return bool(self.resolve(view))

    def inventory(self) -> list[tuple[str, dict[str, Any]]]:
        """Complete eligible rows, followed by the latest 32 committed milestones."""
        rows: list[tuple[str, dict[str, Any]]] = []
        rows.extend(
            ("eligible agent", {"agent_id": key}) for key in sorted(self.agents) if self.agents[key]
        )
        rows.extend(
            ("eligible path", {"path": key})
            for key in sorted(self.paths)
            if self.paths[key] and _canonical(key)
        )
        rows.extend(
            ("eligible file", {"change_id": change, "path": path})
            for change, path in sorted(self.files)
            if _canonical(path)
        )
        rows.extend(("eligible behavior", {"change_id": key}) for key in sorted(self.behaviors))
        moments = list(self.moments.items())
        rows.extend(
            ("eligible moment", {"event_seq": seq, "type": kind})
            for seq, kind in reversed(moments[-32:])
        )
        return rows


def _canonical(path: str) -> bool:
    try:
        _PATH.validate_python(path)
    except ValidationError:
        return False
    return True


def project_views(events: Iterable[ServerEvent]) -> ViewProjection:
    """Fold the same current graph/change/focus and latest whole facts as the UI."""
    projection = ViewProjection()
    graph: GraphSnapshot | None = None
    agents: dict[str, str] = {}
    focus: dict[str, list[str]] = {}
    changes: dict[str, dict[str, Any]] = {}
    facts: dict[tuple[str, int, str], Any] = {}
    for event in events:
        if event.type == "run.started":
            projection, graph, agents, focus, changes, facts = (
                ViewProjection(),
                None,
                {},
                {},
                {},
                {},
            )
        if event.type in MOMENT_EVENT_TYPES:
            projection.moments[event.seq] = event.type
        p = event.payload
        match event.type:
            case "graph.snapshot":
                assert event.type == "graph.snapshot"
                graph = event.payload
            case "graph.delta":
                assert event.type == "graph.delta"
                if graph is not None:
                    graph = apply_delta(graph, event.payload)
            case "agent.status":
                assert event.type == "agent.status"
                agents[event.payload.agent_id] = event.payload.change_id
            case "agent.focus":
                assert event.type == "agent.focus"
                focus[event.payload.agent_id] = event.payload.node_ids
            case "change.updated" | "change.proposed":
                data = p.model_dump(mode="json")
                change = changes.setdefault(data["change_id"], {})
                change["graph"] = data["graph"]
                if event.type == "change.updated":
                    projection.files.add((data["change_id"], data["file"]["path"]))
                else:
                    change["proposal"] = data
                    change["terminal"] = False
            case "change.resolved":
                assert event.type == "change.resolved"
                changes.setdefault(event.payload.change_id, {})["terminal"] = (
                    event.payload.decision in {"approved", "rejected"}
                    and event.payload.merge != "conflict"
                )
            case "change.facts_recorded":
                assert event.type == "change.facts_recorded"
                f = event.payload
                facts[f.change_id, f.revision, f.head_commit] = f
            case _:
                pass
    _resolve_nodes(projection, graph, agents, focus, changes)
    for change_id, change in changes.items():
        proposal = change.get("proposal")
        if proposal:
            f = facts.get((change_id, proposal["revision"], proposal["head_commit"]))
            if f and any(n.compared_count > 0 for n in f.behavior_diff or ()):
                projection.behaviors.add(change_id)
    return projection


def _resolve_nodes(
    projection: ViewProjection,
    graph: GraphSnapshot | None,
    agents: dict[str, str],
    focus: dict[str, list[str]],
    changes: dict[str, dict[str, Any]],
) -> None:
    nodes = {node.id: node.path for node in graph.nodes} if graph else {}
    for change in changes.values():
        if not change.get("terminal"):
            for n in change.get("graph", {}).get("nodes_added", []):
                nodes.setdefault(n["id"], n["path"])
    for node_id, path in nodes.items():
        projection.paths.setdefault(path, []).append(node_id)
    for agent, change_id in agents.items():
        delta = changes.get(change_id, {}).get("graph", {})
        ids = [n["id"] for n in delta.get("nodes_added", [])]
        ids += delta.get("nodes_modified", []) + delta.get("nodes_removed", [])
        projection.agents[agent] = list(
            dict.fromkeys(node for node in [*ids, *focus.get(agent, [])] if node in nodes)
        )


def capture_view_resolver(
    events: Mapping[int, ServerEvent], through_seq: int
) -> Callable[[LeadView], bool]:
    """Freeze the eligible projection at a committed horizon without lending the log."""
    if not 0 <= through_seq <= len(events):
        raise ValueError("view horizon is not committed")
    projection = project_views(events[seq] for seq in range(1, through_seq + 1))
    return lambda view: view.through_seq == through_seq and projection.accepts(view.view)


def resolve_view_references(
    events: Iterable[ServerEvent], request: LeadView
) -> list[str | int | tuple[str, str]]:
    """Resolve at the displayed prefix, with the request's original moment horizon."""
    if request.view.kind == "moment" and request.view.event_seq > request.through_seq:
        return []
    return project_views(events).resolve(request.view)
