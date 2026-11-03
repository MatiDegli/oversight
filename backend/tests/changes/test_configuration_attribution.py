"""Immutable whole-file diagnostics, moving bases and contained comparison failures."""

import asyncio
import json

import pytest
from changes_helpers import EDIT, GIT_TIMEOUT_S, Harness
from decision_helpers import observed_target

from oversight.agents import AgentRun, ScriptedModel, UsageLedger, parse_script
from oversight.changes import _configuration as configuration
from oversight.workspace import ProcessResult, Tree
from oversight_contracts.events import AgentStep, ErrorReport, SuiteExecution
from oversight_contracts.review import ChangeFacts

NAMES = (
    "pytest.ini",
    ".pytest.ini",
    "pytest.toml",
    ".pytest.toml",
    "pyproject.toml",
    "setup.cfg",
    "tox.ini",
    "conftest.py",
)


async def snapshot(h: Harness, tree: Tree, *, commit: bool = False) -> str:
    async with h.workspace.locks(agent_id=tree.agent_id):
        ctx = h.manager._ctx
        await ctx.git(tree, "add", "-A")
        if commit:
            await ctx.git(tree, "commit", "-qm", "Fixture base", commit_as=tree.agent_id)
        return (
            await ctx.git(tree, "rev-parse", "HEAD")
            if commit
            else await ctx.git(tree, "write-tree")
        ).strip()


@pytest.mark.parametrize("name", NAMES)
@pytest.mark.parametrize("operation", ["add", "modify", "delete"])
@pytest.mark.anyio
async def test_whole_files_at_any_depth(h: Harness, name: str, operation: str) -> None:
    tree = await h.open(1)
    path = "nested/tests/" + name
    target = tree.root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    base = h.base_commit
    if operation != "add":
        target.write_text("[project]\nversion = '1'\n")
        base = await snapshot(h, tree, commit=True)
    if operation == "delete":
        target.unlink()
    else:
        target.write_text("[project]\nversion = '2'\n")
    captured = await snapshot(h, tree)
    async with h.workspace.locks(agent_id="agent-1"):
        note = await configuration.attribution(h.manager._ctx, tree, base, captured, limit=5)
    assert f"changed by this change (base {base})" in note
    assert json.dumps(path) in note and "suite results reflect it" in note
    assert "whole-file matching, pytest relevance unproven" in note
    assert "ignored files not compared" in note
    assert "configuration outside the repository unknown" in note


@pytest.mark.parametrize(
    "path",
    [
        "Pytest.ini",
        "tests/Conftest.py",
        "sitecustomize.py",
        "pkg/usercustomize.py",
        "pkg/startup.py",
    ],
)
def test_case_and_startup_nonmatches(path: str) -> None:
    assert not configuration.is_configuration(path)


@pytest.mark.anyio
@pytest.mark.parametrize("count,limit", [(6, 5), (21, 20)])
async def test_sorted_display_omission_is_not_incomplete_comparison(
    h: Harness, count: int, limit: int
) -> None:
    tree = await h.open(1)
    paths = [f"config/{i:02}/conftest.py" for i in range(count)]
    for path in reversed(paths):
        target = tree.root / path
        target.parent.mkdir(parents=True)
        target.write_text("# fixture\n")
    captured = await snapshot(h, tree)
    async with h.workspace.locks(agent_id="agent-1"):
        note = await configuration.attribution(
            h.manager._ctx, tree, h.base_commit, captured, limit=limit
        )
    assert note.index(paths[0]) < note.index(paths[limit - 1])
    assert all(path in note for path in paths[:limit])
    assert paths[limit] not in note and "1 paths omitted from display" in note
    assert "unknown (" not in note and "comparison incomplete" not in note


