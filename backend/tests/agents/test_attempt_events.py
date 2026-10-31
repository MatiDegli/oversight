"""Failed HTTP attempts through the author loop, using only an in-memory transport."""

import asyncio
from typing import cast

import httpx2 as httpx
import pytest
from agent_helpers import Recorder, make_run
from pydantic import TypeAdapter

from oversight.agents import RetryPolicy
from oversight.events import EVENT_TYPES, EventPayload
from oversight.workspace import Tree, Workspace
from oversight_contracts.events import ModelAttemptFailed, ServerEvent, UsageTick

from .test_client import COMPLETION, FakeApi, client, ok, status

pytestmark = pytest.mark.anyio
ADAPTER = TypeAdapter(ServerEvent)
Opened = tuple[Workspace, Tree]
FINISH = COMPLETION | {
    "choices": [
        {
            "index": 0,
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "finish",
                        "type": "function",
                        "function": {"name": "finish", "arguments": '{"summary":"Done"}'},
                    }
                ],
            },
        }
    ]
}


class ValidatingRecorder(Recorder):
    """Validate every emitted envelope against the v1.8 contracts before storing it."""

    async def publish(self, payload: EventPayload) -> None:
        ADAPTER.validate_python(
            {
                "seq": len(self.log) + 1,
                "ts": "2026-10-30T12:00:00.000Z",
                "type": EVENT_TYPES[type(payload)],
                "payload": payload.model_dump(mode="json"),
            }
        )
        await super().publish(payload)

    def failures(self) -> list[ModelAttemptFailed]:
        return cast(list[ModelAttemptFailed], self.of("ModelAttemptFailed"))


@pytest.mark.parametrize(
    "error,cause,http_status",
    [
        (httpx.ReadTimeout("timeout"), "timeout", None),
        (httpx.ConnectError("offline"), "connection", None),
        (status(429), "rate_limited", 429),
        (status(500), "server_error", 500),
        (status(599), "server_error", 599),
        (status(400), "refused", 400),
        (status(401), "refused", 401),
    ],
)
async def test_failure_cause_and_author_identity(
    small_tree: Opened, error: httpx.Response | Exception, cause: str, http_status: int | None
) -> None:
    recorder = ValidatingRecorder()
    api = FakeApi(error, ok(FINISH))
    sleeps: list[float] = []
    model = client(api, sleeps)
    try:
        end = await make_run(small_tree, model, recorder).run()
    finally:
        await model.aclose()
    [failure] = recorder.failures()
    assert failure.model_dump() == {
        "agent_id": "agent-1",
        "change_id": "change-1",
        "role": "agent",
        "model": "muse-spark",
        "provider": "meta",
        "attempt": 1,
        "max_attempts": 5,
        "cause": cause,
        "status": http_status,
        "retry_after_s": None,
        "wait_s": None if cause == "refused" else 1.0,
        "final": "not_retryable" if cause == "refused" else None,
    }
    assert end.state == ("halted" if cause == "refused" else "ready_for_review")
    assert sleeps == ([] if cause == "refused" else [1.0])
    ticks = cast(list[UsageTick], recorder.of("UsageTick"))
    # Transport failures still charge their one unknown receipt; HTTP errors do not.
    assert len(ticks) == (0 if cause == "refused" else 2 if http_status is None else 1)
    if http_status is None:
        assert ticks[0].input_tokens is None and ticks[0].cost_usd is None


async def test_retry_after_event_precedes_wait_then_success(small_tree: Opened) -> None:
    recorder = ValidatingRecorder()
    api = FakeApi(status(429, {"retry-after": "7"}), ok(FINISH))
    model = client(api, [])

    async def sleep(delay: float) -> None:
        [failure] = recorder.failures()
        assert failure.wait_s == delay == 7.0
        assert failure.retry_after_s == 7.0 and failure.final is None
        assert len(api.bodies) == 1

    model._sleep = sleep
    try:
        end = await make_run(small_tree, model, recorder).run()
    finally:
        await model.aclose()
    assert end.state == "ready_for_review" and len(api.bodies) == 2
    assert len(recorder.failures()) == 1
    assert recorder.kinds().index("ModelAttemptFailed") < recorder.kinds().index("UsageTick")


