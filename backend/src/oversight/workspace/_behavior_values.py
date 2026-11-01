"""Standalone stdlib typed values shared by the isolated behavior worker."""

import hashlib
import json
import math
from typing import Any, cast

MAX_RESULT = 8192
MAX_DEPTH = 6
MAX_ITEMS = 32
MAX_NODES = 128
MAX_TEXT = 512
MAX_RANGE_ITEMS = 10_000


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


class RangeArgumentError(ValueError):
    """A fixed data-protocol refusal, never an exception from the project function."""


def range_value(data: Any) -> range:
    if len(data) != 4 or any(type(value) is not int for value in data[1:]):
        raise RangeArgumentError("range requires three int values")
    if data[3] == 0:
        raise RangeArgumentError("range step is zero")
    result = range(*data[1:])
    try:
        if len(result) <= MAX_RANGE_ITEMS:
            return result
    except OverflowError:
        pass
    raise RangeArgumentError("range length cap")


class IteratorObservationError(ValueError):
    """A bounded observation cut, never a project exception or equality evidence."""


class IteratorRelease:
    """Retain an interrupted iterator until its caller can release it under an alarm."""

    def __init__(self) -> None:
        self.value: Any = None

    def release(self) -> None:
        value, self.value = self.value, None
        close = None
        try:
            close = getattr(value, "close", None)
            if callable(close):
                close()
        finally:
            close = value = None


def release_exception(error: BaseException) -> None:
    """Discard traceback chains while the caller's cleanup alarm is still active."""
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        pending.extend(
            part for part in (current.__cause__, current.__context__) if part is not None
        )
        current.__traceback__ = None
        current.__cause__ = current.__context__ = None


def observation_literal(literal: str) -> tuple[str, bool]:
    import ast

    data = ast.literal_eval(literal)
    if len(data) == 2 and data[0] == "oversight.iterator_items.v1":
        if type(data[1]) is not str:
            raise IteratorObservationError("invalid iterator observation")
        return data[1], True
    return literal, False


def iterator_items(value: Any) -> tuple[Any, bool]:
    """Consume actual iterators; preserve ordinary return values and their identity."""
    try:
        iterator = iter(value)
    except TypeError:
        return value, False
    if iterator is not value:
        return value, False
    items: list[Any] = []
    for _ in range(1_000):
        try:
            items.append(next(iterator))
        except StopIteration:
            return items, True
    # Do not pull an extra item: reaching the cap cannot certify exhaustion.
    raise IteratorObservationError("iterator item cap")
