"""Concurrent admission preserves original-text limits across committed or failed history."""

import asyncio

import pytest

from oversight.agents import RunControl
from oversight.redaction import Redactor

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
async def test_cancelled_admission_cannot_deliver_and_refunds_budget(close: bool) -> None:
    control = RunControl()
    entered = asyncio.Event()

    async def record() -> None:
        entered.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(control.record_message("x" * 4096, record))
    await entered.wait()
    if close:
        control.close()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not control.pending_messages
    fresh = control.for_revision()
    assert fresh.message("x" * 4096) and fresh.message("y" * 4096)
    assert not fresh.message("z")


def test_redaction_is_idempotent_even_when_key_overlaps_the_marker() -> None:
    redact = Redactor("redacted")
    masked = redact("redacted and plain prose")
    assert masked == "[redacted] and plain prose"
    assert redact(masked) == masked
