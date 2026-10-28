"""Library defaults are stable even when a developer enables the pack locally."""

import importlib
from unittest.mock import Mock

import pytest
from agent_helpers import Recorder, make_run, script

from oversight.agents import _prompts
from oversight_contracts.events import AgentStep


@pytest.mark.anyio
async def test_environment_cannot_enable_an_existing_runtime(small_tree, monkeypatch):
    monkeypatch.setenv("OVERSIGHT_HARNESS_PACK", "true")
    model = script(
        [("read_file", {"path": "shop/mod.py"})],
        [("edit_file", {"path": "shop/mod.py", "old_text": "return 1", "new_text": "return ("})],
        [("finish", {"summary": "Done."})],
    )
    recorder = Recorder()
    end = await make_run(small_tree, model, recorder).run()
    assert end.state == "ready_for_review"
    assert "return (" in (small_tree[1].root / "shop/mod.py").read_text()
    assert all(
        isinstance(step, AgentStep) and step.result == "ok" for step in recorder.of("AgentStep")
    )


@pytest.mark.anyio
async def test_libraries_never_reopen_a_developer_env_file(small_tree, monkeypatch):
    monkeypatch.delenv("OVERSIGHT_HARNESS_PACK", raising=False)
    read_env = Mock(side_effect=AssertionError("library reopened settings"))
    monkeypatch.setattr("oversight.settings.read_env_file", read_env)
    importlib.reload(_prompts)
    model = script([("run_tests", {})], [("finish", {"summary": "Done."})])
    end = await make_run(small_tree, model, Recorder()).run()
    assert end.state == "ready_for_review"
    read_env.assert_not_called()
