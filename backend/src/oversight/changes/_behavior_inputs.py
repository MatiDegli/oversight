"""AST-only finite inputs. No imports, evaluation of expressions, or model requests."""

from __future__ import annotations

import ast
import inspect
import time
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any, cast

from oversight.changes._behavior_aliases import (
    _AliasBindings,  # pyright: ignore[reportPrivateUsage]
    module_aliases,
    shadowed,
)
from oversight.changes._shortcuts_common import Source, walk_local

if TYPE_CHECKING:
    from oversight.changes._behavior_sources import Sources

MAX_INPUTS = 48
MAX_LITERAL = 4096
MAX_AST_NODES = 128
_OPAQUE_DEFAULT = object()


class _LiteralLimit(ValueError):
    pass


class BindingFailure(TypeError):
    """A fixed input rejection reason, separate from inspect's diagnostic text."""


@dataclass(frozen=True)
class BehaviorInput:
    call: str
    before: str
    after: str
    input_identity: str | None = None


@dataclass(frozen=True)
class BehaviorInputs:
    inputs: tuple[BehaviorInput, ...] = ()
    skipped_binding: int = 0
    reason: str | None = None
    receiver_class: str | None = None
    method_kind: str | None = None
    properties: tuple[str, ...] = ()


def module_name(path: str) -> str:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def literal_text(value: Any) -> str:
    if type(value) is set:
        return (
            "{" + ", ".join(sorted(literal_text(v) for v in cast(Any, value))) + "}"
            if value
            else "set()"
        )
    if type(value) in (tuple, list):
        parts = ", ".join(literal_text(v) for v in value)
        return ("[" + parts + "]") if type(value) is list else "(" + parts + ",)" if value else "()"
    if type(value) is dict:
        return (
            "{"
            + ", ".join(
                f"{literal_text(k)}: {literal_text(v)}"
                for k, v in cast(dict[Any, Any], value).items()
            )
            + "}"
        )
    return repr(value)


def _literal(node: ast.expr) -> Any:
    nodes = list(ast.walk(node))
    if len(nodes) > MAX_AST_NODES or len(ast.unparse(node)) > MAX_LITERAL:
        raise _LiteralLimit("literal size cap")
    return ast.literal_eval(node)


def _constant_defaults(source: Source) -> dict[str, ast.expr]:
    bindings = _AliasBindings(source.deadline)
    bindings.visit(source.tree)
    if bindings.star_import:
        return {}
    declared = {
        name
        for node in ast.walk(source.tree)
        if isinstance(node, (ast.Global, ast.Nonlocal))
        for name in node.names
    }
    constants: dict[str, ast.expr] = {}
    for stmt in source.tree.body:
        if not isinstance(stmt, (ast.Assign, ast.AnnAssign)) or stmt.value is None:
            continue
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        if len(targets) == 1 and isinstance(targets[0], ast.Name):
            name = targets[0].id
            if bindings.counts[name] == 1 and name not in declared:
                constants[name] = stmt.value
    return constants


def _default(node: ast.expr, constants: dict[str, ast.expr]) -> Any:
    if isinstance(node, ast.Name):
        node = constants.get(node.id, node)
    try:
        return _literal(node)
    except _LiteralLimit:
        raise
    except (ValueError, TypeError):
        # This marker never enters pools, rendered calls or effective arguments.
        return _OPAQUE_DEFAULT


def _signature(source: Source, node: ast.FunctionDef | ast.AsyncFunctionDef) -> inspect.Signature:
    if node.args.vararg or node.args.kwarg:
        raise ValueError("variadic signature")
    positional = [*node.args.posonlyargs, *node.args.args]
    constants = _constant_defaults(source)
    missing = inspect.Parameter.empty
    defaults = [missing] * (len(positional) - len(node.args.defaults))
    defaults += [_default(value, constants) for value in node.args.defaults]
    params: list[inspect.Parameter] = []
    for index, (arg, default) in enumerate(zip(positional, defaults, strict=True)):
        kind = (
            inspect.Parameter.POSITIONAL_ONLY
            if index < len(node.args.posonlyargs)
            else inspect.Parameter.POSITIONAL_OR_KEYWORD
        )
        params.append(
            inspect.Parameter(arg.arg, kind, default=default, annotation=_annotation(arg))
        )
    for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True):
        params.append(
            inspect.Parameter(
                arg.arg,
                inspect.Parameter.KEYWORD_ONLY,
                default=missing if default is None else _default(default, constants),
                annotation=_annotation(arg),
            )
        )
    return inspect.Signature(params)


