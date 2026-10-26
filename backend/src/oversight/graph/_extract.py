"""Parse one file with the standard library's ast into a `FileParse`.

Nodes are modules, classes, and the functions and methods defined at module or class level, also
inside `if`, `try`, `with` and loops there. Anything defined inside a function, and comprehensions,
generator expressions and lambdas, are not nodes: their calls belong to the nearest enclosing node.
Names resolve innermost scope first, as Python does: a parameter, a comprehension target or a local
assignment hides a module-level function of the same name.
"""

import ast
import hashlib
import io
import tokenize
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Literal

from oversight.graph._facts import (
    Base,
    Binding,
    CallFact,
    CallRef,
    ClassFacts,
    ClassName,
    DefBinding,
    DynamicRef,
    FileParse,
    FixtureRef,
    GlobalName,
    ImportBinding,
    ImportFact,
    LocalImport,
    OtherBinding,
    ParamBinding,
    SelfBase,
    UnknownBase,
)
from oversight_contracts.graph import GraphNode, NodeKind, ParseError

type _Def = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
type _Func = ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda
type _Comp = ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp
type _ScopeKind = Literal["module", "class", "function", "comprehension"]

_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_COMPS = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def module_name(path: str) -> tuple[str, bool] | None:
    """`shop/orders.py` is `shop.orders`; a package's `__init__.py` is the package. None when a
    segment is not an identifier, so the file cannot be imported as a module."""
    parts = path.removesuffix(".py").split("/")
    is_package = parts[-1] == "__init__"
    if is_package:
        parts = parts[:-1]
    if not parts or not all(part.isidentifier() for part in parts):
        return None
    return ".".join(parts), is_package


