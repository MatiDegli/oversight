"""Deterministic, opt-in pruning of covered read results in retained model history."""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from oversight.agents._prompts import (
    HISTORY_LOW_WATER,
    HISTORY_PROTECTED_CALLS,
    HISTORY_REARM,
    HISTORY_STUB,
)
from oversight.agents.client import Message, ToolSchema

_ROW = re.compile(r"\s*(\d+)\| (.*)")
_RANGE = re.compile(r"\[lines \d+ to \d+ of \d+; read more with start_line\]")


def _json_bytes(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def estimate_tokens(messages: Sequence[Message], tools: Sequence[ToolSchema]) -> float:
    """A prompt-size heuristic only; never provider usage or billing evidence."""
    return _json_bytes({"messages": messages, "tools": tools}) / 4


@dataclass
class _Read:
    message: Message
    call: int
    path: str
    rows: dict[int, str]
    whole: bool


def _read(message: Message, call: dict[str, object], position: int) -> _Read | None:
    function = call.get("function")
    if not isinstance(function, dict):
        return None
    function = cast(dict[str, object], function)
    if function.get("name") != "read_file":
        return None
    try:
        arguments: object = json.loads(str(function.get("arguments")))
    except (ValueError, TypeError):
        return None
    if not isinstance(arguments, dict):
        return None
    path = cast(dict[str, object], arguments).get("path")
    if not isinstance(path, str):
        return None
    content = message.get("content")
    if not isinstance(content, str):
        return None
    lines = content.splitlines()
    whole = not lines or _RANGE.fullmatch(lines[-1]) is None
    if not whole:
        lines.pop()
    rows: dict[int, str] = {}
    for line in lines:
        match = _ROW.fullmatch(line)
        if match is None:
            return None
        rows[int(match[1])] = match[2]
    if content and not rows:
        return None
    return _Read(message, position, path, rows, whole)


def _reads(messages: Sequence[Message]) -> tuple[list[_Read], int]:
    """Pair ordered results locally, even when ids repeat across assistant turns."""
    reads: list[_Read] = []
    pending: list[dict[str, object]] = []
    position = total = base = 0
    for message in messages:
        if message["role"] == "assistant":
            calls = message.get("tool_calls")
            pending = cast(list[dict[str, object]], calls) if isinstance(calls, list) else []
            base, position = total, 0
            total += len(pending)
        elif message["role"] == "tool" and position < len(pending):
            read = _read(message, pending[position], base + position)
            position += 1
            if read is not None:
                reads.append(read)
    return reads, total


@dataclass
class HistoryPruner:
    threshold: int | None
    last_batch_tokens: float | None = None

    def prune(self, messages: list[Message], tools: Sequence[ToolSchema]) -> None:
        if self.threshold is None:
            return
        estimate = estimate_tokens(messages, tools)
        if estimate < self.threshold or (
            self.last_batch_tokens is not None
            and estimate - self.last_batch_tokens < self.threshold * HISTORY_REARM
        ):
            return
        reads, total = _reads(messages)
        later: dict[str, list[_Read]] = {}
        for read in reversed(reads):
            if estimate <= self.threshold * HISTORY_LOW_WATER:
                break
            covered = any(
                newer.whole
                or all(newer.rows.get(number) == text for number, text in read.rows.items())
                for newer in later.get(read.path, [])
            )
            later.setdefault(read.path, []).append(read)
            if not covered or not read.rows or read.call >= total - HISTORY_PROTECTED_CALLS:
                continue
            original = cast(str, read.message["content"])
            stub = HISTORY_STUB.format(first=min(read.rows), last=max(read.rows), path=read.path)
            saved = _json_bytes(original) - _json_bytes(stub)
            if len(stub) < len(original) and saved > 0:
                read.message["content"] = stub
                estimate -= saved / 4
                later[read.path].pop()  # Only retained reads can cover older results.
        # Also record exhausted/no-candidate batches, to avoid rechecking every turn.
        self.last_batch_tokens = estimate
