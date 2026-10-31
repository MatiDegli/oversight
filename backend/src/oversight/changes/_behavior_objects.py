"""Finite object arguments discovered from source, transported only as constructor data."""

import ast
from dataclasses import dataclass
from typing import Any

from oversight.changes._behavior_aliases import _AliasBindings, module_aliases  # pyright: ignore[reportPrivateUsage]
from oversight.changes._behavior_builtins import range_argument
from oversight.changes._behavior_construction import construction_ast
from oversight.changes._behavior_fixtures import fixture
from oversight.changes._behavior_inputs import (
    _literal,  # pyright: ignore[reportPrivateUsage]
    _resolved,  # pyright: ignore[reportPrivateUsage]
    literal_text,
)
from oversight.changes._behavior_sources import Sources
from oversight.changes._review_identity import bound_inputs
from oversight.changes._shortcuts_common import Source, check_deadline, is_test_file

TAG = "oversight.object_arguments.v1"


@dataclass(frozen=True)
class Constructor:
    module: str
    name: str
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


def class_signature(cache: Sources, module: str, name: str):
    # Receivers and arguments must retain exactly the same static class checks.
    from oversight.changes._behavior_methods import (
        _class_reason,  # pyright: ignore[reportPrivateUsage]
        _constructor,  # pyright: ignore[reportPrivateUsage]
        _decorator,  # pyright: ignore[reportPrivateUsage]
        _function_reason,  # pyright: ignore[reportPrivateUsage]
    )

    key = (module, name)
    if key in cache.constructors:
        return cache.constructors[key][0]
    matches = cache.modules.get(module, [])
    if len(matches) != 1 or is_test_file(matches[0]):
        raise ValueError("argument is not a project class")
    source = cache.get(matches[0])
    classes = [node for _, node in cache.classes.get(module, []) if node.name == name]
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
    signature = _constructor(source, cls)
    cache.constructors[key] = (signature, construction_ast(cls, cache.deadline, source))
    return signature


def constructor(source: Source, path: str, node: ast.Call, cache: Sources) -> Constructor:
    aliases = module_aliases(source)
    matches: list[tuple[str, str]] = []
    for module, classes in cache.classes.items():
        for _, cls in classes:
            check_deadline(cache.deadline)
            if _resolved(source, node, path, module, cls.name, aliases, cache):
                matches.append((module, cls.name))
    if len(matches) != 1:
        raise ValueError("unresolved argument constructor")
    args, kwargs = literal_arguments(node)
    module, name = matches[0]
    class_signature(cache, module, name).bind(*args, **kwargs)
    return Constructor(module, name, args, kwargs)


def literal_arguments(call: ast.Call) -> tuple[tuple[Any, ...], dict[str, Any]]:
    if any(kw.arg is None for kw in call.keywords):
        raise ValueError("expanded arguments")
    args = tuple(_literal(arg) for arg in call.args)
    kwargs = {kw.arg: _literal(kw.value) for kw in call.keywords if kw.arg is not None}
    if len(kwargs) != len(call.keywords):
        raise ValueError("duplicate keywords")
    return args, kwargs


def arguments(source: Source, path: str, call: ast.Call, files: dict[str, str] | Sources):
    cache = files if isinstance(files, Sources) else Sources(files, source.deadline)

    def value(node: ast.expr):
        check_deadline(cache.deadline)
        try:
            return _literal(node)
        except (ValueError, TypeError):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "range":
                    return range_argument(source, node)
                return constructor(source, path, node, cache)
            if isinstance(node, ast.Name):
                return fixture(source, path, node, cache)
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
    if not any(isinstance(v, (Constructor, range)) for v in (*args, *kwargs.values())):
        return literal_text(values)

    def data(value: Any):
        if isinstance(value, Constructor):
            return ("constructor", value.module, value.name, value.args, value.kwargs)
        if type(value) is range:
            return ("range", value.start, value.stop, value.step)
        return ("literal", value)

    return literal_text(
        (TAG, tuple(data(v) for v in args), {k: data(v) for k, v in kwargs.items()})
    )


def invocation_values(text: str) -> tuple[tuple[Any, ...], dict[str, Any]]:
    values = ast.literal_eval(text)
    if len(values) == 2:
        return values
    tag, args, kwargs = values
    if tag != TAG:
        raise ValueError("unsupported argument encoding")

    def value(data: Any) -> Any:
        if data[0] == "constructor":
            return Constructor(*data[1:])
        if data[0] == "range":
            return range(*data[1:])
        return data[1]

    return tuple(value(arg) for arg in args), {key: value(arg) for key, arg in kwargs.items()}


def input_identity(
    named: dict[str, Any], files: dict[str, str] | Sources, deadline: float
) -> str | None:
    cache = files if isinstance(files, Sources) else Sources(files, deadline)
    data: dict[str, Any] = {}
    for key, value in named.items():
        check_deadline(cache.deadline)
        if isinstance(value, Constructor):
            sig = class_signature(cache, value.module, value.name)
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
                cache.constructors[(value.module, value.name)][1],
            )
        else:
            data[key] = ("literal", value)
    # Use a disjoint identity domain: a literal tuple can look exactly like constructor data.
    if any(isinstance(v, Constructor) for v in named.values()):
        identity = bound_inputs(data)
        return f"{TAG}:{identity}" if identity is not None else None
    return bound_inputs(named)


def method_arguments(module: str, cls: str, name: str, cache: Sources):
    found: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    for path in sorted(cache.files):
        check_deadline(cache.deadline)
        if not path.endswith(".py"):
            continue
        try:
            source = cache.get(path)
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
                    receiver = _assigned_receiver(source, node, receiver.id, cache)
                if isinstance(receiver, ast.Call) and _resolved(
                    source, receiver, path, module, cls, aliases, cache
                ):
                    try:
                        found.append(arguments(source, path, node, cache))
                    except (ValueError, TypeError, SyntaxError, RecursionError):
                        continue
        except (ValueError, TypeError, SyntaxError, RecursionError):
            continue
    return found


def _assigned_receiver(
    source: Source, call: ast.Call, name: str, cache: Sources
) -> ast.expr | None:
    parents = cache.parents(source)
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
