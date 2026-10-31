"""Supplied proposed-test calls using new parameters, never generated behavior inputs."""

import ast
import inspect
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from oversight.changes._behavior_aliases import module_aliases
from oversight.changes._behavior_consumption import observation_call
from oversight.changes._behavior_inputs import (
    MAX_INPUTS,
    MAX_LITERAL,
    BehaviorInput,
    _definition,  # pyright: ignore[reportPrivateUsage]
    _effect_reason,  # pyright: ignore[reportPrivateUsage]
    _resolved,  # pyright: ignore[reportPrivateUsage]
    _signature,  # pyright: ignore[reportPrivateUsage]
    calls,
    literal_text,
)
from oversight.changes._behavior_methods import (
    _bound_signature,  # pyright: ignore[reportPrivateUsage]
    _decorator,  # pyright: ignore[reportPrivateUsage]
    _function_reason,  # pyright: ignore[reportPrivateUsage]
    _properties,  # pyright: ignore[reportPrivateUsage]
)
from oversight.changes._behavior_methods import (
    _definition as method_definition,  # pyright: ignore[reportPrivateUsage]
)
from oversight.changes._behavior_objects import (
    _assigned_receiver,  # pyright: ignore[reportPrivateUsage]
    arguments,
    class_signature,
    constructor,
    display,
    input_identity,
    invocation,
)
from oversight.changes._behavior_sources import Sources
from oversight.changes._review_identity import bound_inputs, canonical
from oversight.changes._shortcuts_common import check_deadline, is_test_file, walk_local


@dataclass(frozen=True)
class SuppliedCall:
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    receiver: str | None = None
    receiver_text: str = ""
    consumed: bool = False


@dataclass(frozen=True)
class ExtensionInputs:
    parameters: tuple[str, ...]
    samples: tuple[BehaviorInput, ...]
    receiver_class: str | None = None
    properties: tuple[str, ...] = ()
    reason: str | None = None


def _function_calls(module: str, name: str, cache: Sources) -> Iterator[SuppliedCall]:
    tests = {path: text for path, text in cache.files.items() if is_test_file(path)}
    consumed = {
        invocation(values)
        for values in calls(
            tests, module, name, cache.deadline, objects=True, cache=cache, consumed_only=True
        )
    }
    for args, kwargs in calls(tests, module, name, cache.deadline, objects=True, cache=cache):
        yield SuppliedCall(args, kwargs, consumed=invocation((args, kwargs)) in consumed)


def _method_calls(module: str, cls: str, name: str, cache: Sources) -> Iterator[SuppliedCall]:
    for path in sorted(cache.files):
        check_deadline(cache.deadline)
        if not is_test_file(path):
            continue
        try:
            source = cache.get(path)
            aliases = module_aliases(source)
            for node in ast.walk(source.tree):
                check_deadline(cache.deadline)
                if not (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == name
                ):
                    continue
                receiver = node.func.value
                if isinstance(receiver, ast.Name):
                    receiver = _assigned_receiver(source, node, receiver.id, cache)
                if not isinstance(receiver, ast.Call) or not _resolved(
                    source, receiver, path, module, cls, aliases, cache
                ):
                    continue
                try:
                    value = constructor(source, path, receiver, cache)
                    args, kwargs = arguments(source, path, node, cache)
                    yield SuppliedCall(
                        args, kwargs, literal_text((value.args, value.kwargs)), display(value) + "."
                    )
                except (ValueError, TypeError, SyntaxError, RecursionError):
                    continue
        except (ValueError, TypeError, SyntaxError, RecursionError):
            continue


def _sample_identity(
    candidate: SuppliedCall, cache: Sources, receiver: tuple[str, str] | None
) -> str | None:
    supplied_arguments = {
        **{f"positional:{index}": value for index, value in enumerate(candidate.args)},
        **{f"keyword:{key}": value for key, value in candidate.kwargs.items()},
    }
    identity = input_identity(supplied_arguments, cache, cache.deadline)
    if receiver is None or identity is None:
        return identity
    args, kwargs = ast.literal_eval(candidate.receiver or "")
    bound = class_signature(cache, *receiver).bind(*args, **kwargs)
    bound.apply_defaults()
    receiver_identity = bound_inputs(dict(bound.arguments))
    return canonical([receiver_identity, identity]) if receiver_identity is not None else None