def _annotation(arg: ast.arg) -> str | None:
    if isinstance(arg.annotation, ast.Name):
        return arg.annotation.id
    if isinstance(arg.annotation, ast.Constant) and isinstance(arg.annotation.value, str):
        return arg.annotation.value
    return None


def binding_arguments(
    sig: inspect.Signature, named: dict[str, Any]
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    if named.keys() - sig.parameters.keys():
        raise BindingFailure("unknown parameter")
    for name, param in sig.parameters.items():
        if param.default is _OPAQUE_DEFAULT and name not in named:
            raise BindingFailure("opaque default requires an explicit argument")
        if param.default is inspect.Parameter.empty and name not in named:
            raise BindingFailure("required argument has no literal value")
        if param.kind == inspect.Parameter.POSITIONAL_ONLY:
            if name in named:
                args.append(named[name])
            elif param.default is not inspect.Parameter.empty:
                args.append(param.default)
        elif name in named:
            kwargs[name] = named[name]
    sig.bind(*args, **kwargs)
    return tuple(args), kwargs


def _resolved(
    source: Source, call: ast.Call, path: str, module: str, name: str, aliases: dict[str, str]
) -> bool:
    resolved = source.resolve(call.func)
    if (
        isinstance(call.func, ast.Name)
        and call.func.id in aliases
        and not shadowed(source, call, call.func.id)
    ):
        resolved = aliases[call.func.id]
    current = module_name(path)
    if resolved and resolved.startswith("."):
        level = len(resolved) - len(resolved.lstrip("."))
        package = current.split(".") if path.endswith("/__init__.py") else current.split(".")[:-1]
        resolved = ".".join([*package[: len(package) - level + 1], resolved.lstrip(".")])
    if resolved == f"{module}.{name}":
        return True
    if current != module or not isinstance(call.func, ast.Name) or call.func.id != name:
        return False
    return not shadowed(source, call, name)


def calls(
    files: dict[str, str],
    module: str,
    name: str,
    deadline: float,
    *,
    objects: bool = False,
    cache: Sources | None = None,
):
    from oversight.changes._behavior_objects import arguments, literal_arguments
    from oversight.changes._behavior_sources import Sources

    cache = cache if cache is not None else Sources(files, deadline)
    for path in sorted(files):
        if time.monotonic() >= deadline:
            raise TimeoutError
        if not path.endswith(".py"):
            continue
        try:
            source = cache.get(path)
            aliases = module_aliases(source)
        except (SyntaxError, ValueError, RecursionError):
            continue
        calls = sorted(
            (n for n in ast.walk(source.tree) if isinstance(n, ast.Call)),
            key=lambda n: (n.lineno, n.col_offset),
        )
        for call in calls:
            if not _resolved(source, call, path, module, name, aliases):
                continue
            try:
                yield arguments(source, path, call, cache) if objects else literal_arguments(call)
            except (ValueError, TypeError, SyntaxError, RecursionError):
                continue


def _definition(cache: Sources, module: str, name: str):
    found: list[tuple[Source, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for path in cache.files:
        if path.endswith(".py") and module_name(path) == module:
            source = cache.get(path)
            found.extend(
                (source, n)
                for n in source.tree.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
            )
    if len(found) != 1:
        raise ValueError("ambiguous definition")
    return found[0]


def _effect_reason(source: Source, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    """Conservative own-body syntax check, including imported aliases, without imports.

    This is not interprocedural analysis: effects hidden in helpers remain unobserved.
    Nested definitions' bodies belong to those definitions, not this function.
    """
    file_calls = {
        "open",
        "read",
        "write",
        "read_text",
        "write_text",
        "read_bytes",
        "write_bytes",
        "readline",
        "readlines",
        "writelines",
        "unlink",
        "rename",
        "replace",
        "mkdir",
        "rmdir",
        "touch",
        "remove",
        "listdir",
        "scandir",
        "walk",
        "stat",
        "lstat",
        "exists",
        "is_file",
        "is_dir",
        "glob",
        "rglob",
    }
    for child in walk_local(node):
        if isinstance(child, (ast.Global, ast.Nonlocal)):
            return "global or nonlocal assignment"
        if not isinstance(child, (ast.Name, ast.Attribute, ast.Call)):
            continue
        name = source.resolve(child.func if isinstance(child, ast.Call) else child) or ""
        root, leaf = name.split(".")[0], name.rsplit(".", 1)[-1]
        if name in {
            "os.environ",
            "os.environb",
            "os.getenv",
            "os.getenvb",
            "os.putenv",
            "os.unsetenv",
        }:
            return "environment access"
        if root in {"random", "secrets"} or name == "os.urandom":
            return "randomness"
        if root in {"time", "datetime"}:
            return "time access"
        if root in {
            "socket",
            "requests",
            "urllib",
            "http",
            "httpx",
            "aiohttp",
            "ftplib",
            "smtplib",
        }:
            return "network access"
        if isinstance(child, ast.Call):
            if name in {"print", "input", "builtins.print", "builtins.input"}:
                return "print or input"
            if leaf in file_calls or root in {"pathlib", "io", "shutil", "tempfile"}:
                return "file access"
    return None


def behavior_inputs(
    module: str,
    name: str,
    base: dict[str, str],
    head: dict[str, str],
    *,
    deadline: float = float("inf"),
) -> BehaviorInputs:
    from oversight.changes._behavior_sources import Sources

    caches = [Sources(files, deadline) for files in (base, head)]
    try:
        definitions = [_definition(cache, module, name) for cache in caches]
        nodes = [node for _, node in definitions]
        if any(
            isinstance(n, ast.AsyncFunctionDef)
            or any(isinstance(c, (ast.Yield, ast.YieldFrom)) for c in walk_local(n))
            for n in nodes
        ):
            return BehaviorInputs(reason="async or generator")
        for source, node in definitions:
            if reason := _effect_reason(source, node):
                return BehaviorInputs(reason=reason)
        signatures = [_signature(source, node) for source, node in definitions]
        integers = [
            value
            for source, _ in definitions
            for value in _literals(source.tree)
            if type(value) is int
        ]
    except (ValueError, TypeError, SyntaxError, RecursionError):
        return BehaviorInputs(reason="unsupported signature")
    from oversight.changes._behavior_generation import generate

    return generate(module, name, base, head, nodes, signatures, integers, deadline, caches=caches)


def _literals(node: ast.Module | ast.FunctionDef | ast.AsyncFunctionDef) -> list[Any]:
    """Signed numeric literals belong to the unary expression, not its positive operand."""
    stack: list[ast.AST] = list(reversed(node.body))
    literals: list[ast.expr] = []
    while stack:
        child = stack.pop()
        if not isinstance(node, ast.Module) and isinstance(
            child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
        ):
            continue
        if (
            isinstance(child, ast.UnaryOp)
            and isinstance(child.op, (ast.USub, ast.UAdd))
            and isinstance(child.operand, ast.Constant)
            and type(child.operand.value) is int
        ):
            literals.append(child)
            continue
        if isinstance(child, ast.Constant):
            literals.append(child)
        stack.extend(reversed(list(ast.iter_child_nodes(child))))
    return [
        ast.literal_eval(child)
        for child in sorted(literals, key=lambda c: (c.lineno, c.col_offset))
    ]
