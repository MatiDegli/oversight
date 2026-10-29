"""Bounded caller observer loaded only by external pytest; no Oversight imports.

Snapshots describe entry state, never executable reconstruction instructions. Only exact
builtin containers/scalars and dataclasses with a plain, fully declared __dict__ are admitted.
Generators, coroutines, other threads and existing tracers make recording incomplete.
"""

import dis
import inspect
import json
import math
import os
import sys
import threading
from pathlib import Path
from types import FrameType, GetSetDescriptorType
from typing import Any, cast

MAX_CALLER = 48
MAX_CALLS = 4096
MAX_REPORT = 1_048_576
MAX_SNAPSHOT = 4096
MAX_DEPTH = 5
MAX_ITEMS = 32
MAX_VALUES = 128
MAX_TEXT = 512


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


class Unsupported(ValueError):
    pass


def encode(value: Any, depth: int, seen: set[int], left: list[int]) -> list[Any]:
    left[0] -= 1
    if depth > MAX_DEPTH or left[0] < 0:
        raise Unsupported("snapshot depth or size cap")
    kind = cast(Any, type(value))
    if value is None:
        return ["none"]
    if kind is bool:
        return ["bool", value]
    if kind is int and value.bit_length() <= 4096:
        return ["int", hex(value)]
    if kind is float:
        if math.isnan(value):
            raise Unsupported("NaN payload is unsupported")
        return ["float", value.hex()]
    if kind in (str, bytes) and len(value) <= MAX_TEXT:
        return [kind.__name__, value if kind is str else value.hex()]
    if id(value) in seen:
        raise Unsupported("aliased or cyclic value")
    seen.add(id(value))

    def child(item: object) -> list[Any]:
        return encode(item, depth + 1, seen, left)

    if kind in (tuple, list) and len(value) <= MAX_ITEMS:
        return [kind.__name__, [child(item) for item in value]]
    if kind is dict and len(value) <= MAX_ITEMS:
        # Preserve insertion order: a caller can observe it.
        return ["dict", [[child(k), child(v)] for k, v in value.items()]]
    fields = type.__getattribute__(kind, "__dict__").get("__dataclass_fields__")
    if type(fields) is dict:
        fields = cast(dict[str, Any], fields)
        if len(fields) > MAX_ITEMS:
            raise Unsupported("snapshot size cap")
        descriptor = inspect.getattr_static(value, "__dict__", None)
        if type(descriptor) is not GetSetDescriptorType:
            raise Unsupported("unsupported dataclass state")
        if any(
            type.__getattribute__(cls, "__dict__").get("__slots__")
            for cls in type.__getattribute__(kind, "__mro__")
        ):
            raise Unsupported("unsupported dataclass slots")
        state = cast(dict[str, Any], descriptor.__get__(value, kind))
        if type(state) is dict and set(state) == set(fields):
            name = (
                type.__getattribute__(kind, "__module__")
                + "."
                + type.__getattribute__(kind, "__qualname__")
            )
            return ["dataclass", name, [[k, child(state[k])] for k in sorted(state)]]
    raise Unsupported("unsupported value type")


def snapshot(value: object) -> list[Any]:
    result = encode(value, 0, set(), [MAX_VALUES])
    if len(canonical(result).encode()) > MAX_SNAPSHOT:
        raise Unsupported("snapshot byte cap")
    return result