@pytest.mark.anyio
async def test_ignored_inputs_and_disk_edits_after_capture(h: Harness) -> None:
    tree = await h.open(1)
    (tree.root / ".gitignore").write_text("ignored/\n")
    ignored = tree.root / "ignored/conftest.py"
    ignored.parent.mkdir()
    ignored.write_text("# excluded\n")
    run = await h.workspace.run_tests(tree, timeout_s=60)
    (tree.root / "conftest.py").write_text("# after capture\n")
    note = await h.manager.tests_executed("agent-1", run)
    assert note is not None and "no compared configuration differences" in note
    assert "ignored files not compared" in note and "unchanged" not in note
    assert '"conftest.py"' not in note and "ignored/conftest.py" not in note
    [execution] = h.payloads(SuiteExecution)
    assert execution.tested_tree == run.tested_tree and execution.suite.cases_complete


@pytest.mark.anyio
async def test_moved_base_and_later_inheritance_do_not_attribute_as_own(h: Harness) -> None:
    await h.open(1)
    assert await h.edit(1, "pyproject.toml", "pythonpath", "# from A\npythonpath")
    assert await h.edit(1, *EDIT)
    assert await h.finish(1)
    tree_b = await h.run_agent(2, ("shop/mod.py", "return 1", "return 4"))
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    main = await h.main_head()
    await h.manager.approve("change-2", None, **observed_target(h.manager, "change-2"))
    assert h.manager._agents["agent-2"].state == "conflict"
    await h.manager.request_changes(
        "change-2", "Resolve.", **observed_target(h.manager, "change-2")
    )
    rec = h.manager._agents["agent-2"]
    assert rec.base_commit == main and tree_b.base_commit != main
    read = await tree_b.read_text("shop/mod.py")
    await tree_b.write_text("shop/mod.py", "def value():\n    return 4\n", expected_hash=read.hash)
    assert await h.manager.file_edited("agent-2", "shop/mod.py")
    run = await h.workspace.run_tests(tree_b, timeout_s=60)
    note = await h.manager.tests_executed("agent-2", run)
    assert note is not None and f"no compared configuration differences (base {main})" in note
    assert "changed by this change" not in note
    assert await h.finish(2)
    facts = h.payloads(ChangeFacts)[-1]
    [proposal_note] = [p for p in facts.problems if p.startswith(configuration.PREFIX)]
    assert proposal_note == note
    tree_c = await h.open(3)
    later = await h.manager.tests_executed(
        "agent-3", await h.workspace.run_tests(tree_c, timeout_s=60)
    )
    assert later == note
    assert await h.finish(3)
    assert proposal_note in h.payloads(ChangeFacts)[-1].problems


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mode", ["raise", "helper_raise", "timeout", "cut", "malformed", "missing", "unreadable"]
)
async def test_comparison_failure_through_real_manager_and_loop(
    h: Harness, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    tree = await h.open(1)
    original = h.workspace.git

    async def git(tree: Tree, *args: str, **kwargs) -> ProcessResult:
        if "--raw" in args:
            if mode == "raise":
                raise OSError("private contents must not reach the note")
            return ProcessResult(
                1 if mode == "unreadable" else 0,
                "" if mode == "timeout" else "partial\0",
                "private stderr",
                mode == "timeout",
                mode == "cut",
                0,
            )
        return await original(tree, *args, **kwargs)

    async def broken(*args, **kwargs) -> str:
        raise RuntimeError("private helper error")

    if mode == "helper_raise":
        monkeypatch.setattr(configuration, "attribution", broken)
    elif mode == "missing":
        h.manager._agents["agent-1"].base_commit = ""
    else:
        monkeypatch.setattr(h.workspace, "git", git)
    model = ScriptedModel(
        parse_script(
            json.dumps(
                {
                    "task": "Do it.",
                    "turns": [
                        {"tool_calls": [{"name": "run_tests", "arguments": {}}]},
                        {"tool_calls": [{"name": "finish", "arguments": {"summary": "Done."}}]},
                    ],
                }
            )
        )
    )
    # Restore an invalid base after the agent comparison so proposal's pre-existing Git work
    # remains valid. Only the new comparison is being faulted here.
    if mode == "missing":
        real_tests_executed = h.manager.tests_executed

        async def restore(agent_id: str, run):
            note = await real_tests_executed(agent_id, run)
            h.manager._agents[agent_id].base_commit = h.base_commit
            return note

        monkeypatch.setattr(h.manager, "tests_executed", restore)
    end = await AgentRun(
        h.task(1),
        model=model,
        workspace=h.workspace,
        tree=tree,
        sink=h.manager,
        publish=h.publish,
        ledger=UsageLedger(budget_usd=10),
    ).run()
    assert end.state == "ready_for_review"
    [step] = [s for s in h.payloads(AgentStep) if s.tool == "run_tests"]
    assert "configuration attribution unknown" in step.summary
    assert "private" not in step.summary
    assert step.summary.split("\n")[-1] in str(model.requests[-1])
    assert not any("unexpected error" in e.message for e in h.payloads(ErrorReport))
    agent_suites = [e for e in h.payloads(SuiteExecution) if e.phase == "agent"]
    assert len(agent_suites) == 1 and agent_suites[0].suite.cases_complete
    calls = [c for c in h.workspace.calls if "--raw" in c.args]
    assert all(c.timeout_s == GIT_TIMEOUT_S and "agent-1" in c.held for c in calls)


@pytest.mark.anyio
async def test_stop_during_comparison_propagates_after_one_suite(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    tree = await h.open(1)
    run = await h.workspace.run_tests(tree, timeout_s=60)
    entered = asyncio.Event()

    async def hold(*args, **kwargs) -> str:
        entered.set()
        await asyncio.Event().wait()
        return "unreachable"

    monkeypatch.setattr(configuration, "attribution", hold)
    task = asyncio.create_task(h.manager.tests_executed("agent-1", run))
    async with asyncio.timeout(5):
        await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(h.payloads(SuiteExecution)) == 1
    assert not h.payloads(ErrorReport)


@pytest.mark.anyio
async def test_missing_tested_identity_is_unknown(h: Harness) -> None:
    tree = await h.open(1)
    async with h.workspace.locks(agent_id="agent-1"):
        note = await configuration.attribution(h.manager._ctx, tree, h.base_commit, None, limit=5)
    assert "unknown (missing Git identity)" in note


@pytest.mark.anyio
@pytest.mark.parametrize("fault", ["identity", "unterminated", "partial", "blob", "deadline"])
async def test_incomplete_comparison_never_labels_a_partial_path_as_attributable(
    h: Harness, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    tree = await h.open(1)
    original = h.manager._ctx.run
    raw = f":000000 100644 {'0' * 40} {'1' * 40} A\0conftest.py\0"

    async def run(tree: Tree, *args: str) -> ProcessResult:
        if fault == "identity" and args[0] == "rev-parse":
            return ProcessResult(0, "not-a-tree\n", "", False, False, 0)
        if args[0] == "cat-file":
            return ProcessResult(1, "", "unreadable blob", False, False, 0)
        if "--raw" in args:
            if fault == "deadline":
                raise TimeoutError
            output = raw + "broken\0" if fault == "partial" else raw
            if fault == "unterminated":
                output = output[:-1]
            return ProcessResult(0, output, "", False, False, 0)
        return await original(tree, *args)

    monkeypatch.setattr(h.manager._ctx, "run", run)
    captured = await snapshot(h, tree)
    async with h.workspace.locks(agent_id="agent-1"):
        note = await configuration.attribution(
            h.manager._ctx, tree, h.base_commit, captured, limit=5
        )
    assert "configuration attribution unknown" in note
    assert "changed by this change" not in note and '"conftest.py"' not in note
    assert len(note) < 400


@pytest.mark.anyio
async def test_agent_attribution_reads_base_after_taking_the_lock(h: Harness) -> None:
    tree = await h.open(1)
    assert await h.edit(1, "pyproject.toml", "pythonpath", "# edit\npythonpath")
    new_base = await snapshot(h, tree, commit=True)
    run = await h.workspace.run_tests(tree, timeout_s=60)
    async with h.workspace.locks(agent_id="agent-1"):
        reporting = asyncio.create_task(h.manager.tests_executed("agent-1", run))
        # A queued report cannot cache the record base before it acquires this lock.
        await asyncio.sleep(0)
        h.manager._agents["agent-1"].base_commit = new_base
    note = await reporting
    assert note is not None and f"no compared configuration differences (base {new_base})" in note
