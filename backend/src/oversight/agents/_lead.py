"""A bounded coordination loop: read main, propose plans, and answer one thread turn."""

# Share canonical tool history helpers within agents; they are deliberately not public exports.
# pyright: reportPrivateUsage=false

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from oversight.agents._attempts import attempt_observer
from oversight.agents._lead_history import LeadHistory
from oversight.agents._lead_replies import reply_text
from oversight.agents._lead_tools import LEAD_TOOL_SCHEMAS, LeadTools
from oversight.agents._prompts import LEAD_PROMPT
from oversight.agents._replies import ReplyError, ReplyRecovery
from oversight.agents.client import (
    CALL_SCOPE,
    CallScope,
    Message,
    ModelAPIError,
    ModelClient,
    ModelReply,
)
from oversight.agents.loop import (
    FAILURE_LIMIT,
    NO_TOOL_REMINDER,
    SAME_RESULT_LIMIT,
    _assistant,
    _canonical,
)
from oversight.events import EventPayload
from oversight_contracts.events import LeadReply


@dataclass(frozen=True)
class LeadLimits:
    tool_calls: int = 12
    model_calls: int = 12 + FAILURE_LIMIT
    wall_clock_s: float = 300
    plans: int = 3
    subtasks: int = 6
    task_chars: int = 2_000


LEAD_LIMITS = LeadLimits()
STARTING_DETAIL = (
    "Limits: 12 tool calls, 18 model calls, 300 s, 3 plans of up to 6 subtasks, "
    "2,000 characters per task."
)
BRAKE_DETAIL = "The spending brake stopped the next model call."
LEDGER_DETAIL = "A model call could not be recorded in the ledger."


@dataclass(frozen=True)
class LeadEnd:
    state: Literal["idle", "halted"]
    detail: str
    reply: LeadReply | None = None


class _Halt(Exception):
    """A server-owned stop rule."""


class LeadRun:
    def __init__(
        self,
        *,
        agent_id: str,
        task: str,
        make_client: Callable[[], ModelClient],
        tools: LeadTools,
        record: Callable[[ModelReply], Awaitable[None]],
        publish: Callable[[EventPayload], Awaitable[object]],
        admit: Callable[[], str | None],
        on_working: Callable[[], Awaitable[None]],
        on_step: Callable[[str | None], Awaitable[None]],
        limits: LeadLimits = LEAD_LIMITS,
        mask: Callable[[str], str] = str,
    ) -> None:
        self.agent_id, self.task, self.make_client, self.tools = agent_id, task, make_client, tools
        self.record, self.publish, self.admit = record, publish, admit
        self.on_working, self.on_step, self.limits = on_working, on_step, limits
        self.mask = mask
        self.history = LeadHistory(task)
        self.steps = self.turns = self.failures = 0
        self.turn_steps = 0
        self.human: str | None = None
        self.answers_seq: int | None = None
        self.text: str | None = None
        self.seen: Counter[tuple[str, str, str]] = Counter()

    def detail(self, text: str) -> str:
        if self.tools.plan_ids:
            text += " Requested " + ", ".join(self.tools.plan_ids) + "."
        return text

    async def run(self, human: str | None = None, answers_seq: int | None = None) -> LeadEnd:
        self.turns = self.failures = self.turn_steps = 0
        self.seen.clear()
        self.tools.through_seq = None
        self.human, self.answers_seq, self.text = human, answers_seq, None
        deadline = asyncio.timeout(self.limits.wall_clock_s)
        try:
            async with deadline:
                token = CALL_SCOPE.set(
                    CallScope(
                        deadline.when(),
                        self.record,
                        role="lead",
                        attempt_failed=attempt_observer(
                            self.publish,
                            agent_id=self.agent_id,
                            change_id=None,
                            role="lead",
                        ),
                    )
                )
                try:
                    await self.on_working()
                    self._cancelled()
                    client = self.make_client()
                    return await self._loop(client)
                finally:
                    CALL_SCOPE.reset(token)
        except TimeoutError:
            if not deadline.expired():
                raise
            return self._reply("time")
        except ModelAPIError:
            reason = "The model API failed after its retries."
        except (ReplyError, _Halt) as error:
            reason = str(error)
        return LeadEnd("halted", self.detail(reason))

    async def _loop(self, client: ModelClient) -> LeadEnd:
        messages: list[Message] = [
            {"role": "system", "content": LEAD_PROMPT},
            *self.history.messages(self.human, reserved=len(LEAD_PROMPT)),
        ]
        recovery = ReplyRecovery()
        while True:
            self._cancelled()
            if self.turns >= self.limits.model_calls:
                return self._reply("model_calls")
            if self.admit() is not None:
                return self._reply("spend_brake")
            self.turns += 1
            reply = await client.complete(messages, LEAD_TOOL_SCHEMAS)
            await self.record(reply)
            self._cancelled()
            if recovery.recover(reply, messages):
                self._failure(False)
                continue
            if (reply.text or "").strip():
                self.text = reply.text
            messages.append(_assistant(reply))
            if not reply.tool_calls:
                if (reply.text or "").strip():
                    return self._reply()
                self._failure(False)
                messages.append({"role": "user", "content": NO_TOOL_REMINDER})
                continue
            for call in reply.tool_calls:
                outcome = await self.tools.call(call.name, call.arguments)
                self.steps += 1
                self.turn_steps += 1
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": outcome.content}
                )
                if outcome.finish:
                    self.text = outcome.content
                    return self._reply()
                await self.on_step(outcome.plan_id)
                self._cancelled()
                key = (call.name, _canonical(call.arguments), outcome.content)
                self.seen[key] += 1
                if self.seen[key] >= SAME_RESULT_LIMIT:
                    raise _Halt("Stopped: the same call gave the same result 4 times.")
                self._failure(outcome.ok)
                if self.turn_steps >= self.limits.tool_calls:
                    return self._reply("tool_calls")

    def _reply(
        self, cut: Literal["tool_calls", "model_calls", "time", "spend_brake"] | None = None
    ) -> LeadEnd:
        self._cancelled()
        text = reply_text(self.text, self.mask)
        reply = LeadReply(
            agent_id=self.agent_id,
            answers_seq=self.answers_seq,
            text=text,
            through_seq=self.tools.through_seq,
            cut=cut,
        )
        self.history.append(self.human, text)
        detail = (
            "Requested " + ", ".join(self.tools.plan_ids) + "."
            if self.tools.plan_ids
            else "Turn completed."
        )
        if cut is not None:
            reasons = {
                "tool_calls": f"Reached the limit of {self.limits.tool_calls} tool calls.",
                "model_calls": f"Reached the limit of {self.limits.model_calls} model calls.",
                "time": f"Ran past the time limit of {self.limits.wall_clock_s:g} s.",
                "spend_brake": BRAKE_DETAIL,
            }
            detail = self.detail(reasons[cut])
        return LeadEnd("idle", detail, reply)

    def _failure(self, ok: bool) -> None:
        self.failures = 0 if ok else self.failures + 1
        if self.failures >= FAILURE_LIMIT:
            raise _Halt("Stopped after 6 failures in a row.")

    @staticmethod
    def _cancelled() -> None:
        # Durable writes finish despite cancellation. Observe the pending stop immediately
        # afterward, before another tool or model call can start.
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
