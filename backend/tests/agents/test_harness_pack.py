"""Both harness arms through scripted tool calls; no provider transport or credits."""

import hashlib
import json
from functools import partial
from pathlib import Path
from typing import cast

import pytest
from agent_helpers import Recorder, make_run, script

from oversight.agents import _prompts
from oversight.agents.client import Message
from oversight.settings import Settings
from oversight.workspace import Tree, Workspace
from oversight_contracts.events import AgentStep

pytestmark = pytest.mark.anyio
Opened = tuple[Workspace, Tree]
READ = ("read_file", {"path": "shop/mod.py"})
EDIT = ("edit_file", {"path": "shop/mod.py", "old_text": "return 1", "new_text": "return 2"})
FINISH = ("finish", {"summary": "Done."})
RUN_TESTS = ("run_tests", {})


make_run = partial(make_run, harness_pack=True)


async def put(tree: Tree, path: str, text: str) -> None:
    try:
        digest = (await tree.read_text(path)).hash
    except FileNotFoundError:
        digest = None
    await tree.write_text(path, text, expected_hash=digest)


def observations(messages: list[Message]) -> list[str]:
    return [str(m["content"]) for m in messages if m["role"] == "tool"]


def steps(recorder: Recorder) -> list[AgentStep]:
    return cast(list[AgentStep], recorder.of("AgentStep"))


@pytest.mark.parametrize("replacement", ["return (", "return )"])
async def test_syntax_guard_refuses_before_write(small_tree: Opened, replacement: str) -> None:
    original = (small_tree[1].root / "shop/mod.py").read_bytes()
    model = script(
        [READ],
        [("edit_file", {"path": "shop/mod.py", "old_text": "return 1", "new_text": replacement})],
        [FINISH],
    )
    recorder = Recorder()
    await make_run(small_tree, model, recorder).run()
    error = observations(model.requests[-1])[-1]
    assert error.startswith("Error:") and "line 2" in error
    assert ("never closed" if replacement == "return (" else "unmatched") in error
    assert (small_tree[1].root / "shop/mod.py").read_bytes() == original
    assert not recorder.of("sink.file_edited")
    assert steps(recorder)[1].result == "error"


async def test_already_broken_python_keeps_edit_behavior(small_tree: Opened) -> None:
    await put(small_tree[1], "shop/mod.py", "def broken(:\n    return 1\n")
    model = script([READ], [EDIT], [FINISH], [FINISH])
    recorder = Recorder()
    end = await make_run(small_tree, model, recorder).run()
    assert end.state == "ready_for_review"
    assert observations(model.requests[2])[-1] == "Edited shop/mod.py."
    assert "return 2" in (small_tree[1].root / "shop/mod.py").read_text()


async def test_broad_search_reports_bounded_counts_per_file(small_tree: Opened) -> None:
    for index in range(55):
        await put(small_tree[1], f"hits/{index:02}.txt", "needle\n" * (index + 1))
    model = script([("search", {"query": "needle", "path": "hits"})], [FINISH])
    recorder = Recorder()
    await make_run(small_tree, model, recorder).run()
    content = observations(model.requests[-1])[0]
    assert "hits/00.txt: 1 matches" in content
    assert "hits/01.txt: 2 matches" in content
    assert "hits/49.txt: 50 matches" in content
    assert "5 more files" in content and "narrow" in content
    assert "hits/50.txt" not in content
    assert len(content) < 12_000


@pytest.mark.parametrize("oversized", [False, True])
async def test_first_failure_survives_both_output_cuts(small_tree: Opened, oversized: bool) -> None:
    # The first failure includes a real production frame; later failures push it out of both tails.
    await put(
        small_tree[1], "shop/mod.py", "def value():\n    assert False, 'production assertion'\n"
    )
    padding = "x" * (12_000 if oversized else 100)
    await put(
        small_tree[1],
        "tests/test_mod.py",
        "from shop.mod import value\n\ndef test_00_first():\n"
        + ("    print('x' * 12000)\n" if oversized else "")
        + "    value()\n"
        + "\n".join(
            f"\ndef test_later_{i}():\n    assert False, {padding!r}\n"
            for i in range(15 if oversized else 50)
        ),
    )
    model = script([RUN_TESTS], [FINISH])
    recorder = Recorder()
    await make_run(small_tree, model, recorder).run()
    output = observations(model.requests[-1])[0]
    assert "tests/test_mod.py::test_00_first" in output
    assert "shop/mod.py:2: AssertionError" in output
    assert "assert False, 'production assertion'" in output
    assert "AssertionError: production assertion" in output
    assert "shop/mod.py:2: AssertionError" in recorder.suites[0].output
    if oversized:
        assert "[first failure block truncated]" in output
    assert len(recorder.suites[0].output) <= 8192
    assert len(output.split("\n\n", 1)[1]) <= 4000


