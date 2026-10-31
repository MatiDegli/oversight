"""Loop-owned ledger readiness and notifications, including cancellation draining."""

import asyncio
import threading
from pathlib import Path

import pytest

from oversight.agents import LedgerError, LedgerFile, LedgerState, UnknownUsage

pytestmark = pytest.mark.anyio
ENTRY = UnknownUsage("run", "agent-1", "model", "2026-10-31T12:00:00Z")


async def test_load_record_loss_and_unsubscribe(tmp_path: Path) -> None:
    ledger = LedgerFile(tmp_path / "ledger.json")
    seen = []
    unsubscribe = ledger.watch(
        lambda: seen.append((ledger.change_revision, ledger.loaded, ledger.writing, ledger.state))
    )
    assert not ledger.loaded and not ledger.writing and ledger.change_revision == 0
    await ledger.load()
    await ledger.record(0.25, ENTRY)
    ledger.notify_loss()
    assert seen == [
        (1, True, False, LedgerState()),
        (2, True, False, LedgerState(0.25)),
        (3, True, False, LedgerState(0.25)),
    ]
    unsubscribe()
    unsubscribe()
    ledger.notify_loss()
    assert ledger.change_revision == 4 and len(seen) == 3


@pytest.mark.parametrize("operation", ["load", "record"])
async def test_failed_io_signals_loss(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    ledger = LedgerFile(tmp_path / "ledger.json")
    if operation == "record":
        await ledger.load()
    before = ledger.change_revision
    seen = []
    ledger.watch(lambda: seen.append((ledger.state, ledger.writing)))

    def fail(*_args: object) -> None:
        raise LedgerError("injected")

    monkeypatch.setattr(ledger, "read" if operation == "load" else "write", fail)
    with pytest.raises(LedgerError):
        if operation == "load":
            await ledger.load()
        else:
            await ledger.record(1, ENTRY)
    assert seen == [(None, False)] and ledger.change_revision == before + 1
    assert ledger.loaded is (operation == "record")


@pytest.mark.parametrize("fail", [False, True])
async def test_cancel_drain_notifies_after_writer_finishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    ledger = LedgerFile(tmp_path / "ledger.json")
    await ledger.load()
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = ledger.write
    seen = []
    ledger.watch(lambda: seen.append((ledger.change_revision, ledger.writing, ledger.state)))

    def held(state: LedgerState) -> None:
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        if fail:
            raise LedgerError("injected drain loss")
        original(state)

    monkeypatch.setattr(ledger, "write", held)
    pending = asyncio.create_task(ledger.record(0.25, ENTRY))
    try:
        await entered.wait()
        assert ledger.writing and ledger.state is None and ledger.loaded
        pending.cancel()
        await asyncio.sleep(0)
        assert not pending.done() and not seen
        pending.cancel()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await pending
    assert seen == [(2, False, None if fail else LedgerState(0.25))]
    assert isinstance(cancelled.value.__cause__, LedgerError) is fail


async def test_wait_changed_rechecks_and_unsubscribes(tmp_path: Path) -> None:
    ledger = LedgerFile(tmp_path / "ledger.json")
    deadline = asyncio.get_running_loop().time() + 1
    await ledger.load()
    # The change happened before the waiter subscribed.
    await ledger.wait_changed(0, deadline=deadline)
    pending = asyncio.create_task(ledger.wait_changed(ledger.change_revision, deadline=deadline))
    await asyncio.sleep(0)
    ledger.notify_loss()
    await pending


async def test_wait_deadline_does_not_cancel_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = LedgerFile(tmp_path / "ledger.json")
    await ledger.load()
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = ledger.write

    def held(state: LedgerState) -> None:
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(5)
        original(state)

    monkeypatch.setattr(ledger, "write", held)
    pending = asyncio.create_task(ledger.record(1, ENTRY))
    try:
        await entered.wait()
        deadline = loop.time() + 0.02
        with pytest.raises(TimeoutError):
            await ledger.wait_changed(ledger.change_revision, deadline=deadline)
        assert loop.time() >= deadline and not pending.done() and ledger.writing
        with pytest.raises(TimeoutError):
            await ledger.wait_changed(ledger.change_revision, deadline=loop.time() - 1)
    finally:
        release.set()
        await pending
    assert ledger.state == LedgerState(1) and not ledger.writing
