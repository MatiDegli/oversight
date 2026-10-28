"""PR3a runs real worktrees, the event store and the PR1 adapter, with scripted models only."""

import asyncio
import json
from collections import defaultdict
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from decision_helpers import observed_target
from live_helpers import DEADLINE_S, two_fakes
from pydantic import TypeAdapter
from revise_helpers import MODEL_TURNS_A, revisable, revisable_b

from oversight.agents import (
    Message,
    ModelClient,
    ModelReply,
    ReviewInputs,
    ReviewOutcome,
    Script,
    ScriptedModel,
    ToolSchema,
    parse_script,
    review_candidate,
)
from oversight.app import LiveRun
from oversight.events import EventRejected, parse_needs_you
from oversight_contracts.commands import RequestChanges, StartAgent
from oversight_contracts.common import Slot
from oversight_contracts.events import ModelProfile, ServerEvent
from oversight_contracts.review import ChangeTarget

pytestmark = pytest.mark.anyio
EVENT: TypeAdapter[Any] = TypeAdapter(ServerEvent)


class Reviewer:
    def __init__(self, *, held: bool = False, late: bool = False) -> None:
        self.calls: list[ChangeTarget] = []
        self.at: defaultdict[str, asyncio.Event] = defaultdict(asyncio.Event)
        self.release: defaultdict[str, asyncio.Event] = defaultdict(asyncio.Event)
        self.cancelled = asyncio.Event()
        self.held, self.late = held, late

    async def __call__(
        self,
        client: ModelClient,
        target: ChangeTarget,
        inputs: ReviewInputs,
        *,
        author: ModelProfile,
        author_reply_models: Sequence[str | None] = (),
    ) -> ReviewOutcome:
        self.calls.append(target)
        self.at[target.change_id].set()
        if self.held:
            try:
                await self.release[target.change_id].wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                if not self.late:
                    raise
        assert inputs.task == "Document the function."
        assert inputs.diff and inputs.tests.outcome == "passed"
        text = f"SHOULD-FIX {inputs.files[0]}:2: Shorten the docstring.\nVERDICT: request changes"
        return await review_candidate(
            ScriptedModel(
                Script("review", [ModelReply(text, [], model=client.profile.model)]), client.profile
            ),
            target,
            inputs,
            author=author,
            author_reply_models=author_reply_models,
        )


class BlockedRevision(ScriptedModel):
    def __init__(self) -> None:
        super().__init__(parse_script(revisable()))
        self.calls = 0
        self.entered, self.cancelled = asyncio.Event(), asyncio.Event()

    async def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema],
    ) -> ModelReply:
        if self.calls >= MODEL_TURNS_A:
            self.entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
        self.calls += 1
        return await super().complete(messages, tools)


@asynccontextmanager
async def running(
    tmp_path: Path,
    reviewer: Reviewer,
    *,
    enabled: bool = True,
    blocked: bool = False,
) -> AsyncIterator[LiveRun]:
    script = tmp_path / "review.json"
    script.write_text(json.dumps({"task": "review", "turns": [{"text": "VERDICT: accept"}]}))
    settings = two_fakes(
        tmp_path,
        agent_model_b="fake-b",
        cross_review=enabled,
        reviewer_fake_script=script,
        reviewer_fake_b_script=script,
    )

    def factory(profile: ModelProfile, slot: str) -> ModelClient:
        if blocked:
            return BlockedRevision()
        return ScriptedModel(
            parse_script(revisable() if slot == "A" else revisable_b(), profile), profile
        )

    live = LiveRun(settings, factory, reviewer=reviewer)
    async with asyncio.timeout(DEADLINE_S):
        await live.startup()
        try:
            yield live
        finally:
            await live.shutdown()


def events(live: LiveRun) -> list[Any]:
    return [EVENT.validate_json(e.envelope) for e in live.store._events]


async def until(live: LiveRun, kind: str, **values: Any) -> Any:
    async for stored in live.store.subscribe(0):
        e = EVENT.validate_json(stored.envelope)
        if e.type == kind and all(getattr(e.payload, k) == v for k, v in values.items()):
            return e
    raise AssertionError("store ended before expected event")


async def start(live: LiveRun, slot: Slot = "A") -> None:
    await live.start(StartAgent(task="Document the function.", slot=slot))


