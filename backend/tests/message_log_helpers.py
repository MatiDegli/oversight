"""Hold a completed tool's step and message persistence to isolate the next model-call gate."""

# The test boundary deliberately observes the active control's gate.
# pyright: reportPrivateUsage=false

import asyncio
from collections.abc import Sequence
from types import TracebackType
from typing import Self

import pytest
from live_helpers import LiveServer

from oversight.agents import Message, ModelClient, ModelReply, ToolSchema
from oversight.events import EventPayload, StoredEvent
from oversight_contracts.events import AgentMessageSent, AgentStep


class MessageBarrier:
    def __init__(
        self,
        server: LiveServer,
        model: ModelClient,
        monkeypatch: pytest.MonkeyPatch,
        text: str,
        *,
        fail: bool = False,
    ) -> None:
        self.step_entered, self.step_release = asyncio.Event(), asyncio.Event()
        self.write_entered, self.write_release = asyncio.Event(), asyncio.Event()
        self.gate_entered, self.model_called = asyncio.Event(), asyncio.Event()
        self.requests: list[list[Message]] = []
        self._next_gate = False
        publish = server.store.publish
        gate = server.live._controls["agent-1"].control.gate
        complete = model.complete

        async def held(payload: EventPayload, *, ts: str | None = None) -> StoredEvent:
            first_step = isinstance(payload, AgentStep) and payload.step == 1
            if first_step:
                self.step_entered.set()
                await self.step_release.wait()
            if isinstance(payload, AgentMessageSent) and payload.text == text:
                self.write_entered.set()
                await self.write_release.wait()
                if fail:
                    raise OSError("synthetic disk failure")
            stored = await publish(payload, ts=ts)
            if first_step:
                self._next_gate = True
            return stored

        async def observed_gate(*args, **kwargs):
            if self._next_gate:
                self._next_gate = False
                self.gate_entered.set()
            return await gate(*args, **kwargs)

        async def observed_call(
            messages: Sequence[Message], tools: Sequence[ToolSchema]
        ) -> ModelReply:
            self.requests.append([dict(message) for message in messages])
            self.model_called.set()
            return await complete(messages, tools)

        monkeypatch.setattr(server.store, "publish", held)
        monkeypatch.setattr(server.live._controls["agent-1"].control, "gate", observed_gate)
        monkeypatch.setattr(model, "complete", observed_call)

    async def assert_pre_call_blocked(self) -> None:
        self.step_release.set()
        await self.gate_entered.wait()
        assert not self.model_called.is_set(), "model called before message commit"
        assert not self.requests, "model received uncommitted message history"

    def release(self) -> None:
        self.step_release.set()
        self.write_release.set()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.release()
