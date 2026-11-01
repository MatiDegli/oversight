"""Pure, bounded observations of newly duplicated function structure; never a gate."""

import ast
import copy
import hashlib
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from oversight_contracts.common import NodeId
from oversight_contracts.graph import GraphNode, GraphSnapshot
from oversight_contracts.policy import EvidenceRef
from oversight_contracts.review import ShapeFact

MIN_STATEMENTS = 4
MAX_FUNCTIONS = 4000
MAX_FACTS = 24
type _Function = ast.FunctionDef | ast.AsyncFunctionDef
_PARSE_FAILURES = (SyntaxError, ValueError, RecursionError, MemoryError)


@dataclass(frozen=True)
class DuplicateEvidence:
    facts: tuple[ShapeFact, ...]
    complete: bool
    problems: tuple[str, ...]


class _Strip(ast.NodeTransformer):
    """Remove function metadata before collecting bindings or counting statements."""

    def visit_FunctionDef(self, node: _Function) -> ast.AST:
        node.decorator_list = []
        node.returns = None
        node.type_comment = None
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            node.body = node.body[1:]
        return self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_arg(self, node: ast.arg) -> ast.AST:
        node.annotation = None
        node.type_comment = None
        return node

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AST | None:
        if node.value is None:
            return None
        return self.visit(ast.Assign(targets=[node.target], value=node.value))


def _parameters(args: ast.arguments) -> list[ast.arg]:
    return (
        args.posonlyargs
        + args.args
        + ([args.vararg] if args.vararg else [])
        + args.kwonlyargs
        + ([args.kwarg] if args.kwarg else [])
    )


def _binding(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
        return node.id
    if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
        return node.name or None
    if isinstance(node, ast.arg):
        return node.arg
    if isinstance(node, ast.alias):
        return node.asname or node.name.split(".")[0]
    if isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar):
        return node.name
    if isinstance(node, ast.MatchMapping):
        return node.rest
    return None


class _Bindings(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: dict[str, str] = {}

    def generic_visit(self, node: ast.AST) -> None:
        name = _binding(node)
        if name is not None and name not in self.names:
            self.names[name] = f"_{len(self.names)}"
        super().generic_visit(node)

    def visit_arguments(self, node: ast.arguments) -> None:
        for arg in _parameters(node):
            self.visit(arg)
        for default in [*node.defaults, *node.kw_defaults]:
            if default is not None:
                self.visit(default)


class _Rename(ast.NodeTransformer):
    def __init__(self, names: Mapping[str, str]) -> None:
        self.names = names

    def generic_visit(self, node: ast.AST) -> ast.AST:
        if isinstance(node, ast.Name):
            node.id = self.names.get(node.id, node.id)
        elif isinstance(node, ast.arg):
            node.arg = self.names[node.arg]
        elif isinstance(node, ast.alias):
            node.asname = self.names[_binding(node) or ""]
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            node.name = self.names.get(node.name, node.name)
        elif isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar):
            if node.name is not None:
                node.name = self.names[node.name]
        elif isinstance(node, ast.MatchMapping) and node.rest is not None:
            node.rest = self.names[node.rest]
        return super().generic_visit(node)


def _fingerprint(source: _Function) -> str | None:
    function = copy.deepcopy(source)
    _Strip().visit(function)
    statements = sum(
        isinstance(node, ast.stmt) for stmt in function.body for node in ast.walk(stmt)
    )
    if statements < MIN_STATEMENTS:
        return None
    function.name = ""
    bindings = _Bindings()
    bindings.visit(function)
    _Rename(bindings.names).visit(function)
    return hashlib.sha256(ast.dump(function, include_attributes=False).encode()).hexdigest()


def _fingerprints(
    nodes: Sequence[GraphNode],
    files: Mapping[str, str],
    parse_errors: set[str],
    skipped: set[str],
) -> dict[NodeId, str]:
    by_path: dict[str, list[GraphNode]] = defaultdict(list)
    for node in nodes:
        by_path[node.path].append(node)
    fingerprints: dict[NodeId, str] = {}
    for path, members in sorted(by_path.items()):
        if path in parse_errors or path not in files:
            skipped.add(path)
            continue
        try:
            tree = ast.parse(files[path])
            definitions = {
                min([node.lineno, *(d.lineno for d in node.decorator_list)]): node
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            }
            captured: dict[NodeId, str] = {}
            for member in members:
                definition = definitions.get(member.start_line)
                if definition is None:
                    skipped.add(path)
                    continue
                fingerprint = _fingerprint(definition)
                if fingerprint is not None:
                    captured[member.id] = fingerprint
            fingerprints.update(captured)
        except _PARSE_FAILURES:
            skipped.add(path)
    return fingerprints


def _fact(members: list[GraphNode], changed: set[NodeId], head_tree: str) -> ShapeFact:
    anchor = next(node for node in members if node.id in changed)
    others = [node for node in members if node.id != anchor.id]
    names = ", ".join(f"`{node.qualname}` ({node.path})" for node in others[:3])
    if len(others) > 3:
        names += f", +{len(others) - 3} more"
    return ShapeFact(
        kind="duplicate_logic",
        path=anchor.path,
        node_id=anchor.id,
        message=f"`{anchor.qualname}` has the same logic as {names}.",
        evidence=[EvidenceRef(git_object=head_tree, path=anchor.path)],
    )


def compare_duplicates(
    base: GraphSnapshot,
    head: GraphSnapshot,
    base_files: Mapping[str, str],
    head_files: Mapping[str, str],
    changed: Collection[NodeId],
    *,
    head_tree: str,
) -> DuplicateEvidence:
    """Compare graph functions, reporting only groups with newly changed logic.

    Names use a function-wide binding table, including nested scopes; this is structural
    evidence, not a claim of semantic equivalence. Inputs are never mutated.
    """
    head_errors = {error.path for error in head.parse_errors}
    skipped = set(head_errors)
    nodes = sorted(
        (
            node
            for node in head.nodes
            if node.kind in {"function", "method"}
            and not node.is_test
            and node.path not in head_errors
        ),
        key=lambda node: node.id,
    )
    selected = nodes[:MAX_FUNCTIONS]
    changed_ids = set(changed) & {node.id for node in selected}
    fingerprints = _fingerprints(selected, head_files, head_errors, skipped)
    old = _fingerprints(
        [node for node in base.nodes if node.id in changed_ids],
        base_files,
        {error.path for error in base.parse_errors},
        skipped,
    )
    logic_changed = {
        node_id
        for node_id in changed_ids
        if node_id in fingerprints and fingerprints[node_id] != old.get(node_id)
    }
    groups: dict[str, list[GraphNode]] = defaultdict(list)
    for node in selected:
        if node.id in fingerprints:
            groups[fingerprints[node.id]].append(node)
    facts = sorted(
        (
            _fact(members, logic_changed, head_tree)
            for members in groups.values()
            if len(members) > 1 and any(node.id in logic_changed for node in members)
        ),
        key=lambda fact: (fact.path, fact.node_id or ""),
    )
    problems: list[str] = []
    if len(facts) > MAX_FACTS:
        problems.append(f"shape: {len(facts) - MAX_FACTS} more duplicate observations omitted")
    if len(nodes) > MAX_FUNCTIONS:
        problems.append(f"shape: duplicate check covered {len(selected)} of {len(nodes)} functions")
    if skipped:
        problems.append(f"shape: {len(skipped)} files were not compared for duplicate logic")
    return DuplicateEvidence(tuple(facts[:MAX_FACTS]), not problems, tuple(problems))