async def test_confirmed_order_and_one_automatic_revision(tmp_path: Path) -> None:
    reviewer = Reviewer(held=True)
    async with running(tmp_path, reviewer) as live:
        await start(live)
        await reviewer.at["change-1"].wait()
        manager = live._manager
        assert manager is not None
        rec = manager._agents["agent-1"]
        assert rec.state == "ready" and rec.review_ready and not live._runs
        first_review = live._reviews["change-1"]
        reviewer.release["change-1"].set()
        await first_review
        assert "agent-1" in live._runs and "agent-1" in live._decisions
        await until(live, "change.proposed", revision=2)
        await until(live, "error")  # second adverse review exhausts the single round
        await asyncio.gather(*list(live._reviews.values()), *list(live._runs.values()))
        es = events(live)
        order = [e.type for e in es if e.type.startswith("change.") and e.type != "change.updated"]
        assert order == [
            "change.proposed",
            "change.facts_recorded",
            "change.reviewed",
            "change.resolved",
            "change.proposed",
            "change.facts_recorded",
            "change.reviewed",
        ]
        for reviewed_event in (e for e in es if e.type == "change.reviewed"):
            revision = reviewed_event.payload.revision
            proposed = next(
                e for e in es if e.type == "change.proposed" and e.payload.revision == revision
            )
            facts = next(
                e
                for e in es
                if e.type == "change.facts_recorded" and e.payload.revision == revision
            )
            suite = next(
                e
                for e in es
                if e.type == "tests.executed"
                and e.payload.phase == "proposal"
                and e.payload.revision == revision
            )
            assert suite.seq < proposed.seq < facts.seq < reviewed_event.seq
        resolved = next(e for e in es if e.type == "change.resolved")
        reviewed = next(e for e in es if e.type == "change.reviewed")
        assert (
            resolved.payload.decided_by == "cross_review"
            and resolved.payload.review_seq == reviewed.seq
        )
        assert rec.state == "ready" and rec.review_consumed and not rec.person_acted
        assert live._kept["agent-1"].max_steps == 60
        note = parse_needs_you(es[-1].payload.message)
        assert note and note.code == "round_exhausted"
        assert [e.payload.state for e in es if e.type == "agent.status"] == [
            "starting",
            "working",
            "ready_for_review",
            "revising",
            "ready_for_review",
        ]
        assert (
            len([e for e in es if e.type == "tests.executed" and e.payload.phase == "proposal"])
            == 2
        )


async def test_main_moved_by_a_preserves_b_review(tmp_path: Path) -> None:
    reviewer = Reviewer(held=True)
    async with running(tmp_path, reviewer) as live:
        await start(live, "A")
        await start(live, "B")
        await reviewer.at["change-1"].wait()
        await reviewer.at["change-2"].wait()
        manager = live._manager
        assert manager is not None
        target = next(t for t in reviewer.calls if t.change_id == "change-2")
        decision = manager.approve("change-1", None, **observed_target(manager, "change-1"))
        await decision
        assert reviewer.cancelled.is_set()  # A's human approval cancels only A's review
        assert manager.snapshot.version != target.main_commit
        reviewer.release["change-2"].set()
        result = await until(live, "change.resolved", change_id="change-2")
        assert result.payload.decided_by == "cross_review"
        assert manager._agents["agent-2"].base_commit == target.base_commit


@pytest.mark.parametrize("late", [False, True])
async def test_stop_admission_cancels_review_and_drops_late_result(
    tmp_path: Path, late: bool
) -> None:
    reviewer = Reviewer(held=True, late=late)
    async with running(tmp_path, reviewer) as live:
        await start(live)
        await reviewer.at["change-1"].wait()
        review = live._reviews["change-1"]
        live.stop("agent-1")
        assert review.cancelling()  # synchronous, before Stop's task starts
        await until(live, "change.resolved", reason="stopped")
        await asyncio.gather(review, return_exceptions=True)
        assert reviewer.cancelled.is_set() and not live._reviews
        assert not any(e.type == "change.reviewed" for e in events(live))


async def test_stop_during_automatic_revision_reaches_kept_run(tmp_path: Path) -> None:
    reviewer = Reviewer()
    async with running(tmp_path, reviewer, blocked=True) as live:
        await start(live)
        await until(live, "agent.status", state="revising")
        model = live._kept["agent-1"]._model
        assert isinstance(model, BlockedRevision)
        await model.entered.wait()
        live.stop("agent-1")
        await until(live, "change.resolved", reason="stopped")
        assert model.cancelled.is_set() and not live._runs and not live._decisions
        assert len(reviewer.calls) == 1


async def test_shutdown_drains_reviews_before_stopping_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewer = Reviewer(held=True, late=True)
    async with running(tmp_path, reviewer) as live:
        await start(live)
        await reviewer.at["change-1"].wait()
        real = live._stop_runs

        async def stop_runs() -> None:
            assert live._closing and not live._reviews
            assert reviewer.cancelled.is_set()
            await real()

        monkeypatch.setattr(live, "_stop_runs", stop_runs)
    assert not any(e.type in ("change.reviewed", "change.resolved") for e in events(live))


async def test_halted_author_gets_no_review_or_new_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oversight.agents import AgentRun, ModelAPIError

    async def failed_loop(self: AgentRun, messages: Any) -> str:
        raise ModelAPIError("scripted failure")

    monkeypatch.setattr(AgentRun, "_loop", failed_loop)
    reviewer = Reviewer()
    async with running(tmp_path, reviewer) as live:
        await start(live)
        await asyncio.gather(*list(live._runs.values()))
        await asyncio.gather(*list(live._reviews.values()))
        error = [e for e in events(live) if e.type == "error"][-1]
        note = parse_needs_you(error.payload.message)
        assert note and note.code == "author_halted"
        assert not reviewer.calls and live._kept["agent-1"].max_steps == 30
        assert live._manager is not None
        assert live._manager._agents["agent-1"].state == "ready"
        assert not any(e.type in ("change.reviewed", "change.resolved") for e in events(live))


