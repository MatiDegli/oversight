"""Conservative bindings for literal parameter tables defined in one module."""

import ast
from collections import Counter
from collections.abc import Callable


class _ModuleBindings(ast.NodeVisitor):
    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.counts[node.id] += 1

    def visit_Import(self, node: ast.Import) -> None:
        self.counts.update(a.asname or a.name.split(".")[0] for a in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.counts.update(a.asname or a.name for a in node.names)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.counts[node.name] += 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.counts[node.name] += 1


def _root(node: ast.expr) -> str | None:
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


class ModuleTables:
    def __init__(self, tree: ast.Module, resolve: Callable[[ast.expr], str | None]) -> None:
        bindings = _ModuleBindings()
        bindings.visit(tree)
        self.values: dict[str, ast.List | ast.Tuple] = {}
        self.reasons: dict[str, str] = {}
        for stmt in tree.body:
            if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                continue
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            for target in targets:
                if not isinstance(target, ast.Name):
                    continue
                if bindings.counts[target.id] != 1:
                    self.reasons[target.id] = "rebound"
                elif not isinstance(stmt.value, (ast.List, ast.Tuple)):
                    self.reasons[target.id] = "computed or aliased"
                elif any(isinstance(row, ast.Starred) for row in stmt.value.elts):
                    self.reasons[target.id] = "computed by unpacking"
                else:
                    self.values[target.id] = stmt.value
        # Attribute calls, writes through a subscript/attribute, and aliases can
        # mutate the table without rebinding its name. Never execute these edits.
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and resolve(node.func) not in {
                "pytest.mark.parametrize",
                "len",
                "list",
                "tuple",
                "sorted",
            }:
                for arg in [*node.args, *[kw.value for kw in node.keywords]]:
                    name = _root(arg)
                    if name in self.values:
                        self.reasons[name] = "passed to a call that may mutate it"
            if isinstance(node, ast.Global):
                for name in node.names:
                    if name in self.values:
                        self.reasons[name] = "rebound through a global declaration"
            name = None
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                name = _root(node.func.value)
            elif isinstance(node, (ast.Subscript, ast.Attribute)) and isinstance(
                node.ctx, (ast.Store, ast.Del)
            ):
                name = _root(node.value)
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(node.value, ast.Name):
                name = node.value.id
            if name in self.values:
                self.reasons[name] = "mutated or aliased"
        for name in self.reasons:
            self.values.pop(name, None)

    def reason(self, name: str) -> str:
        return f"named parameter table '{name}' is {self.reasons.get(name, 'imported or unbound')}"
