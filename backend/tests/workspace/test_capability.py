"""C3 policy guards, with real suite boundaries and injected kernel evidence."""

import dataclasses
import sys
from unittest.mock import AsyncMock

import pytest

from oversight.workspace import IsolationUnavailable, SuiteUnavailable, Workspace, capability, core
from oversight.workspace.integrity import IntegrityError
from oversight.workspace.isolation import IsolatedRunner
from oversight.workspace.runner import ProcessResult, run_process

REAL_CHECK = capability.check_effect

pytestmark = pytest.mark.anyio


async def grant(workspace: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
    effect = AsyncMock()
    monkeypatch.setattr(capability, "check_effect", effect)
    await workspace.check_isolation()
    assert workspace.isolation.available
    effect.assert_awaited_once()


async def test_required_refuses_without_fallback(workspace: Workspace, monkeypatch) -> None:
    monkeypatch.setattr(
        capability, "check_effect", AsyncMock(side_effect=IsolationUnavailable("host unavailable"))
    )
    await workspace.check_isolation()
    suite = AsyncMock()
    open_runner = AsyncMock()
    monkeypatch.setattr(core, "guarded_suite", suite)
    monkeypatch.setattr(core, "run_process", open_runner)
    with pytest.raises(IsolationUnavailable, match=r"^isolation unavailable: host unavailable$"):
        await workspace.run_tests(workspace.main, timeout_s=5, isolation="required")
    suite.assert_not_awaited()
    open_runner.assert_not_awaited()


async def test_best_effort_selects_c_and_snapshot_is_read_only(workspace: Workspace, monkeypatch):
    await grant(workspace, monkeypatch)
    suite = AsyncMock()
    monkeypatch.setattr(core, "guarded_suite", suite)
    open_runner = AsyncMock()
    await workspace.run_tests(workspace.main, timeout_s=5, spawn=open_runner)
    assert isinstance(suite.call_args.kwargs["spawn"], IsolatedRunner)
    open_runner.assert_not_awaited()
    with pytest.raises(dataclasses.FrozenInstanceError):
        workspace.isolation.born_with_c = False  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(AttributeError):
        workspace.isolation = None  # pyright: ignore[reportAttributeAccessIssue]


async def test_born_without_c_never_enables_authority_even_after_reset(make_workspace, monkeypatch):
    workspace = make_workspace()
    await workspace.acquire_runtime()
    monkeypatch.setattr(
        capability, "check_effect", AsyncMock(side_effect=IsolationUnavailable("host unavailable"))
    )
    await workspace.check_isolation()
    retry = AsyncMock()
    monkeypatch.setattr(capability, "check_effect", retry)
    await workspace.reset()
    await workspace.check_isolation()
    assert workspace.isolation.checked and not workspace.isolation.born_with_c
    assert not workspace.isolation.rules_available
    retry.assert_not_awaited()
    suite = AsyncMock()
    monkeypatch.setattr(core, "guarded_suite", suite)
    await workspace.run_tests(workspace.main, timeout_s=5)
    assert suite.call_args.kwargs["spawn"] is run_process
    with pytest.raises(IsolationUnavailable):
        await workspace.run_tests(workspace.main, timeout_s=5, isolation="required")
    assert suite.await_count == 1


async def test_ordinary_failing_suite_keeps_c(workspace, demo_python, monkeypatch):
    await grant(workspace, monkeypatch)
    workspace._demo_python = demo_python
    (workspace.main.root / "tests/test_fail.py").write_text("def test_fail():\n    assert False\n")
    monkeypatch.setattr(IsolatedRunner, "__call__", staticmethod(run_process))
    run = await workspace.run_tests(workspace.main, timeout_s=15)
    assert run.result.outcome == "failed" and run.result.failed == 1
    assert workspace.isolation.available and workspace.isolation.rules_available


async def test_failed_effect_revokes_c_and_never_falls_back(workspace, monkeypatch):
    await grant(workspace, monkeypatch)
    monkeypatch.setattr(
        capability, "check_effect", AsyncMock(side_effect=IsolationUnavailable("effect failed"))
    )
    with pytest.raises(IsolationUnavailable):
        await workspace._isolation.verify_effect()
    assert workspace.isolation.born_with_c and workspace.isolation.invalidated
    assert not workspace.isolation.rules_available
    suite = AsyncMock()
    monkeypatch.setattr(core, "guarded_suite", suite)
    for policy in ("best_effort", "required"):
        with pytest.raises(IsolationUnavailable, match=r"^isolation unavailable: effect failed$"):
            await workspace.run_tests(workspace.main, timeout_s=5, isolation=policy)
    suite.assert_not_awaited()
    monkeypatch.setattr(capability, "check_effect", AsyncMock())
    await workspace.check_isolation()
    assert not workspace.isolation.available


async def test_execution_discriminator_revokes_c(workspace, monkeypatch):
    await grant(workspace, monkeypatch)
    # Fail inside the actual suite spawn boundary, not from a pytest exit code.
    monkeypatch.setattr(
        IsolatedRunner, "__call__", AsyncMock(side_effect=IsolationUnavailable("entry failed"))
    )
    with pytest.raises(SuiteUnavailable):
        await workspace.run_tests(workspace.main, timeout_s=5)
    assert workspace.isolation.invalidated and not workspace.isolation.available
    with pytest.raises(IsolationUnavailable):
        await workspace.run_tests(workspace.main, timeout_s=5)


async def test_rules_suspension_keeps_isolated_suites(workspace, monkeypatch):
    await grant(workspace, monkeypatch)
    workspace.integrity.suspend(IntegrityError("restored metadata", ".git"), uncertain=False)
    assert workspace.isolation.rules_suspended and not workspace.isolation.rules_available
    suite = AsyncMock()
    monkeypatch.setattr(core, "guarded_suite", suite)
    await workspace.run_tests(workspace.main, timeout_s=5)
    assert isinstance(suite.call_args.kwargs["spawn"], IsolatedRunner)
    assert workspace.isolation.available


async def test_startup_effect_uses_launch_confirmation_not_a_suite(tmp_path, monkeypatch):
    # Override the autouse unavailable-host fixture with the real effect orchestration.
    check = REAL_CHECK
    calls = []

    async def run(self, argv, *, cwd, timeout_s, **kwargs):
        assert self.port > 0 and cwd == tmp_path
        assert argv == [sys.executable, "-I", "-S", "-c", "pass"]
        self.entry = {"refused": True}
        calls.append("entry")
        return ProcessResult(0, "", "", False, False, 1)

    monkeypatch.setattr(IsolatedRunner, "__call__", run)
    await check(tmp_path)
    assert calls == ["entry"]


@pytest.mark.parametrize("failure", ["entry", "exit", "cleanup"])
async def test_startup_rejects_failed_effect(tmp_path, monkeypatch, failure):
    check = REAL_CHECK

    async def run(self, *args, **kwargs):
        self.entry = {"refused": failure != "entry"}
        self.cleanup_complete = failure != "cleanup"
        return ProcessResult(1 if failure == "exit" else 0, "", "", False, False, 1)

    monkeypatch.setattr(IsolatedRunner, "__call__", run)
    with pytest.raises(IsolationUnavailable, match="effect probe failed"):
        await check(tmp_path)
