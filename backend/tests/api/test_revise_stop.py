"""Request changes and Stop over a real server on the live app, with the scripted model: the
routes and their errors, a revision in the kept run, the slot, the brake per profile, and Stop at
each point of a run."""

import asyncio
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from live_helpers import (
    DEADLINE_S,
    BySlot,
    GatedModel,
    Stepped,
    error_code,
    is_type,
    live_server,
    live_settings,
    meta_settings,
    start,
)
from revise_helpers import (
    MODEL_TURNS_A,
    STEPS_A,
    HeldRequests,
    metered,
    ready,
    revisable,
    revisable_b,
    scripted_a,
)

from oversight.agents import (
    MUSE_SPARK,
    AgentRun,
    LedgerState,
    Message,
    ModelReply,
    RunEnd,
    ScriptedModel,
    ToolSchema,
    parse_script,
)
from oversight.app import LIMITS
from oversight_contracts.events import AgentStatus, ChangeProposed, ChangeResolved

pytestmark = pytest.mark.anyio

CHANGE = "/changes/change-1"


async def request(s: Any, comment: str = "Shorter, please.", change: str = "change-1") -> Any:
    return await s.client.post(f"/changes/{change}/request-changes", json={"comment": comment})


def states(events: list[Any], agent: str = "agent-1") -> list[str]:
    return [
        e.payload.state for e in events if e.type == "agent.status" and e.payload.agent_id == agent
    ]


def resolutions(events: list[Any]) -> list[ChangeResolved]:
    return [e.payload for e in events if e.type == "change.resolved"]


def last_status(events: list[Any]) -> AgentStatus:
    return [e.payload for e in events if e.type == "agent.status"][-1]


# --- A revision in the kept run -------------------------------------------------------------------


async def test_a_revision_resumes_the_kept_run_with_the_comment(tmp_path: Path) -> None:
    model = scripted_a()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: model) as s,
    ):
        assert (await start(s)).status_code == 201
        first = await ready(s)
        answer = await request(s, "Say less.")
        assert (answer.status_code, answer.content) == (202, b"")
        events = await ready(s, after=first[-1].seq)
        # The request, then the revision from `revising`, then the same change proposed again.
        order = [
            e.payload.decision if e.type == "change.resolved" else e.payload.revision
            for e in events
            if e.type in ("change.resolved", "change.proposed")
        ]
        assert order == ["changes_requested", 2]
        assert states(events) == ["revising", "ready_for_review"]
        [requested] = resolutions(events)
        assert (requested.revision, requested.comment) == (1, "Say less.")
        # The model that built the first run answers the revision, and was sent the comment.
        assert "Say less." in str(model.requests[MODEL_TURNS_A][-1]["content"])
        [proposal] = [e.payload for e in events if isinstance(e.payload, ChangeProposed)]
        assert proposal.change_id == "change-1" and proposal.summary == "Shortened the docstring."
        assert (await s.client.post(f"{CHANGE}/approve")).status_code == 202
        await s.until(is_type("agent.status", state="done"))
        merged = resolutions(s.events())[-1]
        assert (merged.decision, merged.merge, merged.revision) == ("approved", "merged", 2)
        assert s.live._kept == {} and s.live._slots == {"A": None}  # pyright: ignore[reportPrivateUsage]


async def test_request_changes_keeps_the_slot(tmp_path: Path) -> None:
    # A resolution that is a request for changes does not free the slot, nor drop the kept run:
    # only a rejection or a merge does.
    model = scripted_a()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: model) as s,
    ):
        live = s.live
        await start(s)
        first = await ready(s)
        assert (await request(s)).status_code == 202
        events = await s.until(is_type("change.resolved"), first[-1].seq)
        assert resolutions(events)[0].decision == "changes_requested"
        assert live._slots == {"A": "agent-1"} and "agent-1" in live._kept  # pyright: ignore[reportPrivateUsage]
        await ready(s, after=events[-1].seq)
        assert live._slots == {"A": "agent-1"} and "agent-1" in live._kept  # pyright: ignore[reportPrivateUsage]
        assert error_code(await start(s)) == (409, "slot_busy")
        assert (await s.client.post(f"{CHANGE}/reject")).status_code == 202
        await s.until(is_type("agent.status", state="done"))
        assert live._slots == {"A": None} and live._kept == {}  # pyright: ignore[reportPrivateUsage]
        assert (await start(s)).status_code == 201


# --- Errors -------------------------------------------------------------------------------------


@pytest.mark.parametrize("body", [{}, {"comment": ""}, {"comment": "   "}, {"comment": 3}])
async def test_a_missing_or_blank_comment_is_invalid_request(
    tmp_path: Path, body: dict[str, Any]
) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        await start(s)
        await ready(s)
        refused = await s.client.post(f"{CHANGE}/request-changes", json=body)
        assert error_code(refused) == (422, "invalid_request")
        # Nothing was taken: the change is still reviewable.
        assert (await request(s)).status_code == 202


async def test_unknown_and_malformed_ids_are_not_found(tmp_path: Path) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        for change in ("change-9", "not-an-id"):
            assert error_code(await request(s, change=change)) == (404, "not_found")
        for agent in ("agent-9", "not-an-id"):
            assert error_code(await s.client.post(f"/agents/{agent}/stop")) == (404, "not_found")