async def test_finish_reminds_once_after_an_untested_edit(small_tree: Opened) -> None:
    model = script([READ], [EDIT], [FINISH], [FINISH])
    recorder = Recorder()
    end = await make_run(small_tree, model, recorder).run()
    assert end.state == "ready_for_review" and end.steps == 4
    assert observations(model.requests[-1])[-1] == "Error: run the tests first"
    assert [s.result for s in steps(recorder)] == ["ok", "ok", "error", "ok"]
    assert len(recorder.of("sink.proposed")) == 1


async def test_test_run_clears_reminder_but_a_new_edit_rearms_it(small_tree: Opened) -> None:
    edit_again = (
        "edit_file",
        {"path": "shop/mod.py", "old_text": "return 2", "new_text": "return 3"},
    )
    model = script([READ], [EDIT], [RUN_TESTS], [edit_again], [FINISH], [FINISH])
    recorder = Recorder()
    await make_run(small_tree, model, recorder).run()
    assert observations(model.requests[-1])[-1] == "Error: run the tests first"


async def test_test_run_after_edit_allows_first_finish(small_tree: Opened) -> None:
    model = script([READ], [EDIT], [RUN_TESTS], [FINISH])
    recorder = Recorder()
    end = await make_run(small_tree, model, recorder).run()
    assert end.state == "ready_for_review" and end.steps == 4
    assert all(s.result == "ok" for s in steps(recorder))


async def test_pack_prompt_has_one_evidence_path_and_stop(small_tree: Opened) -> None:
    model = script([FINISH])
    await make_run(small_tree, model, Recorder()).run()
    prompt = str(model.requests[0][0]["content"])
    assert "one hypothesis" in prompt and "evidence" in prompt
    assert "Stop" in prompt and "several approaches" not in prompt
    assert _prompts.prompt_version(True) != "agent-v1-6972184812a7"


@pytest.mark.parametrize("history_prune_tokens", [None, 8000])
async def test_startup_exports_the_selected_prompt_version(
    tmp_path: Path, history_prune_tokens: int | None
) -> None:
    from live_helpers import live_settings

    from oversight.app import LiveRun
    from oversight_contracts.commands import StartAgent

    for enabled in (False, True):
        live = LiveRun(
            live_settings(
                tmp_path / str(enabled),
                harness_pack=enabled,
                history_prune_tokens=history_prune_tokens,
            )
        )
        await live.startup()
        try:
            stream = live.store.subscribe()
            started = await anext(stream)
            version = json.loads(started.envelope)["payload"]["prompt_version"]
            assert version == _prompts.prompt_version(enabled, history_prune_tokens)
            assert (version == "agent-v1-6972184812a7") is (
                not enabled and history_prune_tokens is None
            )
            agent = await live.start(StartAgent(task="Do the task."))
            assert live._kept[agent.agent_id].prompt_version == version
        finally:
            await live.shutdown()


