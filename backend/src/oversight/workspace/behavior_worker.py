"""Standalone stdlib worker, executed by the demo venv, never by importing Oversight.

The parent requires network isolation. C removes the network, not filesystem access;
throwaway checkouts contain ordinary relative file effects, not arbitrary filesystem effects.
The report is a bounded file, never stdout. Limits deliberately measure only a finite projection.
"""

import ast
import copy
import importlib
import inspect
import json
import os
import signal
import sys
from collections.abc import Callable
from dataclasses import is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

# The script also runs directly under the demo interpreter, without Oversight installed.
if TYPE_CHECKING or __package__:
    from ._behavior_values import (
        MAX_RESULT,
        MAX_TEXT,
        RangeArgumentError,
        canonical,
        class_name,
        encode,
        fingerprint,
        range_value,
        value_result,
    )
else:
    from _behavior_values import (
        MAX_RESULT,
        MAX_TEXT,
        RangeArgumentError,
        canonical,
        class_name,
        encode,
        fingerprint,
        range_value,
        value_result,
    )

MAX_FILE = 1_048_576


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


def call(
    function: Callable[..., Any], literal: str, timeout_s: float, root: Path | None = None
) -> dict[str, Any]:
    def execute() -> dict[str, Any]:
        args, kwargs, memo = _arguments(literal, root or Path.cwd())
        bound = inspect.signature(function).bind(*args, **kwargs)
        bound.apply_defaults()
        arguments = copy.deepcopy((bound.args, bound.kwargs), memo)
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
    except RangeArgumentError as error:
        return {"kind": "unrepresentable", "reason": str(error)}
    except ArgumentClassMismatch:
        return {"kind": "unrepresentable", "reason": "argument class mismatch"}
    except BaseException as error:
        result = {"kind": "unrepresentable", "class": class_name(error)}
    if len(canonical(result).encode()) > MAX_RESULT:
        return {**result, "value": ["cut", "encoded result size"]}
    return result


def _literal_state(value: Any, seen: set[int] | None = None) -> None:
    """Public state is a literal projection, never an object's repr or arbitrary fields."""
    seen = set() if seen is None else seen
    kind = cast(Any, type(value))
    if value is None or kind in (bool, int, float, str, bytes):
        return
    if kind not in (list, tuple, dict) or id(value) in seen:
        raise ValueError("nonliteral receiver state")
    seen.add(id(value))
    try:
        items = (item for pair in value.items() for item in pair) if kind is dict else value
        for item in items:
            _literal_state(item, seen)
    finally:
        seen.remove(id(value))


def _stored(receiver: object) -> dict[str, Any]:
    try:
        values = object.__getattribute__(receiver, "__dict__")
    except AttributeError:
        return {}
    values = cast(dict[Any, Any], values)
    if type(values) is not dict or any(type(key) is not str for key in values):
        raise ValueError("unsupported receiver dictionary")
    return cast(dict[str, Any], values)


def _state(receiver: object, properties: list[str]) -> tuple[Any, Any]:
    computed = [(name, getattr(receiver, name)) for name in sorted(properties)]
    stored = [
        (name, value)
        for name, value in sorted(_stored(receiver).items())
        if not name.startswith("_")
    ]
    state = (stored, computed)
    _literal_state(state)
    return state


def _state_display(state: tuple[Any, Any]) -> dict[str, Any]:
    return {
        tag: [[name, encode(value)] for name, value in entries]
        for tag, entries in zip(("stored", "properties"), state, strict=True)
    }


def _project_class(module_name: str, name: str, root: Path) -> type[Any]:
    module = importlib.import_module(module_name)
    cls = getattr(module, name)
    if type(cls) is not type:
        raise ValueError("receiver metaclass")
    if not inspect.isclass(cls) or cls.__module__ != module_name:
        raise ValueError("receiver is not a project class")
    if cls.__qualname__ != name or cls.__bases__ != (object,):
        raise ValueError("receiver is not a simple class")
    if module.__file__ is None:
        raise ValueError("receiver module has no file")
    path = Path(module.__file__).resolve()
    relative = path.relative_to(root.resolve())
    if (
        "tests" in relative.parts
        or relative.name == "conftest.py"
        or relative.name.startswith("test_")
        or relative.name.endswith("_test.py")
    ):
        raise ValueError("test module receiver")
    hooks = {
        "__getattr__",
        "__getattribute__",
        "__setattr__",
        "__delattr__",
        "__slots__",
        "__del__",
        "__new__",
    }
    if is_dataclass(cls) and cls.__dict__["__dataclass_params__"].frozen:
        # Frozen dataclasses generate these hooks and refuse user-defined versions.
        hooks -= {"__setattr__", "__delattr__"}
    if hooks.intersection(cls.__dict__):
        raise ValueError("custom receiver hooks")
    return cls


def _receiver_class(target: dict[str, Any], root: Path) -> type[Any]:
    cls = _project_class(target["module"], target["receiver_class"], root)
    descriptor = inspect.getattr_static(cls, target["name"])
    kind = target["method_kind"]
    if kind in ("property", "setter"):
        if not isinstance(descriptor, property) or (kind == "setter" and descriptor.fset is None):
            raise ValueError("method descriptor mismatch")
    elif kind != "method" or not inspect.isfunction(descriptor):
        raise ValueError("method descriptor mismatch")
    for name in target.get("properties", []):
        if name.startswith("_") or not isinstance(inspect.getattr_static(cls, name), property):
            raise ValueError("property allowlist mismatch")
    return cls


class ArgumentClassMismatch(ValueError):
    pass


