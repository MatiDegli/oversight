"""AST comparisons of a module or class's own statements, never executing source."""

import ast
from copy import deepcopy

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


def _module_equal(base: ast.Module, head: ast.Module) -> bool:
    return ast.dump(_own_tree(base)) == ast.dump(_own_tree(head))


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
