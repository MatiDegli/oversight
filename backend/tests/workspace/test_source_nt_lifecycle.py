"""NT completion signals across the real suite wrapper, snapshots, locks and discard."""

import asyncio
from unittest.mock import Mock

import pytest

from oversight.workspace import IntegrityError, SuiteInterrupted, WorkspaceBusy, runner, suite_guard
from oversight.workspace.runner import NtCleanupError, NtCleanupRunner, ProcessResult

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("mode", ["normal", "timeout", "cancel"])
@pytest.mark.parametrize("verified", [True, False])
async def test_cleanup_precedes_snapshot_or_retains_runtime(workspace, monkeypatch, mode, verified):
    agent = await workspace.create_worktree("agent-1")
    entered, finish = asyncio.Event(), asyncio.Event()
    order = []

    original = runner._execute

    async def execute(argv, cwd, timeout_s, env, cap, abort):
        if "pytest" not in argv:
            return await original(argv, cwd, timeout_s, env, cap, abort)
        entered.set()
        await finish.wait()
        order.append("leader/job checked")
        if not verified:
            raise NtCleanupError("injected members still alive")
        return ProcessResult(0, "", "", mode == "timeout", False, 1)

    monkeypatch.setattr(runner, "_execute", execute)
    # Force only the NT completion transport, without changing the process platform.
    spawn = NtCleanupRunner(runner.run_process)
    from oversight.workspace.snapshot import finish_snapshot

    restored = finish_snapshot

    def restore(*args):
        assert verified and order == ["leader/job checked"]
        order.append("restore")
        return restored(*args)

    monkeypatch.setattr(suite_guard, "finish_snapshot", restore)
    task = asyncio.create_task(workspace.run_tests(agent, timeout_s=300, spawn=spawn))
    await asyncio.wait_for(entered.wait(), 5)
    assert not workspace.operations.idle
    if mode == "cancel":
        task.cancel("first cancellation")
        await asyncio.sleep(0)
        task.cancel("repeated cancellation")
    assert not task.done() and not order
    finish.set()
    try:
        if not verified:
            with pytest.raises(SuiteInterrupted if mode == "cancel" else IntegrityError) as caught:
                await task
            if mode == "cancel":
                assert isinstance(caught.value, SuiteInterrupted)
                assert caught.value.run.result.passed == 0 and caught.value.run.cases == ()
                assert caught.value.run.problems
            assert order == ["leader/job checked"]
            assert workspace.integrity.main_suspect and workspace.integrity.rules_suspended
            with pytest.raises(IntegrityError):
                await workspace.discard_worktree("agent-1", delete_branch=True)
            with pytest.raises(IntegrityError):
                await workspace.reset()
            workspace.release_runtime()
            assert workspace._lock_fd is not None
            from oversight.workspace import Workspace

            other = Workspace(workspace.runtime_root, workspace._template, pytest_pin="9.1.1")
            with pytest.raises(WorkspaceBusy):
                await other.acquire_runtime()
            assert agent.root.exists()
        elif mode == "cancel":
            with pytest.raises(SuiteInterrupted):
                await task
            assert order[-1] == "restore"
        else:
            await task
            assert order[-1] == "restore"
        assert workspace.operations.idle
    finally:
        # The injected Job now has no live process. Native tests must prove that separately.
        workspace._cleanup_unverified = False


async def test_nt_reap_signal_preserves_original_cancellation(monkeypatch, tmp_path):
    entered, finish = asyncio.Event(), asyncio.Event()

    async def execute(*args):
        entered.set()
        await finish.wait()
        raise NtCleanupError("job query failed")

    monkeypatch.setattr(runner, "_execute", execute)
    task = asyncio.create_task(runner.run_process(["fixture"], cwd=tmp_path, timeout_s=300))
    await entered.wait()
    task.cancel("person stopped")
    await asyncio.sleep(0)
    finish.set()
    with pytest.raises(asyncio.CancelledError, match="person stopped") as caught:
        await task
    assert isinstance(caught.value.__cause__, NtCleanupError)


async def test_reap_close_failure_has_nt_only_signal(monkeypatch):
    async def failed(*args, **kwargs):
        raise OSError("query/close failure")

    monkeypatch.setattr(runner, "_reap_nt_job", failed)
    with pytest.raises(NtCleanupError, match="cleanup is unverified"):
        await runner._reap_nt(Mock(), Mock(), 123)


