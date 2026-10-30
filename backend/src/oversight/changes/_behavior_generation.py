"""Bound finite argument generation, including data-only project constructors."""

import ast
import inspect
import time
from itertools import zip_longest
from typing import Any

from oversight.changes._behavior_inputs import (
    _OPAQUE_DEFAULT,  # pyright: ignore[reportPrivateUsage]
    MAX_INPUTS,
    MAX_LITERAL,
    BehaviorInput,
    BehaviorInputs,
    _literals,  # pyright: ignore[reportPrivateUsage]
    binding_arguments,
    calls,
    literal_text,
)
from oversight.changes._behavior_objects import display, input_identity, invocation
from oversight.changes._behavior_sources import Sources


def generate(
    module: str,
    name: str,
    base: dict[str, str],
    head: dict[str, str],
    nodes: list[ast.FunctionDef | ast.AsyncFunctionDef],
    signatures: list[inspect.Signature],
    integers: list[int],
    deadline: float,
    observed: list[list[tuple[tuple[Any, ...], dict[str, Any]]]] | None = None,
    caches: list[Sources] | None = None,
) -> BehaviorInputs:
    caches = caches if caches is not None else [Sources(files, deadline) for files in (base, head)]
    inputs: list[BehaviorInput] = []
    seen: set[str] = set()
    skipped = 0
    pools: dict[str, list[Any]] = {key: [] for sig in signatures for key in sig.parameters}
    seeds: list[dict[str, Any]] = []
    base_params = signatures[0].parameters

    def add(named: dict[str, Any]) -> None:
        nonlocal skipped
        if time.monotonic() >= deadline:
            raise TimeoutError
        try:
            before, after = (binding_arguments(sig, named) for sig in signatures)
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
        sample = _sample(name, named, signatures, before, after, caches, deadline)
        if sample is not None:
            inputs.append(sample)
            seeds.append(named)

    for side, (cache, sig) in enumerate(zip(caches, signatures, strict=True)):
        observed_calls = (
            observed[side]
            if observed is not None
            else calls(cache.files, module, name, deadline, objects=True, cache=cache)
        )
        for args, kwargs in observed_calls:
            try:
                bound = sig.bind(*args, **kwargs)
            except TypeError:
                skipped += 1
                continue
            for param, value in bound.arguments.items():
                pools[param].append(value)
            add(dict(bound.arguments))
    pools = _pools(pools, nodes, signatures, integers)
    seed = {param: values[0] for param, values in pools.items() if values and param in base_params}
    if seeds:
        seed = dict(seeds[0])
    add(seed)
    # Share the cap across parameters so the first parameter cannot starve later ones.
    for row in zip_longest(*pools.values(), fillvalue=inspect.Parameter.empty):
        for param, value in zip(pools, row, strict=True):
            if value is inspect.Parameter.empty:
                continue
            add({**seed, param: value})
    return BehaviorInputs(tuple(inputs), skipped, None if inputs else "unsupported signature")


def _sample(
    name: str,
    named: dict[str, Any],
    signatures: list[inspect.Signature],
    before: tuple[tuple[Any, ...], dict[str, Any]],
    after: tuple[tuple[Any, ...], dict[str, Any]],
    caches: list[Sources],
    deadline: float,
) -> BehaviorInput | None:
    rendered = ", ".join(f"{key}={display(value)}" for key, value in named.items())
    identities: list[str | None] = []
    for sig, (args, kwargs), cache in zip(signatures, (before, after), caches, strict=True):
        effective = sig.bind(*args, **kwargs)
        effective.apply_defaults()
        # Removed parameters and changed shared defaults must stay unequal.
        arguments = {
            key: value
            for key, value in effective.arguments.items()
            if key in signatures[0].parameters
        }
        try:
            identities.append(input_identity(arguments, cache, deadline))
        except (ValueError, TypeError, SyntaxError, RecursionError):
            return None
    return BehaviorInput(
        f"{name}({rendered})",
        invocation(before),
        invocation(after),
        identities[0] if identities[0] == identities[1] else None,
    )


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
            pools[param].extend((0, -1))
            for value in integers:
                pools[param].extend((value - 1, value, value + 1))
        if is_str:
            for node in nodes:
                pools[param].extend(value for value in _literals(node) if type(value) is str)
        pools[param].extend(defaults)
        unique = {literal_text(v): v for v in pools[param] if not (is_int and type(v) is bool)}
        pools[param] = list(unique.values())[:MAX_INPUTS]
    return pools
