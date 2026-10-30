"""Real runtime publishers with scripted authors and in-memory model HTTP responses."""

import asyncio
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from backend.tests.api.test_cross_review_lifecycle import events, start, until
from backend.tests.api.test_decision_accounting import runtime
from decision_client_helpers import INPUT, body
from decision_client_helpers import FakeApi as DecisionApi
from decision_client_helpers import client as decision_client
from live_helpers import DEADLINE_S, meta_settings
from revise_helpers import revisable

from oversight import runtime_decisions, runtime_review
from oversight.agents import ScriptedModel, parse_script
from oversight.app import LiveRun
from oversight.events import EventPayload
from oversight_contracts.events import ModelAttemptFailed

from .test_client import FakeApi, client, ok, status
from .test_review_call import completion

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("publish_fails", [False, True])
async def test_runtime_review_publishes_failed_attempt_with_author_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, publish_fails: bool
) -> None:
    api = FakeApi(status(503), ok(completion().model_dump()))
    sleeps: list[float] = []
    model = client(api, sleeps)
    monkeypatch.setattr(runtime_review, "configured_reviewer", lambda *args: model)
    settings = meta_settings(
        tmp_path,
        agent_model="muse-spark-open",
        agent_model_b="muse-spark",
        baseten_api_key="test",
        cross_review=True,
        review_model="muse-spark",
    )
    live = LiveRun(settings, lambda p, slot: ScriptedModel(parse_script(revisable(), p), p))
    attempts: list[ModelAttemptFailed] = []
    original = live._publish

    async def publish(payload: EventPayload) -> object:
        if isinstance(payload, ModelAttemptFailed):
            attempts.append(payload)
            if publish_fails:
                raise RuntimeError("attempt publication unavailable")
        return await original(payload)

    monkeypatch.setattr(live, "_publish", publish)
    async with asyncio.timeout(DEADLINE_S):
        await live.startup()
        try:
            await start(live)
            reviewed = await until(live, "change.reviewed")
            [tick] = [
                e for e in events(live) if e.type == "usage.tick" and e.payload.role == "review"
            ]
            [failure] = attempts
            assert failure.agent_id == tick.payload.agent_id == "agent-1"
            assert failure.change_id == tick.payload.change_id == reviewed.payload.change_id
            assert failure.role == "review" and failure.cause == "server_error"
            assert failure.status == 503 and failure.wait_s == 1 and failure.final is None
            stored = [e for e in events(live) if e.type == "model.attempt_failed"]
            assert len(stored) == (0 if publish_fails else 1)
            if stored:
                assert stored[0].payload == failure and stored[0].seq < tick.seq < reviewed.seq
            assert reviewed.payload.verdict == "clean" and reviewed.payload.complete
            assert len(api.bodies) == 2 and sleeps == [1.0]
        finally:
            await live.shutdown()
            await model.aclose()


@pytest.mark.parametrize("publish_fails", [False, True])
async def test_runtime_decision_publishes_failed_attempt_with_author_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, publish_fails: bool
) -> None:
    api = DecisionApi(status(503), ok(body()))
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    model = decision_client(api, sleep=sleep, jitter=lambda: 1.0)
    input = replace(
        INPUT, agent_id="agent-7", target=INPUT.target.model_copy(update={"change_id": "change-7"})
    )
    async with runtime(tmp_path, monkeypatch, api) as live:
        monkeypatch.setattr(runtime_decisions, "configured_decision", lambda _: model)
        # Script admission/receipts to permit the second send. The real unknown-usage brake
        # remains covered by test_http_error_is_recorded_and_unknown_usage_prevents_retry.
        bank = live.difference_decisions.accounting
        for method in ("load", "admit", "record"):
            monkeypatch.setattr(bank, method, AsyncMock())
        attempts: list[ModelAttemptFailed] = []
        original = live._publish

        async def publish(payload: EventPayload) -> object:
            if isinstance(payload, ModelAttemptFailed):
                attempts.append(payload)
                if publish_fails:
                    raise RuntimeError("attempt publication unavailable")
            return await original(payload)

        monkeypatch.setattr(live, "_publish", publish)
        outcome = await live.difference_decisions.classify_difference(
            input, deadline=asyncio.get_running_loop().time() + 30
        )
        [failure] = attempts
        assert failure.agent_id == input.agent_id and failure.change_id == input.target.change_id
        assert failure.role == "review" and failure.cause == "server_error"
        assert failure.status == 503 and failure.wait_s == 1 and failure.final is None
        stored = [e for e in events(live) if e.type == "model.attempt_failed"]
        assert len(stored) == (0 if publish_fails else 1)
        if stored:
            assert stored[0].payload == failure
        assert outcome.choice == "asked_for" and outcome.unavailable is None
        assert outcome.target == input.target and outcome.node_id == input.difference.node_id
        assert len(api.bodies) == 2 and sleeps == [1.0]