async def test_five_503s_emit_four_waits_and_final_attempts_used(small_tree: Opened) -> None:
    recorder = ValidatingRecorder()
    api = FakeApi(*(status(503) for _ in range(5)))
    sleeps: list[float] = []
    model = client(api, sleeps)
    try:
        end = await make_run(small_tree, model, recorder).run()
    finally:
        await model.aclose()
    failures = recorder.failures()
    assert [failure.attempt for failure in failures] == [1, 2, 3, 4, 5]
    assert all(failure.max_attempts == 5 and failure.status == 503 for failure in failures)
    assert [failure.wait_s for failure in failures] == [1.0, 2.0, 4.0, 8.0, None]
    assert [failure.final for failure in failures] == [None] * 4 + ["attempts_used"]
    assert sleeps == [1.0, 2.0, 4.0, 8.0] and len(api.bodies) == 5
    assert end.detail == "The model API failed: model API error after 5 attempts: HTTP 503."


async def test_retry_beyond_deadline_emits_final_without_wait(small_tree: Opened) -> None:
    recorder = ValidatingRecorder()
    api = FakeApi(status(429, {"retry-after": "75"}), ok(FINISH))
    sleeps: list[float] = []
    model = client(api, sleeps)
    try:
        end = await make_run(small_tree, model, recorder, max_wall_clock_s=10).run()
    finally:
        await model.aclose()
    [failure] = recorder.failures()
    assert failure.final == "deadline" and failure.wait_s is None
    assert failure.retry_after_s == 75 and failure.attempt == 1
    assert sleeps == [] and len(api.bodies) == 1
    assert (
        end.detail
        == "The model API failed: model API retry wait of 75 s exceeds the task deadline."
    )


@pytest.mark.parametrize("terminal", [False, True])
async def test_publication_failure_preserves_retry_and_final_error(
    small_tree: Opened, terminal: bool, caplog: pytest.LogCaptureFixture
) -> None:
    class BrokenPublisher(ValidatingRecorder):
        async def publish(self, payload: EventPayload) -> None:
            await super().publish(payload)
            if isinstance(payload, ModelAttemptFailed):
                raise RuntimeError("event store unavailable")

    recorder = BrokenPublisher()
    api = FakeApi(status(503), status(503) if terminal else ok(FINISH))
    sleeps: list[float] = []
    model = client(api, sleeps, retry=RetryPolicy(max_attempts=2))
    try:
        end = await make_run(small_tree, model, recorder).run()
    finally:
        await model.aclose()
    assert sleeps == [1.0] and len(api.bodies) == 2
    assert len(recorder.failures()) == (2 if terminal else 1)
    assert "Could not publish failed model attempt" in caplog.text
    assert end.detail == (
        "The model API failed: model API error after 2 attempts: HTTP 503." if terminal else None
    )
    assert end.state == ("halted" if terminal else "ready_for_review")


async def test_cancellation_in_observer_propagates_without_retry(small_tree: Opened) -> None:
    entered = asyncio.Event()

    class HeldPublisher(ValidatingRecorder):
        async def publish(self, payload: EventPayload) -> None:
            await super().publish(payload)
            if isinstance(payload, ModelAttemptFailed):
                entered.set()
                await asyncio.Event().wait()

    recorder = HeldPublisher()
    api = FakeApi(status(429), ok(FINISH))
    sleeps: list[float] = []
    model = client(api, sleeps)
    task = asyncio.create_task(make_run(small_tree, model, recorder).run())
    try:
        async with asyncio.timeout(5):
            await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await model.aclose()
    assert len(recorder.failures()) == 1 and len(api.bodies) == 1 and sleeps == []
    assert recorder.of("UsageTick") == []
