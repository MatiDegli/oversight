"""In-memory controls with atomic limits and a commit barrier before message delivery."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import cast

from oversight.agents.client import CALL_SCOPE, Message
from oversight_contracts.events import AgentState


@dataclass
class _Budget:
    accepted_bytes: int = 0


class RunControl:
    """One drive's controls. Mutators return False on refusal, True on acceptance.

    Call only on the run's event loop. Synchronous admission and finalization are atomic
    there. Message admission permanently reserves lifetime bytes, including queued text;
    delivery, pause and revision never replenish them. HTTP admission records a masked copy
    before the next model call can receive the original text.
    A revision installs a fresh control sharing only that budget; old controls stay closed.
    """

    def __init__(self) -> None:
        self._inbox: list[str] = []
        self._admission = asyncio.Lock()
        self._budget = _Budget()
        self._pause_requested = False
        self._paused = False
        self._closed = False
        self._wake = asyncio.Event()

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def pause_requested(self) -> bool:
        return self._pause_requested

    @property
    def pending_messages(self) -> bool:
        return bool(self._inbox)

    def message(self, text: str) -> bool:
        """Accept verbatim text, subject to character, queue and lifetime UTF-8 limits."""
        if self._closed or len(text) > 4096 or len(self._inbox) >= 32:
            return False
        size = len(text.encode("utf-8"))
        if self._budget.accepted_bytes + size > 8192:
            return False
        self._inbox.append(text)
        self._budget.accepted_bytes += size
        return True

    async def record_message(self, text: str, record: Callable[[], Awaitable[None]]) -> bool:
        """Reserve limits atomically, commit history, then permit verbatim delivery.

        The gate waits on this lock. Failed or cancelled writes roll back the reservation;
        close may drop the inbox during the write, but never delivers unrecorded text.
        """
        async with self._admission:
            if not self.message(text):
                return False
            try:
                await record()
            except BaseException:
                if not self._closed:
                    self._inbox.pop()
                self._budget.accepted_bytes -= len(text.encode("utf-8"))
                raise
            return True

    def pause(self) -> bool:
        if self._closed:
            return False
        self._pause_requested = True
        return True

    def resume(self) -> bool:
        if self._closed:
            return False
        self._pause_requested = False
        self._wake.set()
        return True

    def close(self) -> None:
        """Claim terminal finalization, dropping text that must never reach a revision."""
        self._closed = True
        self._inbox.clear()
        self._pause_requested = False
        self._wake.set()

    def for_revision(self) -> "RunControl":
        control = RunControl()
        control._budget = self._budget
        return control

    def deliver(self, messages: list[Message]) -> None:
        messages.extend({"role": "user", "content": text} for text in self._inbox)
        self._inbox.clear()

    def claim_finish(self) -> bool:
        if self._inbox or self._pause_requested:
            return False
        self.close()
        return True

    async def gate(
        self,
        deadline: asyncio.Timeout,
        status: Callable[[AgentState], Awaitable[None]],
        active: AgentState,
    ) -> None:
        if self._closed:
            raise asyncio.CancelledError
        while self._pause_requested or self._admission.locked():
            async with self._admission:
                pass
            if self._closed:
                raise asyncio.CancelledError
            if not self._pause_requested:
                continue
            loop = asyncio.get_running_loop()
            end = deadline.when()
            # An exhausted deadline wins over a pause, even before its callback runs.
            if end is not None and loop.time() >= end:
                raise TimeoutError
            started = loop.time()
            self._paused = True
            deadline.reschedule(None)
            try:
                await status("paused")
                while self._pause_requested:
                    self._wake.clear()
                    await self._wake.wait()
            finally:
                self._paused = False
                if end is not None:
                    deadline.reschedule(end + loop.time() - started)
            if self._closed:
                raise asyncio.CancelledError
            scope = CALL_SCOPE.get()
            if scope is not None:
                CALL_SCOPE.set(replace(scope, deadline=deadline.when()))
            await status(active)
        if self._closed:
            raise asyncio.CancelledError


def answer_unanswered(messages: list[Message]) -> None:
    """Answer calls cut short by a halt, stop or finish before adding a user message."""
    answered = {str(m["tool_call_id"]) for m in messages if m["role"] == "tool"}
    missing: list[Message] = []
    for message in messages:
        if message["role"] != "assistant":
            continue
        calls = message.get("tool_calls")
        if not isinstance(calls, list):
            continue
        for call in cast(list[dict[str, object]], calls):
            call_id = str(call.get("id"))
            if call_id not in answered:
                missing.append({"role": "tool", "tool_call_id": call_id, "content": "Not run."})
    messages.extend(missing)
