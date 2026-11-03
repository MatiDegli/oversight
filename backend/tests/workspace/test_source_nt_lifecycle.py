"""NT completion signals across the real suite wrapper, snapshots, locks and discard."""

import asyncio
from typing import Never
from unittest.mock import Mock

import pytest

from oversight.workspace import IntegrityError, SuiteInterrupted, WorkspaceBusy, runner, suite_guard
from oversight.workspace.runner import NtCleanupError, NtCleanupRunner, ProcessResult

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("count", [0, 3, 12])
def test_own_readiness_failure_reports_at_most_ten_venv_relative_hardlinks(tmp_path, count):
    from .test_source_nt_windows import venv_link_diagnostics

    source = tmp_path / "source"
    site = source / ".venv/Lib/site-packages"
    site.mkdir(parents=True)
    (site / "unlinked.py").write_bytes(b"fixture")
    cache = tmp_path / "cached.py"
    cache.write_bytes(b"cached")
    linked = {f"Lib/site-packages/linked{number}.py" for number in range(count)}
    for relative in linked:
        (source / ".venv" / relative).hardlink_to(cache)
    (source / "outside-venv.py").hardlink_to(cache)
    paths = venv_link_diagnostics(source / ".venv")
    assert len(paths) == len(set(paths)) == min(count, 10)
    assert set(paths) <= linked


@pytest.mark.parametrize("mode", ["normal", "cancel"])
async def test_cleanup_diagnostic_survives_nt_signal_and_cancellation(tmp_path, monkeypatch, mode):
    entered, finish = asyncio.Event(), asyncio.Event()
    failure = OSError("membership unknown")

    async def reap(*args, **kwargs):
        entered.set()
        await finish.wait()
        raise failure

    async def execute(*args):
        await runner._reap_nt(Mock(), runner.ProcessCollector(100), 101)

    monkeypatch.setattr(runner, "_reap_nt_job", reap)
    monkeypatch.setattr(runner, "_execute", execute)
    task = asyncio.create_task(runner.run_process(["fixture"], cwd=tmp_path, timeout_s=1))
    await asyncio.wait_for(entered.wait(), 5)
    if mode == "cancel":
        task.cancel("person stopped")
        await asyncio.sleep(0)
    finish.set()
    with pytest.raises(asyncio.CancelledError if mode == "cancel" else NtCleanupError) as caught:
        await task
    cleanup = caught.value.__cause__ if mode == "cancel" else caught.value
    assert isinstance(cleanup, NtCleanupError)
    assert "membership unknown" in str(cleanup)
    assert "cleanup is unverified" in str(cleanup)
    assert cleanup.__cause__ is failure
    if mode == "cancel":
        assert str(caught.value) == "person stopped"


async def test_native_combination_exercise_uses_real_merged_tree(
    tmp_path, make_workspace, demo_python
):
    from types import SimpleNamespace

    from .test_source_nt_windows import assert_combined_failure_then_repair

    source = tmp_path / "combined-template"
    module = source / "sample/__init__.py"
    module.parent.mkdir(parents=True)
    module.write_bytes(b"def value():\n    return 41\n")
    (source / "test_value.py").write_bytes(
        b"import sample\ndef test_value():\n    assert sample.value() == 41\n"
    )
    (source / "test_async.py").write_bytes(b"def test_async_copy():\n    assert True\n")
    workspace = make_workspace("combined", source=source, demo_python=demo_python)
    await workspace.acquire_runtime()
    await workspace.reset()
    agent = await workspace.create_worktree("agent-1")
    await assert_combined_failure_then_repair(
        workspace, SimpleNamespace(source=source, module="sample/__init__.py"), agent
    )


