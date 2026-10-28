"""Production review transport with scripted authors and in-memory provider responses only."""

import asyncio
import contextvars
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from backend.tests.agents.test_review_call import completion
from decision_helpers import observed_target
from live_helpers import DEADLINE_S, meta_settings
from openai.types.chat import ChatCompletion
from revise_helpers import revisable, revisable_b

from oversight import runtime_review
from oversight.agents import (
    MUSE_SPARK,
    MUSE_SPARK_OPEN,
    LedgerError,
    ModelClient,
    OpenAIClient,
    ScriptedModel,
    _review_call,
    parse_script,
)
from oversight.app import LiveRun
from oversight.events import parse_needs_you
from oversight_contracts.commands import RequestChanges
from oversight_contracts.events import ModelProfile

from .test_cross_review_lifecycle import events, start, until

pytestmark = pytest.mark.anyio
MARKER: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "author_test_marker", default=None
)


@asynccontextmanager
async def production(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    create: Callable[..., Any],
    *,
    reviewer_profile: ModelProfile = MUSE_SPARK,
) -> AsyncIterator[LiveRun]:
    author_profile = MUSE_SPARK_OPEN if reviewer_profile == MUSE_SPARK else MUSE_SPARK
    settings = meta_settings(
        tmp_path,
        agent_model=author_profile.id,
        agent_model_b=reviewer_profile.id,
        baseten_api_key="test-key",
        cross_review=True,
        review_model=reviewer_profile.id,
    )
    model = OpenAIClient(
        reviewer_profile,
        base_url="https://review.test/v1",
        api_key="test",
        max_output_tokens=100,
    )
    model._create = create

    def configured(*args: Any) -> ModelClient:
        assert MARKER.get() is None  # the review task did not copy the author's Context
        return model

    monkeypatch.setattr(runtime_review, "configured_reviewer", configured)

    def factory(profile: ModelProfile, slot: str) -> ModelClient:
        return ScriptedModel(parse_script(revisable(), profile), profile)

    live = LiveRun(settings, factory)
    async with asyncio.timeout(DEADLINE_S):
        await live.startup()
        try:
            token = MARKER.set("author")
            try:
                yield live
            finally:
                MARKER.reset(token)
        finally:
            await live.shutdown()


async def test_production_fake_scripts_use_the_accounted_helper(tmp_path: Path) -> None:
    from live_helpers import two_fakes

    script = tmp_path / "review.json"
    script.write_text('{"task":"review","turns":[{"text":"VERDICT: accept"}]}')
    live = LiveRun(
        two_fakes(
            tmp_path,
            agent_model_b="fake-b",
            cross_review=True,
            review_model="fake-b",
            reviewer_fake_b_script=script,
        ),
        lambda p, slot: ScriptedModel(
            parse_script(revisable() if slot == "A" else revisable_b(), p), p
        ),
    )
    async with asyncio.timeout(DEADLINE_S):
        await live.startup()
        try:
            await start(live)
            reviewed = await until(live, "change.reviewed")
            [tick] = [
                e for e in events(live) if e.type == "usage.tick" and e.payload.role == "review"
            ]
            assert tick.seq < reviewed.seq and tick.payload.change_id == "change-1"
            assert tick.payload.cost_usd is None and tick.payload.model == "fake-scripted-b"
            assert live.ledger.state is not None and not live.ledger.state.unknown
        finally:
            await live.shutdown()


