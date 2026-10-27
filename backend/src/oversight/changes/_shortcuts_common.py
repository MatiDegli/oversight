"""Shared syntax inventory and lexical name resolution for the pure detector."""

import ast
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cached_property
from pathlib import PurePosixPath

from oversight.changes._shortcuts_tables import ModuleTables

type Change = tuple[str, str | None, str | None]
type FuncNode = ast.FunctionDef | ast.AsyncFunctionDef
type Definition = FuncNode | ast.ClassDef
type Bindings = dict[str, str | None]


class ShortcutKind(StrEnum):
    TEST_REMOVED = "test_removed"
    ASSERTION_LOOSENED = "assertion_loosened"
    SKIP_ADDED = "skip_added"
    EXPECTED_VALUE_CHANGED = "expected_value_changed"
    LITERAL_BRANCH = "literal_branch"
    STUB_LEFT = "stub_left"
    PARSE_ERROR = "parse_error"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Finding:
    path: str
    line: int
    kind: ShortcutKind
    message: str


class DetectionResult(list[Finding]):
    """Facts plus an explicit analysis status; absence of facts is no safety proof."""

    @property
    def status(self) -> str:
        if any(f.kind in {ShortcutKind.UNKNOWN, ShortcutKind.PARSE_ERROR} for f in self):
            return "unknown"
        return "findings" if self else "no_facts"

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(
            f.message for f in self if f.kind in {ShortcutKind.UNKNOWN, ShortcutKind.PARSE_ERROR}
        )


