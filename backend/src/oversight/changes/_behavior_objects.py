"""Finite object arguments discovered from source, transported only as constructor data."""

import ast
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from oversight.changes._behavior_aliases import _AliasBindings, module_aliases  # pyright: ignore[reportPrivateUsage]
from oversight.changes._behavior_inputs import (
    _literal,  # pyright: ignore[reportPrivateUsage]
    _resolved,  # pyright: ignore[reportPrivateUsage]
    literal_text,
    module_name,
)
from oversight.changes._review_identity import bound_inputs
from oversight.changes._shortcuts_common import Source, is_test_file

TAG = "oversight.object_arguments.v1"


@dataclass(frozen=True)
class Constructor:
    module: str
    name: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


def class_signature(files: dict[str, str], module: str, name: str, deadline: float):
    # Receivers and arguments must retain exactly the same static class checks.
    from oversight.changes._behavior_methods import (
        _class_reason,  # pyright: ignore[reportPrivateUsage]
        _constructor,  # pyright: ignore[reportPrivateUsage]
        _decorator,  # pyright: ignore[reportPrivateUsage]
        _function_reason,  # pyright: ignore[reportPrivateUsage]
    )

    matches = [path for path in files if path.endswith(".py") and module_name(path) == module]
    if len(matches) != 1 or is_test_file(matches[0]):
        raise ValueError("argument is not a project class")
    source = Source(files[matches[0]], deadline)
    classes = [n for n in source.tree.body if isinstance(n, ast.ClassDef) and n.name == name]
    if len(classes) != 1:
        raise ValueError("ambiguous argument class")
    cls = classes[0]
    if reason := _class_reason(source, cls):
        raise ValueError(reason)
    for node in cls.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and _decorator(node) == "setter"
            and (reason := _function_reason(source, node))
        ):
            raise ValueError(reason)
    return _constructor(source, cls)


def constructor(source: Source, path: str, node: ast.Call, files: dict[str, str]) -> Constructor:
    aliases = module_aliases(source)
    matches: list[tuple[str, str]] = []
    for candidate, text in files.items():
        if not candidate.endswith(".py") or is_test_file(candidate):
            continue
        try:
            other = Source(text, source.deadline)
        except (SyntaxError, ValueError, RecursionError):
            continue
        module = module_name(candidate)
        for cls in other.tree.body:
            if isinstance(cls, ast.ClassDef) and _resolved(
                source, node, path, module, cls.name, aliases
            ):
                matches.append((module, cls.name))
    if len(matches) != 1:
        raise ValueError("unresolved argument constructor")
    args, kwargs = literal_arguments(node)
    module, name = matches[0]
    class_signature(files, module, name, source.deadline).bind(*args, **kwargs)
    return Constructor(module, name, args, kwargs)


def literal_arguments(call: ast.Call) -> tuple[tuple[Any, ...], dict[str, Any]]:
    if any(kw.arg is None for kw in call.keywords):
        raise ValueError("expanded arguments")
    args = tuple(_literal(arg) for arg in call.args)
    kwargs = {kw.arg: _literal(kw.value) for kw in call.keywords if kw.arg is not None}
    if len(kwargs) != len(call.keywords):
        raise ValueError("duplicate keywords")
    return args, kwargs


def _fixture(source: Source, path: str, node: ast.Name, files: dict[str, str]) -> Constructor:
    if not is_test_file(path):
        raise ValueError("fixture outside test")
    parents = {
        child: parent for parent in ast.walk(source.tree) for child in ast.iter_child_nodes(parent)
    }
    parent: ast.AST = node
    while parent in parents and not isinstance(
        parent, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)
    ):
        if isinstance(parent, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)) and any(
            isinstance(target, ast.Name) and target.id == node.id
            for generator in parent.generators
            for target in ast.walk(generator.target)
        ):
            raise ValueError("shadowed fixture parameter")
        parent = parents[parent]
    if not isinstance(parent, ast.FunctionDef) or parent not in source.tests.values():
        raise ValueError("not a test parameter")
    parameters = [*parent.args.posonlyargs, *parent.args.args, *parent.args.kwonlyargs]
    if node.id not in {arg.arg for arg in parameters}:
        raise ValueError("not a test parameter")
    bindings = _AliasBindings(source.deadline)
    for stmt in parent.body:
        bindings.visit(stmt)
    if node.id in bindings.names or bindings.star_import or node.id not in source.scopes[node]:
        raise ValueError("rebound fixture parameter")
    directory = PurePosixPath(path).parent
    candidates = [path, *(str(p / "conftest.py") for p in (directory, *directory.parents))]
    for candidate in dict.fromkeys(candidates):
        if candidate not in files:
            continue
        fixture_source = Source(files[candidate], source.deadline)
        found = [
            n
            for n in fixture_source.tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == node.id
        ]
        bindings = _AliasBindings(source.deadline)
        bindings.visit(fixture_source.tree)
        if not found:
            if node.id in bindings.names or bindings.star_import:
                raise ValueError("unresolved local fixture")
            continue
        if len(found) != 1 or not isinstance(found[0], ast.FunctionDef):
            raise ValueError("ambiguous or async fixture")
        if bindings.counts[node.id] != 1 or bindings.star_import:
            raise ValueError("rebound fixture")
        fixture = found[0]
        value = _fixture_return(fixture_source, fixture)
        return constructor(fixture_source, candidate, value, files)
    raise ValueError("unresolved fixture")


