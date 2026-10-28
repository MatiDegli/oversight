"""A passing combined suite cannot authorize a different index, tree or merge parent."""

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from changes_helpers import EDIT, Harness, bounded
from decision_helpers import observed_target
from git_attack_helpers import raw_git

from oversight.workspace import ProcessResult, SuiteRun, Tree
from oversight_contracts.events import ChangeResolved, ErrorReport, SuiteExecution

pytestmark = pytest.mark.anyio


async def prepare(h: Harness) -> str:
    await h.run_agent(1, EDIT)
    (h.workspace.main.root / "ordinary.py").write_text("# unrelated main advance\n")
    await h.workspace.main.commit_all("Advance main", h.workspace.user_author())
    return await h.main_head()


async def damage(h: Harness, attack: str) -> Path | None:
    root = h.workspace.main.root
    metadata = root / ".git"
    if attack in {"unstaged", "staged"}:
        (root / "shop/mod.py").write_text("# code the suite did not test\n")
        if attack == "staged":
            await raw_git(root, "add", "--", "shop/mod.py")
    elif attack == "untracked":
        (root / "out.json").write_text("{}\n")
    elif attack == "gitlink":
        await raw_git(root, "update-index", "--add", "--cacheinfo", f"160000,{h.base_commit},sub")
    elif attack == "index_format":
        await raw_git(root, "update-index", "--index-version", "4")
    elif attack == "HEAD":
        (metadata / "refs/heads/main").write_text(f"{h.base_commit}\n")
    elif attack == "HEAD_symbolic":
        (metadata / "HEAD").write_text("ref: refs/heads/agent-1\n")
    elif attack == "ORIG_HEAD":
        (metadata / "ORIG_HEAD").write_text(f"{h.base_commit}\n")
    elif attack == "MERGE_HEAD_extra":
        with (metadata / "MERGE_HEAD").open("ab") as handle:
            handle.write(b"extra\n")
    elif attack == "MERGE_HEAD_no_newline":
        path = metadata / "MERGE_HEAD"
        path.write_bytes(path.read_bytes().rstrip(b"\n"))
    elif attack == "MERGE_HEAD_deleted":
        (metadata / "MERGE_HEAD").unlink()
    elif attack == "gitmodules":
        # Ignored .gitmodules escapes status; the pre-commit veto must still reject it.
        (root / ".gitmodules").write_text("[submodule]\n")
    else:
        lock = metadata / attack
        lock.write_text("unknown owner\n")
        return lock
    return None


ATTACKS = [
    "unstaged",
    "staged",
    "untracked",
    "gitlink",
    "index_format",
    "HEAD",
    "HEAD_symbolic",
    "ORIG_HEAD",
    "MERGE_HEAD_extra",
    "MERGE_HEAD_no_newline",
    "MERGE_HEAD_deleted",
    "index.lock",
    "HEAD.lock",
    "refs/heads/main.lock",
    "gitmodules",
]


@pytest.mark.parametrize("attack", ATTACKS)
@bounded
async def test_combined_suite_cannot_change_what_will_commit(
    h: Harness, monkeypatch: pytest.MonkeyPatch, attack: str
) -> None:
    pre = await prepare(h)
    with (h.workspace.main.root / ".git/info/exclude").open("a") as handle:
        handle.write(".gitmodules\n")
    real = h.workspace.run_tests
    after: int | None = None
    lock: Path | None = None

    async def suite(tree: Tree, *, timeout_s: float) -> SuiteRun:
        nonlocal after, lock
        run = await real(tree, timeout_s=timeout_s)
        assert run.result.outcome == "passed"
        lock = await damage(h, attack)
        after = len(h.workspace.calls)
        return run

    monkeypatch.setattr(h.workspace, "run_tests", suite)
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))

    assert after is not None
    later = h.workspace.calls[after:]
    assert not any(call.args[0] == "commit" for call in later), "untested merge reached commit"
    assert h.payloads(ChangeResolved) == []
    errors = h.payloads(ErrorReport)
    assert any("code=integrity_invalid" in e.message for e in errors)
    if attack in {"HEAD", "HEAD_symbolic", "ORIG_HEAD"} or attack.startswith("MERGE_HEAD") or lock:
        assert h.manager.main_suspect
        assert not any(call.args == ("merge", "--abort") for call in later)
        assert any("code=main_suspect" in e.message for e in errors)
    if lock:
        assert await asyncio.to_thread(lock.read_text) == "unknown owner\n"
    if attack not in {"HEAD", "HEAD_symbolic"}:
        assert await h.main_head() == pre


