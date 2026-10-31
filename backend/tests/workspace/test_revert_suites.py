"""Inverse suite ownership, deadlines, cancellation and guards."""

import asyncio
import sys
import textwrap
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from backend.tests.workspace.workspace_helpers import (
    MESSAGE,
    candidate,
)
from backend.tests.workspace.workspace_helpers import api as api
from backend.tests.workspace.workspace_helpers import pytestmark as pytestmark

from oversight.workspace import SuiteRun, run_process
from oversight.workspace.suite import SuiteInterrupted
from oversight_contracts.events import SuiteResult


async def test_reentrant_required_suite(workspace, api, monkeypatch):
    h, t = await candidate(workspace, api)
    suite = AsyncMock(
        return_value=SuiteRun(
            SuiteResult(
                outcome="passed", passed=1, failed=0, skipped=0, duration_ms=1, failed_tests=[]
            ),
            "",
            problems=(),
        )
    )
    monkeypatch.setattr("oversight.workspace.suite_guard.run_suite", suite)
    await workspace.check_isolation()
    async with workspace.locks(main=True, agent_id="agent-1"):
        async with api.prepare_inverse(
            workspace,
            agent_id="agent-1",
            main_commit=h,
            inverse_tree=t,
            message=MESSAGE,
            timeout_s=5,
        ) as item:
            run = await workspace.run_tests(item.tree, timeout_s=5, isolation="required")
            assert run.tested_commit == item.inverse_commit
        assert api.inverse_suite_problem(run, tree=t, cleaned=item.cleaned) is None


async def suite_signal(task, gate, timeout_s: float = 60):
    signal = asyncio.create_task(gate.wait_signal())
    try:
        done, _ = await asyncio.wait(
            {signal, task}, timeout=timeout_s, return_when=asyncio.FIRST_COMPLETED
        )
        if task in done:
            await task  # Surface setup/isolation failures instead of waiting for an absent child.
            pytest.fail("suite owner ended before signaling")
        if signal not in done:
            pytest.fail("suite child did not signal within its deadline")
        return int(await signal)
    finally:
        signal.cancel()
        # Gate releases and joins its FIFO worker before cancellation escapes.
        await asyncio.gather(signal, return_exceptions=True)


@pytest.mark.parametrize("owner_fails", [False, True])
async def test_suite_signal_watches_owner_and_deadline(gate_factory, owner_fails):
    gate = gate_factory("absent-suite")
    error = OSError("suite setup failed")

    async def owner():
        if owner_fails:
            raise error
        await asyncio.Event().wait()

    task = asyncio.create_task(owner())
    try:
        async with asyncio.timeout(5):
            with pytest.raises(OSError if owner_fails else pytest.fail.Exception) as caught:
                await suite_signal(task, gate, timeout_s=0.05)
            if owner_fails:
                assert caught.value is error
    finally:
        task.cancel()
        with suppress(OSError, asyncio.CancelledError):
            await task


