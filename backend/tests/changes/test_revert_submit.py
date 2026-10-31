"""Synchronous Revert admission, submitted outcomes and manager shutdown."""

import asyncio
import json
import os
import sys
from unittest.mock import AsyncMock

import pytest
from live_helpers import SERVER_EVENT
from test_revert import install_spend_evidence
from test_revert import system as system
from test_revert_preflight import repo as repo

from oversight.changes import ChangeManager, Person, RevertRefused, WrongState
from oversight.changes import _delivery as delivery
from oversight.workspace import run_process

pytestmark = pytest.mark.anyio


async def test_submit_success_event_then_delta(system, tmp_path):
    s = system
    await install_spend_evidence(s, tmp_path)
    task = s.service.submit(s.target, s.command, principal=Person)
    assert task not in s.manager._ctx.state.tasks
    assert (await task).result == "reverted"
    assert [event.type for event in s.events[-2:]] == ["revert.resolved", "graph.delta"]
    assert json.loads(s.events[-1].envelope)["payload"]["cause"] == "revert"
    for event in s.events:
        SERVER_EVENT.validate_json(event.envelope)


async def test_submit_duplicate_refused_before_any_await(system):
    s = system
    task = s.service.submit(s.target, s.command, principal=Person)
    try:
        with pytest.raises(RevertRefused, match="already reserved or consumed"):
            s.service.submit(s.target, s.command, principal=Person)
        assert len(s.events) == 2 and not task.done()
    finally:
        await task


async def test_submit_cancel_before_start_releases_key(system):
    s = system
    before = list(s.events)
    task = s.service.submit(s.target, s.command, principal=Person)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert s.events == before
    assert not s.service._reserved and not s.service._owners
    assert not s.service._attempts[-1].started
    assert (await s.service.submit(s.target, s.command, principal=Person)).result == "reverted"


async def test_submit_post_admission_refusal_publishes_one_error(system):
    s = system
    before = len(s.events)
    async with s.ws.locks(main=True):
        task = s.service.submit(s.target, s.command, principal=Person)
        await asyncio.sleep(0)  # The admitted body waits for main, then rechecks its integrity.
        assert s.service._attempts[-1].started
        s.manager._ctx.state.suspect = True
    with pytest.raises(RevertRefused, match="Main is suspended"):
        await task
    await s.service.aclose()
    assert not s.service._reserved and not s.service._owners
    reports = [SERVER_EVENT.validate_json(e.envelope) for e in s.events[before:]]
    assert len(reports) == 1 and reports[0].type == "error"
    report = reports[0].payload
    assert not report.fatal and report.source == "server"
    assert report.agent_id is None and report.change_id is None
    assert "change-1" in report.message and "Main is suspended" in report.message


async def test_port_shutdown_cancels_and_settles(system, monkeypatch, tmp_path):
    s = system
    ctx = s.manager._ctx
    manager = ChangeManager(
        s.ws,
        ctx.publish,
        main_graph=ctx.main_graph,
        main_snapshot=ctx.state.snapshot,
        run_id=s.store.run_id,
        revert_test_timeout_s=60,
    )
    await s.ws.check_isolation()
    entered, cleaned = asyncio.Event(), asyncio.Event()
    pid_file = tmp_path / "child.pid"
    source = f"import os,time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(60)"

    async def sleeping(argv, **kwargs):
        entered.set()
        try:
            return await run_process([sys.executable, "-c", source], **kwargs)
        finally:
            assert s.ws.operations.holds(main=True, agent_id="agent-1")
            cleaned.set()

    monkeypatch.setattr(s.ws._isolation, "select", lambda *args: sleeping)
    close_delivery = AsyncMock(wraps=delivery.aclose)
    monkeypatch.setattr(delivery, "aclose", close_delivery)
    manager.revert(s.target, s.command)
    port = manager._revert
    assert port is not None and len(port._tasks) == 1
    task = next(iter(port._tasks))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        async with asyncio.timeout(10):
            while not pid_file.exists() or not pid_file.read_text():  # noqa: ASYNC110
                await asyncio.sleep(0.005)
    finally:
        await asyncio.wait_for(manager.aclose(), 15)
    assert task.cancelled() and cleaned.is_set()
    close_delivery.assert_awaited_once_with(manager._ctx)
    outcome = json.loads(s.events[-1].envelope)["payload"]
    assert s.events[-1].type == "revert.resolved" and outcome["result"] == "needs_you"
    assert await s.head() == s.command.main_commit
    assert not manager.main_suspect and not port.service._reserved
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
    await s.clean()


async def test_manager_without_run_id_has_no_revert(system):
    s = system
    assert s.manager._revert is None
    with pytest.raises(WrongState, match="Revert is not available in this session"):
        s.manager.revert(s.target, s.command)
    assert len(s.events) == 2


async def test_shutdown_still_drains_delivery_after_unsettled_revert(system, monkeypatch):
    s, ctx = system, system.manager._ctx
    manager = ChangeManager(
        s.ws,
        ctx.publish,
        main_graph=ctx.main_graph,
        main_snapshot=ctx.state.snapshot,
        run_id=s.store.run_id,
        revert_test_timeout_s=60,
    )
    assert manager._revert is not None
    close = AsyncMock(side_effect=RevertRefused("unsettled", "Still settling"))
    monkeypatch.setattr(manager._revert.service, "aclose", close)
    close_cards = AsyncMock()
    close_delivery = AsyncMock(wraps=delivery.aclose)
    monkeypatch.setattr(manager, "close_review_cards", close_cards)
    monkeypatch.setattr(delivery, "aclose", close_delivery)
    with pytest.raises(RevertRefused, match="Still settling"):
        await manager.aclose()
    close_cards.assert_awaited_once()
    close_delivery.assert_awaited_once_with(manager._ctx)
