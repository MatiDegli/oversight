"""Request changes over a real server on the live app, with the scripted model: the route and its
errors, a revision in the kept run, the slot, the brake per profile, and the paths of a revision
that does not complete. Stop's own tests are in test_stop_route.py."""

import asyncio
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from decision_helpers import observed_body
from live_helpers import (
    DEADLINE_S,
    BySlot,
    LiveServer,
    Stepped,
    error_code,
    is_type,
    live_server,
    live_settings,
    meta_settings,
    start,
)
from revise_helpers import (
    CHANGE,
    MODEL_TURNS_A,
    HeldRequests,
    last_status,
    metered,
    opened,
    ready,
    request,
    resolutions,
    revisable,
    revisable_b,
    scripted_a,
    states,
)

from oversight.agents import (
    MUSE_SPARK,
    AgentRun,
    LedgerState,
    Message,
    ModelReply,
    RunEnd,
    Script,
    ScriptedModel,
    ToolSchema,
    parse_script,
)
from oversight.api import ApiError
from oversight.app import LIMITS, LiveRun
from oversight.changes import ChangeManager, _revision
from oversight_contracts.commands import RequestChanges
from oversight_contracts.events import ChangeProposed
from oversight_contracts.graph import ParseError

pytestmark = pytest.mark.anyio

# --- A revision in the kept run -------------------------------------------------------------------


def fail_graph_context(s: LiveServer, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    if failure == "exception":

        def unavailable(*_args: Any) -> str:
            raise RuntimeError("graph lookup failed")

        monkeypatch.setattr(_revision, "graph_context", unavailable)
    else:
        manager = s.live._manager  # pyright: ignore[reportPrivateUsage]
        assert manager is not None
        rec = manager._agents["agent-1"]  # pyright: ignore[reportPrivateUsage]
        rec.head.graph.parse_errors.append(
            ParseError(path="shop/pricing.py", line=1, message="invalid syntax")
        )


@pytest.mark.parametrize("graph_failure", [None, "exception", "parse"])
async def test_a_revision_resumes_the_kept_run_with_the_comment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    graph_failure: str | None,
) -> None:
    model = scripted_a()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: model) as s,
    ):
        assert (await start(s)).status_code == 201
        first = await ready(s)
        comment = "  Say less.\nKeep this spacing.  "
        if graph_failure:
            fail_graph_context(s, monkeypatch, graph_failure)
        answer = await request(s, comment)
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
        assert (requested.revision, requested.comment) == (1, comment)
        # The model that built the first run answers the revision, and was sent the comment.
        message = str(model.requests[MODEL_TURNS_A][-1]["content"])
        assert comment in message
        heading = (
            "Functions one call away from your change; "
            "check whether the comment applies to them too"
        )
        if graph_failure:
            assert heading not in message
            assert "Graph context unavailable for change-1; sending comment alone:" in caplog.text
            reason = (
                "graph lookup failed"
                if graph_failure == "exception"
                else "parse errors in shop/pricing.py"
            )
            assert reason in caplog.text
        else:
            assert message.count(heading) == 1
            assert comment + "\n\n" + heading in message
            assert "apply_coupon (shop/pricing.py)" in message
            assert "Callers:" in message and "Siblings:" in message
        [proposal] = [e.payload for e in events if isinstance(e.payload, ChangeProposed)]
        assert proposal.change_id == "change-1" and proposal.summary == "Shortened the docstring."
        assert (
            await s.client.post(
                f"{CHANGE}/approve",
                json={
                    **observed_body(s, "change-1"),
                    "revision": max(1, observed_body(s, "change-1")["revision"]),
                },
            )
        ).status_code == 202
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
        assert (
            await s.client.post(f"{CHANGE}/reject", json=observed_body(s, "change-1"))
        ).status_code == 202
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
        refused = await s.client.post(
            f"{CHANGE}/request-changes", json={**observed_body(s), **body}
        )
        assert refused.status_code == 422, refused.text
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
    model = Stepped(parse_script(revisable()))
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: model) as s,
        HeldRequests(s.store, monkeypatch) as held,
    ):
        await start(s)
        # The first model call waits for a permit, so the change stays `working` until it is given.
        await s.until(is_type("agent.status", state="working"))
        working = await request(s)
        assert error_code(working) == (409, "wrong_state")
        assert "needs conflict or ready" in working.json()["error"]["message"]
        for _ in range(MODEL_TURNS_A + 2):  # the first run, then the revision's two calls
            model.permits.release()
        first = await ready(s)
        assert (await request(s)).status_code == 202
        await held.at["agent-1"].wait()  # deciding: its request is not in the log yet
        assert error_code(await request(s)) == (409, "wrong_state")
        assert error_code(await s.client.post("/agents/agent-1/stop")) == (409, "wrong_state")
        assert error_code(
            await s.client.post(
                f"{CHANGE}/approve",
                json={
                    **observed_body(s, "change-1"),
                    "revision": max(1, observed_body(s, "change-1")["revision"]),
                },
            )
        ) == (409, "wrong_state")
        held.open("agent-1")
        events = await ready(s, after=first[-1].seq)
        assert states(events) == ["revising", "ready_for_review"]
        assert (
            await s.client.post(
                f"{CHANGE}/approve",
                json={
                    **observed_body(s, "change-1"),
                    "revision": max(1, observed_body(s, "change-1")["revision"]),
                },
            )
        ).status_code == 202
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


async def test_the_brake_refuses_a_revision_of_the_metered_agent_only(tmp_path: Path) -> None:
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
        assert refused.status_code == 403, refused.text
        assert error_code(refused) == (403, "spend_brake")
        assert "above 90%" in refused.json()["error"]["message"]
        assert (await request(s, change="change-1")).status_code == 202
        await ready(s, "agent-1", after=first_ready["agent-1"])
        ledger.state = LedgerState(1.0, ())
        assert (await request(s, change="change-2")).status_code == 202
        events = await ready(s, "agent-2", after=first_ready["agent-2"])
        assert states(events, "agent-2") == ["revising", "ready_for_review"]


