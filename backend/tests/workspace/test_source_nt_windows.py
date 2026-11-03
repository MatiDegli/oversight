"""Public native composition fixtures; skipped Linux cases are never NT acceptance."""

import importlib
import sys
import tempfile
from functools import wraps
from itertools import islice
from pathlib import Path

import pytest
from live_helpers import live_settings

from oversight import app
from oversight.workspace import SuiteUnavailable, base_probe_checkout, core

from . import test_platform_windows as process

driver = importlib.import_module("demo_check_nt")

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        sys.platform != "win32", reason="native Windows original-venv/startup/import proof"
    ),
]


def long_temp_dir() -> Path:
    """Keep paths short while expanding Windows 8.3 aliases before native validation."""
    return Path(tempfile.mkdtemp(prefix="ov-")).resolve()


@pytest.fixture
def readiness_errors(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, OSError]]:
    """Retain readiness failures while letting production handle the same exception."""
    errors: list[tuple[str, OSError]] = []
    prepare = core.prepare_environment  # pyright: ignore[reportPrivateImportUsage]
    probe = core.probe_environment  # pyright: ignore[reportPrivateImportUsage]

    @wraps(prepare)
    def record_prepare(*args, **kwargs):
        try:
            return prepare(*args, **kwargs)
        except OSError as error:
            errors.append(("prepare_environment", error))
            raise

    @wraps(probe)
    async def record_probe(*args, **kwargs):
        try:
            return await probe(*args, **kwargs)
        except OSError as error:
            errors.append(("probe_environment", error))
            raise

    monkeypatch.setattr(core, "prepare_environment", record_prepare)
    monkeypatch.setattr(core, "probe_environment", record_probe)
    return errors


async def assert_combined_failure_then_repair(workspace, fixture, agent):
    module = agent.root / fixture.module
    module.write_bytes(b"def value():\n    return 42\n")
    head = await agent.commit_all("Candidate value", workspace.agent_author("agent-1"))
    assert head is not None
    test = workspace.main.root / "test_value.py"
    test.write_bytes(test.read_bytes().replace(b"== 41", b"== 43"))
    base = await workspace.main.commit_all(
        "Concurrent expectation", workspace.agent_author("agent-1")
    )
    assert base is not None
    async with (
        workspace.locks(main=True, agent_id="agent-1"),
        base_probe_checkout(
            workspace, agent=agent, base=base, head=head, paths=(), timeout_s=75
        ) as combined,
    ):
        await workspace.git_ok(combined, "merge", "--no-commit", "--no-ff", head)
        assert combined.root not in (agent.root, workspace.main.root)
        assert (combined.root / fixture.module).read_bytes() == module.read_bytes()
        combined_test = combined.root / "test_value.py"
        assert combined_test.read_bytes() == test.read_bytes()
        failed = await workspace.run_tests(combined, timeout_s=75)
        assert failed.result.outcome == "failed" and failed.result.failed == 1
        assert not any(fixture.source.parent.rglob("FILTER_SENTINEL"))
        combined_test.write_bytes(combined_test.read_bytes().replace(b"== 43", b"== 42"))
        repaired = await workspace.run_tests(combined, timeout_s=75)
        assert repaired.result.outcome == "passed" and repaired.result.passed == 2
        assert any(case.test_id == "test_async.py::test_async_copy" for case in repaired.cases)
        assert repaired.tested_tree != failed.tested_tree
        assert not repaired.problems
        assert not any(fixture.source.parent.rglob("FILTER_SENTINEL"))


@pytest.mark.parametrize("layout", ["packages/foo/src", "backend/src"])
async def test_original_venv_each_copy_and_guard_admission(tmp_path, layout, readiness_errors):
    tmp_path = long_temp_dir()
    fixture = await driver.prepare_own_fixture(tmp_path, layout=layout)
    before = {
        p: (fixture.source / p).read_bytes()
        for p in (
            fixture.module,
            ".git/HEAD",
            ".git/index",
            ".git/config",
            "crlf.py",
            "test_value.py",
            "test_async.py",
        )
    }
    live = app.LiveRun(
        live_settings(tmp_path, workspace_source=str(fixture.source))
    )  # injected demo_python must never be used
    try:
        await live.startup()
        assert live._environment_unavailable is None, {
            "readiness_errors": [
                f"{call}: {type(error).__name__}: {error} (context: {error.__context__!r})"
                for call, error in readiness_errors
            ],
            "venv_files_with_st_nlink_ne_1": list(
                islice(
                    (
                        path.relative_to(fixture.source / ".venv").as_posix()
                        for path in (fixture.source / ".venv").rglob("*")
                        if path.is_file() and path.stat().st_nlink != 1
                    ),
                    10,
                )
            ),
        }
        assert not any(tmp_path.rglob("FILTER_SENTINEL"))
        assert (live.workspace.main.root / "crlf.py").read_bytes() == b"line = 1\r\n"
        (live.workspace.main.root / "crlf.py").write_bytes(b"line = 1\r\n")
        agent = await live.workspace.create_worktree("agent-1")
        module = agent.root / fixture.module
        module.write_bytes(b"def value():\n    return -1\n")
        failed = await live.workspace.run_tests(agent, timeout_s=75)
        assert failed.result.outcome == "failed" and failed.result.failed == 1
        assert failed.result.passed == 1  # the autoloaded async test still runs
        assert not any(tmp_path.rglob("FILTER_SENTINEL"))
        module.write_bytes(b"def value():\n    return 41\n")
        repaired = await live.workspace.run_tests(agent, timeout_s=75)
        assert repaired.result.outcome == "passed" and repaired.result.passed == 2
        assert any(case.test_id == "test_async.py::test_async_copy" for case in repaired.cases)
        assert repaired.tested_tree != failed.tested_tree
        assert not repaired.problems
        assert not any(tmp_path.rglob("FILTER_SENTINEL"))
        await assert_combined_failure_then_repair(live.workspace, fixture, agent)
        assert before == {p: (fixture.source / p).read_bytes() for p in before}
        print(
            "native source HEAD/index/config/selected bytes unchanged; "
            "original venv; copied imports; pytest-asyncio autoload; "
            "agent and merged copies fail then repair; clean filter never executed"
        )
    finally:
        await live.shutdown()
        driver.cleanup(tmp_path)


async def test_missing_original_venv_still_serves_graph(tmp_path):
    tmp_path = long_temp_dir()
    source = tmp_path / "source"
    source.mkdir()
    (source / "code.py").write_text("value = 1\n", encoding="utf-8")
    for args in (("init", "-q", "-b", "main"), ("add", "."), ("commit", "-qm", "Fixture")):
        await driver.fixture_git(source, *args)
    live = app.LiveRun(live_settings(tmp_path, workspace_source=str(source)))
    try:
        await live.startup()
        assert live._environment_unavailable is not None and live.store.graph()[1] is not None
        with pytest.raises(SuiteUnavailable):
            await live.workspace.run_tests(live.workspace.main, timeout_s=75)
    finally:
        await live.shutdown()
        driver.cleanup(tmp_path)


@pytest.mark.parametrize("mode", ["timeout", "cancel", "normal"])
async def test_native_launcher_descendants_dead_before_wrapper_returns(tmp_path, demo_python, mode):
    await process._grandchild(tmp_path, demo_python, mode)


@pytest.mark.parametrize("volume", ["network", "OneDrive", "ReFS"])
async def test_unavailable_fixture_volume_has_named_skip(volume):
    pytest.skip(
        f"{volume} disposable fixture volume not provisioned; injected refusals remain mandatory"
    )