def is_collected_file(path: str) -> bool:
    name = PurePosixPath(path).name
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def is_test_file(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if not parts:
        return False
    name = parts[-1]
    return name.endswith(".py") and (
        name == "conftest.py"
        or name.startswith("test_")
        or name.endswith("_test.py")
        or "tests" in parts[:-1]
    )


class _Bindings(ast.NodeVisitor):
    """Collect one scope's bindings; conflicting/rebound names are unknown.

    This is deliberately conservative about order and control flow. A parameter
    or assignment cannot be mistaken for an inherited pytest/unittest import.
    Nested definitions contribute their name, never their body's imports.
    """

    def __init__(self) -> None:
        self.names: Bindings = {}

    def bind(self, name: str, target: str | None = None) -> None:
        if name in self.names and self.names[name] != target:
            self.names[name] = None
        else:
            self.names[name] = target

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.bind(node.id)

    def visit_arg(self, node: ast.arg) -> None:
        self.bind(node.arg)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            name = alias.asname or alias.name.split(".")[0]
            self.bind(name, alias.name if alias.asname else name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            if alias.name != "*":
                module = "." * node.level + (node.module or "")
                target = f"{module}.{alias.name}"
                self.bind(alias.asname or alias.name, target)

    def visit_FunctionDef(self, node: FuncNode) -> None:
        self.bind(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.bind(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass

    def visit_ListComp(self, node: ast.AST) -> None:
        pass

    visit_SetComp = visit_ListComp
    visit_DictComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.bind(node.name)
        self.generic_visit(node)


def _scope_bindings(body: list[ast.stmt], args: ast.arguments | None = None) -> Bindings:
    visitor = _Bindings()
    if args is not None:
        visitor.visit(args)
    for stmt in body:
        visitor.visit(stmt)
    return visitor.names


def _class_is_collectable(node: ast.ClassDef, source: "Source") -> bool:
    if any(source.resolve(base) == "unittest.TestCase" for base in node.bases):
        return True
    return node.name.startswith("Test") and not any(
        isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name == "__init__"
        for child in node.body
    )


@dataclass
class Source:
    """One definition walk per parsed version, shared by every detection."""

    text: str
    deadline: float = float("inf")
    tree: ast.Module = field(init=False)
    functions: dict[str, FuncNode] = field(default_factory=dict[str, FuncNode], init=False)
    classes: dict[str, ast.ClassDef] = field(default_factory=dict[str, ast.ClassDef], init=False)
    tests: dict[str, FuncNode] = field(default_factory=dict[str, FuncNode], init=False)
    scopes: dict[ast.AST, Bindings] = field(default_factory=dict[ast.AST, Bindings], init=False)
    definition_bindings: dict[Definition, Bindings] = field(
        default_factory=dict[Definition, Bindings], init=False
    )

    def __post_init__(self) -> None:
        self.tree = ast.parse(self.text)
        # Python 3 unicode-prefix spelling does not change a string's value.
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Constant):
                node.kind = None
        self._visit(self.tree, "", _scope_bindings(self.tree.body), True)

    def _visit(
        self,
        node: ast.AST,
        prefix: str,
        scope: Bindings,
        collect: bool,
        closure: Bindings | None = None,
    ) -> None:
        self.scopes[node] = scope
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            qual = f"{prefix}.{node.name}" if prefix else node.name
            definitions = self.classes if isinstance(node, ast.ClassDef) else self.functions
            key = qual
            suffix = 2
            while key in definitions:
                key = f"{qual}#{suffix}"
                suffix += 1
            if isinstance(node, ast.ClassDef):
                self.classes[key] = node
                for base in [*node.bases, *[kw.value for kw in node.keywords]]:
                    self._visit(base, prefix, scope, False)
                child_collect = collect and _class_is_collectable(node, self)
                args = None
            else:
                self.functions[key] = node
                if collect and node.name.startswith("test_"):
                    self.tests[key] = node
                child_collect = False
                args = node.args
            for dec in node.decorator_list:
                self._visit(dec, prefix, scope, False)
            # Methods close over the surrounding lexical scope, not class locals.
            outer = closure if closure is not None else scope
            local = _scope_bindings(node.body, args)
            self.definition_bindings[node] = local
            inner = {**outer, **local}
            if args is not None and closure is not None:
                positional = [*args.posonlyargs, *args.args]
                if (
                    positional
                    and positional[0].arg in {"self", "cls"}
                    and positional[0].arg not in _scope_bindings(node.body)
                ):
                    # A method's receiver is bound even though its runtime value
                    # is unknown. Keep attribute resolution in this resolver.
                    inner[positional[0].arg] = prefix
            for stmt in node.body:
                self._visit(
                    stmt,
                    key,
                    inner,
                    child_collect,
                    outer if isinstance(node, ast.ClassDef) else None,
                )
            return
        if isinstance(node, ast.Lambda):
            inner = _Bindings()
            inner.visit(node.args)
            self._visit(node.body, prefix, {**scope, **inner.names}, False)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            inner = dict(scope)
            for generator in node.generators:
                self._visit(generator.iter, prefix, inner, False)
                targets = _Bindings()
                targets.visit(generator.target)
                inner = {**inner, **targets.names}
                for condition in generator.ifs:
                    self._visit(condition, prefix, inner, False)
            expressions = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
            for expr in expressions:
                self._visit(expr, prefix, inner, False)
            return
        for child in ast.iter_child_nodes(node):
            self._visit(child, prefix, scope, collect, closure)

    def resolve(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Call):
            return self.resolve(node.func)
        if isinstance(node, ast.Name):
            return self.scopes.get(node, {}).get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = self.resolve(node.value)
            return f"{base}.{node.attr}" if base is not None else None
        return None

    @cached_property
    def tables(self) -> ModuleTables:
        return ModuleTables(self.tree, self.resolve)

    @cached_property
    def table_shadows(self) -> dict[ast.Name, bool]:
        """Index enclosing definitions once, rather than walking them per lookup."""
        shadows: dict[ast.Name, bool] = {}
        stack: list[tuple[ast.AST, frozenset[str]]] = [(self.tree, frozenset())]
        while stack:
            check_deadline(self.deadline)
            node, hidden = stack.pop()
            if isinstance(node, ast.Name):
                shadows[node] = node.id in hidden
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                local = frozenset(hidden | self.definition_bindings[node].keys())
                body = set(node.body)
                for child in ast.iter_child_nodes(node):
                    stack.append((child, local if child in body else hidden))
            else:
                stack.extend((child, hidden) for child in ast.iter_child_nodes(node))
        return shadows

    def table_value(self, node: ast.Name) -> ast.List | ast.Tuple | None:
        check_deadline(self.deadline)
        if self.table_shadows.get(node, False):
            return None
        return self.tables.values.get(node.id)

    @property
    def definitions(self) -> dict[str, Definition]:
        return {**self.functions, **self.classes}


def walk_local(func: FuncNode) -> list[ast.AST]:
    """Nodes owned by func itself, excluding nested definition bodies."""
    out: list[ast.AST] = []
    stack: list[ast.AST] = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        out.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return out


def is_literal_like(node: ast.expr) -> bool:
    try:
        ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return False
    return True


def check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise TimeoutError("analysis timeout")


def within_deadline[T](items: Iterable[T], deadline: float) -> Iterator[T]:
    for item in items:
        check_deadline(deadline)
        yield item


class DeadlineTransformer(ast.NodeTransformer):
    def __init__(self, deadline: float = float("inf")) -> None:
        self.deadline = deadline

    def visit(self, node: ast.AST) -> ast.AST | list[ast.AST] | None:
        check_deadline(self.deadline)
        return super().visit(node)