async def no_move(self: ChangeManager, rec: Any, comment: str) -> None:
    """The manager's answer when a redo's base cannot move: `None`, the change as it was."""
    return None


class BlockedRevision(ScriptedModel):
    """Plays its script for `after` calls, then waits for a cancel, which it records."""

    def __init__(self, script: Script, after: int) -> None:
        super().__init__(script)
        self.after, self.calls = after, 0
        self.entered, self.cancelled = asyncio.Event(), asyncio.Event()

    async def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolSchema]
    ) -> ModelReply:
        if self.calls >= self.after:
            self.entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        self.calls += 1
        return await super().complete(messages, tools)


async def test_a_request_taken_as_an_earlier_revision_ends_keeps_its_own_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The first request ends at once with no revision (the base could not move). The second is
    # taken from a done-callback on its decision, so before the first `_revise` resumes and
    # clears its records: it must leave the second's, or Stop finds no run to cancel.
    held, gate = asyncio.Event(), asyncio.Event()

    async def held_no_move(self: ChangeManager, rec: Any, comment: str) -> None:
        held.set()
        await gate.wait()
        return None

    model = BlockedRevision(parse_script(revisable()), after=MODEL_TURNS_A)
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: model) as s,
        opened(gate),
    ):
        live = s.live
        await start(s)
        first = await ready(s)
        with monkeypatch.context() as patch:
            patch.setattr(ChangeManager, "_request_changes", held_no_move)
            live.request_changes(
                "change-1", RequestChanges(comment="First.", **observed_body(s, "change-1"))
            )
        revise = live._runs["agent-1"]  # pyright: ignore[reportPrivateUsage]
        decision = live._decisions["agent-1"]  # pyright: ignore[reportPrivateUsage]
        # Registered at once, so before `revise` first waits on the decision: it runs first.
        decision.add_done_callback(
            lambda _: live.request_changes(
                "change-1", RequestChanges(comment="Second.", **observed_body(s, "change-1"))
            )
        )
        await held.wait()  # the decision waits, and `revise` waits on it
        gate.set()
        await asyncio.wait({revise})
        second_revise = live._runs.get("agent-1")  # pyright: ignore[reportPrivateUsage]
        second_decision = live._decisions.get("agent-1")  # pyright: ignore[reportPrivateUsage]
        assert second_revise not in (None, revise) and second_decision not in (None, decision)
        await model.entered.wait()  # the second revision is running
        assert (await s.client.post("/agents/agent-1/stop")).status_code == 202
        await s.until(is_type("change.resolved", reason="stopped"), first[-1].seq)
        assert model.cancelled.is_set()  # Stop reached the run it was meant for
        assert live._runs == {} and live._decisions == {}  # pyright: ignore[reportPrivateUsage]


async def test_a_request_that_moves_no_base_ends_with_no_run_and_the_change_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The manager answers `None` when a redo's base cannot move, after an `error` and with the
    # change as it was (its own tests cover why). `_revise` then resumes nothing, and the kept run
    # serves the next request.
    model = scripted_a()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: model) as s,
    ):
        live = s.live
        await start(s)
        first = await ready(s)
        calls = len(model.requests)
        with monkeypatch.context() as patch:
            patch.setattr(ChangeManager, "_request_changes", no_move)
            live.request_changes(
                "change-1",
                RequestChanges(comment="Shorter, please.", **observed_body(s, "change-1")),
            )
            await asyncio.wait({live._runs["agent-1"]})  # pyright: ignore[reportPrivateUsage]
        assert len(model.requests) == calls and not resolutions(s.events()[first[-1].seq :])
        assert not [e for e in s.events() if e.type == "error"]  # a quiet end, not a failed run
        assert live._runs == {} and live._decisions == {}  # pyright: ignore[reportPrivateUsage]
        assert live._slots == {"A": "agent-1"} and "agent-1" in live._kept  # pyright: ignore[reportPrivateUsage]
        assert (await request(s)).status_code == 202  # the change is reviewable again
        events = await ready(s, after=first[-1].seq)
        assert states(events) == ["revising", "ready_for_review"]


def test_the_commands_before_the_run_starts_are_wrong_state(tmp_path: Path) -> None:
    live = LiveRun(live_settings(tmp_path))
    for refused in (
        lambda: live.request_changes(
            "change-1", RequestChanges(comment="c", revision=1, head_commit="observed")
        ),
        lambda: live.stop("agent-1"),
    ):
        with pytest.raises(ApiError) as caught:
            refused()
        assert caught.value.code == "wrong_state"


async def test_a_revision_that_fails_is_reported_and_leaves_the_change_unproposed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, gate = asyncio.Event(), asyncio.Event()

    async def failing(self: AgentRun, comment: str, note: str | None) -> RunEnd:
        entered.set()
        await gate.wait()
        raise RuntimeError("the revision failed")

    model = scripted_a()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: model) as s,
        opened(gate),
    ):
        await start(s)
        await ready(s)
        monkeypatch.setattr(AgentRun, "revise", failing)
        assert (await request(s)).status_code == 202
        await entered.wait()  # held in the revision, so its task is still registered
        revise = s.live._runs["agent-1"]  # pyright: ignore[reportPrivateUsage]
        gate.set()
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
        assert halted.max_steps == 2 * LIMITS.max_steps and halted.step <= halted.max_steps
        assert any(e.type == "error" and e.payload.fatal for e in events)
        assert s.live.brake_reason() is not None
