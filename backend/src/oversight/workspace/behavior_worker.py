"""Standalone stdlib worker, executed by the demo venv, never by importing Oversight.

The parent requires network isolation. C removes the network, not filesystem access;
throwaway checkouts contain ordinary relative file effects, not arbitrary filesystem effects.
The report is a bounded file, never stdout. Limits deliberately measure only a finite projection.
"""

import ast
import copy
import hashlib
import importlib
import inspect
import json
import math
import os
import signal
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

MAX_FILE = 1_048_576
MAX_RESULT = 8192
MAX_DEPTH = 6
MAX_ITEMS = 32
MAX_NODES = 128
MAX_TEXT = 512


def class_name(value: object) -> str:
    cls = type(value)
    return f"{cls.__module__}.{cls.__qualname__}"[:MAX_TEXT]


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def encode(
    value: Any, depth: int = 0, seen: set[int] | None = None, left: list[int] | None = None
) -> list[Any]:
    """Type tags preserve bool/int, list/tuple, nan and signed zero; cuts are explicit."""
    seen, left = (set() if seen is None else seen), ([MAX_NODES] if left is None else left)
    left[0] -= 1
    if depth > MAX_DEPTH or left[0] < 0:
        return ["cut", class_name(value)]
    kind = cast(Any, type(value))
    if value is None:
        return ["none"]
    if kind is bool:
        return ["bool", value]
    if kind is int:
        return ["int", hex(value)] if value.bit_length() <= 4096 else ["cut", "int"]
    if kind is float:
        return ["float", "nan" if math.isnan(value) else value.hex()]
    if kind in (str, bytes):
        text = value[:MAX_TEXT] if kind is str else value[:MAX_TEXT].hex()
        return [kind.__name__, text, len(value), "cut" if len(value) > MAX_TEXT else "complete"]
    if id(value) in seen:
        return ["cycle", class_name(value)]
    seen.add(id(value))

    def child(item: object) -> list[Any]:
        return encode(item, depth + 1, seen, left)

    try:
        if kind in (list, tuple, set, frozenset):
            # Sort sets before capping, so hash iteration cannot choose a different subset.
            if kind in (set, frozenset):
                if len(value) > MAX_ITEMS:
                    return ["cut", kind.__name__, len(value)]
                items = sorted((child(item) for item in value), key=canonical)
            else:
                items = [child(item) for item in value[:MAX_ITEMS]]
            return [
                kind.__name__,
                items,
                len(value),
                "cut" if len(value) > MAX_ITEMS else "complete",
            ]
        if kind is dict:
            if len(value) > MAX_ITEMS:
                return ["cut", "dict", len(value)]
            return ["dict", sorted(([child(k), child(v)] for k, v in value.items()), key=canonical)]
        fields: Any = getattr(value, "__dict__", None)
        if type(fields) is dict:
            fields = cast(dict[str, Any], fields)
            names = sorted(k for k in fields if type(k) is str)[:MAX_ITEMS]
            return [
                "object",
                class_name(value),
                [[k, child(fields[k])] for k in names],
                len(fields),
            ]
        return ["repr", class_name(value), repr(value)[:MAX_TEXT]]
    finally:
        seen.remove(id(value))


