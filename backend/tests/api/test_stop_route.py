"""Stop over a real server on the live app, with the scripted model: at each point of a run, and
right after a Request changes. Its 404 and 409 are in test_revise_stop.py, with Request changes'."""

import asyncio
from collections.abc import Sequence
from pathlib import Path

import pytest
from live_helpers import (
    DEADLINE_S,
    GatedModel,
    Stepped,
    error_code,
    is_type,
    live_server,
    live_settings,
    start,
)
from revise_helpers import (
    MODEL_TURNS_A,
    STEPS_A,
    last_status,
    ready,
    request,
    resolutions,
    revisable,
    states,
)

from oversight.agents import Message, ModelReply, ScriptedModel, ToolSchema, parse_script
from oversight.app import LIMITS

pytestmark = pytest.mark.anyio


async def test_stop_a_running_agent_discards_its_change_and_frees_the_slot(
    tmp_path: Path,
) -> None:
    model = GatedModel()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: model) as s,
    ):
        await start(s)
        await model.entered.wait()
        answer = await s.client.post("/agents/agent-1/stop")
        assert (answer.status_code, answer.content) == (202, b"")
        events = await s.until(is_type("change.resolved"))
        stopped = last_status(events)
        assert (stopped.state, stopped.detail) == ("stopped", "Stopped by the user.")
        [resolved] = resolutions(events)
        assert (resolved.decision, resolved.reason, resolved.merge) == ("rejected", "stopped", None)
        assert model.cancelled.is_set()
        assert s.live._runs == {} and s.live._slots == {"A": None}  # pyright: ignore[reportPrivateUsage]
        assert error_code(await s.client.post("/agents/agent-1/stop")) == (409, "wrong_state")
        assert (await start(s)).status_code == 201


async def test_stop_an_idle_agent(tmp_path: Path) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        await start(s)
        await ready(s)
        assert (await s.client.post("/agents/agent-1/stop")).status_code == 202
        events = await s.until(is_type("change.resolved"))
        [resolved] = resolutions(events)
        assert (resolved.decision, resolved.reason, resolved.revision) == ("rejected", "stopped", 1)
        assert states(events)[-1] == "stopped" and "done" not in states(events)
        assert s.live._kept == {} and s.live._slots == {"A": None}  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("revising", [False, True], ids=["first-run", "revision"])
async def test_a_stopped_run_reports_its_steps(tmp_path: Path, revising: bool) -> None:
    # The run is cancelled, so it ends with no `RunEnd`: the steps come from the kept run, and the
    # budget on the card has grown with each request.
    model = Stepped(parse_script(revisable()))
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda *_: model) as s,
    ):
        await start(s)
        if revising:
            for _ in range(MODEL_TURNS_A):
                model.permits.release()
            await ready(s)
            assert (await request(s)).status_code == 202
            await s.until(is_type("agent.status", state="revising"))
            model.permits.release()  # the revision edits once, then waits for its next call
            await s.until(is_type("agent.step", step=STEPS_A + 1))
            expected_steps, budget = STEPS_A + 1, 2 * LIMITS.max_steps
        else:
            for _ in range(2):
                model.permits.release()
            await s.until(is_type("agent.step", step=3))
            expected_steps, budget = 3, LIMITS.max_steps
        assert (await s.client.post("/agents/agent-1/stop")).status_code == 202
        events = await s.until(is_type("change.resolved", reason="stopped"))
        stopped = last_status(events)
        assert (stopped.state, stopped.step, stopped.max_steps) == (
            "stopped",
            expected_steps,
            budget,
        )


async def test_stop_right_after_request_changes_never_runs_the_revision(
    tmp_path: Path,
) -> None:
    # The decision ends with the change `working`, and Stop lands before the revision's task
    # resumes from awaiting it (a done-callback added first runs first). Stop cancels that task,
    # which is the agent's run in `_runs`, so the model is never asked again, and the log ends with
    # the stop's resolution.
    calls: list[int] = []

    class Counting(ScriptedModel):
        async def complete(
            self, messages: Sequence[Message], tools: Sequence[ToolSchema]
        ) -> ModelReply:
            calls.append(len(calls))
            return await super().complete(messages, tools)

    model = Counting(parse_script(revisable()))
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: model) as s,
    ):
        live = s.live
        await start(s)
        first = await ready(s)
        first_run_calls = len(calls)
        stopped = asyncio.Event()

        def stop_now(_: object) -> None:
            live.stop("agent-1")
            stopped.set()

        live.request_changes("change-1", "Shorter, please.")
        live._decisions["agent-1"].add_done_callback(stop_now)  # pyright: ignore[reportPrivateUsage]
        await stopped.wait()
        events = await s.until(is_type("change.resolved", reason="stopped"), first[-1].seq)
        assert [r.decision for r in resolutions(events)] == ["changes_requested", "rejected"]
        assert states(events) == ["stopped"]  # no `revising`: the revision never started
        assert len(calls) == first_run_calls
        assert live._runs == {} and live._decisions == {}  # pyright: ignore[reportPrivateUsage]
        assert live._slots == {"A": None} and live._kept == {}  # pyright: ignore[reportPrivateUsage]
        assert s.events()[-1].type == "change.resolved"