@pytest.mark.parametrize("operation", ["wait_signal", "open_gate"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_gate_absent_peer_releases_executor(tmp_path, operation, cancel):
    # A separate interpreter makes a stuck executor join an ordinary, bounded failure.
    helper_dir = str(Path(__file__).parent)
    script = f"import sys; sys.path.insert(0, {helper_dir!r})\n" + textwrap.dedent(f"""
        import asyncio
        from pathlib import Path
        from conftest import Gate

        async def check():
            gate = Gate(Path.cwd(), "absent")
            started = asyncio.Event()
            loop = asyncio.get_running_loop()
            to_thread = asyncio.to_thread

            async def observe(fn, *args, **kwargs):
                def worker():
                    loop.call_soon_threadsafe(started.set)
                    return fn(*args, **kwargs)
                return await to_thread(worker)

            asyncio.to_thread = observe
            wait = gate.{operation}() if {cancel!r} else gate.{operation}(timeout_s=0.05)
            task = asyncio.create_task(wait)
            if {cancel!r}:
                await started.wait()
                task.cancel()
            try:
                await task
            except (asyncio.CancelledError if {cancel!r} else TimeoutError):
                pass
            else:
                raise AssertionError("missing peer completed")

        asyncio.run(check())
        print("executor closed")
    """)
    result = await run_process([sys.executable, "-c", script], cwd=tmp_path, timeout_s=15)
    assert not result.timed_out, result.stderr
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "executor closed"


async def test_cancel_sleeping_child_and_repeated_cleanup_cancellation(
    workspace, api, monkeypatch, gate_factory, wait_gone
):
    h, t = await candidate(workspace, api)
    cleaning = asyncio.Event()
    gate = gate_factory("inverse-suite")
    await workspace.check_isolation()
    partial = SuiteRun(
        SuiteResult(outcome="error", passed=1, failed=0, skipped=0, duration_ms=1, failed_tests=[]),
        "partial suite output",
    )

    async def sleeping_suite(**kwargs):
        try:
            await run_process(
                [
                    sys.executable,
                    "-c",
                    "import os, time; " + gate.child_signal + "; time.sleep(60)",
                ],
                cwd=kwargs["tree_root"],
                timeout_s=30,
            )
        except asyncio.CancelledError:
            raise SuiteInterrupted(partial) from None
        raise AssertionError("sleeping suite completed")

    monkeypatch.setattr("oversight.workspace.suite_guard.run_suite", sleeping_suite)
    real = workspace.git
    seen = []

    async def observe(tree, *args, **kwargs):
        if args[:2] == ("worktree", "remove"):
            cleaning.set()
            await asyncio.sleep(0.03)
        return await real(tree, *args, **kwargs)

    monkeypatch.setattr(workspace, "git", observe)

    async def owner():
        async with (
            workspace.locks(main=True, agent_id="agent-1"),
            api.prepare_inverse(
                workspace,
                agent_id="agent-1",
                main_commit=h,
                inverse_tree=t,
                message=MESSAGE,
                timeout_s=5,
            ) as item,
        ):
            seen.append(item)
            await workspace.run_tests(item.tree, timeout_s=30, isolation="required")

    task = asyncio.create_task(owner())
    try:
        pid = await suite_signal(task, gate)
        task.cancel()
        async with asyncio.timeout(60):
            await cleaning.wait()
            task.cancel()
            with pytest.raises(SuiteInterrupted) as interrupted:
                await task
    finally:
        if not task.done():
            task.cancel()
            async with asyncio.timeout(60):
                with suppress(asyncio.CancelledError):
                    await task
    assert interrupted.value.run.output == partial.output
    assert interrupted.value.run.result == partial.result
    await asyncio.to_thread(wait_gone, pid)
    assert seen[0].cleaned
    assert not seen[0].tree.root.exists()
    assert not seen[0].tree.git_identity.directory.exists()
    assert workspace.operations.idle


@pytest.mark.parametrize(
    "change,reason",
    [
        ({"outcome": "failed"}, "outcome"),
        ({"outcome": "error"}, "outcome"),
        ({"outcome": "timeout"}, "outcome"),
        ({"passed": 0}, "passed"),
        ({"failed": 1}, "failed"),
        ({"skipped": 1}, "skipped"),
        ({"problems": ("bad report",)}, "problems"),
        ({"tested_tree": "other"}, "tree"),
        ({"cleaned": False}, "cleanup"),
        ({}, None),
    ],
)
def test_suite_guard(api, change, reason):
    run = SuiteRun(
        SuiteResult(
            outcome="passed", passed=1, failed=0, skipped=0, duration_ms=1, failed_tests=[]
        ),
        "",
        problems=(),
        tested_tree="T",
    )
    result_fields = {
        k: v for k, v in change.items() if k in ("outcome", "passed", "failed", "skipped")
    }
    run = replace(
        run,
        result=SuiteResult.model_validate(run.result.model_dump() | result_fields),
        **{k: v for k, v in change.items() if k in ("problems", "tested_tree")},
    )
    problem = api.inverse_suite_problem(run, tree="T", cleaned=change.get("cleaned", True))
    assert problem is None if reason is None else reason in problem