@pytest.mark.parametrize("own", [False, True])
@pytest.mark.parametrize("verified", [False, True])
@pytest.mark.parametrize("mode", ["normal", "timeout", "cancel"])
async def test_nt_finalizer_preserves_all_artifacts_until_cleanup_is_verified(
    workspace, monkeypatch, own, verified, mode
):
    import json
    from pathlib import Path

    from oversight.workspace import CallReport, CallTarget, SuiteUnavailable, suite
    from oversight.workspace.core import PLUGIN_DIR
    from oversight.workspace.snapshot import finish_snapshot
    from oversight.workspace.source_nt import OwnEnvironment

    artifacts, reads = {}, []
    original_tally, original_cases = suite._take_own_tally, suite._take_cases

    def tally(path):
        reads.append("tally")
        return original_tally(path)

    def cases(path, redact):
        reads.append("junit")
        return original_cases(path, redact)

    def calls(*args):
        reads.append("calls")
        return CallReport((), True, ())

    monkeypatch.setattr(suite, "_take_own_tally", tally)
    monkeypatch.setattr(suite, "_take_cases", cases)
    monkeypatch.setattr(suite, "read_calls", calls)
    restored = Mock(wraps=finish_snapshot)
    monkeypatch.setattr(suite_guard, "finish_snapshot", restored)

    def prepare(tree, deadline):
        policy = dict(
            source=str(tree.parent / "source"),
            tree=str(tree),
            libraries=[str(tree.parent / "venv/Lib")],
            packages=[],
        )
        return OwnEnvironment(Path("original-python.exe"), guard=json.dumps(policy))

    def write_artifacts(env):
        report = Path(env["OVERSIGHT_REPORT"])
        artifacts.update(
            {
                report: b'{"kind":"guard","ok":true}\n{"kind":"test","when":"call",'
                b'"nodeid":"test_copy.py::test_value","outcome":"passed"}\n',
                report.with_suffix(".xml"): b'<testsuite><testcase classname="test_copy" '
                b'name="test_value"/></testsuite>',
                report.with_suffix(".calls"): b"retained caller evidence",
                report.with_suffix(".calls-input"): Path(
                    env["OVERSIGHT_CALL_TARGETS"]
                ).read_bytes(),
            }
        )
        for path, data in artifacts.items():
            path.write_bytes(data)

    async def spawn(argv, *, env=None, **kwargs):
        if "-c" in argv:
            return ProcessResult(0, '{"ok": true}', "", False, False, 0)
        write_artifacts(env)
        if mode == "cancel":
            if not verified:
                raise asyncio.CancelledError("person stopped") from NtCleanupError("live members")
            raise asyncio.CancelledError("person stopped")
        if not verified:
            raise NtCleanupError("live members")
        return ProcessResult(0, "", "", mode == "timeout", False, 1)

    tracked = NtCleanupRunner(spawn)

    async def run():
        # Exercise every artifact at the wrapper boundary. Workspace's production NT
        # isolation refusal for measured suites remains unchanged.
        async with workspace.locks(main=True), workspace.git_lease():
            return await suite_guard.guarded_suite(
                workspace,
                workspace.main,
                python=Path("demo-python"),
                plugin_dir=PLUGIN_DIR,
                flags=workspace.git_flags,
                git_env=workspace._git_env(workspace.main, None, None),
                timeout_s=75,
                case_ids=(),
                spawn=tracked,
                _own_prepare=prepare if own else None,
                measured_targets=(CallTarget("fixture:value", "test_copy.py", "value", 1, 2),),
            )

    try:
        if mode == "cancel":
            with pytest.raises(SuiteInterrupted) as caught:
                await run()
            if not verified:
                assert caught.value.run.result.passed == 0 and not caught.value.run.cases
        elif not verified:
            with pytest.raises(IntegrityError) as caught:
                await run()
            pending = caught.value.__context__
            assert isinstance(pending, SuiteUnavailable)
            assert pending.run.result.passed == 0 and not pending.run.cases
            assert pending.run.measured is None
            if own:
                assert pending.run.problems == ("Windows Job cleanup is unverified",)
                assert "tests unavailable: Windows Job cleanup is unverified" in pending.run.output
        else:
            await run()
        if verified:
            restored.assert_called_once()
            assert reads and all(not path.exists() for path in artifacts)
        else:
            assert not reads and len(artifacts) == 4
            assert all(path.read_bytes() == data for path, data in artifacts.items())
            restored.assert_not_called()
            assert workspace.integrity.main_suspect
    finally:
        workspace._cleanup_unverified = False
