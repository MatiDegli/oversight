"""Run controls use held scripted replies/tools, never a provider or real-time sleeps."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import cast

import pytest
from agent_helpers import Recorder, make_run, script
from pydantic import TypeAdapter

from oversight.agents import (
    AgentRun,
    Message,
    ModelAPIError,
    ModelReply,
    RunControl,
    ToolSchema,
)
from oversight.agents.client import CALL_SCOPE
from oversight.agents.tools import Toolbox, ToolOutcome
from oversight.events import EVENT_TYPES, EventPayload
from oversight.workspace import Tree, Workspace
from oversight_contracts.events import AgentStatus, AgentStep, ServerEvent

pytestmark = pytest.mark.anyio
Opened = tuple[Workspace, Tree]
READ = ("read_file", {"path": "shop/mod.py"})
FINISH = ("finish", {"summary": "Done."})
EVENT = TypeAdapter(ServerEvent)


class Observed(Recorder):
    def __init__(self) -> None:
        super().__init__()
        self.paused = asyncio.Event()

    async def publish(self, payload: EventPayload) -> None:
        # Every emitted status (and all other events) passes the unchanged wire schema.
        EVENT.validate_python(
            dict(
                seq=len(self.log) + 1,
                ts="2026-10-30T22:02:00.000Z",
                type=EVENT_TYPES[type(payload)],
                payload=payload.model_dump(),
            )
        )
        await super().publish(payload)
        if isinstance(payload, AgentStatus) and payload.state == "paused":
            self.paused.set()

    def states(self) -> list[str]:
        return [s.state for s in self.statuses()]

    def statuses(self) -> list[AgentStatus]:
        return cast(list[AgentStatus], self.of("AgentStatus"))


class HeldModel:
    def __init__(self, *turns: Sequence[tuple[str, object]], hold: int = 0) -> None:
        self._model = script(*turns)
        self.profile = self._model.profile
        self.requests = self._model.requests
        self.hold = hold
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.deadlines: list[float | None] = []

    async def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolSchema]
    ) -> ModelReply:
        scope = CALL_SCOPE.get()
        assert scope is not None
        self.deadlines.append(scope.deadline)
        reply = await self._model.complete(messages, tools)
        if len(self.requests) - 1 == self.hold:
            self.entered.set()
            await self.release.wait()
        return reply


@pytest.fixture(autouse=True)
async def tasks() -> AsyncIterator[list[asyncio.Task[object]]]:
    pending: list[asyncio.Task[object]] = []
    yield pending
    for task in pending:
        if not task.done():
            task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)


def start(run: AgentRun, tasks: list[asyncio.Task[object]]) -> asyncio.Task:
    task = asyncio.create_task(run.run())
    tasks.append(task)
    return task


async def wait(event: asyncio.Event) -> None:
    async with asyncio.timeout(5):
        await event.wait()


async def test_pause_during_model_keeps_reply_usage_history_and_budgets(
    small_tree: Opened, tasks: list[asyncio.Task[object]]
) -> None:
    model, recorder = HeldModel([READ], [FINISH]), Observed()
    run = make_run(small_tree, model, recorder)
    original = recorder.publish

    async def publish(payload: EventPayload) -> None:
        await original(payload)
        if (
            isinstance(payload, AgentStatus)
            and payload.state == "working"
            and recorder.states().count("paused") == 1
        ):
            assert run.control.pause()
            asyncio.get_running_loop().call_soon(run.control.resume)

    run._publish = publish
    assert isinstance(run.control, RunControl)
    task = start(run, tasks)
    await wait(model.entered)
    assert run.control.pause() and run.control.pause()
    assert recorder.states() == ["starting", "working"]
    model.release.set()
    await wait(recorder.paused)
    assert len(recorder.of("UsageTick")) == 1
    assert recorder.of("AgentStep") == []
    assert run.control.paused and run.control.pause()
    assert run.control.resume()
    end = await task
    assert end.steps == 2 and end.state == "ready_for_review"
    assert recorder.states() == [
        "starting",
        "working",
        "paused",
        "working",
        "paused",
        "working",
        "ready_for_review",
    ]
    paused, resumed = recorder.statuses()[2:4]
    assert paused.model_copy(update={"state": "working"}) == resumed
    assert resumed.detail is None and resumed.max_steps == 20 and resumed.step == 0
    assert model.requests[1][-1]["role"] == "tool"
    assert "def value()" in str(model.requests[1][-1]["content"])
    assert [cast(AgentStep, s).step for s in recorder.of("AgentStep")] == [1, 2]


async def test_pause_during_batch_keeps_remaining_calls_and_messages_follow_results(
    small_tree: Opened, monkeypatch: pytest.MonkeyPatch, tasks: list[asyncio.Task[object]]
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    original = Toolbox.call
    calls: list[str] = []

    async def held(self: Toolbox, name: str, args: str) -> ToolOutcome:
        calls.append(name)
        if len(calls) == 1:
            entered.set()
            await release.wait()
        return await original(self, name, args)

    monkeypatch.setattr(Toolbox, "call", held)
    model, recorder = script([READ, ("list_files", {})], [FINISH]), Observed()
    run = make_run(small_tree, model, recorder)
    task = start(run, tasks)
    await wait(entered)
    assert run.control.pause() and run.control.message("  ¡Hola! 😀\n")
    assert run.control.message("二番目")
    release.set()
    await wait(recorder.paused)
    assert calls == ["read_file"] and run.steps == 1
    assert run.control.resume()
    end = await task
    assert calls == ["read_file", "list_files", "finish"] and end.steps == 3
    history = model.requests[1]
    assert [m["role"] for m in history[-4:]] == ["tool", "tool", "user", "user"]
    assert [m["content"] for m in history[-2:]] == ["  ¡Hola! 😀\n", "二番目"]
    assert "¡Hola!" not in repr(recorder.log) and "二番目" not in repr(recorder.log)


async def test_resume_cancels_pending_pause_without_status(
    small_tree: Opened, tasks: list[asyncio.Task[object]]
) -> None:
    model, recorder = HeldModel([FINISH]), Observed()
    run = make_run(small_tree, model, recorder)
    task = start(run, tasks)
    await wait(model.entered)
    assert run.control.pause() and run.control.resume() and run.control.resume()
    model.release.set()
    assert (await task).state == "ready_for_review"
    assert recorder.states() == ["starting", "working", "ready_for_review"]


async def test_limits_refuse_atomically_and_delivery_does_not_replenish(
    small_tree: Opened, tasks: list[asyncio.Task[object]]
) -> None:
    model, recorder = HeldModel([READ], [FINISH], [FINISH]), Observed()
    run = make_run(small_tree, model, recorder)
    control = run.control
    assert not control.message("x" * 4097)
    assert not control.message("😀" * 2049)  # under the character cap, over the byte cap
    assert control.message("😀" * 1024)  # 4096 bytes
    assert control.message("x" * 4096)  # exactly 8192 lifetime bytes
    assert not control.message("x")
    task = start(run, tasks)
    await wait(model.entered)  # both messages already delivered
    assert control.pause() and control.resume()
    assert not control.message("x")
    model.release.set()
    await task
    assert [m["content"] for m in model.requests[0][-2:]] == ["😀" * 1024, "x" * 4096]
    assert not control.message("") and not control.pause() and not control.resume()
    await run.revise("Again.", None)
    assert run.control is not control and not run.control.message("x")


async def test_queue_cap_refuses_33rd_without_spending_or_dropping(
    small_tree: Opened, tasks: list[asyncio.Task[object]]
) -> None:
    model, recorder = HeldModel([READ], [FINISH]), Observed()
    run = make_run(small_tree, model, recorder)
    task = start(run, tasks)
    await wait(model.entered)
    for _ in range(32):
        assert run.control.message("q")
    assert not run.control.message("x" * 4096)
    model.release.set()
    await task
    assert [m["content"] for m in model.requests[1][-32:]] == ["q"] * 32
    # The refused message spent nothing: all remaining bytes are available in a revision.
    fresh = run.control.for_revision()
    assert fresh.message("x" * 4096) and fresh.message("x" * 4064)
    assert not fresh.message("x")


async def test_deferred_finish_answers_skipped_calls_then_delivers_messages(
    small_tree: Opened, tasks: list[asyncio.Task[object]]
) -> None:
    model, recorder = (
        HeldModel([FINISH, READ], [("finish", {"summary": "After message."})]),
        Observed(),
    )
    run = make_run(small_tree, model, recorder)
    task = start(run, tasks)
    await wait(model.entered)
    assert run.control.message("Please reconsider.")
    model.release.set()
    end = await task
    assert end.summary == "After message." and end.steps == 2
    assert [m["content"] for m in model.requests[1][-3:]] == [
        "Finished.",
        "Not run.",
        "Please reconsider.",
    ]
    assert [cast(AgentStep, s).tool for s in recorder.of("AgentStep")] == ["finish", "finish"]
    assert recorder.of("sink.proposed") == [("agent-1", "After message.")]


async def test_deferred_finish_at_turn_bound_proposes_null(
    small_tree: Opened, tasks: list[asyncio.Task[object]]
) -> None:
    # Alternate empty and successful turns to avoid failures/no-progress/step bounds.
    turns: list[list[tuple[str, object]]] = []
    for index in range(7):
        turns.extend([[], [("search", {"query": f"value{index}"})]])
    turns.extend([[], [FINISH, READ]])
    model, recorder = HeldModel(*turns, hold=15), Observed()
    run = make_run(small_tree, model, recorder, max_steps=10)
    task = start(run, tasks)
    await wait(model.entered)
    assert run.control.message("Too late for another turn.")
    model.release.set()
    end = await task
    assert end.state == "halted" and end.summary is None
    assert end.detail == "Reached the limit of 16 model calls."
    assert recorder.of("sink.proposed") == [("agent-1", None)]
    assert not any(
        m.get("content") == "Too late for another turn." for r in model.requests for m in r
    )


@pytest.mark.parametrize("failure", [False, True])
async def test_controls_do_not_reset_progress_and_halts_win(
    small_tree: Opened, tasks: list[asyncio.Task[object]], failure: bool
) -> None:
    calls = (
        [[("read_file", {"path": f"missing-{n}.py"})] for n in range(6)]
        if failure
        else [[READ]] * 4
    )
    model, recorder = HeldModel(*calls, hold=len(calls) - 1), Observed()
    run = make_run(small_tree, model, recorder)
    original = recorder.publish

    async def publish(payload: EventPayload) -> None:
        await original(payload)
        if isinstance(payload, AgentStep) and payload.step == len(calls):
            assert run.control.pause() and run.control.message("One more.")

    run._publish = publish
    task = start(run, tasks)
    await wait(model.entered)  # three identical results, or five failures, already counted
    assert run.control.pause() and run.control.message("Keep trying.")
    model.release.set()
    await wait(recorder.paused)
    assert run.control.resume()
    end = await task
    assert end.state == "halted" and end.steps == len(calls)
    assert ("6 failures" if failure else "same result 4 times") in end.detail
    assert recorder.states().count("paused") == 1
    assert recorder.of("sink.proposed") == [("agent-1", None)]


async def test_actual_paused_time_extends_both_deadlines_without_sleeping(
    small_tree: Opened, monkeypatch: pytest.MonkeyPatch, tasks: list[asyncio.Task[object]]
) -> None:
    model, recorder = HeldModel([READ], [FINISH]), Observed()
    run = make_run(small_tree, model, recorder)
    task = start(run, tasks)
    await wait(model.entered)
    assert run.control.pause()
    loop = asyncio.get_running_loop()
    original_time = loop.time
    monkeypatch.setattr(loop, "time", lambda: original_time() + 50)
    model.release.set()
    await wait(recorder.paused)
    monkeypatch.setattr(loop, "time", lambda: original_time() + 1050)
    assert run.control.resume()
    end = await task
    assert end.state == "ready_for_review"
    before, after = model.deadlines
    assert before is not None and after is not None
    assert 1000 <= after - before < 1001


async def test_pending_pause_time_still_exhausts_deadline(
    small_tree: Opened, monkeypatch: pytest.MonkeyPatch, tasks: list[asyncio.Task[object]]
) -> None:
    model, recorder = HeldModel([READ]), Observed()
    run = make_run(small_tree, model, recorder)
    task = start(run, tasks)
    await wait(model.entered)
    assert run.control.pause()
    loop = asyncio.get_running_loop()
    original_time = loop.time
    monkeypatch.setattr(loop, "time", lambda: original_time() + 121)
    model.release.set()
    end = await task
    assert end.state == "halted" and "time limit" in end.detail
    assert "paused" not in recorder.states() and run.control.closed


@pytest.mark.parametrize("stop", [False, True])
async def test_undelivered_messages_never_cross_halt_or_stop_into_revision(
    small_tree: Opened, tasks: list[asyncio.Task[object]], stop: bool
) -> None:
    model, recorder = HeldModel([READ], [FINISH]), Observed()
    run = make_run(small_tree, model, recorder, max_steps=1)
    task = start(run, tasks)
    await wait(model.entered)
    assert run.control.message("Must be forgotten.")
    control = run.control
    if stop:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        model.release.set()
        assert (await task).state == "halted"
    assert control.closed and not control.message("late")
    await run.revise("Start again.", None)
    assert all(m.get("content") != "Must be forgotten." for m in model.requests[-1])
    assert control.closed and run.control is not control


async def test_pause_during_revision_restores_revising(
    small_tree: Opened, tasks: list[asyncio.Task[object]]
) -> None:
    model, recorder = HeldModel([FINISH], [READ], [FINISH], hold=1), Observed()
    run = make_run(small_tree, model, recorder)
    await run.run()
    task = asyncio.create_task(run.revise("Again.", None))
    tasks.append(task)
    await wait(model.entered)
    assert run.control.pause()
    model.release.set()
    await wait(recorder.paused)
    assert run.control.resume()
    assert (await task).steps == 3
    assert recorder.states()[-4:] == ["revising", "paused", "revising", "ready_for_review"]
    assert recorder.statuses()[-2].max_steps == 40


async def test_finalization_closes_before_sink_and_rejects_controls(
    small_tree: Opened, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorder = Observed()
    run = make_run(small_tree, script([FINISH]), recorder)
    original = recorder.proposed

    async def proposed(*args, **kwargs):
        assert run.control.closed
        assert not run.control.message("late")
        assert not run.control.pause() and not run.control.resume()
        return await original(*args, **kwargs)

    monkeypatch.setattr(recorder, "proposed", proposed)
    assert (await run.run()).state == "ready_for_review"


async def test_terminal_model_error_wins_over_pending_controls(
    small_tree: Opened, tasks: list[asyncio.Task[object]]
) -> None:
    class Broken(HeldModel):
        async def complete(self, messages, tools):
            await super().complete(messages, tools)
            raise ModelAPIError("scripted terminal failure")

    model, recorder = Broken([FINISH]), Observed()
    run = make_run(small_tree, model, recorder)
    task = start(run, tasks)
    await wait(model.entered)
    assert run.control.pause() and run.control.message("Cannot rescue it.")
    model.release.set()
    assert (await task).state == "halted"
    assert "paused" not in recorder.states() and run.control.closed


async def test_finalization_gate_can_pause_and_defer_finish_without_holding_locks(
    small_tree: Opened, monkeypatch: pytest.MonkeyPatch, tasks: list[asyncio.Task[object]]
) -> None:
    model, recorder = script([FINISH, READ], [FINISH]), Observed()
    run = make_run(small_tree, model, recorder)
    original = recorder.publish

    async def publish(payload: EventPayload) -> None:
        await original(payload)
        if isinstance(payload, AgentStep) and payload.step == 1:
            assert run.control.pause()

    monkeypatch.setattr(run, "_publish", publish)
    task = start(run, tasks)
    await wait(recorder.paused)
    assert run.steps == 1 and recorder.of("sink.proposed") == []
    async with asyncio.timeout(5):
        async with small_tree[0].locks(main=True, agent_id="agent-1"):
            assert run.control.message("After finish, before finalization.")
    assert run.control.resume()
    assert (await task).steps == 2
    assert [m["content"] for m in model.requests[1][-3:]] == [
        "Finished.",
        "Not run.",
        "After finish, before finalization.",
    ]


async def test_deferred_finish_at_step_bound_loses_summary(
    small_tree: Opened, tasks: list[asyncio.Task[object]]
) -> None:
    model, recorder = HeldModel([FINISH, READ]), Observed()
    run = make_run(small_tree, model, recorder, max_steps=1)
    original = recorder.publish

    async def publish(payload: EventPayload) -> None:
        await original(payload)
        if isinstance(payload, AgentStep):
            assert run.control.pause()

    run._publish = publish
    task = start(run, tasks)
    await wait(model.entered)
    assert run.control.message("No step budget left.")
    model.release.set()
    end = await task
    assert end.state == "halted" and end.summary is None and end.steps == 1
    assert "paused" not in recorder.states()
    assert recorder.of("sink.proposed") == [("agent-1", None)]


async def test_stop_while_paused_closes_control_without_proposing(
    small_tree: Opened, tasks: list[asyncio.Task[object]]
) -> None:
    run = make_run(small_tree, script([FINISH]), recorder := Observed())
    assert run.control.pause()
    task = start(run, tasks)
    await wait(recorder.paused)
    assert run.control.message("Undelivered.")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert run.control.closed and not run.control.paused
    assert recorder.of("sink.proposed") == [] and recorder.of("AgentStep") == []


async def test_closed_paused_gate_cancels_without_restoring_status_or_proposing(
    small_tree: Opened, tasks: list[asyncio.Task[object]]
) -> None:
    model, recorder = HeldModel([READ]), Observed()
    run = make_run(small_tree, model, recorder)
    task = start(run, tasks)
    await wait(model.entered)
    assert run.control.pause()
    model.release.set()
    await wait(recorder.paused)
    run.control.close()  # Outside the drive, as Stop now does.
    with pytest.raises(asyncio.CancelledError):
        await task
    assert recorder.states() == ["starting", "working", "paused"]
    assert recorder.of("sink.proposed") == [] and recorder.of("AgentStep") == []
    assert len(model.requests) == 1 and not run.control.paused


async def test_second_run_is_refused_without_reusing_closed_control(small_tree: Opened) -> None:
    model, recorder = script([FINISH]), Observed()
    run = make_run(small_tree, model, recorder)
    await run.run()
    before = list(recorder.log)
    with pytest.raises(RuntimeError, match="cannot run twice"):
        await run.run()
    assert len(model.requests) == 1 and recorder.log == before
