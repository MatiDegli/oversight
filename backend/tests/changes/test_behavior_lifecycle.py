"""Revision retention, cancellation, shared deadlines and the unchanged off path."""

import asyncio
import os
import threading
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from changes_helpers import EDIT, PATH, TEMPLATE, Harness, start_harness
from decision_helpers import observed_target

from oversight.changes import BehaviorDiff
from oversight.changes._behavior import FunctionBehavior
from oversight.changes._behavior_scopes import InertScopes
from oversight_contracts.review import ChangeFacts

pytestmark = pytest.mark.anyio


@pytest.fixture
def session_c() -> bool:
    return True


async def test_target_parsing_runs_off_event_loop_thread(h: Harness, monkeypatch):
    loop_thread = threading.get_ident()
    parsing_threads = []
    original = InertScopes.unchanged

    def unchanged(scopes, base_node, head_node):
        parsing_threads.append(threading.get_ident())
        return original(scopes, base_node, head_node)

    monkeypatch.setattr(InertScopes, "unchanged", unchanged)
    h.manager._ctx.behavior_diff = True
    await h.run_agent(1, (PATH, "def value()", "from pathlib import Path\n\ndef value()"), EDIT)
    assert parsing_threads
    assert all(thread != loop_thread for thread in parsing_threads)