@pytest.mark.parametrize("mode", ["normal", "cancel"])
@pytest.mark.parametrize("masked", [False, True])
async def test_capture_cleanup_failure_never_restores_snapshot(
    workspace, monkeypatch, mode, masked
):
    from pathlib import Path
    from types import SimpleNamespace

    from oversight.workspace import test_tree
    from oversight.workspace.snapshot import finish_snapshot

    entered, finish = asyncio.Event(), asyncio.Event()

    async def execute(*args):
        entered.set()
        await finish.wait()
        raise NtCleanupError("capture Job still alive")

    restored = Mock(wraps=finish_snapshot)
    monkeypatch.setattr(runner, "_execute", execute)
    monkeypatch.setattr(suite_guard, "finish_snapshot", restored)
    unlinks = []
    if masked:

        class SharingViolation(PermissionError):
            winerror = 32

        original_unlink = Path.unlink
        monkeypatch.setattr(test_tree, "sys", SimpleNamespace(platform="win32"))

        def unlink(path, *args, **kwargs):
            if path.name.startswith("suite-index-"):
                unlinks.append(path)
                raise SharingViolation("index sharing violation") from None
            return original_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", unlink)
    task = asyncio.create_task(workspace.run_tests(workspace.main, timeout_s=75))
    await asyncio.wait_for(entered.wait(), 5)
    if mode == "cancel":
        task.cancel("capture stopped")
        await asyncio.sleep(0)
        task.cancel("capture stopped again")
    assert not task.done()
    finish.set()
    try:
        with pytest.raises(
            asyncio.CancelledError if mode == "cancel" and not masked else IntegrityError
        ) as caught:
            await task
        if mode == "cancel" and not masked:
            assert str(caught.value) == "capture stopped"
        if masked:
            assert len(unlinks) == 14  # Both index and lock exhaust the real NT retry loop.
            assert unlinks[-1].suffix == ".lock"
        restored.assert_not_called()
        assert workspace.integrity.main_suspect and workspace.integrity.rules_suspended
        workspace.release_runtime()
        assert workspace._lock_fd is not None
        with pytest.raises(IntegrityError):
            await workspace.reset()
    finally:
        workspace._cleanup_unverified = False


@pytest.mark.parametrize("mode", ["normal", "cancel"])
@pytest.mark.parametrize("operation", ["git", "run_ok", "venv_probe"])
async def test_untracked_cleanup_failure_retains_runtime(workspace, monkeypatch, mode, operation):
    from oversight.workspace import core

    entered, finish = asyncio.Event(), asyncio.Event()

    async def execute(*args):
        entered.set()
        await finish.wait()
        raise NtCleanupError("untracked Job still alive")

    monkeypatch.setattr(runner, "_execute", execute)
    if operation == "venv_probe":
        workspace._demo_python_given = False
        venv = workspace.runtime_root / "demo-venv"
        venv.mkdir()
        (venv / "oversight-stamp").write_text("pytest==9.1.1\n")
        monkeypatch.setattr(core, "remove_tree", Mock(side_effect=AssertionError("deleted venv")))

    async def run():
        if operation == "git":
            async with workspace.locks(main=True):
                await workspace.git(workspace.main, "rev-parse", "HEAD")
        elif operation == "run_ok":
            await workspace._run_ok(["fixture"], cwd=workspace.main.root, timeout_s=75, env={})
        else:
            await workspace.ensure_demo_venv()

    task = asyncio.create_task(run())
    await asyncio.wait_for(entered.wait(), 5)
    if mode == "cancel":
        task.cancel("person stopped")
        await asyncio.sleep(0)
        task.cancel("stopped again")
    finish.set()
    try:
        with pytest.raises(
            asyncio.CancelledError if mode == "cancel" else NtCleanupError
        ) as caught:
            await task
        if mode == "cancel":
            assert str(caught.value) == "person stopped"
            assert isinstance(caught.value.__cause__, NtCleanupError)
        assert workspace.integrity.main_suspect and workspace.integrity.rules_suspended
        workspace.release_runtime()
        assert workspace._lock_fd is not None
        with pytest.raises(IntegrityError):
            await workspace.reset()
        with pytest.raises(IntegrityError):
            await run()
    finally:
        workspace._cleanup_unverified = False


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


