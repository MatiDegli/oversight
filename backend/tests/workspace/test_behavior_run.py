"""Required isolation, demo interpreter, metadata guard and worker lifecycle."""

import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from oversight.workspace import (
    BehaviorTarget,
    IsolationUnavailable,
    Workspace,
    WorkspaceError,
    base_probe_checkout,
)
from oversight.workspace.behavior_run import _read_report
from oversight.workspace.integrity import IntegrityError

pytestmark = pytest.mark.anyio
TARGET = BehaviorTarget("value", "shop.mod", "value", ("((), {})",))


@pytest.mark.parametrize("calls", [("((), {})",), ()])
async def test_report_row_requires_problem_key(tmp_path, calls):
    report = tmp_path / "report.json"
    results = [{"kind": "value", "value": ["int", "0x1"]} for _ in calls]
    report.write_text(json.dumps({"value": {"results": results}}))
    target = BehaviorTarget("value", "shop.mod", "value", calls)
    with pytest.raises(WorkspaceError, match=r"missing.*problem"):
        _read_report(report, (target,))


async def test_required_isolation_selects_before_spawning(workspace, monkeypatch):
    worker = AsyncMock()
    monkeypatch.setattr("oversight.workspace.core.run_behavior_worker", worker)
    with pytest.raises(IsolationUnavailable):
        await workspace.run_behavior(workspace.main, (TARGET,), timeout_s=1)
    worker.assert_not_awaited()


async def test_demo_python_locks_env_and_no_overlay(workspace, monkeypatch):
    await workspace.check_isolation()
    agent = await workspace.create_worktree("agent-1")
    source = await agent.read_text("shop/mod.py")
    await agent.write_text(
        "shop/mod.py", source.content.replace("return 1", "return 2"), expected_hash=source.hash
    )
    head = await agent.commit_all("Proposed", workspace.agent_author("agent-1"))
    seen = []

    async def worker(owner, tree, *, python, targets, timeout_s, spawn):
        assert owner is workspace and python == workspace._demo_python
        assert owner.operations.holds(main=True, agent_id="agent-1")
        seen.append((tree.root, (tree.root / "shop/mod.py").read_text()))
        assert tree.root != agent.root
        return {}

    monkeypatch.setattr("oversight.workspace.core.run_behavior_worker", worker)
    async with workspace.locks(main=True, agent_id="agent-1"):
        for commit in (agent.base_commit, head):
            async with base_probe_checkout(
                workspace, agent=agent, base=commit, head=head, paths=(), timeout_s=5
            ) as tree:
                await workspace.run_behavior(tree, (TARGET,), timeout_s=1)
    assert "return 1" in seen[0][1] and "return 2" in seen[1][1]
    assert not list(workspace.tmp_dir.glob("base-probe-*"))


async def test_isolation_failure_revokes_session(workspace, monkeypatch):
    await workspace.check_isolation()
    worker = AsyncMock(side_effect=IsolationUnavailable("entry failed"))
    monkeypatch.setattr("oversight.workspace.core.run_behavior_worker", worker)
    with pytest.raises(IsolationUnavailable):
        await workspace.run_behavior(workspace.main, (TARGET,), timeout_s=1)
    assert workspace.isolation.invalidated
    with pytest.raises(IsolationUnavailable):
        await workspace.run_behavior(workspace.main, (TARGET,), timeout_s=1)
    assert worker.await_count == 1


async def test_git_metadata_is_restored_after_worker(workspace: Workspace):
    await workspace.check_isolation()
    workspace._demo_python = Path(sys.executable)
    (workspace.main.root / "shop/mod.py").write_text(
        "from pathlib import Path\n"
        "def value():\n"
        "    Path('.git/config').write_text('')\n"
        "    return 1\n"
    )
    config = workspace.main.root / ".git/config"
    original = config.read_bytes()
    with pytest.raises(IntegrityError, match="changed Git metadata"):
        await workspace.run_behavior(workspace.main, (TARGET,), timeout_s=30)
    assert config.read_bytes() == original
    assert workspace.integrity.rules_suspended and not workspace.integrity.main_suspect


@pytest.mark.parametrize("cancel", [False, True])
async def test_timeout_or_cancel_reaps_exact_worker_and_removes_files(
    workspace, gate_factory, cancel, wait_gone
):
    await workspace.check_isolation()
    workspace._demo_python = Path(sys.executable)
    ready = gate_factory("ready")
    (workspace.main.root / "shop/mod.py").write_text(
        "import os, signal\n"
        "def value():\n"
        "    signal.signal(signal.SIGALRM, signal.SIG_IGN)\n"
        f"    {ready.child_signal}\n"
        f"    {ready.child_wait}\n"
    )
    # Read readiness without polling or leaving a blocked thread if the worker fails.
    reader = asyncio.StreamReader()
    pipe = os.fdopen(os.open(ready.signal, os.O_RDONLY | os.O_NONBLOCK), "rb", buffering=0)
    transport, _ = await asyncio.get_running_loop().connect_read_pipe(
        lambda: asyncio.StreamReaderProtocol(reader), pipe
    )
    task = asyncio.create_task(workspace.run_behavior(workspace.main, (TARGET,), timeout_s=5))
    try:
        async with asyncio.timeout(30):
            pid = int(await reader.read())
        assert pid != os.getpid()
        if cancel:
            task.cancel()
        with pytest.raises(asyncio.CancelledError if cancel else TimeoutError):
            await task
    finally:
        transport.close()
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    await asyncio.to_thread(wait_gone, pid)
    assert not list(workspace.tmp_dir.glob("behavior-*"))
    assert workspace.operations.idle
