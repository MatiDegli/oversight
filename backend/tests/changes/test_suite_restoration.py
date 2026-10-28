"""A tampering combined suite is not evidence, even after successful restitution."""

import asyncio
from typing import Any

import pytest
from changes_helpers import EDIT, Harness, bounded
from decision_helpers import observed_target

from oversight.changes import WrongState
from oversight.workspace import IntegrityError, ProcessResult, core, run_process, test_tree
from oversight_contracts.events import ChangeResolved, ErrorReport, SuiteExecution

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "item", ["config", "index", "HEAD", "main_ref", "MERGE_HEAD", "ORIG_HEAD", "index.lock"]
)
@bounded
async def test_combined_tamper_restores_before_abort_or_blocks_all_git(
    h: Harness, monkeypatch: pytest.MonkeyPatch, item: str
) -> None:
    await h.run_agent(1, EDIT)
    root = h.workspace.main.root
    path = "refs/heads/main" if item == "main_ref" else item
    # This test enters the combined suite on main only; the proposal was already recorded.
    (root / "tests/test_metadata.py").write_text(
        "from pathlib import Path\n\ndef test_metadata():\n"
        f"    target = Path('.git') / {path!r}\n"
        + (
            "    with target.open('a') as handle:\n"
            "        handle.write('[core]\\n    fsmonitor = untrusted\\n')\n"
            if item == "config"
            else "    target.write_text('1' * 40 + '\\n')\n"
        )
    )
    await h.workspace.main.commit_all("Advance main with the suite", h.workspace.user_author())
    pre = await h.main_head()
    metadata = root / ".git"
    originals = {
        name: (metadata / name).read_bytes() for name in ("config", "HEAD", "refs/heads/main")
    }
    sentinel = h.workspace.tmp_dir / "git-sentinel"
    calls: list[str] = []

    async def spawn(argv: list[str], **kwargs: Any) -> ProcessResult:
        result = await run_process(argv, **kwargs)
        if argv[0] == "git":
            calls.append(argv[len(core.GIT_FLAGS) + 1])
            assert not await asyncio.to_thread(sentinel.exists)
        return result

    monkeypatch.setattr(core, "run_process", spawn)
    monkeypatch.setattr(test_tree, "run_process", spawn)
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    assert h.workspace.integrity.rules_suspended
    assert h.payloads(ChangeResolved) == []
    assert not any(e.phase == "combined" for e in h.payloads(SuiteExecution))
    assert "commit" not in calls
    errors = h.payloads(ErrorReport)
    if item == "index.lock":
        assert h.manager.main_suspect and "code=main_suspect" in errors[-1].message
        assert (metadata / "index.lock").read_text() == "1" * 40 + "\n"
        assert (metadata / "MERGE_HEAD").exists()
        before = len(calls)
        # A second click is rejected before even an object-only Git can run.
        with pytest.raises(WrongState):
            await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
        assert len(calls) == before
    else:
        assert not h.manager.main_suspect
        assert any("code=integrity_invalid" in error.message for error in errors)
        assert not (metadata / "MERGE_HEAD").exists()
        assert await h.main_head() == pre
        assert {name: (metadata / name).read_bytes() for name in originals} == originals
        assert (await h.main_git("status", "--porcelain")).stdout == ""


@bounded
async def test_agent_suite_uncertainty_blocks_an_existing_human_approval(h: Harness) -> None:
    await h.run_agent(1, EDIT)
    tree = h.workspace.worktree("agent-1")

    async def poison(argv: Any, **kwargs: Any) -> ProcessResult:
        result = await run_process(argv, **kwargs)
        (tree.git_identity.directory / "index.lock").write_bytes(b"unowned")
        return result

    with pytest.raises(IntegrityError, match="code=main_suspect"):
        await h.workspace.run_tests(tree, timeout_s=10, spawn=poison)
    assert h.manager.main_suspect
    before = len(h.workspace.calls)
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    assert len(h.workspace.calls) == before
    assert h.payloads(ChangeResolved) == []
    assert "code=main_suspect" in h.payloads(ErrorReport)[-1].message