async def test_staging_relist_rewrap_retains_runtime(workspace, monkeypatch, tmp_path):
    from dataclasses import replace
    from functools import partial
    from unittest.mock import AsyncMock

    from oversight.workspace import core, source_nt

    from .test_source_nt import source_case, unnamed
    from .test_source_nt_copy import fixture, selection

    with source_case(tmp_path) as (source, _):
        source = replace(source, runtime=workspace.runtime_root)
        await fixture(source)
        assert workspace._lock_fd is not None
        chosen = await selection(source, workspace._lock_fd)
        cleanup = NtCleanupError("relist Job still alive")

        async def relist(*args, **kwargs):
            raise cleanup

        workspace._own_source = source
        monkeypatch.setattr(core, "inventory", AsyncMock(return_value=chosen.inventory))
        monkeypatch.setattr(core, "validate_selection", lambda _: chosen)
        monkeypatch.setattr(core, "executable", lambda _: r"C:\Git\git.exe")
        monkeypatch.setattr(core, "run_process", relist)
        # Keep the real copy, relist and `from None` refusal boundary.
        monkeypatch.setattr(
            core,
            "stage_source",
            partial(
                source_nt.stage_source,
                ancestors=(),
                sensitive=lambda _: False,
                streams=unnamed,
            ),
        )
        try:
            with pytest.raises(OSError, match="unsafe or changed selected input") as caught:
                await workspace.stage_own_source()
            assert caught.value.__cause__ is None and caught.value.__suppress_context__
            assert caught.value.__context__ is cleanup
            assert not list(source.runtime.glob("source-stage-*"))
            assert workspace.integrity.main_suspect and workspace.integrity.rules_suspended
            workspace.release_runtime()
            assert workspace._lock_fd is not None
            with pytest.raises(IntegrityError):
                await workspace.reset()
        finally:
            workspace._cleanup_unverified = False


@pytest.mark.parametrize("boundary", ["guard", "acquire", "runner"])
@pytest.mark.parametrize("nt_failure", [False, True])
async def test_masked_cleanup_latches_each_boundary(workspace, monkeypatch, boundary, nt_failure):
    from oversight.workspace import core

    cleanup = NtCleanupError("live members") if nt_failure else OSError("ordinary failure")

    def masked(*args, **kwargs) -> Never:
        try:
            raise cleanup
        finally:
            raise OSError("later cleanup") from None

    async def spawn(*args, **kwargs) -> Never:
        masked()

    tracked = NtCleanupRunner(spawn)
    if boundary == "acquire":
        workspace.release_runtime()
        monkeypatch.setattr(core, "refuse_orphans", masked)
    try:
        with pytest.raises(OSError, match="later cleanup"):
            if boundary == "guard":
                async with workspace.guard_nt_cleanup():
                    masked()
            elif boundary == "acquire":
                await workspace.acquire_runtime()
            else:
                await tracked(["fixture"], cwd=workspace.main.root, timeout_s=1)
        if boundary == "runner":
            assert tracked.cleanup_complete is not nt_failure
        else:
            assert workspace.integrity.main_suspect is nt_failure
            assert workspace.integrity.rules_suspended is nt_failure
            workspace.release_runtime()
            assert (workspace._lock_fd is not None) is nt_failure
    finally:
        workspace._cleanup_unverified = False


async def test_cleanup_signal_search_handles_both_links_cycles_and_deep_chains():
    error = OSError("outer cleanup")
    error.__cause__ = ValueError("unrelated explicit cause")
    error.__cause__.__context__ = error
    hidden = OSError("suppressed intermediate")
    hidden.__context__ = NtCleanupError("still alive")
    hidden.__suppress_context__ = True
    error.__context__ = hidden
    assert runner.nt_cleanup_failed(error)
    error.__context__ = None
    assert not runner.nt_cleanup_failed(error)
    error.__context__ = hidden
    for _ in range(70):
        cause = ValueError("unrelated cause chain")
        cause.__cause__ = error.__cause__
        error.__cause__ = cause
    assert runner.nt_cleanup_failed(error)
    error.__context__ = None
    assert not runner.nt_cleanup_failed(error)
    # A long ordinary chain must terminate without recursion or suspending POSIX work.
    for _ in range(1000):
        outer = OSError("ordinary cleanup")
        outer.__context__ = error
        error = outer
    assert not runner.nt_cleanup_failed(error)
    assert not runner.nt_cleanup_failed(None)


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
