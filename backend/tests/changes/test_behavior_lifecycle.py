"""Revision retention, cancellation, shared deadlines and the unchanged off path."""

import asyncio
import os
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from changes_helpers import EDIT, Harness, start_harness
from decision_helpers import observed_target

from oversight_contracts.review import ChangeFacts

pytestmark = pytest.mark.anyio


@pytest.fixture
def session_c() -> bool:
    return True


async def test_revisions_keep_independent_results(h: Harness):
    h.manager._ctx.behavior_diff = True
    await h.run_agent(1, EDIT)
    rec = h.manager._agents["agent-1"]
    first = rec.behavior_diffs[1]
    assert first.functions[0].differing == 1
    await h.manager.request_changes("change-1", "Again", **observed_target(h.manager, "change-1"))
    assert await h.edit(1, "shop/mod.py", "return 3", "return (1)")
    assert await h.finish(1)
    second = rec.behavior_diffs[2]
    assert rec.behavior_diffs[1] is first
    assert first.revision == 1 and second.revision == 2
    assert second.functions[0].compared == 1 and second.functions[0].differing == 0


async def test_removed_async_generator_and_import_side_reasons(h: Harness):
    h.manager._ctx.behavior_diff = True
    await h.open(1)
    assert await h.edit(1, "shop/mod.py", "def twice() -> int:\n    return value() + value()\n", "")
    assert await h.edit(1, "shop/mod.py", "def value() -> int:", "async def value() -> int:")
    assert await h.finish(1)
    by_id = {r.node_id: r for r in h.manager._agents["agent-1"].behavior_diffs[1].functions}
    assert by_id["shop.mod:twice"].reason == "removed (no proposed version)"
    assert by_id["shop.mod:value"].reason == "async or generator"


async def test_import_failure_does_not_become_raised_difference(h: Harness):
    h.manager._ctx.behavior_diff = True
    await h.open(1)
    assert await h.edit(1, "shop/mod.py", "def value()", "raise ImportError('side')\n\ndef value()")
    assert await h.edit(1, *EDIT)
    assert await h.finish(1)
    result = next(
        r
        for r in h.manager._agents["agent-1"].behavior_diffs[1].functions
        if r.node_id == "shop.mod:value"
    )
    assert result.compared == result.differing == 0
    assert result.reason == "import failed on proposed side"


@pytest.mark.parametrize("cancel", [False, True])
async def test_probe_deadline_and_cancel_clean_checkouts_and_real_process(h: Harness, cancel):
    h.manager._ctx.behavior_diff = True
    await h.open(1)
    rec = h.manager._agents["agent-1"]
    rec.task = replace(rec.task, limits=rec.task.limits.model_copy(update={"test_timeout_s": 1}))
    pid_file = h.workspace.tmp_dir / "worker-pid"
    assert await h.edit(
        1,
        "shop/mod.py",
        "    return 1",
        "    import signal, time, os\n    from pathlib import Path\n"
        "    signal.signal(signal.SIGALRM, signal.SIG_IGN)\n"
        "    (Path(os.environ['HOME']).parent / 'worker-pid').write_text(str(os.getpid()))\n"
        "    time.sleep(60)\n    return 3",
    )
    # Proposal pytest also calls value. Wait for the behavior worker specifically.
    original = h.workspace.run_behavior
    started = asyncio.Event()
    timeouts = []

    async def worker(tree, targets, *, timeout_s):
        timeouts.append(timeout_s)
        if tree.base_commit != rec.base_commit:
            pid_file.unlink(missing_ok=True)
            started.set()
        return await original(tree, targets, timeout_s=timeout_s)

    h.workspace.run_behavior = worker
    task = asyncio.create_task(h.finish(1))
    async with asyncio.timeout(10):
        await started.wait()
        for _ in range(300):
            if pid_file.exists():
                break
            await asyncio.sleep(0.01)
        pid = int(pid_file.read_text())
        if cancel:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            h.manager.run_ended("agent-1", None)
        else:
            assert await task
    [result] = rec.behavior_diffs[1].functions
    assert result.reason == ("interrupted" if cancel else "budget exhausted")
    assert all(0 < seconds <= 2 for seconds in timeouts)
    assert timeouts == sorted(timeouts, reverse=True)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert not list(h.workspace.tmp_dir.glob("base-probe-*"))
    assert not list(h.workspace.tmp_dir.glob("behavior-*"))
    assert "base-probe-" not in (await h.main_git("worktree", "list", "--porcelain")).stdout
    assert h.payloads(ChangeFacts)[-1].revision == 1


async def test_off_events_are_byte_identical_with_no_worker(tmp_path, monkeypatch):
    probe = AsyncMock(side_effect=AssertionError("disabled probe called"))
    worker = AsyncMock(side_effect=AssertionError("disabled worker called"))
    monkeypatch.setattr("oversight.changes._proposal.probe_behavior", probe)
    monkeypatch.setattr("oversight.workspace.core.run_behavior_worker", worker)
    logs = []
    for number in range(2):
        path = tmp_path / str(number)
        path.mkdir()
        harness = await start_harness(path)
        try:
            if number:
                harness.manager._ctx.behavior_diff = False
            original = harness.workspace.run_tests

            async def fixed_duration(
                tree, *, timeout_s, case_ids=(), isolation="best_effort", original=original
            ):
                run = await original(
                    tree, timeout_s=timeout_s, case_ids=case_ids, isolation=isolation
                )
                return replace(run, result=run.result.model_copy(update={"duration_ms": 0}))

            monkeypatch.setattr(harness.workspace, "run_tests", fixed_duration)
            await harness.run_agent(1, EDIT)
            logs.append([event.envelope for event in harness.log])
            assert not harness.manager._agents["agent-1"].behavior_diffs
        finally:
            await harness.close()
    assert logs[0] == logs[1]
    probe.assert_not_awaited()
    worker.assert_not_awaited()