@pytest.mark.parametrize("reviewer_profile", [MUSE_SPARK, MUSE_SPARK_OPEN])
@pytest.mark.parametrize("empty", [False, True])
async def test_reviewer_provider_prices_and_persists_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reviewer_profile: ModelProfile, empty: bool
) -> None:
    before: list[Any] = []

    async def create(**params: Any) -> ChatCompletion:
        before.append(live.ledger.state)
        return completion(empty=empty).model_copy(update={"model": reviewer_profile.model})

    async with production(tmp_path, monkeypatch, create, reviewer_profile=reviewer_profile) as live:
        await start(live)
        reviewed = await until(live, "change.reviewed")
        [tick] = [e for e in events(live) if e.type == "usage.tick" and e.payload.role == "review"]
        assert tick.seq < reviewed.seq and tick.payload.change_id == "change-1"
        assert tick.payload.model == reviewer_profile.model
        assert tick.payload.input_tokens == 700 and tick.payload.output_tokens == 100
        assert live.ledger.state is not None
        if reviewer_profile.provider == "meta":
            assert tick.payload.cost_usd == pytest.approx(0.00009)
            assert live.ledger.state.spent_usd == pytest.approx(0.00009)
            assert live.brake_reason() is None
        else:
            # The scripted Meta author's unknowns exist; the Baseten review adds none.
            assert tick.payload.cost_usd is None and live.ledger.state == before[0]
            assert all(entry.model != reviewer_profile.model for entry in live.ledger.state.unknown)


@pytest.mark.parametrize("action", ["stop", "timeout", "connection"])
async def test_meta_unknown_is_drained_and_closes_brake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    entered = asyncio.Event()
    limits: list[asyncio.Timeout] = []

    def timeout_at(deadline: float) -> asyncio.Timeout:
        limit = asyncio.timeout_at(deadline)
        limits.append(limit)
        return limit

    monkeypatch.setattr(
        _review_call, "asyncio", SimpleNamespace(**(vars(asyncio) | {"timeout_at": timeout_at}))
    )

    async def create(**params: Any) -> ChatCompletion:
        entered.set()
        if action == "connection":
            import httpx2 as httpx
            import openai

            raise openai.APIConnectionError(request=httpx.Request("POST", "https://review.test"))
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async with production(tmp_path, monkeypatch, create) as live:
        await start(live)
        await entered.wait()
        review = live._reviews["change-1"]
        if action == "stop":
            live.stop("agent-1")
        else:
            limits[0].reschedule(asyncio.get_running_loop().time())
        await asyncio.gather(review, return_exceptions=True)
        [tick] = [
            e.payload for e in events(live) if e.type == "usage.tick" and e.payload.role == "review"
        ]
        assert tick.cost_usd is None and tick.input_tokens is None and tick.output_tokens is None
        assert live.ledger.state is not None and len(live.ledger.state.unknown) == 1
        assert live.ledger.read() == live.ledger.state
        assert live.brake_reason() is not None and not live._ledger_lost_agents
        assert not any(
            e.type in {"change.reviewed", "change.resolved"}
            and getattr(e.payload, "decided_by", None) == "cross_review"
            for e in events(live)
        )
        if action == "timeout":
            assert any(
                e.type == "error"
                and (n := parse_needs_you(e.payload.message))
                and n.code == "review_timeout"
                for e in events(live)
            )
        if action == "stop":
            assert review.cancelled() and not any(e.type == "change.reviewed" for e in events(live))


