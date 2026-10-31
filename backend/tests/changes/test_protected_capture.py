"""Inert protected-path capture: separate receipts, real candidate objects and strict decoding."""

import asyncio
from dataclasses import replace
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from changes_helpers import Harness

from oversight.changes._candidate import changed_inventory
from oversight.changes._protected_paths import (
    BUILTIN_TABLE_VERSION,
    ChangedFileInventory,
    ProtectedPathSnapshot,
    capture_proposal_paths,
    check_protected_paths,
)
from oversight.changes._support import ChangeContext
from oversight.workspace import MergeGuard, ProcessResult, WorkspaceError
from oversight_contracts.common import FileStatus
from oversight_contracts.events import ChangeProposed, FileStat, SuiteResult
from oversight_contracts.graph import ChangeGraph, ImpactCone
from oversight_contracts.policy import EvidenceRef

RUN_SEQ = 1
PROPOSAL_SEQ = 17


def file(path: str, status: FileStatus = "modified") -> FileStat:
    return FileStat(path=path, status=status, lines_added=3, lines_removed=2)


def proposal(files: list[FileStat]) -> ChangeProposed:
    return ChangeProposed(
        change_id="change-1",
        agent_id="agent-1",
        revision=1,
        task="Update the project.",
        summary="Done.",
        base_commit="base",
        head_commit="head",
        files=files,
        lines_added=sum(f.lines_added for f in files),
        lines_removed=sum(f.lines_removed for f in files),
        graph=ChangeGraph(
            nodes_added=[], nodes_modified=[], nodes_removed=[], edges_added=[], edges_removed=[]
        ),
        tests=SuiteResult(
            outcome="passed", passed=1, failed=0, skipped=0, duration_ms=1, failed_tests=[]
        ),
        impact=ImpactCone(
            seeds=[],
            entries=[],
            edges_followed="exact",
            max_depth=3,
            max_nodes=20,
            omitted_by_depth=0,
            omitted_by_limit=0,
            tests_reaching=0,
        ),
    )


def test_capture_preserves_files_statuses_counts_and_separate_receipts() -> None:
    source = proposal([file("a.txt", "added"), file("b.py"), file("c.bin", "deleted")])
    captured = capture_proposal_paths(source, proposal_seq=PROPOSAL_SEQ, run_started_seq=RUN_SEQ)
    assert captured is not None
    snapshot, inventory = captured
    assert BUILTIN_TABLE_VERSION == 1
    assert snapshot == ProtectedPathSnapshot(evidence=(EvidenceRef(event_seq=RUN_SEQ),))
    assert snapshot.paths is None and snapshot.complete
    assert inventory == ChangedFileInventory(
        files=tuple(source.files), complete=True, evidence=(EvidenceRef(event_seq=PROPOSAL_SEQ),)
    )
    # Changing the event's mutable list cannot change the captured inventory.
    source.files.clear()
    assert inventory.files == (file("a.txt", "added"), file("b.py"), file("c.bin", "deleted"))


def test_capture_empty_diff_is_complete_and_evidenced() -> None:
    captured = capture_proposal_paths(
        proposal([]), proposal_seq=PROPOSAL_SEQ, run_started_seq=RUN_SEQ
    )
    assert captured is not None
    assert captured[1] == ChangedFileInventory(
        files=(), complete=True, evidence=(EvidenceRef(event_seq=PROPOSAL_SEQ),)
    )
    checked = check_protected_paths(*captured)
    assert checked is not None and checked.matches == ()


@pytest.mark.parametrize("proposal_seq,run_started_seq", [(None, 1), (17, None), (None, None)])
@pytest.mark.parametrize("files", [[], [file("uv.lock")]])
def test_capture_missing_receipt_is_unavailable(
    proposal_seq: int | None, run_started_seq: int | None, files: list[FileStat]
) -> None:
    assert (
        capture_proposal_paths(
            proposal(files), proposal_seq=proposal_seq, run_started_seq=run_started_seq
        )
        is None
    )