def fingerprint(value: Any, seen: set[int] | None = None) -> str:
    """Hash the full typed value under the call alarm; refuse uncertain representations.

    The display encoder's depth, item and text cuts never become equality evidence.
    Unsupported values and cycles may be displayed privately, but are not compared.
    """
    seen = set() if seen is None else seen
    kind = cast(Any, type(value))
    digest = hashlib.sha256()

    def token(part: str | bytes) -> None:
        raw = part.encode("utf-8", errors="surrogatepass") if isinstance(part, str) else part
        digest.update(str(len(raw)).encode() + b":" + raw)

    token(kind.__module__ + "." + kind.__qualname__)
    if value is None:
        pass
    elif kind is bool:
        token(str(value))
    elif kind is int:
        token(hex(value))
    elif kind is float:
        token("nan" if math.isnan(value) else value.hex())
    elif kind in (str, bytes):
        token(value)
    else:
        if id(value) in seen:
            raise ValueError("cyclic result")
        seen.add(id(value))
        try:
            if kind in (list, tuple, set, frozenset):
                items = (fingerprint(item, seen) for item in value)
                if kind in (set, frozenset):
                    items = sorted(items)
                for item in items:
                    token(item)
            elif kind is dict:
                for key, item in sorted(
                    (fingerprint(k, seen), fingerprint(v, seen)) for k, v in value.items()
                ):
                    token(key)
                    token(item)
            else:
                # __dict__ alone misses slots, descriptors and opaque native state.
                raise ValueError("unsupported full result")
        finally:
            seen.remove(id(value))
    return digest.hexdigest()


def value_result(value: Any) -> dict[str, Any]:
    display = encode(value)
    try:
        identity = fingerprint(value)
    except (ValueError, TypeError, RecursionError):
        return {"kind": "unrepresentable", "value": display}
    return {"kind": "value", "value": display, "fingerprint": identity}


class CallTimeout(BaseException):
    pass


def alarm(_signum: int, _frame: object) -> None:
    raise CallTimeout


def limited(action: Callable[[], Any], timeout_s: float) -> Any:
    signal.setitimer(signal.ITIMER_REAL, timeout_s)
    try:
        return action()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


def call(function: Callable[..., Any], literal: str, timeout_s: float) -> dict[str, Any]:
    def execute() -> dict[str, Any]:
        args, kwargs = ast.literal_eval(literal)
        bound = inspect.signature(function).bind(*args, **kwargs)
        bound.apply_defaults()
        arguments = copy.deepcopy((bound.args, bound.kwargs))
        try:
            value = function(*arguments[0], **arguments[1])
        except CallTimeout:
            raise
        except BaseException as error:
            message = str(error)
            full_class = f"{type(error).__module__}.{type(error).__qualname__}"
            return {
                "kind": "raised",
                "class": class_name(error),
                "message": message[:MAX_TEXT],
                "fingerprint": fingerprint(("raised", full_class, message)),
            }
        return value_result(value)

    try:
        result = limited(execute, timeout_s)
    except CallTimeout:
        return {"kind": "timed_out"}
    except BaseException as error:
        result = {"kind": "unrepresentable", "class": class_name(error)}
    if len(canonical(result).encode()) > MAX_RESULT:
        return {**result, "value": ["cut", "encoded result size"]}
    return result


def probe(target: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    try:
        function = limited(
            lambda: getattr(importlib.import_module(target["module"]), target["name"]), timeout_s
        )
    except BaseException:
        return {"problem": "import failed", "results": []}
    results = [call(function, literal, timeout_s) for literal in target["calls"][:48]]
    return {"problem": None, "results": results}


def main() -> None:
    root, request, report = map(Path, sys.argv[1:4])
    timeout_s = min(1.0, max(0.001, float(sys.argv[4])))
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(root))
    signal.signal(signal.SIGALRM, alarm)
    # Includes imports, Python writes and native writes, without ever collecting their output.
    with open(os.devnull, "w") as sink:
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
    with request.open("rb") as handle:
        raw = handle.read(MAX_FILE + 1)
    if len(raw) > MAX_FILE:
        raise ValueError("request too large")
    targets: list[dict[str, Any]] = json.loads(raw)
    # Emit incrementally: the total cap applies even to a project with many changed functions.
    with report.open("w", encoding="ascii") as handle:
        handle.write("{")
        size = 2
        for index, target in enumerate(targets):
            row = canonical(target["id"]) + ":" + canonical(probe(target, timeout_s))
            if size + len(row) + 1 > MAX_FILE:
                break
            handle.write(("," if index else "") + row)
            handle.flush()
            size += len(row) + 1
        handle.write("}")


if __name__ == "__main__":
    main()
