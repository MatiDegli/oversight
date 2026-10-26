"""The tool loop: one agent's task, from its first model call to `finish` or a halt.

Events per tool call follow docs/proposal.md, 4.5: an edit sends `change.updated` (through the
`ChangeSink`), then `agent.focus`, then `agent.step`; a read sends `agent.focus`, then
`agent.step`. `agent.status` follows the tool calls only, never the model's text. v1 sends no model
text at all, so nothing goes out per token.
"""

import asyncio
import hashlib
import json
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from oversight.agents.client import (
    Message,
    ModelAPIError,
    ModelClient,
    ModelReply,
    require_verified,
)
from oversight.agents.sink import ChangeSink
from oversight.agents.tools import TOOL_SCHEMAS, Toolbox, ToolOutcome, interrupted
from oversight.agents.usage import Pricing, UsageLedger
from oversight.events import EventPayload
from oversight.workspace import Tree, Workspace
from oversight_contracts.common import AgentId, ChangeId, Slot
from oversight_contracts.events import (
    AgentFocus,
    AgentState,
    AgentStatus,
    AgentStep,
    ErrorReport,
    Limits,
    UsageTick,
)

SYSTEM_PROMPT = """You are a coding agent working on a small Python repository, in your own copy.
Use the tools to do the task you are given. Read the code before you change it: prefer one read
of a whole function to several small reads. Make small, exact edits with edit_file. Run the tests
before you finish. When the task is done, call finish with a short note for the reviewer. A person
reviews your change before it is merged."""

PROMPT_VERSION = (
    "agent-v1-"
    + hashlib.sha256(
        (SYSTEM_PROMPT + json.dumps(TOOL_SCHEMAS, sort_keys=True)).encode()
    ).hexdigest()[:12]
)
"""A name and a hash of the system prompt and tool schemas, for `run.started`."""

NO_TOOL_REMINDER = "Use a tool to continue, or call finish when the task is done."
SAME_RESULT_LIMIT = 4
FAILURE_LIMIT = 6
ERROR_CHARS = 200

type Publish = Callable[[EventPayload], Awaitable[object]]


@dataclass(frozen=True)
class AgentTask:
    agent_id: AgentId
    change_id: ChangeId
    slot: Slot
    task: str
    limits: Limits


@dataclass(frozen=True)
class RunEnd:
    state: Literal["ready_for_review", "halted"]
    detail: str | None
    steps: int
    summary: str | None
    """The agent's note from `finish`; `None` when the run halted."""


