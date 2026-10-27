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
from typing import Literal, cast

from oversight.agents._replies import ReplyError, ReplyRecovery
from oversight.agents.client import (
    CALL_SCOPE,
    CallScope,
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
from oversight.workspace import Tree, Workspace, WorkspaceError
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

REVISION_PROMPT = """The reviewer has requested changes to the change you proposed.

Reviewer comment:
{comment}
{note}
Read a file again before editing it, even if you read or edited it earlier: the file may have \
changed since you last saw it. Make the requested changes, run the tests, and call finish with \
a short note for the reviewer."""

PROMPT_VERSION = (
    "agent-v1-"
    + hashlib.sha256(
        (SYSTEM_PROMPT + REVISION_PROMPT + json.dumps(TOOL_SCHEMAS, sort_keys=True)).encode()
    ).hexdigest()[:12]
)
"""A name and a hash of the system prompt and tool schemas, for `run.started`."""

NO_TOOL_REMINDER = "Use a tool to continue, or call finish when the task is done."
SAME_RESULT_LIMIT = 4
FAILURE_LIMIT = 6
ERROR_CHARS = 200
PROPOSAL_FAILED = "Its change could not be proposed."
EDIT_NOT_RECORDED = " (saved, but not recorded for review)"
"""Added to the summary of an edit step whose record the sink did not take."""

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


class _RootLost(_Halt):
    """The first scan found the worktree's root gone or swapped: nothing may touch it again."""


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
        self._workspace = workspace
        self._model = model
        self._sink = sink
        self._publish = publish
        self._ledger = ledger
        self._pricing = pricing
        self._monotonic = monotonic
        self._tools = Toolbox(workspace, tree, test_timeout_s=task.limits.test_timeout_s)
        self._step = 0
        self._halted = False
        self._turns = 0
        """Model calls so far. Bounded on their own, so the run ends even if a stop rule fails."""
        self._recovery = ReplyRecovery()
        self._failures = 0
        self._seen: Counter[tuple[str, str, str]] = Counter()
        self._messages: list[Message] | None = None
        """The conversation, kept across `run` and every `revise` on this run."""
        self._max_steps = task.limits.max_steps
        """The step budget: `limits.max_steps` per task or revision, cumulative."""
        self._max_turns = task.limits.max_steps + FAILURE_LIMIT
        """The model-call bound, grown with the step budget on each revision."""
        self._running = False
        """Whether a `run` or `revise` drive is in progress on this run."""

    @property
    def steps(self) -> int:
        """Tool calls finished so far."""
        return self._step

    @property
    def max_steps(self) -> int:
        """The step budget so far: `limits.max_steps`, and the same again for each revision."""
        return self._max_steps

    @property
    def halted(self) -> bool:
        """Whether the run has handed its own `halted` status to the publisher, which queues it
        before it first waits. A cancel after that leaves the event on its way, and a second
        `halted` would overwrite the first one's reason."""
        return self._halted

    async def _status(self, state: AgentState, detail: str | None = None) -> None:
        task = self._task
        self._halted = self._halted or state == "halted"  # no await before the publish queues it
        await self._publish(
            AgentStatus(
                agent_id=task.agent_id,
                slot=task.slot,
                change_id=task.change_id,
                task=task.task,
                model=self._model.profile.id,
                state=state,
                step=self._step,
                max_steps=self._max_steps,
                detail=detail,
            )
        )

    async def run(self) -> RunEnd:
        if self._running:
            raise RuntimeError("a run is already in progress on this agent run")
        self._running = True
        try:
            self._messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": self._task.task},
            ]
            return await self._drive("starting")
        finally:
            self._running = False

    async def revise(self, comment: str, note: str | None) -> RunEnd:
        """Resume the kept run with the reviewer's comment as a new user message.

        The history, step count and ledger carry over; the step budget and the turn bound
        grow by `limits.max_steps`, and the no-progress counts start over."""
        if self._running:
            raise RuntimeError("cannot revise while a run is in progress")
        if self._messages is None:
            raise RuntimeError("cannot revise before the first run")
        self._running = True
        try:
            self._answer_unanswered()
            note_block = f"\n{note}\n" if note else ""
            self._messages.append(
                {
                    "role": "user",
                    "content": REVISION_PROMPT.format(comment=comment, note=note_block),
                }
            )
            self._max_steps += self._task.limits.max_steps
            self._max_turns += self._task.limits.max_steps
            self._recovery = ReplyRecovery()
            self._failures = 0
            self._seen.clear()
            return await self._drive("revising")
        finally:
            self._running = False

    def _answer_unanswered(self) -> None:
        """Give every tool call left without a result a `Not run.` result, so the kept
        history is a valid conversation again: a call cut short mid-call by the time limit
        or a stop, and the calls after `finish` in its reply, never got one."""
        assert self._messages is not None
        answered = {
            str(message["tool_call_id"]) for message in self._messages if message["role"] == "tool"
        }
        missing: list[Message] = []
        for message in self._messages:
            if message["role"] != "assistant":
                continue
            calls = message.get("tool_calls")
            if not isinstance(calls, list):
                continue
            for call in cast(list[dict[str, object]], calls):
                call_id = str(call.get("id"))
                if call_id not in answered:
                    missing.append({"role": "tool", "tool_call_id": call_id, "content": "Not run."})
        self._messages.extend(missing)

    async def _drive(self, first_status: AgentState) -> RunEnd:
        messages = self._messages
        assert messages is not None
        self._halted = False
        await self._status(first_status)
        summary: str | None = None
        detail: str | None = None
        root_lost = False
        try:
            # The clock starts before the scan, so a slow scan cannot spend the budget unseen.
            async with asyncio.timeout(self._task.limits.max_wall_clock_s) as deadline:
                try:
                    await self._tools.refresh_graph()
                except WorkspaceError as error:
                    reason = " ".join(str(error).split()) or type(error).__name__
                    raise _RootLost(f"The worktree cannot be used: {reason}.") from error
                if first_status == "starting":
                    await self._status("working")
                token = CALL_SCOPE.set(CallScope(deadline.when(), self._usage))
                try:
                    summary = await self._loop(messages)
                finally:
                    CALL_SCOPE.reset(token)
        except _Halt as halt:
            detail = halt.detail
            root_lost = isinstance(halt, _RootLost)
        except TimeoutError:
            detail = f"Ran past the time limit of {self._task.limits.max_wall_clock_s} s."
        except ModelAPIError as error:
            detail = f"The model API failed: {error}."
            await self._publish(
                ErrorReport(
                    source="model_api",
                    message=str(error),
                    agent_id=self._task.agent_id,
                    change_id=self._task.change_id,
                    fatal=True,
                )
            )
        except Exception as error:  # not BaseException: cancellation (Stop) must propagate
            detail = f"Stopped by an unexpected error: {self._describe(error)}"
            await self._publish(
                ErrorReport(
                    source="agent",
                    message=detail,
                    agent_id=self._task.agent_id,
                    change_id=self._task.change_id,
                    fatal=True,
                )
            )
        # The sink reads the worktree, and a lost root is not the agent's any more.
        if not root_lost and not await self._propose(summary):
            detail = detail or PROPOSAL_FAILED  # a halt's own reason is the better one
        if detail is None:
            await self._status("ready_for_review")
            return RunEnd("ready_for_review", None, self._step, summary)
        await self._status("halted", detail)
        return RunEnd("halted", detail, self._step, None)

    def _describe(self, error: Exception) -> str:
        """The error's type and its message on one line, without the runtime's location."""
        message = " ".join(str(error).split())
        text = f"{type(error).__name__}: {message}" if message else type(error).__name__
        return self._workspace.redact(text)[:ERROR_CHARS]

    async def _propose(self, summary: str | None) -> bool:
        """Whether the sink proposed the change. A sink that returns `False` has sent its own
        `error`; one that raises has not, so the runtime reports it."""
        try:
            return await self._sink.proposed(self._task.agent_id, summary)
        except Exception as error:
            await self._publish(
                ErrorReport(
                    source="server",
                    message=f"The change could not be proposed: {self._describe(error)}",
                    agent_id=self._task.agent_id,
                    change_id=self._task.change_id,
                    fatal=True,
                )
            )
            return False

    async def _loop(self, messages: list[Message]) -> str:
        # Every reply with a tool call adds a step, so the step limit bounds those; the failure
        # allowance covers replies without one. The bound holds even if a stop rule is broken.
        while True:
            if self._turns >= self._max_turns:
                raise _Halt(f"Reached the limit of {self._max_turns} model calls.")
            self._turns += 1
            reply = await self._model.complete(messages, TOOL_SCHEMAS)
            await self._usage(reply)
            try:
                recovering = self._recovery.recover(reply, messages)
            except ReplyError as error:
                raise _Halt(str(error)) from error
            if recovering:
                self._fail_or_halt(ok=False)
                continue
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
        if self._step >= self._max_steps:
            raise _Halt(f"Reached the step limit of {self._max_steps}.")

    async def _record(self, path: str) -> bool:
        """Whether the sink recorded the edit. A sink that returns `False` has sent its own
        `error`; one that raises has not, so the runtime reports it."""
        agent_id = self._task.agent_id
        try:
            return await self._sink.file_edited(agent_id, path)
        except Exception as error:
            await self._publish(
                ErrorReport(
                    source="server",
                    message=f"The edit to {path} could not be recorded: {self._describe(error)}",
                    agent_id=agent_id,
                    change_id=self._task.change_id,
                    fatal=False,
                )
            )
            return False

    async def _report(self, tool: str, outcome: ToolOutcome, duration_ms: int) -> None:
        agent_id = self._task.agent_id
        # The edit is on disk either way. A lost record only changes what the UI is told: the
        # model's result, the failure count and the stop rules still follow `outcome`.
        recorded = outcome.edited_path is None
        cut_short: asyncio.CancelledError | None = None
        if outcome.edited_path is not None:
            try:
                recorded = await self._record(outcome.edited_path)
            except asyncio.CancelledError as cancelled:
                # Stop or the time limit hit while the sink worked: it is unknown whether it
                # recorded. The edit is on disk, so the UI still gets its focus and its step.
                cut_short = cancelled
        if outcome.focus is not None:
            await self._publish(
                AgentFocus(
                    agent_id=agent_id,
                    mode=outcome.focus,
                    ranges=outcome.ranges,
                    node_ids=outcome.node_ids if recorded else [],  # unknown: the sink never saw it
                )
            )
        error = None
        if not outcome.ok:
            error = outcome.content.removeprefix("Error: ")[:ERROR_CHARS]
        # The step's `error` is what the model was told, and the model is told nothing of a lost
        # record: the step stays "ok" and its summary says so.
        note = "" if recorded else EDIT_NOT_RECORDED
        await self._publish(
            AgentStep(
                agent_id=agent_id,
                step=self._step,
                tool=tool[:80],
                summary=outcome.summary + note,
                result="ok" if outcome.ok else "error",
                error=error,
                duration_ms=duration_ms,
                tests=outcome.tests,
            )
        )
        if cut_short is not None:
            raise cut_short

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