async def test_request_changes_and_stop_need_a_state_that_takes_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = scripted_a()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: model) as s,
    ):
        held = HeldRequests(s.store, monkeypatch)
        await start(s)
        await s.until(is_type("agent.status", state="working"))
        # Working: only Stop takes a change that is not proposed yet; Request changes needs one.
        working = await request(s)
        assert error_code(working) == (409, "wrong_state")
        assert "needs conflict or ready" in working.json()["error"]["message"]
        first = await ready(s)
        assert (await request(s)).status_code == 202
        await held.at["agent-1"].wait()  # deciding: its request is not in the log yet
        assert error_code(await request(s)) == (409, "wrong_state")
        assert error_code(await s.client.post("/agents/agent-1/stop")) == (409, "wrong_state")
        assert error_code(await s.client.post(f"{CHANGE}/approve")) == (409, "wrong_state")
        held.open("agent-1")
        events = await ready(s, after=first[-1].seq)
        assert states(events) == ["revising", "ready_for_review"]
        assert (await s.client.post(f"{CHANGE}/approve")).status_code == 202
        await s.until(is_type("agent.status", state="done"))
        merged = await request(s)  # the kept run went with the merge
        assert error_code(merged) == (409, "wrong_state")
        assert "has no run to resume" in merged.json()["error"]["message"]
        assert error_code(await s.client.post("/agents/agent-1/stop")) == (409, "wrong_state")


async def test_a_suspect_main_refuses_a_request_but_not_a_stop(tmp_path: Path) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        await start(s)
        await ready(s)
        manager = s.live._manager  # pyright: ignore[reportPrivateUsage]
        assert manager is not None
        manager._suspect = True  # pyright: ignore[reportPrivateUsage]
        refused = await request(s)
        assert error_code(refused) == (409, "wrong_state")
        assert "main's state is unknown" in refused.json()["error"]["message"]
        assert (await s.client.post("/agents/agent-1/stop")).status_code == 202
        await s.until(is_type("agent.status", state="stopped"))


async def test_the_brake_refuses_a_revision_of_the_metered_agent_only(
    tmp_path: Path, slot_b_open: None
) -> None:
    # A runs the fake model and B Muse Spark, each with a revision turn. The brake closes after
    # both proposed: B's request is `spend_brake` and changes nothing, A's goes through, and B's
    # goes through once the brake opens again.
    settings = meta_settings(tmp_path, agent_model="fake", agent_model_b="muse-spark")
    models = {
        "A": ScriptedModel(parse_script(revisable())),
        "B": ScriptedModel(parse_script(revisable_b()), MUSE_SPARK),
    }
    clients = BySlot(lambda _profile, slot: models[slot])
    async with asyncio.timeout(DEADLINE_S), live_server(settings, clients) as s:
        await start(s, slot="A")
        await start(s, slot="B")
        first_ready = {agent: (await ready(s, agent))[-1].seq for agent in ("agent-1", "agent-2")}
        ledger = s.live.ledger
        ledger.state = LedgerState(145.0, ())  # 97% of the budget: past the block
        refused = await request(s, change="change-2")
        assert error_code(refused) == (403, "spend_brake")
        assert "above 90%" in refused.json()["error"]["message"]
        assert (await request(s, change="change-1")).status_code == 202
        await ready(s, "agent-1", after=first_ready["agent-1"])
        ledger.state = LedgerState(1.0, ())
        assert (await request(s, change="change-2")).status_code == 202
        events = await ready(s, "agent-2", after=first_ready["agent-2"])
        assert states(events, "agent-2") == ["revising", "ready_for_review"]


async def test_a_revision_that_fails_is_reported_and_leaves_the_change_unproposed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def failing(self: AgentRun, comment: str, note: str | None) -> RunEnd:
        raise RuntimeError("the revision failed")

    model = scripted_a()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: model) as s,
    ):
        await start(s)
        await ready(s)
        monkeypatch.setattr(AgentRun, "revise", failing)
        assert (await request(s)).status_code == 202
        revise = s.live._runs["agent-1"]  # pyright: ignore[reportPrivateUsage]
        await asyncio.wait({revise})
        [report] = [e.payload for e in s.events() if e.type == "error"]
        assert (report.source, report.fatal, report.agent_id) == ("agent", True, "agent-1")
        assert report.message == "agent-1's run failed: RuntimeError: the revision failed"
        manager = s.live._manager  # pyright: ignore[reportPrivateUsage]
        assert manager is not None
        assert manager._agents["agent-1"].state == "unproposed"  # pyright: ignore[reportPrivateUsage]


async def test_a_lost_ledger_halts_a_revision_with_the_agents_own_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The first run's calls were recorded. The revision's call cannot be: the run is stopped as a
    # shutdown stops it, and its card says halted, as for a first run.
    model = ScriptedModel(parse_script(metered(revisable())), MUSE_SPARK)
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(meta_settings(tmp_path), lambda _p, _s: model) as s,
    ):
        await start(s)
        first = await ready(s)

        def broken_fsync(fd: int) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(os, "fsync", broken_fsync)
        assert (await request(s)).status_code == 202
        events = await s.until(is_type("agent.status", state="halted"), first[-1].seq)
        monkeypatch.undo()
        halted = last_status(events)
        assert (halted.agent_id, halted.model) == ("agent-1", "muse-spark")
        assert halted.detail == "The ledger could not record a model call: the run was stopped."
        assert any(e.type == "error" and e.payload.fatal for e in events)
        assert s.live.brake_reason() is not None


# --- Stop -----------------------------------------------------------------------------------------


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
            expected_steps, budget = STEPS_A, 2 * LIMITS.max_steps
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
