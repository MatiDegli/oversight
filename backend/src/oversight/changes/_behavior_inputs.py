"""AST-only finite inputs. No imports, evaluation of expressions, or model requests."""

import ast
import inspect
import time
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import PurePosixPath
from typing import Any, cast

from oversight.changes._behavior_aliases import module_aliases, shadowed
from oversight.changes._review_identity import bound_inputs
from oversight.changes._shortcuts_common import Source, walk_local

MAX_INPUTS = 48
MAX_LITERAL = 4096
MAX_AST_NODES = 128
_OPAQUE_DEFAULT = object()


class _LiteralLimit(ValueError):
    pass


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


def _default(node: ast.expr) -> Any:
    try:
        return _literal(node)
    except _LiteralLimit:
        raise
    except (ValueError, TypeError):
        # This marker never enters pools, rendered calls or effective arguments.
        return _OPAQUE_DEFAULT


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> inspect.Signature:
    if node.args.vararg or node.args.kwarg:
        raise ValueError("variadic signature")
    positional = [*node.args.posonlyargs, *node.args.args]
    missing = inspect.Parameter.empty
    defaults = [missing] * (len(positional) - len(node.args.defaults))
    defaults += [_default(value) for value in node.args.defaults]
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
                default=missing if default is None else _default(default),
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


def _arguments(
    sig: inspect.Signature, named: dict[str, Any]
) -> tuple[tuple[Any, ...], dict[str, Any]]:
    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    if named.keys() - sig.parameters.keys():
        raise TypeError("unknown parameter")
    for name, param in sig.parameters.items():
        if param.default is _OPAQUE_DEFAULT and name not in named:
            raise TypeError("opaque default requires an explicit argument")
        if param.kind == inspect.Parameter.POSITIONAL_ONLY:
            if name in named:
                args.append(named[name])
            elif param.default is not inspect.Parameter.empty:
                args.append(param.default)
            else:
                raise TypeError("missing positional-only parameter")
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


def _calls(files: dict[str, str], module: str, name: str, deadline: float):
    for path, text in sorted(files.items()):
        if time.monotonic() >= deadline:
            raise TimeoutError
        if not path.endswith(".py"):
            continue
        try:
            source = Source(text, deadline)
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
                if any(kw.arg is None for kw in call.keywords):
                    continue
                args = tuple(_literal(arg) for arg in call.args)
                kwargs = {kw.arg: _literal(kw.value) for kw in call.keywords if kw.arg is not None}
                if len(kwargs) != len(call.keywords):
                    continue
                yield args, kwargs
            except (ValueError, TypeError, SyntaxError, RecursionError):
                continue


def _definition(files: dict[str, str], module: str, name: str):
    found: list[tuple[Source, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    for path, text in files.items():
        if path.endswith(".py") and module_name(path) == module:
            source = Source(text)
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
    try:
        definitions = [_definition(files, module, name) for files in (base, head)]
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
        signatures = [_signature(node) for node in nodes]
        integers = [
            value
            for source, _ in definitions
            for value in _literals(source.tree)
            if type(value) is int
        ]
    except (ValueError, TypeError, SyntaxError, RecursionError):
        return BehaviorInputs(reason="unsupported signature")
    return _generate(module, name, base, head, nodes, signatures, integers, deadline)


def _generate(
    module: str,
    name: str,
    base: dict[str, str],
    head: dict[str, str],
    nodes: list[ast.FunctionDef | ast.AsyncFunctionDef],
    signatures: list[inspect.Signature],
    integers: list[int],
    deadline: float,
) -> BehaviorInputs:
    inputs: list[BehaviorInput] = []
    seen: set[str] = set()
    skipped = 0
    pools: dict[str, list[Any]] = {key: [] for sig in signatures for key in sig.parameters}
    seeds: list[dict[str, Any]] = []

    def add(named: dict[str, Any]) -> None:
        nonlocal skipped
        if time.monotonic() >= deadline:
            raise TimeoutError
        try:
            before, after = (_arguments(sig, named) for sig in signatures)
        except TypeError:
            skipped += 1
            return
        key = literal_text((before, after))
        if key in seen or len(key) > MAX_LITERAL or len(inputs) >= MAX_INPUTS:
            return
        if any(
            type(value) is bool
            and any(
                sig.parameters.get(param) and sig.parameters[param].annotation == "int"
                for sig in signatures
            )
            for param, value in named.items()
        ):
            return
        seen.add(key)
        rendered = ", ".join(f"{key}={literal_text(value)}" for key, value in named.items())
        identities: list[str | None] = []
        for sig, (args, kwargs) in zip(signatures, (before, after), strict=True):
            effective = sig.bind(*args, **kwargs)
            effective.apply_defaults()
            identities.append(bound_inputs(dict(effective.arguments)))
        inputs.append(
            BehaviorInput(
                f"{name}({rendered})",
                literal_text(before),
                literal_text(after),
                identities[0] if identities[0] == identities[1] else None,
            )
        )
        seeds.append(named)

    for files, sig in zip((base, head), signatures, strict=True):
        for args, kwargs in _calls(files, module, name, deadline):
            try:
                bound = sig.bind(*args, **kwargs)
            except TypeError:
                skipped += 1
                continue
            for param, value in bound.arguments.items():
                pools[param].append(value)
            add(dict(bound.arguments))
    pools = _pools(pools, nodes, signatures, integers)
    seed = (
        dict(seeds[0]) if seeds else {param: values[0] for param, values in pools.items() if values}
    )
    add(seed)
    # Share the cap across parameters so the first parameter cannot starve later ones.
    for row in zip_longest(*pools.values(), fillvalue=inspect.Parameter.empty):
        for param, value in zip(pools, row, strict=True):
            if value is inspect.Parameter.empty:
                continue
            add({**seed, param: value})
    return BehaviorInputs(tuple(inputs), skipped, None if inputs else "unsupported signature")


def _pools(
    pools: dict[str, list[Any]],
    nodes: list[ast.FunctionDef | ast.AsyncFunctionDef],
    signatures: list[inspect.Signature],
    integers: list[int],
) -> dict[str, list[Any]]:
    for param in pools:
        parameters = [sig.parameters[param] for sig in signatures if param in sig.parameters]
        defaults = [
            p.default
            for p in parameters
            if p.default is not inspect.Parameter.empty and p.default is not _OPAQUE_DEFAULT
        ]
        observed = pools[param] + defaults
        kinds = {p.annotation for p in parameters}
        is_int = "int" in kinds or (not kinds - {None} and any(type(v) is int for v in observed))
        is_str = "str" in kinds or (not kinds - {None} and any(type(v) is str for v in observed))
        if is_int:
            for value in integers:
                pools[param].extend((value - 1, value, value + 1))
        if is_str:
            for node in nodes:
                pools[param].extend(value for value in _literals(node) if type(value) is str)
        pools[param].extend(([0, -1] if is_int else []) + defaults)
        unique = {literal_text(v): v for v in pools[param] if not (is_int and type(v) is bool)}
        pools[param] = list(unique.values())[:MAX_INPUTS]
    return pools


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
