"""The scripted fake model: fixed tool calls read from `fixtures/fake-model/`, one file per task.

Every test and `make check` run agents through it, so nothing spends credits. A script is JSON:

    {"task": "...", "turns": [{"text": null, "tool_calls": [{"name": "read_file",
     "arguments": {"path": "shop/pricing.py"}}], "usage": {"input_tokens": 120}}]}

`arguments` is an object, or a string sent as is (to script malformed JSON). A turn without `usage`
reports none, so the run records it as unknown. Once the turns run out, the model answers with no
tool call, as a real model that stops calling tools would. Optional `turn_delay_s` delays each
scripted turn asynchronously (0 to 5 seconds), for reproducible overlap and cancellation demos.
"""

import asyncio
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from oversight.agents.client import FAKE_MODEL, Message, ModelReply, ToolCall, ToolSchema, Usage
from oversight_contracts.events import ModelProfile


@dataclass(frozen=True)
class Script:
    task: str
    turns: list[ModelReply]
    turn_delay_s: float = 0.0

    def __post_init__(self) -> None:
        _delay(self.turn_delay_s)


def _delay(raw: object) -> float:
    if (
        isinstance(raw, bool)
        or not isinstance(raw, (int, float))
        or not math.isfinite(raw)
        or not 0 <= raw <= 5
    ):
        raise ValueError("turn_delay_s must be a finite number between 0 and 5 seconds")
    return float(raw)


def _usage(raw: object) -> Usage:
    if not isinstance(raw, dict):
        return Usage()
    data = cast(dict[str, object], raw)

    def count(key: str) -> int | None:
        value = data.get(key)
        return value if isinstance(value, int) else None

    return Usage(count("input_tokens"), count("output_tokens"), count("cached_tokens"))


def parse_script(text: str) -> Script:
    data = cast(dict[str, object], json.loads(text))
    turns: list[ModelReply] = []
    number = 0
    for raw_turn in cast(list[dict[str, object]], data["turns"]):
        calls: list[ToolCall] = []
        for raw_call in cast(list[dict[str, object]], raw_turn.get("tool_calls", [])):
            number += 1
            arguments = raw_call.get("arguments", {})
            text_args = arguments if isinstance(arguments, str) else json.dumps(arguments)
            calls.append(
                ToolCall(id=f"call-{number}", name=str(raw_call["name"]), arguments=text_args)
            )
        reply_text = raw_turn.get("text")
        turns.append(
            ModelReply(
                text=reply_text if isinstance(reply_text, str) else None,
                tool_calls=calls,
                usage=_usage(raw_turn.get("usage")),
                model=FAKE_MODEL.model,
            )
        )
    return Script(
        task=str(data["task"]), turns=turns, turn_delay_s=_delay(data.get("turn_delay_s", 0))
    )


def load_script(path: Path) -> Script:
    """Synchronous: read scripts at start-up or in tests, not inside a running loop."""
    return parse_script(path.read_text(encoding="utf-8"))


class ScriptedModel:
    """A `ModelClient` that plays a script, one turn per call, and keeps what it was sent."""

    def __init__(self, script: Script, profile: ModelProfile = FAKE_MODEL) -> None:
        self._turns = list(script.turns)
        self._turn_delay_s = script.turn_delay_s
        self._profile = profile
        self.requests: list[list[Message]] = []

    @property
    def profile(self) -> ModelProfile:
        return self._profile

    async def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolSchema]
    ) -> ModelReply:
        self.requests.append([dict(message) for message in messages])
        if not self._turns:
            return ModelReply(text=None, tool_calls=[], model=self._profile.model)
        if self._turn_delay_s:
            await asyncio.sleep(self._turn_delay_s)
        return self._turns.pop(0)
