"""Conservative pytest fixture binding and single-return constructor discovery."""

import ast
from pathlib import PurePosixPath

from oversight.changes._behavior_aliases import _AliasBindings  # pyright: ignore[reportPrivateUsage]
from oversight.changes._behavior_sources import Sources
from oversight.changes._shortcuts_common import Source, check_deadline, is_test_file

type Function = ast.FunctionDef | ast.AsyncFunctionDef


def fixture(source: Source, path: str, node: ast.Name, cache: Sources):
    from oversight.changes._behavior_objects import constructor

    if not is_test_file(path):
        raise ValueError("fixture outside test")
    parents = cache.parents(source)
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
    _parameter(source, parent, node.id, parents)
    bindings = _AliasBindings(source.deadline)
    for stmt in parent.body:
        bindings.visit(stmt)
    if (
        node.id in bindings.names
        or bindings.star_import
        or node.id not in source.scopes.get(node, {})
    ):
        raise ValueError("rebound fixture parameter")
    enclosing = parents.get(parent)
    while enclosing is not None:
        check_deadline(cache.deadline)
        if isinstance(enclosing, ast.ClassDef) and _named_fixture(source, enclosing.body, node.id):
            raise ValueError("unsupported class fixture")
        enclosing = parents.get(enclosing)
    directory = PurePosixPath(path).parent
    candidates = [path, *(str(p / "conftest.py") for p in (directory, *directory.parents))]
    for candidate in dict.fromkeys(candidates):
        check_deadline(cache.deadline)
        if candidate not in cache.files:
            continue
        other = cache.get(candidate)
        found = [
            n
            for n in other.tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == node.id
        ]
        if not found and _named_fixture(other, other.tree.body, node.id):
            raise ValueError("renamed fixture")
        bindings = _AliasBindings(source.deadline)
        bindings.visit(other.tree)
        if not found:
            if node.id in bindings.names or bindings.star_import:
                raise ValueError("unresolved local fixture")
            continue
        if len(found) != 1 or not isinstance(found[0], ast.FunctionDef):
            raise ValueError("ambiguous or async fixture")
        if bindings.counts[node.id] != 1 or bindings.star_import:
            raise ValueError("rebound fixture")
        return constructor(other, candidate, _fixture_return(other, found[0]), cache)
    raise ValueError("unresolved fixture")


def _parameter(source: Source, test: ast.FunctionDef, name: str, parents: dict[ast.AST, ast.AST]):
    positional = [*test.args.posonlyargs, *test.args.args]
    if name not in {arg.arg for arg in [*positional, *test.args.kwonlyargs]}:
        raise ValueError("not a test parameter")
    defaults = positional[len(positional) - len(test.args.defaults) :]
    defaults += [
        arg
        for arg, value in zip(test.args.kwonlyargs, test.args.kw_defaults, strict=True)
        if value is not None
    ]
    if any(arg.arg == name for arg in defaults):
        raise ValueError("defaulted test parameter")
    scope: ast.AST = test
    while scope in parents:
        if isinstance(scope, (ast.FunctionDef, ast.ClassDef)):
            for dec in scope.decorator_list:
                if (
                    isinstance(dec, ast.Call)
                    and source.resolve(dec.func) == "pytest.mark.parametrize"
                ):
                    values = (
                        dec.args[0]
                        if dec.args
                        else next((kw.value for kw in dec.keywords if kw.arg == "argnames"), None)
                    )
                    try:
                        names = ast.literal_eval(values) if values is not None else None
                    except (ValueError, TypeError):
                        raise ValueError("unresolved parametrization") from None
                    if isinstance(names, str):
                        names = [part.strip() for part in names.split(",")]
                    if not isinstance(names, (tuple, list)) or name in names:
                        raise ValueError("parametrized test parameter")
        scope = parents[scope]


def _named_fixture(source: Source, body: list[ast.stmt], name: str) -> bool:
    for stmt in body:
        check_deadline(source.deadline)
        if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in stmt.decorator_list:
            func = dec.func if isinstance(dec, ast.Call) else dec
            if source.resolve(func) != "pytest.fixture":
                continue
            if stmt.name == name or (
                isinstance(dec, ast.Call)
                and any(
                    kw.arg == "name"
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value == name
                    for kw in dec.keywords
                )
            ):
                return True
    return False


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