async def test_base_and_proposed_workers_have_distinct_pids(tmp_path, monkeypatch):
    monkeypatch.setitem(
        TEMPLATE,
        "shop/mod.py",
        "import os\nfrom pathlib import Path\n"
        "(Path(os.environ['HOME']).parent / 'base-pid').write_text(str(os.getpid()))\n\n"
        + TEMPLATE["shop/mod.py"],
    )
    h = await start_harness(tmp_path)
    base_pid = h.workspace.tmp_dir / "base-pid"
    proposed_pid = h.workspace.tmp_dir / "proposed-pid"
    try:
        await h.workspace.check_isolation()
        h.manager._ctx.behavior_diff = True
        await h.open(1)
        assert await h.edit(1, "shop/mod.py", "base-pid", "proposed-pid")
        assert await h.edit(1, *EDIT)
        original = h.workspace.run_behavior
        workers = []

        async def worker(tree, targets, *, timeout_s):
            # Proposal pytest imports the same module: retain only behavior-worker PIDs.
            pid_file = (
                base_pid
                if tree.base_commit == h.manager._agents["agent-1"].base_commit
                else proposed_pid
            )
            pid_file.unlink(missing_ok=True)
            observation = await original(tree, targets, timeout_s=timeout_s)
            workers.append(
                ("base" if pid_file == base_pid else "proposed", int(pid_file.read_text()))
            )
            return observation

        monkeypatch.setattr(h.workspace, "run_behavior", worker)
        assert await h.finish(1)
        result = next(
            r
            for r in h.manager._agents["agent-1"].behavior_diffs[1].functions
            if r.node_id == "shop.mod:value"
        )
        assert result.compared == result.differing == 1
        assert [side for side, _ in workers] == ["base", "proposed"] * 2
        pids = [pid for _, pid in workers]
        assert all(pid != os.getpid() for pid in pids)
        assert len(set(pids)) == len(pids)
        assert all(base != proposed for base, proposed in zip(pids[::2], pids[1::2], strict=True))
    finally:
        await h.close()


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
    before, after = h.payloads(ChangeFacts)
    assert before.behavior_diff is not None and after.behavior_diff is not None
    assert before.behavior_diff[0].differing_count == 1
    assert after.behavior_diff[0].differing_count == 0


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
    rec.behavior_diffs[0] = BehaviorDiff(
        revision=0,
        functions=(FunctionBehavior("STALE_BEHAVIOR_SENTINEL", compared=1, differing=1),),
    )
    rec.task = replace(rec.task, limits=rec.task.limits.model_copy(update={"test_timeout_s": 1}))
    pid_file = h.workspace.tmp_dir / "worker-pid"
    assert await h.edit(
        1,
        "shop/mod.py",
        "    return 1",
        "    return _block()\n\ndef _block():\n"
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
    result = next(r for r in rec.behavior_diffs[1].functions if r.node_id == "shop.mod:value")
    assert result.reason == ("interrupted" if cancel else "budget exhausted")
    assert all(0 < seconds <= 2 for seconds in timeouts)
    assert timeouts == sorted(timeouts, reverse=True)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert not list(h.workspace.tmp_dir.glob("base-probe-*"))
    assert not list(h.workspace.tmp_dir.glob("behavior-*"))
    assert "base-probe-" not in (await h.main_git("worktree", "list", "--porcelain")).stdout
    assert_unavailable_candidate_facts(h, rec, pid_file)


def assert_unavailable_candidate_facts(h: Harness, rec, pid_file):
    [facts] = h.payloads(ChangeFacts)
    execution = next(e for e in rec.executions.values() if e.phase == "proposal")
    assert (facts.change_id, facts.revision, facts.base_commit, facts.head_commit) == (
        rec.change_id,
        1,
        rec.base_commit,
        rec.head_commit,
    )
    assert facts.main_commit == rec.base_commit
    assert facts.candidate_tree == execution.tested_tree
    assert facts.head_commit == execution.tested_commit
    assert facts.behavior_diff is not None
    by_id = {item.node_id: item for item in facts.behavior_diff}
    assert set(by_id) == {item.node_id for item in rec.behavior_diffs[1].functions}
    public = by_id["shop.mod:value"]
    # The proposed side blocked before completing its first observation in this fixture.
    # Neither cancellation nor deadline exhaustion has a complete differing comparison.
    assert public.compared_count == public.differing_count == 0
    assert public.rows == [] and public.not_probed == "probe unavailable"
    assert all(item.differing_count == 0 for item in facts.behavior_diff)
    serialized = facts.model_dump_json()
    for private in ("STALE_BEHAVIOR_SENTINEL", "interrupted", "budget exhausted", str(pid_file)):
        assert private not in serialized


async def test_off_events_are_byte_identical_with_no_worker(tmp_path, monkeypatch):
    measured = BehaviorDiff(revision=1, functions=())
    probe = AsyncMock(return_value=measured)
    worker = AsyncMock(side_effect=AssertionError("disabled worker called"))
    monkeypatch.setattr("oversight.changes._proposal.probe_behavior", probe)
    monkeypatch.setattr("oversight.workspace.core.run_behavior_worker", worker)
    logs = []
    for number in range(3):
        path = tmp_path / str(number)
        path.mkdir()
        harness = await start_harness(path)
        try:
            harness.manager._ctx.behavior_diff = number == 0
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
            [facts] = harness.payloads(ChangeFacts)
            assert facts.behavior_diff == ([] if number == 0 else None)
            retained = harness.manager._agents["agent-1"].behavior_diffs
            assert retained == ({1: measured} if number == 0 else {})
            assert probe.await_count == 1
        finally:
            await harness.close()
    assert logs[1] == logs[2]
    probe.assert_awaited_once()
    worker.assert_not_awaited()


async def test_setup_errors_are_excluded_from_measured_differences(h: Harness):
    h.manager._ctx.behavior_diff = True
    await h.open(1)
    assert await h.edit(
        1,
        "shop/mod.py",
        "def value() -> int:\n    return 1",
        "class Uncopyable:\n"
        "    def __deepcopy__(self, memo): raise ValueError('setup')\n\n"
        "def value(x=None) -> int:\n    return 3\n\n"
        "value.__defaults__ = (Uncopyable(),)",
    )
    # The AST default is supported; import-time setup replaces it with an uncopyable object.
    assert await h.finish(1)
    result = next(
        r
        for r in h.manager._agents["agent-1"].behavior_diffs[1].functions
        if r.node_id == "shop.mod:value"
    )
    assert result.compared == result.differing == 0
    assert result.rows == ()
    assert result.reason == "unrepresentable input"
