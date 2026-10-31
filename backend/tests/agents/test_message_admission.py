"""Concurrent admission preserves original-text limits across committed or failed history."""

import asyncio
from pathlib import Path

import pytest

from oversight.agents import Message, RunControl
from oversight.events import EventStore, read_store
from oversight.redaction import Redactor
from oversight_contracts.events import AgentMessageSent

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("texts,expected", [(["q"] * 33, 32), (["x" * 4096] * 3, 2)])
async def test_concurrent_admission_limits(texts: list[str], expected: int) -> None:
    control = RunControl()
    entered, release = asyncio.Event(), asyncio.Event()
    commits = 0

    async def record() -> None:
        nonlocal commits
        entered.set()
        await release.wait()
        commits += 1

    tasks = [asyncio.create_task(control.record_message(text, record)) for text in texts]
    try:
        await entered.wait()
        assert not control.claim_finish()
        release.set()
        assert sum(await asyncio.gather(*tasks)) == expected
        assert commits == expected
    finally:
        release.set()
        await asyncio.gather(*tasks)


@pytest.mark.parametrize("close", [False, True])
@pytest.mark.parametrize("fail", [False, True])
async def test_cancelled_admission_waits_for_commit_or_rollback(
    tmp_path: Path, close: bool, fail: bool
) -> None:
    control = RunControl()
    entered, release = asyncio.Event(), asyncio.Event()
    text = "x" * 4096
    path = tmp_path / "runs" / "cancelled.sqlite"
    async with asyncio.timeout(5), EventStore(path, "run-20261027-140312") as store:

        async def record() -> None:
            entered.set()
            await release.wait()
            if fail:
                raise OSError("synthetic disk failure")
            await store.publish(AgentMessageSent(agent_id="agent-1", text=text))

        task = asyncio.create_task(control.record_message(text, record))
        waiter = None
        try:
            await entered.wait()
            if close:
                control.close()
            task.cancel()
            await asyncio.sleep(0)  # Deliver cancellation while the write is still held.
            task.cancel()  # A second cancellation cannot release the commit barrier either.
            waiter = asyncio.create_task(control.wait_for_admission())
            await asyncio.sleep(0)
            assert not task.done() and not waiter.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            await waiter
            assert len(read_store(path)) == (0 if fail else 1)
            delivered: list[Message] = []
            control.deliver(delivered)
            assert delivered == ([] if fail or close else [{"role": "user", "content": text}])
            control.deliver(delivered)
            assert len(delivered) == (0 if fail or close else 1)
            fresh = control.for_revision()
            assert fresh.message("x" * 4096)
            assert fresh.message("y" * 4096) is fail
            assert not fresh.message("z")
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            if waiter is not None:
                await waiter


def test_redaction_is_idempotent_even_when_key_overlaps_the_marker() -> None:
    redact = Redactor("redacted")
    masked = redact("redacted and plain prose")
    assert masked == "[redacted] and plain prose"
    assert redact(masked) == masked