def _fixture_return(source: Source, fixture: ast.FunctionDef) -> ast.Call:
    if len(fixture.decorator_list) != 1:
        raise ValueError("unsupported fixture decorator")
    dec = fixture.decorator_list[0]
    func = dec.func if isinstance(dec, ast.Call) else dec
    if source.resolve(func) != "pytest.fixture":
        raise ValueError("not a pytest fixture")
    if isinstance(dec, ast.Call) and (
        dec.args or any(k.arg not in {"scope", "autouse", "ids"} for k in dec.keywords)
    ):
        raise ValueError("parameterized or renamed fixture")
    if (
        fixture.args.posonlyargs
        or fixture.args.args
        or fixture.args.kwonlyargs
        or fixture.args.vararg
        or fixture.args.kwarg
    ):
        raise ValueError("fixture dependencies")
    if len(fixture.body) != 1 or not isinstance(fixture.body[0], ast.Return):
        raise ValueError("fixture body")
    value = fixture.body[0].value
    if not isinstance(value, ast.Call):
        raise ValueError("fixture is not a constructor")
    return value


def arguments(source: Source, path: str, call: ast.Call, files: dict[str, str]):
    def value(node: ast.expr):
        try:
            return _literal(node)
        except (ValueError, TypeError):
            if isinstance(node, ast.Call):
                return constructor(source, path, node, files)
            if isinstance(node, ast.Name):
                return _fixture(source, path, node, files)
            raise ValueError("unsupported object argument") from None

    if any(kw.arg is None for kw in call.keywords):
        raise ValueError("expanded arguments")
    args = tuple(value(arg) for arg in call.args)
    kwargs = {kw.arg: value(kw.value) for kw in call.keywords if kw.arg is not None}
    if len(kwargs) != len(call.keywords):
        raise ValueError("duplicate keywords")
    return args, kwargs


def display(value: Any) -> str:
    if not isinstance(value, Constructor):
        return literal_text(value)
    parts = [
        *(literal_text(v) for v in value.args),
        *(f"{k}={literal_text(v)}" for k, v in value.kwargs.items()),
    ]
    return f"{value.name}({', '.join(parts)})"


def invocation(values: tuple[tuple[Any, ...], dict[str, Any]]) -> str:
    args, kwargs = values
    if not any(isinstance(v, Constructor) for v in (*args, *kwargs.values())):
        return literal_text(values)

    def data(value: Any):
        if isinstance(value, Constructor):
            return ("constructor", value.module, value.name, value.args, value.kwargs)
        return ("literal", value)

    return literal_text(
        (TAG, tuple(data(v) for v in args), {k: data(v) for k, v in kwargs.items()})
    )


def input_identity(named: dict[str, Any], files: dict[str, str], deadline: float) -> str | None:
    data: dict[str, Any] = {}
    for key, value in named.items():
        if isinstance(value, Constructor):
            sig = class_signature(files, value.module, value.name, deadline)
            bound = sig.bind(*value.args, **value.kwargs)
            bound.apply_defaults()
            effective = bound_inputs(dict(bound.arguments))
            if effective is None:
                return None
            data[key] = (
                "constructor",
                value.module,
                value.name,
                value.args,
                value.kwargs,
                effective,
            )
        else:
            data[key] = ("literal", value)
    # Use a disjoint identity domain: a literal tuple can look exactly like constructor data.
    if any(isinstance(v, Constructor) for v in named.values()):
        identity = bound_inputs(data)
        return f"{TAG}:{identity}" if identity is not None else None
    return bound_inputs(named)


def method_arguments(module: str, cls: str, name: str, files: dict[str, str], deadline: float):
    found: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for path, text in sorted(files.items()):
        if not path.endswith(".py"):
            continue
        try:
            source = Source(text, deadline)
            aliases = module_aliases(source)
            for node in ast.walk(source.tree):
                if (
                    not isinstance(node, ast.Call)
                    or not isinstance(node.func, ast.Attribute)
                    or node.func.attr != name
                ):
                    continue
                receiver = node.func.value
                if isinstance(receiver, ast.Name):
                    receiver = _assigned_receiver(source, node, receiver.id)
                if isinstance(receiver, ast.Call) and _resolved(
                    source, receiver, path, module, cls, aliases
                ):
                    try:
                        found.append(arguments(source, path, node, files))
                    except (ValueError, TypeError, SyntaxError, RecursionError):
                        continue
        except (ValueError, TypeError, SyntaxError, RecursionError):
            continue
    return found


def _assigned_receiver(source: Source, call: ast.Call, name: str) -> ast.expr | None:
    parents = {
        child: parent for parent in ast.walk(source.tree) for child in ast.iter_child_nodes(parent)
    }
    scope: ast.AST = call
    while scope in parents:
        scope = parents[scope]
        if isinstance(scope, (ast.Lambda, ast.ClassDef)):
            return None
        if isinstance(scope, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)) and any(
            isinstance(target, ast.Name) and target.id == name
            for generator in scope.generators
            for target in ast.walk(generator.target)
        ):
            return None
        if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
            break
    if not isinstance(scope, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    if any(isinstance(n, (ast.Global, ast.Nonlocal)) and name in n.names for n in ast.walk(scope)):
        return None
    bindings = _AliasBindings(source.deadline)
    if isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
        bindings.visit(scope.args)
    for stmt in scope.body:
        bindings.visit(stmt)
    if bindings.counts[name] != 1 or bindings.star_import:
        return None
    for stmt in scope.body:
        if not isinstance(stmt, (ast.Assign, ast.AnnAssign)) or stmt.lineno >= call.lineno:
            continue
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        if len(targets) == 1 and isinstance(targets[0], ast.Name) and targets[0].id == name:
            return stmt.value
    return None