def _arguments(literal: str, root: Path) -> tuple[Any, Any, dict[int, Any]]:
    data = ast.literal_eval(literal)
    if len(data) == 2:
        return (*data, {})
    if len(data) != 3 or data[0] != "oversight.object_arguments.v1":
        raise ValueError("argument protocol mismatch")

    memo: dict[int, Any] = {}

    def build(value: Any) -> Any:
        if value[0] == "range":
            return range_value(value)
        if len(value) == 2 and value[0] == "literal":
            return value[1]
        if len(value) != 5 or value[0] != "constructor":
            raise ValueError("argument protocol mismatch")
        _, module, name, args, kwargs = value
        _literal_state((args, kwargs))
        try:
            cls = _project_class(module, name, root)
        except (ValueError, TypeError, AttributeError, ImportError) as error:
            raise ArgumentClassMismatch("argument class mismatch") from error
        binding = inspect.signature(cls).bind(*args, **kwargs)
        binding.apply_defaults()
        args, kwargs = copy.deepcopy((binding.args, binding.kwargs), memo)
        instance = cls(*args, **kwargs)
        # Copy defaults and literals, never clone constructed objects.
        memo[id(instance)] = instance
        return instance

    return tuple(build(v) for v in data[1]), {k: build(v) for k, v in data[2].items()}, memo


def _exception(error: BaseException) -> dict[str, Any]:
    message = str(error)
    full_class = f"{type(error).__module__}.{type(error).__qualname__}"
    return {
        "kind": "raised",
        "class": class_name(error),
        "message": message[:MAX_TEXT],
        "fingerprint": fingerprint(("raised", full_class, message)),
    }


def _method_action(
    receiver: object, cls: type[Any], target: dict[str, Any], invocation: str, root: Path
) -> Callable[[], Any]:
    args, kwargs, memo = _arguments(invocation, root)
    name, kind = target["name"], target["method_kind"]
    if kind in ("property", "setter"):
        descriptor = inspect.getattr_static(cls, name)
        accessor = descriptor.fget if kind == "property" else descriptor.fset
        signature = inspect.signature(accessor)
        parameters = list(signature.parameters.values())
        if len(parameters) != (1 if kind == "property" else 2) or any(
            parameter.kind
            not in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            for parameter in parameters
        ):
            raise ValueError("property arguments")
        binding = signature.bind(receiver, *args, **kwargs)
        binding.apply_defaults()
        if kind == "property":
            return lambda: getattr(receiver, name)
        value = list(binding.arguments.values())[1]
        # Descriptor assignment preserves frozen dataclass semantics.
        return lambda: setattr(receiver, name, value)
    method = getattr(receiver, name)
    binding = inspect.signature(method).bind(*args, **kwargs)
    binding.apply_defaults()
    args, kwargs = copy.deepcopy((binding.args, binding.kwargs), memo)
    return lambda: method(*args, **kwargs)


def method_call(
    target: dict[str, Any], literal: str, timeout_s: float, root: Path
) -> dict[str, Any]:
    def execute() -> dict[str, Any]:
        # Construction, both fingerprints, invocation and getters share ONE call alarm.
        cls = _receiver_class(target, root)
        constructor, invocation = ast.literal_eval(literal)
        args, kwargs = ast.literal_eval(constructor)
        bound = inspect.signature(cls).bind(*args, **kwargs)
        bound.apply_defaults()
        args, kwargs = copy.deepcopy((bound.args, bound.kwargs))
        receiver = cls(*args, **kwargs)  # Never clone or reuse an instance across inputs.
        # Private initialization is part of the input, catching hidden nonce helpers.
        # Only public POST state below can become a method behavior difference.
        stored = _stored(receiver)
        _literal_state(stored)
        initial = fingerprint((stored, _state(receiver, target.get("properties", []))))
        action = _method_action(receiver, cls, target, invocation, root)
        try:
            value = action()
        except CallTimeout:
            raise
        except BaseException as error:
            result = _exception(error)
        else:
            result = value_result(value)
        state = _state(receiver, target.get("properties", []))
        if result["kind"] not in ("value", "raised"):
            return result
        result["result_fingerprint"] = result["fingerprint"]
        result["fingerprint"] = fingerprint((result["kind"], result["fingerprint"], state))
        result["pre_fingerprint"] = initial
        result["state"] = _state_display(state)
        return result

    try:
        result = limited(execute, timeout_s)
    except CallTimeout:
        return {"kind": "timed_out"}
    except RangeArgumentError as error:
        return {"kind": "unrepresentable", "reason": str(error)}
    except ArgumentClassMismatch:
        return {"kind": "unrepresentable", "reason": "argument class mismatch"}
    except BaseException as error:
        return {"kind": "unrepresentable", "class": class_name(error)}
    if len(canonical(result).encode()) > MAX_RESULT:
        return {"kind": "unrepresentable", "value": ["cut", "encoded result size"]}
    return result


def probe(target: dict[str, Any], timeout_s: float, root: Path | None = None) -> dict[str, Any]:
    if target.get("receiver_class") is not None:
        return {
            "problem": None,
            "results": [
                method_call(target, literal, timeout_s, root or Path.cwd())
                for literal in target["calls"][:48]
            ],
        }
    try:
        function = limited(
            lambda: getattr(importlib.import_module(target["module"]), target["name"]), timeout_s
        )
    except BaseException:
        return {"problem": "import failed", "results": []}
    results = [call(function, literal, timeout_s, root) for literal in target["calls"][:48]]
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
            row = canonical(target["id"]) + ":" + canonical(probe(target, timeout_s, root))
            if size + len(row) + 1 > MAX_FILE:
                break
            handle.write(("," if index else "") + row)
            handle.flush()
            size += len(row) + 1
        handle.write("}")


if __name__ == "__main__":
    main()
