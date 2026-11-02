"""Private native composition fixtures; skipped Linux cases are never NT acceptance."""

import importlib
import sys
import tempfile
from pathlib import Path

import pytest
from live_helpers import live_settings

from oversight import app
from oversight.workspace import OwnSource, SuiteUnavailable

from . import test_platform_windows as process

driver = importlib.import_module("demo_check_nt")

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        sys.platform != "win32", reason="native Windows original-venv/startup/import proof"
    ),
]


@pytest.mark.parametrize("layout", ["packages/foo/src", "backend/src"])
@pytest.mark.skipif(
    not hasattr(SuiteUnavailable, "own_source_reason"),
    reason="guarded-result integration must land before native import qualification",
)
async def test_original_venv_each_copy_and_guard_admission(tmp_path, monkeypatch, layout):
    tmp_path = Path(tempfile.mkdtemp(prefix="ov-"))
    fixture = await driver.prepare_own_fixture(tmp_path, layout=layout)
    before = {
        p: (fixture.source / p).read_bytes()
        for p in (fixture.module, ".git/HEAD", ".git/index", ".git/config", "crlf.py")
    }
    monkeypatch.setattr(app, "select_source", lambda _: OwnSource(str(fixture.source)))
    live = app.LiveRun(live_settings(tmp_path))  # injected demo_python must never be used
    try:
        await live.startup()
        assert live._environment_unavailable is None
        assert (live.workspace.main.root / "crlf.py").read_bytes() == b"line = 1\r\n"
        (live.workspace.main.root / "crlf.py").write_bytes(b"line = 1\r\n")
        agent = await live.workspace.create_worktree("agent-1")
        module = agent.root / fixture.module
        module.write_bytes(b"def value():\n    return -1\n")
        failed = await live.workspace.run_tests(agent, timeout_s=75)
        assert failed.result.outcome == "failed" and failed.result.failed == 1
        module.write_bytes(b"def value():\n    return 41\n")
        repaired = await live.workspace.run_tests(agent, timeout_s=75)
        combined = await live.workspace.run_tests(live.workspace.main, timeout_s=75)
        assert repaired.result.outcome == combined.result.outcome == "passed"
        assert repaired.tested_tree != failed.tested_tree
        assert not repaired.problems and not combined.problems
        assert before == {p: (fixture.source / p).read_bytes() for p in before}
        print(
            "native source HEAD/index/config/selected bytes unchanged; "
            "original venv; copied imports"
        )
    finally:
        await live.shutdown()
        driver.cleanup(tmp_path)


async def test_missing_original_venv_still_serves_graph(tmp_path, monkeypatch):
    tmp_path = Path(tempfile.mkdtemp(prefix="ov-"))
    source = tmp_path / "source"
    source.mkdir()
    (source / "code.py").write_text("value = 1\n", encoding="utf-8")
    for args in (("init", "-q", "-b", "main"), ("add", "."), ("commit", "-qm", "Fixture")):
        await driver.fixture_git(source, *args)
    monkeypatch.setattr(app, "select_source", lambda _: OwnSource(str(source)))
    live = app.LiveRun(live_settings(tmp_path))
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