@pytest.mark.parametrize("tested_tree", [None, "f" * 40])
@bounded
async def test_candidate_and_tested_tree_must_match_before_status(
    h: Harness, monkeypatch: pytest.MonkeyPatch, tested_tree: str | None
) -> None:
    pre = await prepare(h)
    real = h.workspace.run_tests
    after = 0

    async def suite(tree: Tree, *, timeout_s: float) -> SuiteRun:
        nonlocal after
        assert any(c.tree == "main" and c.args == ("write-tree",) for c in h.workspace.calls)
        run = await real(tree, timeout_s=timeout_s)
        after = len(h.workspace.calls)
        return replace(run, tested_tree=tested_tree)

    monkeypatch.setattr(h.workspace, "run_tests", suite)
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))

    later = h.workspace.calls[after:]
    assert later[0].args == ("write-tree",)
    assert not any(c.args[0] in {"status", "commit"} for c in later[:2])
    assert not any(c.args[0] == "commit" for c in later)
    assert h.payloads(ChangeResolved) == [] and await h.main_head() == pre
    assert any(
        "index, tested tree and candidate tree differ" in e.message for e in h.payloads(ErrorReport)
    )


@bounded
async def test_index_bytes_are_checked_before_git_can_refresh_them(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await prepare(h)
    real_suite, real_git = h.workspace.run_tests, h.workspace.git
    poisoned = False
    original = b""

    async def suite(tree: Tree, *, timeout_s: float) -> SuiteRun:
        nonlocal poisoned, original
        run = await real_suite(tree, timeout_s=timeout_s)
        original = (tree.root / ".git/index").read_bytes()
        await damage(h, "index_format")
        poisoned = True
        return run

    async def git(tree: Tree, *args: str, **kwargs: Any) -> ProcessResult:
        if poisoned and args[0] in {"status", "write-tree"}:
            # Model a refreshing Git query erasing the evidence before a late check.
            (tree.root / ".git/index").write_bytes(original)
        return await real_git(tree, *args, **kwargs)

    monkeypatch.setattr(h.workspace, "run_tests", suite)
    monkeypatch.setattr(h.workspace, "git", git)
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    assert h.payloads(ChangeResolved) == []
    assert any("real index changed" in e.message for e in h.payloads(ErrorReport))


@bounded
async def test_barrier_runs_after_suite_publication_right_before_commit(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    pre = await prepare(h)
    real = h.publish
    injected = False

    async def publish(payload: Any) -> Any:
        nonlocal injected
        event = await real(payload)
        if isinstance(payload, SuiteExecution) and payload.phase == "combined":
            await damage(h, "staged")
            injected = True
        return event

    monkeypatch.setattr(h.manager._ctx, "publish", publish)  # pyright: ignore[reportPrivateUsage]
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    assert injected and h.payloads(ChangeResolved) == []
    assert await h.main_head() == pre
    assert any("real index changed" in e.message for e in h.payloads(ErrorReport))


@bounded
async def test_failed_suite_cannot_redirect_abort_through_orig_head(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    pre = await prepare(h)
    real = h.workspace.run_tests
    after = 0

    async def suite(tree: Tree, *, timeout_s: float) -> SuiteRun:
        nonlocal after
        run = await real(tree, timeout_s=timeout_s)
        await damage(h, "ORIG_HEAD")
        after = len(h.workspace.calls)
        return replace(run, result=run.result.model_copy(update={"outcome": "failed", "failed": 1}))

    monkeypatch.setattr(h.workspace, "run_tests", suite)
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    assert not any(c.args == ("merge", "--abort") for c in h.workspace.calls[after:])
    assert h.manager.main_suspect and await h.main_head() == pre
    assert h.payloads(ChangeResolved) == []
