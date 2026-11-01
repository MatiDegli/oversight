"""Pure before/after structural observations; size is a review signal, never a gate."""

from collections.abc import Collection, Iterator
from dataclasses import dataclass

from oversight_contracts.common import NodeId
from oversight_contracts.graph import GraphNode, GraphSnapshot
from oversight_contracts.policy import EvidenceRef
from oversight_contracts.review import ShapeFact

FUNCTION_LINES = 80
MAX_FACTS = 24
_KIND_ORDER = {"function_length": 0, "import_added": 1, "import_cycle": 2}


@dataclass(frozen=True)
class ShapeEvidence:
    facts: tuple[ShapeFact, ...]
    complete: bool
    problems: tuple[str, ...]


def compare_shape(
    base: GraphSnapshot,
    head: GraphSnapshot,
    changed: Collection[NodeId],
    *,
    base_tree: str,
    head_tree: str,
    limit: int = FUNCTION_LINES,
) -> ShapeEvidence:
    """Compare parsed graphs, retaining explicit gaps instead of certifying safety."""
    facts = _length_facts(base, head, changed, base_tree, head_tree, limit)
    base_modules, base_edges = _imports(base)
    modules, edges = _imports(head)
    new_edges = edges - base_edges
    for src, dst in sorted(new_edges):
        if src in base_modules and dst in base_modules:
            facts.append(_import_fact(modules[src], head_tree, f"`{src}` now imports `{dst}`."))
    components = _components(modules, edges)
    membership = {node: i for i, component in enumerate(components) for node in component}
    first_new: dict[int, tuple[NodeId, NodeId]] = {}
    for src, dst in sorted(new_edges):
        index = membership[src]
        if index == membership[dst] and len(components[index]) >= 2:
            first_new.setdefault(index, (src, dst))
    for index, (src, dst) in first_new.items():
        component = components[index]
        names = ", ".join(sorted(component)[:6])
        if len(component) > 6:
            names += f", +{len(component) - 6} more"
        message = (
            f"Modules {{{names}}} import each other in a cycle (new: `{src}` imports `{dst}`)."
        )
        facts.append(_import_fact(modules[src], head_tree, message, cycle=True))
    facts.sort(key=lambda fact: (_KIND_ORDER[fact.kind], fact.path, fact.node_id or ""))
    problems: list[str] = []
    if len(facts) > MAX_FACTS:
        problems.append(f"shape: {len(facts) - MAX_FACTS} more observations omitted")
    stale = {error.path for graph in (base, head) for error in graph.parse_errors}
    if stale:
        problems.append(f"shape: {len(stale)} files kept their last good parse")
    return ShapeEvidence(tuple(facts[:MAX_FACTS]), not problems, tuple(problems))


def _length_facts(
    base: GraphSnapshot,
    head: GraphSnapshot,
    changed: Collection[NodeId],
    base_tree: str,
    head_tree: str,
    limit: int,
) -> list[ShapeFact]:
    before = {node.id: node for node in base.nodes if not node.is_test}
    changed_ids = set(changed)
    facts: list[ShapeFact] = []
    for node in head.nodes:
        if node.is_test or node.kind not in {"function", "method"} or node.id not in changed_ids:
            continue
        length = node.end_line - node.start_line + 1
        old = before.get(node.id)
        old_length = old.end_line - old.start_line + 1 if old else 0
        if length <= limit or (old is not None and length <= old_length):
            continue
        name = f"`{node.qualname}`"
        if old is None:
            message = f"{name} is new and {length} lines long (limit {limit})."
        elif old_length <= limit:
            message = f"{name} grew from {old_length} to {length} lines, past {limit}."
        else:
            message = (
                f"{name} was already over {limit} lines and grew from {old_length} to {length}."
            )
        evidence = [EvidenceRef(git_object=head_tree, path=node.path)]
        if old is not None:
            evidence.append(EvidenceRef(git_object=base_tree, path=old.path))
        facts.append(
            ShapeFact(
                kind="function_length",
                path=node.path,
                node_id=node.id,
                message=message,
                evidence=evidence,
            )
        )
    return facts


def _imports(graph: GraphSnapshot) -> tuple[dict[NodeId, GraphNode], set[tuple[NodeId, NodeId]]]:
    modules = {node.id: node for node in graph.nodes if node.kind == "module" and not node.is_test}
    edges = {
        (edge.src, edge.dst)
        for edge in graph.edges
        if edge.kind == "imports" and edge.src in modules and edge.dst in modules
    }
    return modules, edges


def _import_fact(node: GraphNode, tree: str, message: str, *, cycle: bool = False) -> ShapeFact:
    return ShapeFact(
        kind="import_cycle" if cycle else "import_added",
        path=node.path,
        node_id=node.id,
        message=message,
        evidence=[EvidenceRef(git_object=tree, path=node.path)],
    )


def _components(
    modules: Collection[NodeId], edges: set[tuple[NodeId, NodeId]]
) -> list[set[NodeId]]:
    """Kosaraju with explicit stacks: deep import chains cannot exhaust Python's stack."""
    forward: dict[NodeId, list[NodeId]] = {node: [] for node in modules}
    reverse: dict[NodeId, list[NodeId]] = {node: [] for node in modules}
    for src, dst in sorted(edges):
        forward[src].append(dst)
        reverse[dst].append(src)
    seen: set[NodeId] = set()
    finished: list[NodeId] = []
    for root in sorted(modules):
        if root in seen:
            continue
        seen.add(root)
        stack: list[tuple[NodeId, Iterator[NodeId]]] = [(root, iter(forward[root]))]
        while stack:
            node, children = stack[-1]
            child = next(children, None)
            if child is None:
                finished.append(node)
                stack.pop()
            elif child not in seen:
                seen.add(child)
                stack.append((child, iter(forward[child])))
    seen.clear()
    components: list[set[NodeId]] = []
    for root in reversed(finished):
        if root in seen:
            continue
        component: set[NodeId] = set()
        pending = [root]
        seen.add(root)
        while pending:
            node = pending.pop()
            component.add(node)
            for parent in reverse[node]:
                if parent not in seen:
                    seen.add(parent)
                    pending.append(parent)
        components.append(component)
    return components