def is_test_path(path: str) -> bool:
    parts = path.split("/")
    name = parts[-1]
    return (
        any(part in ("tests", "test") for part in parts[:-1])
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


def absolute_module(module: str, is_package: bool, level: int, name: str | None) -> str | None:
    """The module a `from` import names. A package's `__init__.py` is its own package; a plain
    module's package is its parent. None when the import climbs above the root."""
    if level == 0:
        return name
    package = module.split(".") if is_package else module.split(".")[:-1]
    if len(package) < level:
        return None
    parts = package[: len(package) - level + 1] + (name.split(".") if name else [])
    return ".".join(parts)


def parse_file(path: str, data: bytes) -> FileParse | ParseError:
    named = module_name(path)
    if named is None:
        return ParseError(path=path, line=None, message="not an importable module path")
    try:
        tree = ast.parse(data, filename=path)
    except SyntaxError as error:
        return ParseError(path=path, line=error.lineno, message=error.msg)
    except ValueError as error:
        return ParseError(path=path, line=None, message=str(error))
    module, is_package = named
    nodes = _Nodes(module, path, is_test_path(path), _utf8_source(data))
    nodes.visit_body(tree.body, module, "module", "")
    facts = _Facts(module, is_package, nodes)
    facts.visit_module(tree)
    return FileParse(
        path=path,
        module=module,
        is_package=is_package,
        nodes=tuple(nodes.nodes),
        own_hashes=nodes.own_hashes(),
        bindings=facts.module_bindings(),
        star_imports=tuple(facts.star_imports),
        all_names=_all_names(tree),
        classes=facts.classes,
        calls=tuple(facts.calls),
        imports=tuple(facts.imports),
        fixtures=facts.fixtures,
    )


def _utf8_source(data: bytes) -> bytes:
    """The source as UTF-8, which is what the AST's column offsets count, whatever the file's
    declared encoding. A byte-order mark is dropped, as the parser drops it."""
    encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    return data.decode(encoding).encode("utf-8")


class _Nodes:
    """The first pass: nodes, their ids and ranges, and the byte spans for own-source hashes.
    `data` is the source re-encoded as UTF-8, so the AST's offsets index it."""

    def __init__(self, module: str, path: str, is_test: bool, data: bytes) -> None:
        self.module = module
        self.path = path
        self.is_test = is_test
        self.data = data
        lines = data.splitlines(keepends=True)
        self.line_starts = [0]
        for line in lines:
            self.line_starts.append(self.line_starts[-1] + len(line))
        self.nodes: list[GraphNode] = [
            GraphNode(
                id=module,
                kind="module",
                name=module.rsplit(".", 1)[-1],
                qualname=module,
                path=path,
                start_line=1,
                end_line=max(1, len(lines)),
                parent_id=None,
                is_test=is_test,
            )
        ]
        self.ids: dict[int, str] = {}
        self.kinds: dict[str, NodeKind] = {module: "module"}
        self.spans: dict[str, tuple[int, int]] = {module: (0, len(data))}
        self.children: dict[str, list[str]] = {module: []}
        self._counts: dict[str, int] = {}

    def visit_body(
        self, body: list[ast.stmt], parent_id: str, parent_kind: str, prefix: str
    ) -> None:
        for stmt in body:
            if isinstance(stmt, _DEFS):
                self._add(stmt, parent_id, parent_kind, prefix)
            else:
                for nested in _nested_bodies(stmt):
                    self.visit_body(nested, parent_id, parent_kind, prefix)

    def _add(self, defn: _Def, parent_id: str, parent_kind: str, prefix: str) -> None:
        qualname = prefix + defn.name
        count = self._counts.get(qualname, 0) + 1
        self._counts[qualname] = count
        node_id = f"{self.module}:{qualname}" + (f"#{count}" if count > 1 else "")
        kind: NodeKind
        if isinstance(defn, ast.ClassDef):
            kind = "class"
        else:
            kind = "method" if parent_kind == "class" else "function"
        start_line = min([d.lineno for d in defn.decorator_list] + [defn.lineno])
        end_line = defn.end_lineno or defn.lineno
        self.nodes.append(
            GraphNode(
                id=node_id,
                kind=kind,
                name=defn.name,
                qualname=qualname,
                path=self.path,
                start_line=start_line,
                end_line=end_line,
                parent_id=parent_id,
                is_test=self.is_test,
            )
        )
        self.ids[id(defn)] = node_id
        self.kinds[node_id] = kind
        end = self._offset(end_line, defn.end_col_offset)
        self.spans[node_id] = (self._start_offset(defn), end)
        self.children[parent_id].append(node_id)
        self.children[node_id] = []
        if isinstance(defn, ast.ClassDef):
            self.visit_body(defn.body, node_id, "class", qualname + ".")

    def _offset(self, line: int, col: int | None) -> int:
        start = self.line_starts[min(line, len(self.line_starts)) - 1]
        return start + (col or 0)

    def _start_offset(self, defn: _Def) -> int:
        if not defn.decorator_list:
            return self._offset(defn.lineno, defn.col_offset)
        first = min(defn.decorator_list, key=lambda d: (d.lineno, d.col_offset))
        offset = self._offset(first.lineno, first.col_offset)
        at = self.data.rfind(b"@", self.line_starts[first.lineno - 1], offset)
        return at if at >= 0 else offset

    def own_hashes(self) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for node_id, (start, end) in self.spans.items():
            digest = hashlib.sha256()
            cursor = start
            for child in sorted(self.children[node_id], key=lambda c: self.spans[c][0]):
                child_start, child_end = self.spans[child]
                digest.update(self.data[cursor:child_start])
                cursor = child_end
            digest.update(self.data[cursor:end])
            hashes[node_id] = digest.hexdigest()
        return hashes


def _nested_bodies(stmt: ast.stmt) -> Iterator[list[ast.stmt]]:
    """The statement lists inside a compound statement that are still in the same scope."""
    if isinstance(stmt, ast.If | ast.For | ast.AsyncFor | ast.While):
        yield stmt.body
        yield stmt.orelse
    elif isinstance(stmt, ast.With | ast.AsyncWith):
        yield stmt.body
    elif isinstance(stmt, ast.Try | ast.TryStar):
        yield stmt.body
        for handler in stmt.handlers:
            yield handler.body
        yield stmt.orelse
        yield stmt.finalbody
    elif isinstance(stmt, ast.Match):
        for case in stmt.cases:
            yield case.body


@dataclass
class _Scope:
    kind: _ScopeKind
    class_id: str | None = None
    """A class scope's node id; None for a class that is not a node."""
    self_name: str | None = None
    """In a method: its first parameter, which holds the instance or the class."""
    self_class: str | None = None
    bindings: dict[str, list[Binding]] = field(default_factory=dict[str, list[Binding]])
    globals_: set[str] = field(default_factory=set[str])
    nonlocals: set[str] = field(default_factory=set[str])
    rebound_inside: set[str] = field(default_factory=set[str])
    """Names that a function nested in this one declares `nonlocal`, so it may rebind them."""

    def bind(self, name: str, binding: Binding) -> None:
        self.bindings.setdefault(name, []).append(binding)


class _Facts:
    """The second pass: scopes, bindings, imports and call sites."""

    def __init__(self, module: str, is_package: bool, nodes: _Nodes) -> None:
        self.module = module
        self.is_package = is_package
        self.nodes = nodes
        self.stack: list[_Scope] = []
        self.owners: list[str] = [module]
        self.calls: list[CallFact] = []
        self.imports: list[ImportFact] = []
        self.star_imports: list[str] = []
        self.classes: dict[str, ClassFacts] = {}
        self.fixtures: dict[str, str] = {}
        self.assigned: dict[str, set[str]] = {}
        """Per class: the attributes its methods set or delete on `self` or `cls`."""

    def module_bindings(self) -> dict[str, tuple[Binding, ...]]:
        return {name: tuple(b) for name, b in self.stack[0].bindings.items()}

    def visit_module(self, tree: ast.Module) -> None:
        scope = _Scope("module")
        self.stack.append(scope)
        self._collect(scope, tree.body)
        for stmt in tree.body:
            self._visit(stmt)

    # Bindings ------------------------------------------------------------------------------

    def _collect(self, scope: _Scope, nodes: Iterable[ast.AST]) -> None:
        for node in nodes:
            self._bind(scope, node)

    def _store(self, scope: _Scope, name: str, binding: Binding) -> None:
        if name in scope.globals_:
            self.stack[0].bind(name, OtherBinding())
        elif name not in scope.nonlocals:
            scope.bind(name, binding)

    def _bind(self, scope: _Scope, node: ast.AST) -> None:
        """Record what `node` binds in `scope`, without entering nested scopes."""
        if isinstance(node, _DEFS):
            node_id = self.nodes.ids.get(id(node))
            is_node = node_id is not None and scope.kind in ("module", "class")
            binding = DefBinding(node_id) if is_node and node_id else OtherBinding()
            self._store(scope, node.name, binding)
            self._collect(scope, node.decorator_list)
            if isinstance(node, ast.ClassDef):
                self._collect(scope, node.bases)
            else:
                self._collect(scope, _defaults(node.args))
            if scope.kind == "function":
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Nonlocal):
                        scope.rebound_inside.update(sub.names)
        elif isinstance(node, ast.Lambda):
            self._collect(scope, _defaults(node.args))
        elif isinstance(node, _COMPS):
            for sub in ast.walk(node):  # a walrus in a comprehension binds in the enclosing scope
                if isinstance(sub, ast.NamedExpr):
                    self._store(scope, sub.target.id, OtherBinding())
        elif isinstance(node, ast.Import):
            for alias in node.names:
                self.imports.append(ImportFact(alias.name, ()))
                if alias.asname:
                    self._store(scope, alias.asname, ImportBinding(alias.name, None))
                else:  # `import a.b` binds `a`
                    top = alias.name.split(".")[0]
                    self._store(scope, top, ImportBinding(top, None))
        elif isinstance(node, ast.ImportFrom):
            self._bind_from(scope, node)
        elif isinstance(node, ast.Global):
            scope.globals_.update(node.names)
        elif isinstance(node, ast.Nonlocal):
            scope.nonlocals.update(node.names)
        elif isinstance(node, ast.AnnAssign) and node.value is None and scope.kind != "function":
            return  # an annotation alone binds nothing outside a function
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            self._store(scope, node.id, OtherBinding())
        else:
            name = None
            if isinstance(node, ast.ExceptHandler | ast.MatchAs | ast.MatchStar):
                name = node.name
            elif isinstance(node, ast.MatchMapping):
                name = node.rest
            if name:
                self._store(scope, name, OtherBinding())
            self._collect(scope, ast.iter_child_nodes(node))

    def _bind_from(self, scope: _Scope, node: ast.ImportFrom) -> None:
        source = absolute_module(self.module, self.is_package, node.level, node.module)
        if source is None:
            for alias in node.names:
                if alias.name != "*":
                    self._store(scope, alias.asname or alias.name, OtherBinding())
            return
        self.imports.append(ImportFact(source, tuple(alias.name for alias in node.names)))
        for alias in node.names:
            if alias.name == "*":
                self.star_imports.append(source)
            else:
                self._store(scope, alias.asname or alias.name, ImportBinding(source, alias.name))

    # Call sites ----------------------------------------------------------------------------

    def _visit(self, node: ast.AST) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            self._visit_decorators(node.decorator_list)
            self._visit_all(node.args.defaults)
            self._visit_all(d for d in node.args.kw_defaults if d is not None)
            self._enter_function(node)
        elif isinstance(node, ast.ClassDef):
            self._visit_decorators(node.decorator_list)
            self._visit_all(node.bases)
            self._visit_all(k.value for k in node.keywords)
            self._enter_class(node)
        elif isinstance(node, ast.Lambda):
            self._visit_all(node.args.defaults)
            self._visit_all(d for d in node.args.kw_defaults if d is not None)
            self._enter_function(node)
        elif isinstance(node, _COMPS):
            self._enter_comprehension(node)
        else:
            if isinstance(node, ast.Call):
                self._record_call(node)
            elif (
                isinstance(node, ast.Attribute)
                and not isinstance(node.ctx, ast.Load)
                and isinstance(node.value, ast.Name)
                and isinstance(base := self._lookup(node.value.id), SelfBase)
            ):
                self.assigned.setdefault(base.class_id, set()).add(node.attr)
            self._visit_all(ast.iter_child_nodes(node))

    def _visit_decorators(self, decorators: list[ast.expr]) -> None:
        """`@deco` calls `deco` with the definition, in the enclosing scope."""
        for decorator in decorators:
            if not isinstance(decorator, ast.Call) and (ref := self._ref(decorator)) is not None:
                self.calls.append(CallFact(self.owners[-1], ref))
            self._visit(decorator)

    def _visit_all(self, nodes: Iterable[ast.AST]) -> None:
        for node in nodes:
            self._visit(node)

    def _enter_function(self, func: _Func) -> None:
        node_id = None if isinstance(func, ast.Lambda) else self.nodes.ids.get(id(func))
        scope = _Scope("function")
        args = func.args
        params = [*args.posonlyargs, *args.args]
        if args.vararg:
            params.append(args.vararg)
        params.extend(args.kwonlyargs)
        if args.kwarg:
            params.append(args.kwarg)
        for param in params:
            scope.bind(param.arg, ParamBinding())
        parent = self.stack[-1]
        if node_id is not None and not isinstance(func, ast.Lambda):
            static = any(_decorator_name(d) == "staticmethod" for d in func.decorator_list)
            positional = [*args.posonlyargs, *args.args]
            is_method = self.nodes.kinds[node_id] == "method"
            if is_method and parent.class_id and positional and not static:
                scope.self_name = positional[0].arg
                scope.self_class = parent.class_id
            self._record_fixtures(func, node_id, scope.self_name is not None)
        body: list[ast.stmt] | list[ast.expr] = (
            [func.body] if isinstance(func, ast.Lambda) else func.body
        )
        self._collect(scope, body)
        for name in scope.rebound_inside:  # a nested function may assign it through `nonlocal`
            if name in scope.bindings and name not in scope.nonlocals | scope.globals_:
                scope.bind(name, OtherBinding())
        self.stack.append(scope)
        self.owners.append(node_id or self.owners[-1])
        self._visit_all(body)
        self.owners.pop()
        self.stack.pop()

    def _enter_class(self, cls: ast.ClassDef) -> None:
        node_id = self.nodes.ids.get(id(cls))
        bases = tuple(ref for b in cls.bases if (ref := self._ref(b)) is not None)
        scope = _Scope("class", class_id=node_id)
        self.stack.append(scope)
        self.owners.append(node_id or self.owners[-1])
        self._class_body(scope, cls.body)
        self.owners.pop()
        self.stack.pop()
        if node_id is not None:
            bindings = {name: tuple(b) for name, b in scope.bindings.items()}
            assigned = frozenset(self.assigned.get(node_id, ()))
            self.classes[node_id] = ClassFacts(bindings=bindings, bases=bases, assigned=assigned)

    def _class_body(self, scope: _Scope, body: list[ast.stmt]) -> None:
        """Visit a class body in order, binding each statement's names after it runs: unlike a
        function's, a class body sees only the names bound so far."""
        for stmt in body:
            bodies = list(_nested_bodies(stmt))
            if not bodies:
                self._visit(stmt)
                self._bind(scope, stmt)
                continue
            nested = {id(s) for b in bodies for s in b}
            heads = [c for c in ast.iter_child_nodes(stmt) if id(c) not in nested]
            for head in heads:
                if isinstance(head, ast.ExceptHandler):
                    self._visit_all([head.type] if head.type else [])
                elif isinstance(head, ast.match_case):
                    self._visit(head.pattern)
                else:
                    self._visit(head)
            for head in heads:
                if isinstance(head, ast.ExceptHandler):
                    if head.name:
                        self._store(scope, head.name, OtherBinding())
                elif isinstance(head, ast.match_case):  # the guard runs once the pattern binds
                    self._bind(scope, head.pattern)
                    if head.guard:
                        self._visit(head.guard)
                        self._bind(scope, head.guard)
                else:
                    self._bind(scope, head)
            for nested_body in bodies:
                self._class_body(scope, nested_body)

    def _enter_comprehension(self, comp: _Comp) -> None:
        first, *rest = comp.generators
        self._visit(first.iter)  # the first iterable runs in the enclosing scope
        scope = _Scope("comprehension")
        for generator in comp.generators:
            for sub in ast.walk(generator.target):
                if isinstance(sub, ast.Name):
                    scope.bind(sub.id, OtherBinding())
        self.stack.append(scope)
        self._visit(first.target)
        self._visit_all(first.ifs)
        for generator in rest:
            self._visit_all([generator.iter, generator.target, *generator.ifs])
        if isinstance(comp, ast.DictComp):
            self._visit_all([comp.key, comp.value])
        else:
            self._visit(comp.elt)
        self.stack.pop()

    def _record_call(self, call: ast.Call) -> None:
        func = call.func
        if (
            isinstance(func, ast.Call)
            and isinstance(func.func, ast.Name)
            and func.func.id == "getattr"
            and len(func.args) >= 2
            and isinstance(name := func.args[1], ast.Constant)
            and isinstance(name.value, str)
        ):
            self.calls.append(CallFact(self.owners[-1], DynamicRef(name.value)))
            return
        ref = self._ref(func)
        if ref is not None:
            self.calls.append(CallFact(self.owners[-1], ref))

    def _record_fixtures(
        self, func: ast.FunctionDef | ast.AsyncFunctionDef, node_id: str, is_method: bool
    ) -> None:
        is_fixture = any(_decorator_name(d) == "fixture" for d in func.decorator_list)
        if is_fixture and self.nodes.kinds[node_id] == "function":
            self.fixtures[func.name] = node_id
        if not self.nodes.is_test or not (is_fixture or func.name.startswith("test")):
            return
        args = func.args
        params = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        for param in params[1:] if is_method else params:
            self.calls.append(CallFact(node_id, FixtureRef(param.arg)))

    def _ref(self, expr: ast.expr) -> CallRef | None:
        attrs: list[str] = []
        while isinstance(expr, ast.Attribute):
            attrs.append(expr.attr)
            expr = expr.value
        attrs.reverse()
        base: Base = UnknownBase()
        if isinstance(expr, ast.Name):
            base = self._lookup(expr.id)
        elif (
            isinstance(expr, ast.Call)
            and isinstance(expr.func, ast.Name)
            and expr.func.id == "super"
            and (method := self._method_scope()) is not None
            and method.self_class
        ):
            base = SelfBase(method.self_class, via_super=True)
        if isinstance(base, UnknownBase) and not attrs:
            return None
        return CallRef(base, tuple(attrs))

    def _method_scope(self) -> _Scope | None:
        for scope in reversed(self.stack):
            if scope.kind == "function":
                return scope if scope.self_class else None
        return None

    def _lookup(self, name: str) -> Base:
        innermost = len(self.stack) - 1
        for index in range(innermost, -1, -1):
            scope = self.stack[index]
            if scope.kind == "class" and index != innermost:
                continue  # a class body is not visible from the scopes nested in it
            if name in scope.globals_:
                return GlobalName(name)
            if name in scope.nonlocals:
                continue
            bindings = scope.bindings.get(name)
            if not bindings:
                continue
            if scope.kind == "module":
                return GlobalName(name)
            if scope.kind == "class":
                return ClassName(scope.class_id, name) if scope.class_id else UnknownBase()
            if (
                scope.self_class
                and name == scope.self_name
                and all(isinstance(b, ParamBinding) for b in bindings)
            ):
                return SelfBase(scope.self_class, via_super=False)
            imports = tuple(b for b in bindings if isinstance(b, ImportBinding))
            if imports:
                return LocalImport(imports, rebound=len(imports) < len(bindings))
            return UnknownBase()
        return GlobalName(name)


def _defaults(args: ast.arguments) -> list[ast.expr]:
    """The default values of a signature, positional and keyword-only: they run in the enclosing
    scope, so a walrus in one binds there."""
    return [*args.defaults, *(d for d in args.kw_defaults if d is not None)]


def _decorator_name(decorator: ast.expr) -> str | None:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _all_names(tree: ast.Module) -> frozenset[str] | None:
    names: frozenset[str] | None = None
    for stmt in tree.body:
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Name)
            and stmt.targets[0].id == "__all__"
        ):
            value = stmt.value
            if not isinstance(value, ast.List | ast.Tuple):
                return None
            items = [e.value for e in value.elts if isinstance(e, ast.Constant)]
            if len(items) != len(value.elts) or not all(isinstance(i, str) for i in items):
                return None
            names = frozenset(str(i) for i in items)
    return names