@pytest.mark.parametrize("status", ["added", "modified", "deleted"])
@pytest.mark.parametrize(
    "path,protected",
    [
        ("pyproject.toml", True),
        ("packages/app/uv.lock", True),
        ("packages/app/tests/conftest.py", True),
        (".github/workflows/ci.yml", True),
        (".gitlab-ci.yml", True),
        (".gitignore", True),
        (".gitleaks.toml", True),
        ("nested/.gitignore", False),
        ("nested/.github/workflows/ci.yml", False),
        ("uv.locked", False),
        ("shop.py", False),
    ],
)
def test_captured_sources_keep_existing_matcher_results(
    status: FileStatus, path: str, protected: bool
) -> None:
    files = [file(path, status)]
    captured = capture_proposal_paths(
        proposal(files), proposal_seq=PROPOSAL_SEQ, run_started_seq=RUN_SEQ
    )
    assert captured is not None
    checked = check_protected_paths(*captured)
    assert checked == check_protected_paths(
        ProtectedPathSnapshot(evidence=(EvidenceRef(event_seq=RUN_SEQ),)),
        ChangedFileInventory(
            files=tuple(files), complete=True, evidence=(EvidenceRef(event_seq=PROPOSAL_SEQ),)
        ),
    )
    assert checked is not None
    assert bool(checked.matches) is protected


@pytest.fixture
def guard() -> MergeGuard:
    return MergeGuard(Mock(), "a" * 40, "b" * 40, "c" * 40, b"")


def candidate_evidence(guard: MergeGuard) -> tuple[EvidenceRef, ...]:
    return (
        EvidenceRef(event_seq=PROPOSAL_SEQ),
        EvidenceRef(git_object=guard.pre),
        EvidenceRef(git_object=guard.candidate_tree),
    )


