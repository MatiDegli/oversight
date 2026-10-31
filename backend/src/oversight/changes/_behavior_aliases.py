"""Single-binding aliases for AST-only behavior seed discovery."""

from __future__ import annotations

import ast
from collections import Counter
from functools import cached_property
from typing import TYPE_CHECKING

# Both helpers belong to changes; reuse the detector's conservative binding semantics.
from oversight.changes._shortcuts_common import (
    Source,
    _Bindings,  # pyright: ignore[reportPrivateUsage]
    check_deadline,
)

if TYPE_CHECKING:
    from oversight.changes._behavior_sources import Sources


class _AliasBindings(_Bindings):
    def __init__(self, deadline: float) -> None:
        super().__init__()
        self.deadline = deadline
        self.counts: Counter[str] = Counter()
        self.star_import = False

    def visit(self, node: ast.AST) -> None:
        check_deadline(self.deadline)
        super().visit(node)

    def bind(self, name: str, target: str | None = None) -> None:
        self.counts[name] += 1
        super().bind(name, target)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.star_import |= any(alias.name == "*" for alias in node.names)
        super().visit_ImportFrom(node)

    def visit_MatchAs(self, node: ast.MatchAs | ast.MatchStar) -> None:
        if node.name:
            self.bind(node.name)
        self.generic_visit(node)

    visit_MatchStar = visit_MatchAs

    def visit_MatchMapping(self, node: ast.MatchMapping) -> None:
        if node.rest:
            self.bind(node.rest)
        self.generic_visit(node)

    def visit_ListComp(self, node: ast.AST) -> None:
        # Comprehension targets are local; assignment expressions bind the outer scope.
        for child in ast.walk(node):
            check_deadline(self.deadline)
            if isinstance(child, ast.NamedExpr):
                self.visit(child.target)

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_Lambda(self, node: ast.Lambda) -> None:
        for default in [*node.args.defaults, *node.args.kw_defaults]:
            if default is not None:
                self.visit(default)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        super().visit_FunctionDef(node)
        args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        args.extend(arg for arg in (node.args.vararg, node.args.kwarg) if arg is not None)
        for expression in [
            *node.decorator_list,
            *node.args.defaults,
            *node.args.kw_defaults,
            *[arg.annotation for arg in args],
            node.returns,
        ]:
            if expression is not None:
                self.visit(expression)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        super().visit_ClassDef(node)
        for expression in [*node.bases, *node.decorator_list, *[kw.value for kw in node.keywords]]:
            self.visit(expression)


class _BehaviorSyntax:
    """Behavior-only caches; the shared shortcut source stays unchanged."""

    def __init__(self, source: Source) -> None:
        self.source = source
        self.scopes: dict[ast.AST, _AliasBindings] = {}

    @cached_property
    def parents(self) -> dict[ast.AST, ast.AST]:
        parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(self.source.tree):
            check_deadline(self.source.deadline)
            parents.update((child, node) for child in ast.iter_child_nodes(node))
        return parents

    @cached_property
    def alias_inventory(self) -> tuple[_AliasBindings, frozenset[str]]:
        bindings = _AliasBindings(self.source.deadline)
        bindings.visit(self.source.tree)
        declared: set[str] = set()
        for node in ast.walk(self.source.tree):
            check_deadline(self.source.deadline)
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                declared.update(node.names)
        return bindings, frozenset(declared)

    def bindings(
        self, scope: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
    ) -> _AliasBindings:
        if scope not in self.scopes:
            bindings = _AliasBindings(self.source.deadline)
            if not isinstance(scope, ast.ClassDef):
                bindings.visit(scope.args)
            for stmt in scope.body:
                bindings.visit(stmt)
            self.scopes[scope] = bindings
        return self.scopes[scope]


def syntax(source: Source) -> _BehaviorSyntax:
    check_deadline(source.deadline)
    cached = source.__dict__.get("_behavior_syntax")
    if not isinstance(cached, _BehaviorSyntax):
        cached = _BehaviorSyntax(source)
        source.__dict__["_behavior_syntax"] = cached
    return cached