@pytest.mark.parametrize("failure", ["ledger", "os", "cancel_cause", "cancel_os"])
@pytest.mark.parametrize("cancel", ["none", "request", "write"])
async def test_review_ledger_failure_does_not_mark_or_halt_author(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, cancel: str
) -> None:
    entered, release, reporting, reported = (asyncio.Event() for _ in range(4))
    release_report = asyncio.Event()
    request_entered = asyncio.Event()

    async def create(**params: Any) -> ChatCompletion:
        request_entered.set()
        if cancel == "request":
            await asyncio.Event().wait()  # cancellation inside the paid request
        return completion()

    async with production(tmp_path, monkeypatch, create) as live:
        original_record = live.ledger.record

        async def failed_record(*args: Any) -> Any:
            entered.set()
            await release.wait()
            if failure in {"cancel_cause", "cancel_os"}:
                cause = (
                    LedgerError("injected") if failure == "cancel_cause" else OSError("injected")
                )
                raise asyncio.CancelledError from cause
            if failure == "os":
                return await original_record(*args)  # exercise the real durable ledger writer
            raise LedgerError("injected")

        def failed_write(state: Any) -> None:
            raise OSError("injected")

        if failure == "os":
            monkeypatch.setattr(live.ledger, "write", failed_write)
        monkeypatch.setattr(live.ledger, "record", failed_record)
        original_report = live._report

        async def held_report(source: Any, message: str, **kwargs: Any) -> None:
            note = parse_needs_you(message)
            if note and note.code == "ledger":
                reporting.set()
                await release_report.wait()
            await original_report(source, message, **kwargs)
            reported.set()

        monkeypatch.setattr(live, "_report", held_report)
        await start(live)
        await request_entered.wait()
        review = live._reviews["change-1"]
        if cancel == "request":
            review.cancel()
        await entered.wait()
        if cancel == "write":
            review.cancel()
            await asyncio.sleep(0)
        release.set()
        await reporting.wait()
        assert live._ledger_lost and not live._ledger_lost_agents
        assert live.brake_reason() is not None
        if cancel != "none":
            review.cancel()  # even repeated cancellation cannot lose the terminal error
            await asyncio.sleep(0)
            assert not review.done() and not reported.is_set()
        release_report.set()
        await asyncio.gather(review, return_exceptions=True)
        assert reported.is_set() and review.cancelled() == (cancel != "none")
        es = events(live)
        [error] = [e.payload for e in es if e.type == "error"]
        note = parse_needs_you(error.message)
        assert note and note.code == "ledger" and note.revision == 1
        assert error.change_id == "change-1" and error.agent_id == "agent-1" and not error.fatal
        assert "Cross-review" in note.reason and MUSE_SPARK.model in note.reason
        assert "provider=meta" in note.reason
        assert not any(e.type == "usage.tick" and e.payload.role == "review" for e in es)
        assert not any(e.type in {"change.reviewed", "change.resolved"} for e in es)
        assert not any(e.type == "agent.status" and e.payload.state == "halted" for e in es)
        assert live._manager is not None
        rec = live._manager._agents["agent-1"]
        assert rec.state == "ready" and rec.review_ready and not rec.person_acted
        assert live.ledger.read().spent_usd == 0
        monkeypatch.setattr(live.ledger, "record", original_record)
        # A non-metered author can still be resumed by the person and stopped normally.
        live.request_changes(
            "change-1",
            RequestChanges(comment="Shorten it.", **observed_target(live._manager, "change-1")),
        )
        await until(live, "change.proposed", revision=2)
        await asyncio.gather(*list(live._runs.values()))
        live.stop("agent-1")
        await until(live, "change.resolved", reason="stopped")
        assert not live._ledger_lost_agents
        assert not any(
            e.type == "agent.status"
            and e.payload.state == "halted"
            and "ledger" in (e.payload.detail or "")
            for e in events(live)
        )


async def test_invalidation_during_accounting_drains_tick_without_late_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    async def create(**params: Any) -> ChatCompletion:
        return completion()

    async with production(tmp_path, monkeypatch, create) as live:
        original = live.ledger.record

        async def held_record(*args: Any) -> Any:
            entered.set()
            await release.wait()
            return await original(*args)

        monkeypatch.setattr(live.ledger, "record", held_record)
        await start(live)
        await entered.wait()
        review = live._reviews["change-1"]
        try:
            live.stop("agent-1")
            await asyncio.sleep(0)
            assert not review.done()
        finally:
            release.set()
        await asyncio.gather(review, return_exceptions=True)
        [tick] = [
            e.payload for e in events(live) if e.type == "usage.tick" and e.payload.role == "review"
        ]
        assert tick.cost_usd == pytest.approx(0.00009) and review.cancelled()
        assert live.ledger.read().spent_usd == pytest.approx(0.00009)
        assert not any(e.type == "change.reviewed" for e in events(live))