async def test_off_preserves_prompt_and_tool_results(
    small_tree: Opened, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OVERSIGHT_HARNESS_PACK", "false")
    await put(small_tree[1], "shop/notes.txt", "needle\n" * 51)
    bad_edit = (
        "edit_file",
        {"path": "shop/mod.py", "old_text": "return 1", "new_text": "return ("},
    )
    model = script([("search", {"query": "needle"})], [READ], [bad_edit], [FINISH])
    recorder = Recorder()
    end = await make_run(small_tree, model, recorder, harness_pack=False).run()
    assert end.state == "ready_for_review" and end.steps == 4
    content = observations(model.requests[-1])
    assert content[0] == "\n".join(
        [f"shop/notes.txt:{i}: needle" for i in range(1, 51)]
        + ["[1 more matches; narrow the search]"]
    )
    assert content[-1] == "Edited shop/mod.py."
    assert _prompts.prompt_version(False) == _prompts.PROMPT_VERSION == "agent-v1-6972184812a7"
    assert (
        hashlib.sha256(_prompts.SYSTEM_PROMPT.encode()).hexdigest()
        == "8b02c2c5d9e6ea51b5be9c201cc0de04ebc56b51e49f87ea8d83e79cfd98c454"
    )


async def test_off_preserves_failed_test_output(
    small_tree: Opened, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OVERSIGHT_HARNESS_PACK", "false")
    from oversight.agents._observations import test_observation

    run = await small_tree[0].run_tests(small_tree[1], timeout_s=60)
    assert test_observation(run, 4000) == "Tests passed: 1 passed, 0 failed, 0 skipped."
    await put(
        small_tree[1], "tests/test_mod.py", "def test_failure():\n    assert False, 'x' * 12000\n"
    )
    model = script([RUN_TESTS], [FINISH])
    recorder = Recorder()
    await make_run(small_tree, model, recorder, harness_pack=False).run()
    run = recorder.suites[0]
    expected = (
        "Suite outcome: failed; 0 passed, 1 failed, 0 skipped\n"
        "Failed tests: tests/test_mod.py::test_failure\n"
        "Output truncated: showing the last 4000 characters.\n\n" + run.output[-4000:]
    )
    assert len(run.output) == 8192
    assert observations(model.requests[-1])[0] == expected


def test_setting_defaults_off_and_environment_overrides_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OVERSIGHT_HARNESS_PACK", raising=False)
    env_file = tmp_path / "settings.env"
    assert Settings.load(env_file).harness_pack is False
    env_file.write_text("OVERSIGHT_HARNESS_PACK=true\n")
    assert Settings.load(env_file).harness_pack is True
    monkeypatch.setenv("OVERSIGHT_HARNESS_PACK", "false")
    assert Settings.load(env_file).harness_pack is False


@pytest.mark.parametrize("raw,expected", [(None, None), ("", None), ("8000", 8000)])
def test_history_setting_loading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str | None, expected: int | None
) -> None:
    monkeypatch.delenv("OVERSIGHT_HISTORY_PRUNE_TOKENS", raising=False)
    env_file = tmp_path / "history.env"
    assert Settings.load(env_file).history_prune_tokens is None
    env_file.write_text("OVERSIGHT_HISTORY_PRUNE_TOKENS=9000\n")
    assert Settings.load(env_file).history_prune_tokens == 9000
    if raw is None:
        env_file.unlink()
    else:
        monkeypatch.setenv("OVERSIGHT_HISTORY_PRUNE_TOKENS", raw)
    assert Settings.load(env_file).history_prune_tokens == expected
    assert Settings(history_prune_tokens=expected).history_prune_tokens == expected


@pytest.mark.parametrize("raw", ["7999", "eight thousand", "8000.0", "-1"])
def test_history_setting_rejects_invalid_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    monkeypatch.setenv("OVERSIGHT_HISTORY_PRUNE_TOKENS", raw)
    with pytest.raises(ValueError):
        Settings.load(tmp_path / "missing.env")


@pytest.mark.parametrize("value", [7999, "8000", 8000.0, True, "", -1])
def test_history_setting_rejects_invalid_direct_construction(value: object) -> None:
    with pytest.raises(ValueError, match="OVERSIGHT_HISTORY_PRUNE_TOKENS"):
        Settings(history_prune_tokens=value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "name,value",
    [
        ("HISTORY_STUB", "different stub"),
        ("HISTORY_LOW_WATER", 0.5),
        ("HISTORY_REARM", 0.5),
        ("HISTORY_PROTECTED_CALLS", 7),
    ],
)
def test_history_policy_inputs_change_only_enabled_hash(
    monkeypatch: pytest.MonkeyPatch, name: str, value: object
) -> None:
    baseline = _prompts.prompt_version()
    enabled = _prompts.prompt_version(history_prune_tokens=8000)
    assert enabled != _prompts.prompt_version(history_prune_tokens=9000)
    monkeypatch.setattr(_prompts, name, value)
    assert _prompts.prompt_version() == baseline
    assert _prompts.prompt_version(history_prune_tokens=8000) != enabled
