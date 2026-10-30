"""AST comparisons of a module or class's own statements, never executing source."""

import ast
from copy import deepcopy

from oversight.changes._behavior_aliases import (
    _AliasBindings,  # pyright: ignore[reportPrivateUsage]
)
from oversight_contracts.graph import GraphNode


class _WithoutDefinitions(ast.NodeTransformer):
    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        return None

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return None


def _own_tree(node: ast.Module | ast.ClassDef) -> ast.Module | ast.ClassDef:
    own = deepcopy(node)
    body = ast.Module(body=own.body, type_ignores=[])
    _WithoutDefinitions().visit(body)
    own.body = body.body
    return own


def _class(tree: ast.Module, node: GraphNode) -> ast.ClassDef | None:
    # Each graph version supplies its own range, including the first decorator.
    return next(
        (
            child
            for child in ast.walk(tree)
            if isinstance(child, ast.ClassDef)
            and child.name == node.name
            and min([child.lineno, *[d.lineno for d in child.decorator_list]]) == node.start_line
        ),
        None,
    )


def _fresh_import(stmt: ast.stmt, bindings: _AliasBindings, declared: set[str]) -> bool:
    if not isinstance(stmt, (ast.Import, ast.ImportFrom)) or bindings.star_import:
        return False
    return all(
        alias.name != "*"
        and (
            alias.asname
            or (alias.name.split(".")[0] if isinstance(stmt, ast.Import) else alias.name)
        )
        not in bindings.names.keys() | declared
        for alias in stmt.names
    )


def _module_equal(base: ast.Module, head: ast.Module) -> bool:
    before = _own_tree(base).body
    after = _own_tree(head).body
    if ast.dump(ast.Module(body=before, type_ignores=[])) == ast.dump(
        ast.Module(body=after, type_ignores=[])
    ):
        return True
    bindings = _AliasBindings(float("inf"))
    bindings.visit(base)
    # A global declaration in a nested scope can write the module namespace too.
    declared = {
        name for node in ast.walk(base) if isinstance(node, ast.Global) for name in node.names
    }
    index = 0
    for stmt in after:
        if index < len(before) and ast.dump(before[index]) == ast.dump(stmt):
            index += 1
        elif not _fresh_import(stmt, bindings, declared):
            return False
    return index == len(before)


class InertScopes:
    """Parse each recorded file pair once; any unavailable source keeps the target."""

    def __init__(self, base_files: dict[str, str], head_files: dict[str, str]) -> None:
        self.base_files = base_files
        self.head_files = head_files
        self.trees: dict[str, tuple[ast.Module, ast.Module] | None] = {}

    def unchanged(self, base_node: GraphNode, head_node: GraphNode) -> bool:
        path = head_node.path
        if path not in self.trees:
            try:
                self.trees[path] = (
                    ast.parse(self.base_files[path]),
                    ast.parse(self.head_files[path]),
                )
            except (KeyError, SyntaxError, ValueError, RecursionError):
                self.trees[path] = None
        trees = self.trees[path]
        if trees is None:
            return False
        try:
            if head_node.kind == "module":
                return _module_equal(*trees)
            before, after = _class(trees[0], base_node), _class(trees[1], head_node)
            return (
                before is not None
                and after is not None
                and ast.dump(_own_tree(before)) == ast.dump(_own_tree(after))
            )
        except RecursionError:
            return False