async def test_no_decision_without_confirmed_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewer = Reviewer(held=True)
    async with running(tmp_path, reviewer) as live:
        await start(live)
        await reviewer.at["change-1"].wait()
        real = live.store.publish

        async def rejected(payload: Any) -> Any:
            if payload.__class__.__name__ == "CrossReview":
                raise EventRejected("injected review delivery failure")
            return await real(payload)

        monkeypatch.setattr(live.store, "publish", rejected)
        review = live._reviews["change-1"]
        reviewer.release["change-1"].set()
        await review
        assert not live._runs and not live._decisions
        assert not any(e.type == "change.resolved" for e in events(live))


async def test_switch_off_preserves_bytes_and_manual_request(tmp_path: Path) -> None:
    reviewer = Reviewer()
    async with running(tmp_path, reviewer, enabled=False) as live:
        await start(live)
        await asyncio.gather(*list(live._runs.values()))
        before = [e.envelope for e in live.store._events]
        await asyncio.sleep(0)
        assert [e.envelope for e in live.store._events] == before
        assert not live._reviews and not live._review_pending and not reviewer.calls
        live.request_changes(
            "change-1",
            RequestChanges(
                comment="Shorter, please.",
                **observed_target(live._manager, "change-1"),
            ),
        )
        await until(live, "change.proposed", revision=2)
        assert not reviewer.calls
        assert (
            next(e for e in events(live) if e.type == "change.resolved").payload.decided_by
            == "person"
        )


@pytest.mark.parametrize("closing", [False, True])
async def test_cancelled_probes_publish_partial_facts_without_arming_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    closing: bool,
) -> None:
    from oversight.changes import _proposal

    entered = asyncio.Event()

    async def probes(*args: Any, **kwargs: Any) -> Any:
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(_proposal, "probe_added_tests", probes)
    reviewer = Reviewer()
    async with running(tmp_path, reviewer) as live:
        await start(live)
        await entered.wait()
        if closing:
            await live.shutdown()
        else:
            live.stop("agent-1")
            await until(live, "change.resolved", reason="stopped")
        assert any(e.type == "change.facts_recorded" for e in events(live))
        assert not live._review_pending and not live._reviews and not reviewer.calls


async def test_human_save_never_arms_a_review(tmp_path: Path) -> None:
    reviewer = Reviewer(held=True, late=True)
    async with running(tmp_path, reviewer) as live:
        await start(live)
        await reviewer.at["change-1"].wait()
        review = live._reviews["change-1"]
        manager = live._manager
        assert manager is not None
        read = await manager.read_file("agent-1", "shop/pricing.py")
        await manager.save_for_review("agent-1", read.path, read.content + "# person\n", read.hash)
        await review
        assert len(reviewer.calls) == 1 and not live._review_pending
        assert manager._agents["agent-1"].revision == 2
        assert not any(e.type == "change.reviewed" for e in events(live))


async def test_ask_me_preserves_validated_level2_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from oversight.app import ReplayConfig, create_app
    from oversight.events import EventStore, read_jsonl, write_jsonl
    from oversight.settings import REPO_ROOT

    source = REPO_ROOT / "fixtures/events/scripted-level2.jsonl"
    reviewer = Reviewer()
    stores: list[EventStore] = []
    real = EventStore.start

    async def started(store: EventStore) -> None:
        stores.append(store)
        await real(store)

    monkeypatch.setattr(EventStore, "start", started)
    app = create_app(
        two_fakes(tmp_path),
        ReplayConfig(source, allow_fake_provider=True, speed=0),
        reviewer=reviewer,
    )
    async with app.router.lifespan_context(app):
        store = stores[0]
        async for event in store.subscribe(0):
            if event.seq == len(source.read_bytes().splitlines()):
                break
        out = tmp_path / "replayed.jsonl"
        write_jsonl(out, store._events)
        # Serialization includes additive contract defaults; the original recording stays intact.
        expected = b"".join(
            event.model_dump_json().encode() + b"\n" for event in read_jsonl(source)
        )
        assert out.read_bytes() == expected
    assert not reviewer.calls


async def test_cancelled_shutdown_still_drains_review_cleanup(tmp_path: Path) -> None:
    reviewer = Reviewer(held=True)
    cleaning, release = asyncio.Event(), asyncio.Event()

    async def held(*args: Any, **kwargs: Any) -> Any:
        reviewer.at["change-1"].set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleaning.set()
            await release.wait()
            raise

    async with running(tmp_path, reviewer) as live:
        live._reviewer = held
        await start(live)
        await reviewer.at["change-1"].wait()
        shutdown = asyncio.create_task(live.shutdown())
        try:
            await cleaning.wait()
            shutdown.cancel()
            await asyncio.sleep(0)
            assert not shutdown.done() and live._closing
        finally:
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await shutdown
        assert not live._reviews and not live._runs and not live._decisions