class Recorder:
    def __init__(self, request: str, report: str) -> None:
        self.report = report
        self.targets: dict[tuple[str, str], list[dict[str, Any]]] = {}
        with open(request, encoding="utf-8") as handle:
            targets = json.load(handle)
        root = Path.cwd()
        for target in targets:
            key = (str(root / target["path"]), target["qualname"])
            self.targets.setdefault(key, []).append(target)
        self.test_id: str | None = None
        self.counts: dict[str, int] = {}
        self.ordinals: dict[tuple[str, str], int] = {}
        self.active: dict[int, dict[str, Any]] = {}
        self.exceptions: dict[int, object] = {}
        self.problems: list[str] = []
        self.total = self.written = 0
        self.previous = sys.gettrace()
        self.thread_trace = threading.gettrace()
        self.append({"kind": "start", "version": 1})

    def problem(self, reason: str) -> None:
        if reason not in self.problems and len(self.problems) < 16:
            self.problems.append(reason)

    def append(self, record: dict[str, Any], *, terminal: bool = False) -> None:
        raw = canonical(record) + "\n"
        size = len(raw.encode())
        if self.written + size > MAX_REPORT - (0 if terminal else 8192):
            self.problem("recording report byte cap")
            return
        with open(self.report, "a", encoding="utf-8") as handle:
            handle.write(raw)
        self.written += size

    def start(self) -> None:
        if self.previous is not None or self.thread_trace is not None:
            self.problem("existing tracer is unsupported")
            return
        sys.settrace(self.trace)
        threading.settrace(self.other_thread)

    def other_thread(self, frame: FrameType, event: str, arg: Any) -> None:
        self.problem("threaded calls are unsupported")
        return None

    def enter(self, frame: FrameType) -> bool:
        if self.test_id is None:
            return False
        code = frame.f_code
        targets = self.targets.get((code.co_filename, code.co_qualname), ())
        matches = [t for t in targets if t["start_line"] <= code.co_firstlineno <= t["end_line"]]
        if len(matches) != 1:
            return False
        node_id = matches[0]["node_id"]
        if code.co_flags & (
            inspect.CO_GENERATOR | inspect.CO_COROUTINE | inspect.CO_ASYNC_GENERATOR
        ):
            self.problem("generator or coroutine caller is unsupported")
            return False
        if self.counts.get(node_id, 0) >= MAX_CALLER:
            self.problem("caller invocation cap")
            return False
        if self.total >= MAX_CALLS:
            self.problem("global invocation cap")
            return False
        self.total += 1
        self.counts[node_id] = self.counts.get(node_id, 0) + 1
        key = (self.test_id, node_id)
        self.ordinals[key] = self.ordinals.get(key, 0) + 1
        row: dict[str, Any] = dict(
            kind="call",
            test_id=self.test_id,
            node_id=node_id,
            ordinal=self.ordinals[key],
            inputs=None,
            result=None,
            problem=None,
        )
        count = code.co_argcount + code.co_kwonlyargcount
        count += bool(code.co_flags & inspect.CO_VARARGS) + bool(
            code.co_flags & inspect.CO_VARKEYWORDS
        )
        try:
            row["inputs"] = snapshot(
                {name: frame.f_locals[name] for name in code.co_varnames[:count]}
            )
        except (Unsupported, AttributeError) as error:
            row["problem"] = (
                str(error) if isinstance(error, Unsupported) else "unsupported value type"
            )
        self.active[id(frame)] = row
        frame.f_trace_lines = False
        return True

    def trace(self, frame: FrameType, event: str, arg: Any) -> Any:
        try:
            if event == "call":
                return self.trace if self.enter(frame) else None
            identity = id(frame)
            if event == "exception":
                error = arg[1]
                try:
                    self.exceptions[identity] = [
                        "raised",
                        type(error).__module__ + "." + type(error).__qualname__,
                        snapshot(cast(Any, BaseException.args).__get__(error)),
                    ]
                except (Unsupported, AttributeError):
                    self.exceptions[identity] = None
            elif event == "return" and identity in self.active:
                row = self.active.pop(identity)
                error = self.exceptions.pop(identity, None)
                try:
                    returning = dis.opname[frame.f_code.co_code[frame.f_lasti]] in (
                        "RETURN_VALUE",
                        "RETURN_CONST",
                    )
                    row["result"] = ["value", snapshot(arg)] if returning else error
                    if row["result"] is None:
                        row["problem"] = row["problem"] or "unsupported exception"
                except (Unsupported, AttributeError) as error:
                    row["problem"] = row["problem"] or (
                        str(error) if isinstance(error, Unsupported) else "unsupported value type"
                    )
                self.append(row)
            return self.trace
        except Exception:
            self.problem("caller observer failed")
            return None

    def finish(self) -> None:
        if self.previous is None and self.thread_trace is None:
            if sys.gettrace() != self.trace:
                self.problem("caller observer was replaced")
            sys.settrace(self.previous)
            threading.settrace(self.thread_trace)
        if self.active:
            self.problem("unfinished caller invocations")
        self.append({"kind": "end", "problems": self.problems}, terminal=True)


def configure() -> Recorder | None:
    request, report = (
        os.environ.get("OVERSIGHT_CALL_TARGETS"),
        os.environ.get("OVERSIGHT_CALL_REPORT"),
    )
    if not request or not report:
        return None
    recorder = Recorder(request, report)
    recorder.start()
    return recorder