def _samples(
    name: str,
    signatures: list[inspect.Signature],
    candidates: Iterator[SuppliedCall],
    deadline: float,
    generator: bool,
    caches: list[Sources],
    receiver: tuple[str, str] | None,
) -> tuple[tuple[str, ...], tuple[BehaviorInput, ...], str | None]:
    base_parameters = {
        key
        for key, value in signatures[0].parameters.items()
        if value.kind != inspect.Parameter.VAR_KEYWORD
    }
    added = {
        name
        for name, param in signatures[1].parameters.items()
        if name not in base_parameters and param.kind != inspect.Parameter.VAR_KEYWORD
    }
    if not added:
        return (), (), None
    samples: dict[str, BehaviorInput] = {}
    used: set[str] = set()
    for candidate in candidates:
        check_deadline(deadline)
        try:
            bound = signatures[1].bind(*candidate.args, **candidate.kwargs)
        except TypeError:
            continue
        supplied = added.intersection(bound.arguments)
        if not supplied or (generator and not candidate.consumed):
            continue
        raw = invocation((candidate.args, candidate.kwargs))
        if candidate.receiver is not None:
            raw = literal_text((candidate.receiver, raw))
        raw = observation_call(raw, candidate.consumed)
        parts = [
            *(display(value) for value in candidate.args),
            *(f"{key}={display(value)}" for key, value in candidate.kwargs.items()),
        ]
        text = f"{candidate.receiver_text}{name}({', '.join(parts)})"
        if max(len(raw), len(text)) > MAX_LITERAL:
            continue
        if raw not in samples and len(samples) == MAX_INPUTS:
            return tuple(sorted(used)), tuple(samples.values()), "budget exhausted"
        identities: list[str | None] = []
        for cache in caches:
            try:
                identities.append(_sample_identity(candidate, cache, receiver))
            except (ValueError, TypeError, SyntaxError, RecursionError):
                identities.append(None)
        samples[raw] = BehaviorInput(
            text, raw, raw, identities[0] if identities[0] == identities[1] else None
        )
        used.update(supplied)
    return tuple(sorted(used)), tuple(samples.values()), None


def extension_inputs(
    module: str,
    name: str,
    base: dict[str, str],
    head: dict[str, str],
    *,
    method: bool = False,
    deadline: float = float("inf"),
) -> ExtensionInputs | None:
    caches = [Sources(files, deadline) for files in (base, head)]
    receiver_class = None
    properties: tuple[str, ...] = ()
    try:
        if method:
            definitions = [method_definition(cache, module, name) for cache in caches]
            for cache, (source, cls, node) in zip(caches, definitions, strict=True):
                if _decorator(node) != "method" or node.name.startswith("__"):
                    return None
                if _function_reason(source, node):
                    return None
                class_signature(cache, module, cls.name)
            properties = _properties(definitions)
            signatures = [
                _bound_signature(source, node, allow_kwargs=True) for source, _, node in definitions
            ]
            receiver_class, name = definitions[1][1].name, definitions[1][2].name
            candidates = _method_calls(module, receiver_class, name, caches[1])
            generator = False
        else:
            functions = [_definition(cache, module, name) for cache in caches]
            if any(
                isinstance(node, ast.AsyncFunctionDef) or _effect_reason(source, node)
                for source, node in functions
            ):
                return None
            signatures = [_signature(source, node, allow_kwargs=True) for source, node in functions]
            generator = any(
                isinstance(child, (ast.Yield, ast.YieldFrom))
                for _, node in functions
                for child in walk_local(node)
            )
            candidates = _function_calls(module, name, caches[1])
        parameters, samples, reason = _samples(
            name,
            signatures,
            candidates,
            deadline,
            generator,
            caches,
            (module, receiver_class) if receiver_class else None,
        )
        return (
            ExtensionInputs(parameters, samples, receiver_class, properties, reason)
            if samples
            else None
        )
    except (ValueError, TypeError, SyntaxError, RecursionError):
        return None
