"""AST comparisons of a module or class's own statements, never executing source."""

import ast
import builtins
from copy import deepcopy
from importlib import import_module

from oversight.changes._behavior_aliases import (
    _AliasBindings,  # pyright: ignore[reportPrivateUsage]
)
from oversight_contracts.graph import GraphNode

_ALLOWED_MODULES = frozenset(
    {
        "abc",
        "collections",
        "contextlib",
        "dataclasses",
        "datetime",
        "decimal",
        "enum",
        "fractions",
        "functools",
        "hashlib",
        "itertools",
        "json",
        "logging",
        "math",
        "operator",
        "os",
        "pathlib",
        "random",
        "re",
        "string",
        "sys",
        "textwrap",
        "time",
        "typing",
        "uuid",
    }
)
_BUILTIN_NAMES = frozenset(dir(builtins))
_NAMESPACE_READS = frozenset(
    {"eval", "exec", "compile", "getattr", "globals", "vars", "locals", "dir", "__import__"}
)
_NAMESPACE_ATTRIBUTES = frozenset({"__dict__", "__globals__", "f_globals", "modules", "_getframe"})
_REFLECTION_MODULES = frozenset({"importlib", "inspect"})


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


def _fresh_import(
    stmt: ast.stmt, bindings: _AliasBindings, forbidden: set[str], strings: tuple[str, ...]
) -> bool:
    if not isinstance(stmt, (ast.Import, ast.ImportFrom)) or bindings.star_import:
        return False
    if isinstance(stmt, ast.ImportFrom):
        if stmt.level or stmt.module not in _ALLOWED_MODULES:
            return False
        try:
            module = import_module(stmt.module)
        except ImportError:
            return False
        if any(alias.name == "*" or not hasattr(module, alias.name) for alias in stmt.names):
            return False
    elif any(alias.name not in _ALLOWED_MODULES for alias in stmt.names):
        return False
    names = (alias.asname or alias.name for alias in stmt.names)
    return all(
        name not in forbidden and not any(name in text for text in strings) for name in names
    )


def _reflection_import(node: ast.AST) -> bool:
    if isinstance(node, ast.Import):
        return any(alias.name.split(".")[0] in _REFLECTION_MODULES for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        return (node.module or "").split(".")[0] in _REFLECTION_MODULES or any(
            alias.name in _REFLECTION_MODULES for alias in node.names
        )
    return False


# Accepted "possible" risks: other modules' star imports, outside access to this module,
# and this module importing itself by name.
def _module_equal(base: ast.Module, head: ast.Module) -> bool:
    docstring = ast.get_docstring(base)
    if docstring != ast.get_docstring(head):
        return False
    if docstring is not None and ast.dump(base.body[0]) != ast.dump(head.body[0]):
        return False
    # Include every lexical scope and every Name context, even a local store/delete.
    identifiers = {node.id for node in ast.walk(base) if isinstance(node, ast.Name)}
    attributes = {node.attr for node in ast.walk(base) if isinstance(node, ast.Attribute)}
    if (
        identifiers & _NAMESPACE_READS
        or attributes & _NAMESPACE_ATTRIBUTES
        or any(_reflection_import(node) for node in ast.walk(base))
    ):
        return False
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
    forbidden = bindings.names.keys() | declared | identifiers | attributes | _BUILTIN_NAMES
    strings = tuple(
        node.value
        for node in ast.walk(base)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    index = 0
    for stmt in after:
        if index < len(before) and ast.dump(before[index]) == ast.dump(stmt):
            index += 1
        elif not _fresh_import(stmt, bindings, forbidden, strings):
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
