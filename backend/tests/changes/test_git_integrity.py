"""The complete agent-suite → proposal → approval → combined-suite attack path."""

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from changes_helpers import EDIT, Harness, bounded
from decision_helpers import observed_target
from git_attack_helpers import malicious_sub, raw_git, suite_attack

from oversight.workspace import ProcessResult, SuiteRun, Tree, core, run_process, test_tree
from oversight_contracts.events import ChangeProposed, ChangeResolved, ErrorReport

pytestmark = pytest.mark.anyio


def monitor_every_server_git(
    h: Harness, monkeypatch: pytest.MonkeyPatch, sentinel: Path
) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    async def spawn(argv: list[str], **kwargs: Any) -> ProcessResult:
        result = await run_process(argv, **kwargs)
        if argv[0] == "git":
            command = argv[len(core.GIT_FLAGS) + 1]
            calls.append((str(kwargs["cwd"]), command))
            assert not await asyncio.to_thread(sentinel.exists), (
                f"nested filter executed after server git {command}"
            )
        return result

    monkeypatch.setattr(core, "run_process", spawn)
    monkeypatch.setattr(test_tree, "run_process", spawn)
    return calls


@bounded
async def test_agent_suite_to_proposal_capture_regression(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = h.workspace.tmp_dir / "filter-ran"
    tree = await h.open(1)
    assert await h.edit(1, *EDIT)
    # The filter runs from worktrees/agent-1/sub; keep temporary absolute paths out of diffs.
    (tree.root / "tests/test_attack.py").write_text(suite_attack(Path("../../../tmp/filter-ran")))
    pre = await tree.head()
    calls = monitor_every_server_git(h, monkeypatch, sentinel)
    run = await h.workspace.run_tests(tree, timeout_s=10)
    assert run.result.outcome == "passed", run.output
    assert (tree.root / "sub/.git").is_dir()
    assert not sentinel.exists()

    proposed = await h.finish(1)

    # With guards removed, the proposal commits the gitlink, then capture's add executes
    # the filter. Keep this assertion first so the mutation proves the external effect.
    assert not sentinel.exists(), "capture executed the nested filter"
    assert not proposed and h.payloads(ChangeProposed) == []
    assert any("code=integrity_invalid" in e.message for e in h.payloads(ErrorReport))
    assert await tree.head() == pre
    assert not any(command == "commit" for _, command in calls)


@pytest.mark.parametrize(
    "boundary",
    [
        "proposal_capture",
        "as_clicked",
        "preparation",
        "combined_capture",
        "combined_suite",
        "commit",
    ],
)
@bounded
async def test_nested_injection_at_each_remaining_boundary(
    h: Harness, monkeypatch: pytest.MonkeyPatch, boundary: str
) -> None:
    sentinel = h.tmp_path / "filter-ran"
    tree = await h.open(1)
    assert await h.edit(1, *EDIT)
    # Force the existing combined-suite branch with an unrelated main advance.
    (h.workspace.main.root / "ordinary.py").write_text("# main advanced\n")
    await h.workspace.main.commit_all("Advance main", h.workspace.user_author())
    pre = await h.main_head()
    calls = monitor_every_server_git(h, monkeypatch, sentinel)
    real_suite, real_git = h.workspace.run_tests, h.workspace.git
    injected = False

    async def inject(target: Tree) -> None:
        nonlocal injected
        assert not injected
        await malicious_sub(target.root, sentinel)
        injected = True

    async def suite(target: Tree, *, timeout_s: float, **kwargs: Any) -> SuiteRun:
        if boundary == "proposal_capture" and target is tree:
            await inject(target)
        if boundary == "combined_capture" and target is h.workspace.main:
            await inject(target)
        run = await real_suite(target, timeout_s=timeout_s, **kwargs)
        if boundary == "combined_suite" and target is h.workspace.main:
            await inject(target)
        return run

    async def git(target: Tree, *args: str, **kwargs: Any) -> ProcessResult:
        if target is h.workspace.main and (
            (boundary == "preparation" and args[0] == "merge" and "--abort" not in args)
            or (boundary == "commit" and args[0] == "commit")
        ):
            await inject(target)
        return await real_git(target, *args, **kwargs)

    monkeypatch.setattr(h.workspace, "run_tests", suite)
    monkeypatch.setattr(h.workspace, "git", git)
    proposed = await h.finish(1)
    if boundary == "proposal_capture":
        # The current proposal reports an error suite but keeps a reviewable change.
        assert proposed
        assert h.payloads(ChangeProposed)[0].tests.outcome == "error"
    else:
        assert proposed
    if boundary == "as_clicked":
        await inject(tree)
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))

    assert injected and not sentinel.exists()
    assert h.payloads(ChangeResolved) == []
    errors = h.payloads(ErrorReport)
    assert any("code=integrity_invalid" in error.message for error in errors)
    assert await h.main_head() == pre
    assert not any(root == str(h.workspace.main.root) and cmd == "commit" for root, cmd in calls)
    suspect = boundary in {"preparation", "combined_capture", "combined_suite", "commit"}
    assert h.manager.main_suspect is suspect
    if suspect:
        assert any("code=main_suspect" in error.message for error in errors)
    if boundary in {"combined_capture", "combined_suite", "commit"}:
        # MERGE_HEAD remains: scanning refuses the abort too, so recovery cannot certify main.
        assert (h.workspace.main.root / ".git/MERGE_HEAD").exists()
        assert not any(
            root == str(h.workspace.main.root) and cmd == "merge"
            for root, cmd in calls[
                next(
                    i
                    for i, (root, cmd) in enumerate(calls)
                    if root == str(h.workspace.main.root) and cmd == "merge"
                )
                + 1 :
            ]
        )


@pytest.mark.parametrize("when", ["before_add", "after_add"])
@bounded
async def test_proposal_vetoes_gitlinks_before_head(
    h: Harness, monkeypatch: pytest.MonkeyPatch, when: str
) -> None:
    tree = await h.open(1)
    assert await h.edit(1, *EDIT)
    pre = await tree.head()
    real = h.workspace.git

    async def poison() -> None:
        await raw_git(tree.root, "update-index", "--add", "--cacheinfo", f"160000,{pre},sub")

    async def git(target: Tree, *args: str, **kwargs: Any) -> ProcessResult:
        result = await real(target, *args, **kwargs)
        if target is tree and args[0] == "add" and when == "after_add":
            await poison()
        assert args[0] != "commit", "gitlink reached commit"
        return result

    if when == "before_add":
        await poison()
    monkeypatch.setattr(h.workspace, "git", git)
    assert not await h.finish(1)
    assert await tree.head() == pre
    assert any("Git links are forbidden" in e.message for e in h.payloads(ErrorReport))


@pytest.mark.parametrize("code", [0, 1])
@bounded
async def test_yes_no_rejects_truncated_answers(
    h: Harness, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    await h.run_agent(1, EDIT)
    pre = await h.main_head()
    real = h.workspace.git
    hits = 0

    async def git(target: Tree, *args: str, **kwargs: Any) -> ProcessResult:
        nonlocal hits
        result = await real(target, *args, **kwargs)
        if (
            target is h.workspace.main
            and args == ("rev-parse", "-q", "--verify", "MERGE_HEAD")
            and not hits
        ):
            hits += 1
            return replace(result, returncode=code, truncated=True)
        return result

    monkeypatch.setattr(h.workspace, "git", git)
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    assert hits == 1 and await h.main_head() == pre
    assert h.payloads(ChangeResolved) == []
    assert any("output was truncated" in e.message for e in h.payloads(ErrorReport))