class _Halt(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _canonical(arguments: str) -> str:
    try:
        return json.dumps(json.loads(arguments), sort_keys=True)
    except json.JSONDecodeError:
        return arguments


def _assistant(reply: ModelReply) -> Message:
    message: Message = {"role": "assistant", "content": reply.text}
    if reply.tool_calls:
        message["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
            for c in reply.tool_calls
        ]
    return message


class AgentRun:
    """Runs one task in one worktree. Cancel the task that awaits `run` to stop it.

    `UnverifiedProfile` at construction if the model's profile may not run agents yet."""

    def __init__(
        self,
        task: AgentTask,
        *,
        model: ModelClient,
        workspace: Workspace,
        tree: Tree,
        sink: ChangeSink,
        publish: Publish,
        ledger: UsageLedger,
        pricing: Pricing | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        require_verified(model.profile)
        self._task = task
        self._model = model
        self._sink = sink
        self._publish = publish
        self._ledger = ledger
        self._pricing = pricing
        self._monotonic = monotonic
        self._tools = Toolbox(workspace, tree, test_timeout_s=task.limits.test_timeout_s)
        self._step = 0
        self._turns = 0
        """Model calls so far. Bounded on their own, so the run ends even if a stop rule fails."""
        self._failures = 0
        self._seen: Counter[tuple[str, str, str]] = Counter()

    async def _status(self, state: AgentState, detail: str | None = None) -> None:
        task = self._task
        await self._publish(
            AgentStatus(
                agent_id=task.agent_id,
                slot=task.slot,
                change_id=task.change_id,
                task=task.task,
                model=self._model.profile.id,
                state=state,
                step=self._step,
                max_steps=task.limits.max_steps,
                detail=detail,
            )
        )

    async def run(self) -> RunEnd:
        await self._status("starting")
        messages: list[Message] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": self._task.task},
        ]
        summary: str | None = None
        detail: str | None = None
        try:
            # The clock starts before the scan, so a slow scan cannot spend the budget unseen.
            async with asyncio.timeout(self._task.limits.max_wall_clock_s):
                await self._tools.refresh_graph()
                await self._status("working")
                summary = await self._loop(messages)
        except _Halt as halt:
            detail = halt.detail
        except TimeoutError:
            detail = f"Ran past the time limit of {self._task.limits.max_wall_clock_s} s."
        except ModelAPIError as error:
            detail = "The model API failed."
            await self._publish(
                ErrorReport(
                    source="model_api",
                    message=str(error),
                    agent_id=self._task.agent_id,
                    change_id=self._task.change_id,
                    fatal=True,
                )
            )
        await self._sink.proposed(self._task.agent_id, summary)
        if detail is None:
            await self._status("ready_for_review")
            return RunEnd("ready_for_review", None, self._step, summary)
        await self._status("halted", detail)
        return RunEnd("halted", detail, self._step, None)

    async def _loop(self, messages: list[Message]) -> str:
        # Every reply with a tool call adds a step, so the step limit bounds those; the failure
        # allowance covers replies without one. The bound holds even if a stop rule is broken.
        max_turns = self._task.limits.max_steps + FAILURE_LIMIT
        while True:
            if self._turns >= max_turns:
                raise _Halt(f"Reached the limit of {max_turns} model calls.")
            self._turns += 1
            reply = await self._model.complete(messages, TOOL_SCHEMAS)
            await self._usage(reply)
            messages.append(_assistant(reply))
            if not reply.tool_calls:
                self._fail_or_halt(ok=False)
                messages.append({"role": "user", "content": NO_TOOL_REMINDER})
                continue
            for index, call in enumerate(reply.tool_calls):
                started = self._monotonic()
                try:
                    outcome = await self._tools.call(call.name, call.arguments)
                except asyncio.CancelledError:
                    # The time limit or Stop cut the call short: it is still a step, an error.
                    self._step += 1
                    stopped = interrupted(call.name, call.arguments)
                    await self._report(call.name, stopped, self._since(started))
                    raise
                duration_ms = self._since(started)
                self._step += 1
                messages.append(
                    {"role": "tool", "tool_call_id": call.id, "content": outcome.content}
                )
                await self._report(call.name, outcome, duration_ms)
                if outcome.finish_summary is not None:
                    return outcome.finish_summary
                try:
                    self._check(call.name, call.arguments, outcome)
                except _Halt:
                    for skipped in reply.tool_calls[index + 1 :]:
                        messages.append(
                            {"role": "tool", "tool_call_id": skipped.id, "content": "Not run."}
                        )
                    raise

    def _since(self, started: float) -> int:
        return round((self._monotonic() - started) * 1000)

    def _fail_or_halt(self, *, ok: bool) -> None:
        self._failures = 0 if ok else self._failures + 1
        if self._failures >= FAILURE_LIMIT:
            raise _Halt(f"Stopped after {FAILURE_LIMIT} failures in a row.")

    def _check(self, name: str, arguments: str, outcome: ToolOutcome) -> None:
        key = (name, _canonical(arguments), outcome.content)
        self._seen[key] += 1
        if self._seen[key] >= SAME_RESULT_LIMIT:
            raise _Halt(f"Stopped: the same call gave the same result {SAME_RESULT_LIMIT} times.")
        self._fail_or_halt(ok=outcome.ok)
        if self._step >= self._task.limits.max_steps:
            raise _Halt(f"Reached the step limit of {self._task.limits.max_steps}.")

    async def _report(self, tool: str, outcome: ToolOutcome, duration_ms: int) -> None:
        agent_id = self._task.agent_id
        if outcome.edited_path is not None:
            await self._sink.file_edited(agent_id, outcome.edited_path)
        if outcome.focus is not None:
            await self._publish(
                AgentFocus(
                    agent_id=agent_id,
                    mode=outcome.focus,
                    ranges=outcome.ranges,
                    node_ids=outcome.node_ids,
                )
            )
        error = None if outcome.ok else outcome.content.removeprefix("Error: ")[:ERROR_CHARS]
        await self._publish(
            AgentStep(
                agent_id=agent_id,
                step=self._step,
                tool=tool[:80],
                summary=outcome.summary,
                result="ok" if outcome.ok else "error",
                error=error,
                duration_ms=duration_ms,
                tests=outcome.tests,
            )
        )

    async def _usage(self, reply: ModelReply) -> None:
        cost = self._pricing.cost(reply.usage) if self._pricing is not None else None
        spent = self._ledger.record(cost)
        await self._publish(
            UsageTick(
                agent_id=self._task.agent_id,
                model=reply.model,
                input_tokens=reply.usage.input_tokens,
                output_tokens=reply.usage.output_tokens,
                cached_tokens=reply.usage.cached_tokens,
                cost_usd=cost,
                spent_usd=spent,
                budget_usd=self._ledger.budget_usd,
                brake=self._ledger.brake,
            )
        )