def module_aliases(source: Source) -> dict[str, str]:
    bindings, _ = syntax(source).alias_inventory
    if bindings.star_import:
        return {}
    global_names: set[str] = set()
    for node in ast.walk(source.tree):
        check_deadline(source.deadline)
        if isinstance(node, ast.Global):
            global_names.update(node.names)
    imported: set[str] = set()
    aliases: dict[str, str] = {}
    for stmt in source.tree.body:
        check_deadline(source.deadline)
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            imported.update(alias.asname or alias.name.split(".")[0] for alias in stmt.names)
        if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            continue
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        if len(targets) != 1 or not isinstance(targets[0], ast.Name):
            continue
        name = targets[0].id
        root = stmt.value
        while isinstance(root, ast.Attribute):
            root = root.value
        if not isinstance(root, ast.Name) or root.id not in imported:
            continue
        if any(bindings.counts[key] != 1 or key in global_names for key in (name, root.id)):
            continue
        if stmt.value is not None and (resolved := source.resolve(stmt.value)):
            aliases[name] = resolved
    return aliases


def shadowed(source: Source, call: ast.Call, name: str) -> bool:
    # Reuse the definition binding inventory; local assignments hide a name throughout a scope.
    for definition, bindings in source.definition_bindings.items():
        if definition.lineno <= call.lineno <= (definition.end_lineno or definition.lineno):
            if name in bindings:
                return True
            extra = syntax(source).bindings(definition)
            if extra.star_import or name in extra.names:
                return True
    for parent in ast.walk(source.tree):
        check_deadline(source.deadline)
        if (
            isinstance(parent, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp))
            and any(
                isinstance(target, ast.Name) and target.id == name
                for generator in parent.generators
                for target in ast.walk(generator.target)
            )
            and call in ast.walk(parent)
        ):
            return True
        if isinstance(parent, ast.Lambda) and call in ast.walk(parent.body):
            bindings = _Bindings()
            bindings.visit(parent.args)
            bindings.visit(parent.body)
            if name in bindings.names:
                return True
    return False


def test_scope(
    source: Source, call: ast.Call, cache: Sources | None = None
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Only calls in a collected test's own scope, never a nested execution scope."""
    parents = cache.parents(source) if cache is not None else syntax(source).parents
    node: ast.AST = call
    while node in parents:
        check_deadline(source.deadline)
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node if node in source.tests.values() else None
        if isinstance(node, (ast.ClassDef, ast.Lambda)):
            return None
    return None


def local_alias(source: Source, call: ast.Call, cache: Sources | None = None) -> str | None:
    if not isinstance(call.func, ast.Name) or (scope := test_scope(source, call, cache)) is None:
        return None
    name = call.func.id
    bindings = syntax(source).bindings(scope)
    if bindings.counts[name] != 1 or bindings.star_import:
        return None
    module, declared = syntax(source).alias_inventory
    if module.star_import:
        return None
    imports = {
        alias.asname or alias.name.split(".")[0]
        for stmt in source.tree.body
        if isinstance(stmt, (ast.Import, ast.ImportFrom))
        for alias in stmt.names
    }
    for stmt in scope.body:
        check_deadline(source.deadline)
        if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            continue
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        if len(targets) != 1 or not isinstance(targets[0], ast.Name) or targets[0].id != name:
            continue
        if (stmt.end_lineno or stmt.lineno, stmt.end_col_offset or 0) >= (
            call.lineno,
            call.col_offset,
        ):
            return None
        root = stmt.value
        while isinstance(root, ast.Attribute):
            root = root.value
        if not isinstance(root, ast.Name) or root.id not in imports:
            return None
        if module.counts[root.id] != 1 or root.id in bindings.names:
            return None
        if declared.intersection((name, root.id)):
            return None
        # A comprehension target can hide the otherwise unique test-local alias.
        if any(
            isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp))
            and call in ast.walk(node)
            and any(
                isinstance(target, ast.Name) and target.id == name
                for generator in node.generators
                for target in ast.walk(generator.target)
            )
            for node in ast.walk(scope)
        ):
            return None
        return source.resolve(stmt.value) if stmt.value is not None else None
    return None
