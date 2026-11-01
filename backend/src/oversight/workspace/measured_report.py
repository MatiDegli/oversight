"""Private, bounded invocation evidence. No raw invocation payload is a public event."""

import json
import os
import stat
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_CALLER = 48
MAX_CALLS = 4096
MAX_REPORT = 1_048_576
MAX_SNAPSHOT = 4096


@dataclass(frozen=True)
class CallTarget:
    node_id: str
    path: str
    qualname: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class Invocation:
    test_id: str
    node_id: str
    ordinal: int
    inputs: str | None
    result: str | None
    problem: str | None = None

    @property
    def key(self) -> tuple[str, str, int]:
        return self.test_id, self.node_id, self.ordinal


@dataclass(frozen=True)
class CallReport:
    calls: tuple[Invocation, ...] = ()
    complete: bool = False
    problems: tuple[str, ...] = ("caller report unavailable",)


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate recording key")
        result[key] = value
    return result


def _is(value: Any, kind: type[Any]) -> bool:
    return type(value) is kind


def _text(value: Any) -> bool:
    return _is(value, str) and 0 < len(value) <= 512


def _typed(value: Any, depth: int = 0) -> bool:
    if depth > 7 or not _is(value, list) or not value:
        return False
    tag, *rest = value
    if tag == "none":
        return not rest
    if tag == "bool":
        return len(rest) == 1 and _is(rest[0], bool)
    if tag in ("int", "float", "str", "bytes"):
        if len(rest) != 1 or not _is(rest[0], str) or len(rest[0]) > 1030:
            return False
        try:
            if tag == "int":
                return hex(int(rest[0], 16)) == rest[0]
            if tag == "float":
                return float.fromhex(rest[0]).hex() == rest[0]
            if tag == "bytes":
                return bytes.fromhex(rest[0]).hex() == rest[0]
            return len(rest[0]) <= 512
        except ValueError:
            return False
    if tag in ("list", "tuple"):
        return (
            len(rest) == 1
            and _is(rest[0], list)
            and len(rest[0]) <= 32
            and all(_typed(item, depth + 1) for item in rest[0])
        )
    if tag == "dataclass":
        if len(rest) != 2 or not _text(rest[0]):
            return False
        pairs = rest[1]
        return (
            _is(pairs, list)
            and len(pairs) <= 32
            and all(
                _is(pair, list) and len(pair) == 2 and _text(pair[0]) and _typed(pair[1], depth + 1)
                for pair in pairs
            )
            and len({pair[0] for pair in pairs}) == len(pairs)
        )
    if tag == "dict":
        if len(rest) != 1 or not _is(rest[0], list) or len(rest[0]) > 32:
            return False
        return all(
            _is(pair, list) and len(pair) == 2 and all(_typed(v, depth + 1) for v in pair)
            for pair in rest[0]
        ) and len({canonical(pair[0]) for pair in rest[0]}) == len(rest[0])
    return False


def _snapshot(value: Any, *, result: bool = False) -> str | None:
    if value is None:
        return None
    valid = _typed(value)
    if result and _is(value, list):
        valid = (len(value) == 2 and value[0] == "value" and _typed(value[1])) or (
            len(value) == 3 and value[0] == "raised" and _text(value[1]) and _typed(value[2])
        )
    encoded = canonical(value)
    if not valid or len(encoded.encode()) > MAX_SNAPSHOT + (1024 if result else 0):
        raise ValueError("invalid typed snapshot")
    return encoded


def _call(row: Any, targets: set[str]) -> Invocation:
    if not _is(row, dict) or set(row) != {
        "kind",
        "test_id",
        "node_id",
        "ordinal",
        "inputs",
        "result",
        "problem",
    }:
        raise ValueError("invalid invocation")
    if (
        row["node_id"] not in targets
        or not _text(row["test_id"])
        or not _is(row["ordinal"], int)
        or not 1 <= row["ordinal"] <= MAX_CALLER
        or (row["problem"] is not None and not _text(row["problem"]))
    ):
        raise ValueError("invalid invocation identity")
    inputs, result = _snapshot(row["inputs"]), _snapshot(row["result"], result=True)
    if (inputs is None or result is None) and row["problem"] is None:
        raise ValueError("missing snapshot reason")
    return Invocation(
        row["test_id"], row["node_id"], row["ordinal"], inputs, result, row["problem"]
    )


def read_calls(path: Path, targets: Sequence[CallTarget]) -> CallReport:
    """Refuse links/devices, duplicate identities, malformed snapshots and oversized files.

    Valid flushed rows survive a partial file, but its observations cannot establish unchanged.
    """
    calls: list[Invocation] = []
    problems: list[str] = []
    ended = False
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise ValueError("caller report is not a plain file")
            raw = handle.read(MAX_REPORT + 1)
        if len(raw) > MAX_REPORT:
            raise ValueError("caller report byte cap")
        lines = raw.decode("utf-8").splitlines()
        if not lines or json.loads(lines[0], object_pairs_hook=_object) != {
            "kind": "start",
            "version": 1,
        }:
            raise ValueError("caller report start missing")
        seen: set[tuple[str, str, int]] = set()
        counts: Counter[str] = Counter()
        ordinals: dict[tuple[str, str], set[int]] = defaultdict(set)
        for line in lines[1:]:
            row: Any = json.loads(line, object_pairs_hook=_object)
            if ended or not _is(row, dict):
                raise ValueError("invalid caller report ordering")
            if row.get("kind") == "end":
                if (
                    set(row) != {"kind", "problems"}
                    or not _is(row["problems"], list)
                    or len(row["problems"]) > 16
                    or not all(_text(p) for p in row["problems"])
                ):
                    raise ValueError("invalid recording diagnostics")
                problems.extend(row["problems"])
                ended = True
                continue
            call = _call(row, {target.node_id for target in targets})
            if call.key in seen or counts[call.node_id] >= MAX_CALLER or len(calls) >= MAX_CALLS:
                raise ValueError("duplicate invocation or recording cap")
            seen.add(call.key)
            counts[call.node_id] += 1
            ordinals[(call.test_id, call.node_id)].add(call.ordinal)
            calls.append(call)
        if any(values != set(range(1, len(values) + 1)) for values in ordinals.values()):
            raise ValueError("missing invocation ordinal")
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        problems.append(
            "caller report unavailable"
            if isinstance(error, OSError)
            else "caller report unreadable"
        )
    if not ended:
        problems.append("caller report incomplete")
    return CallReport(tuple(calls), not problems, tuple(dict.fromkeys(problems))[:16])