@pytest.mark.anyio
async def test_candidate_inventory_reads_all_statuses_and_both_rename_sides(h: Harness) -> None:
    ctx, tree = h.manager._ctx, h.workspace.main
    root = tree.root
    initial = {
        "delete.txt": b"delete\n",
        "old-ci.txt": b"workflow to move\n",
        ".gitignore": b"pattern to move\n",
    }
    for path, content in initial.items():
        (root / path).write_bytes(content)
    pre = await tree.commit_all("test: prepare inventory base", h.workspace.user_author())
    assert pre is not None
    added = {
        "added.txt": b"new\n",
        "packages/app/pyproject.toml": b"[project]\n",
        "packages/app/tests/conftest.py": b"# configuration\n",
        ".gitlab-ci.yml": b"stages: [test]\n",
        ".pre-commit-config.yaml": b"repos: []\n",
        "image.bin": b"\x00\xff\x01\x00",
    }
    for path, content in added.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    (root / "shop/mod.py").write_text("def value():\n    return 7\n")
    (root / "delete.txt").unlink()
    (root / ".github/workflows").mkdir(parents=True)
    (root / "old-ci.txt").rename(root / ".github/workflows/ci.yml")
    (root / ".gitignore").rename(root / "moved-ignore.txt")
    async with h.workspace.locks(main=True):
        await ctx.git(tree, "add", "-A")
        candidate = (await ctx.git(tree, "write-tree")).strip()
        guard = MergeGuard(tree.git_identity, pre, pre, candidate, b"")
        # Only the immutable candidate tree counts, regardless of subsequent worktree edits.
        (root / "late.txt").write_text("not in the candidate\n")
        inventory = await changed_inventory(ctx, guard, PROPOSAL_SEQ)
        empty_guard = replace(guard, candidate_tree=pre)
        empty = await changed_inventory(ctx, empty_guard, PROPOSAL_SEQ)
    expected = dict.fromkeys(added, "added") | {
        ".github/workflows/ci.yml": "added",
        ".gitignore": "deleted",
        "delete.txt": "deleted",
        "moved-ignore.txt": "added",
        "old-ci.txt": "deleted",
        "shop/mod.py": "modified",
    }
    assert inventory.complete
    assert [(f.path, f.status) for f in inventory.files] == sorted(expected.items())
    assert all(f.lines_added == f.lines_removed == 0 for f in inventory.files)
    assert inventory.evidence == candidate_evidence(guard)
    snapshot = ProtectedPathSnapshot(evidence=(EvidenceRef(event_seq=RUN_SEQ),))
    checked = check_protected_paths(snapshot, inventory)
    assert checked is not None
    assert {m.path for m in checked.matches} == {
        ".github/workflows/ci.yml",
        ".gitignore",
        ".gitlab-ci.yml",
        ".pre-commit-config.yaml",
        "packages/app/pyproject.toml",
        "packages/app/tests/conftest.py",
    }
    assert empty == ChangedFileInventory(
        files=(), complete=True, evidence=candidate_evidence(empty_guard)
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "suffix",
    [
        "A\0new.py",  # Truncated path.
        "D",  # Truncated status.
        "A\0",  # Odd field count.
        "A\0new.py\0D\0",  # Odd field count after a valid entry.
        "\0",  # Empty extra field.
        "A\0\0",  # Empty path.
        *[f"{status}\0other.py\0" for status in ("T", "U", "R100", "C100", "X", "M100", "")],
        *[
            f"A\0{path}\0"
            for path in (
                "/uv.lock",
                "../uv.lock",
                "a/../uv.lock",
                "./uv.lock",
                "a/./uv.lock",
                "a//uv.lock",
                "a/",
                "a\\uv.lock",
                "C:/uv.lock",
                ".git/config",
                "a/.git/config",
                "a/\nuv.lock",
                "a/\tuv.lock",
                "a/\x7fuv.lock",
            )
        ],
    ],
)
@pytest.mark.parametrize("prefix", ["", "M\0uv.lock\0"])
async def test_candidate_malformed_output_never_keeps_partial_inventory(
    guard: MergeGuard, prefix: str, suffix: str
) -> None:
    git = AsyncMock(return_value=prefix + suffix)
    ctx = Mock(workspace=Mock(main=Mock()), git=git)
    inventory = await changed_inventory(cast(ChangeContext, ctx), guard, PROPOSAL_SEQ)
    assert inventory == ChangedFileInventory(
        files=(), complete=False, evidence=candidate_evidence(guard)
    )
    assert (
        check_protected_paths(
            ProtectedPathSnapshot(evidence=(EvidenceRef(event_seq=RUN_SEQ),)), inventory
        )
        is None
    )
    git.assert_awaited_once_with(
        ctx.workspace.main,
        "diff",
        "--name-status",
        "--no-renames",
        "-z",
        guard.pre,
        guard.candidate_tree,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("fault", ["truncated", "failed", "timeout"])
async def test_candidate_git_failure_is_incomplete(
    h: Harness, guard: MergeGuard, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    # A runner may report truncation even when its retained tail ends in NUL.
    result = ProcessResult(
        returncode=0 if fault == "truncated" else None if fault == "timeout" else 1,
        stdout="M\0uv.lock\0",
        stderr="unavailable",
        timed_out=fault == "timeout",
        truncated=fault == "truncated",
        duration_ms=0,
    )
    monkeypatch.setattr(h.workspace, "git", AsyncMock(return_value=result))
    async with h.workspace.locks(main=True):
        inventory = await changed_inventory(h.manager._ctx, guard, PROPOSAL_SEQ)
    assert inventory == ChangedFileInventory(
        files=(), complete=False, evidence=candidate_evidence(guard)
    )


@pytest.mark.anyio
async def test_candidate_cancellation_propagates(guard: MergeGuard) -> None:
    ctx = Mock(workspace=Mock(main=Mock()), git=AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await changed_inventory(cast(ChangeContext, ctx), guard, PROPOSAL_SEQ)


@pytest.mark.anyio
async def test_candidate_unavailable_query_is_incomplete(guard: MergeGuard) -> None:
    ctx = Mock(workspace=Mock(main=Mock()), git=AsyncMock(side_effect=WorkspaceError("lost")))
    inventory = await changed_inventory(cast(ChangeContext, ctx), guard, PROPOSAL_SEQ)
    assert inventory == ChangedFileInventory(
        files=(), complete=False, evidence=candidate_evidence(guard)
    )
