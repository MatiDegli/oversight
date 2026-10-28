"""One suite boundary for agents, proposals, combinations and selected-case probes."""

from collections.abc import Mapping
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

from oversight.workspace.integrity import IntegrityError
from oversight.workspace.isolation import IsolatedRunner
from oversight.workspace.locks import offload
from oversight.workspace.measured_report import CallTarget
from oversight.workspace.runner import Spawn
from oversight.workspace.snapshot import finish_snapshot, take_snapshot
from oversight.workspace.suite import SuiteInterrupted, SuiteRun, SuiteUnavailable, run_suite
from oversight.workspace.suite_files import file_problems, file_stamp
from oversight.workspace.test_tree import capture_test_tree

if TYPE_CHECKING:
    from oversight.workspace.core import Tree, Workspace


def configure_suite(workspace: "Workspace", *, harness_pack: bool) -> None:
    """Select this workspace's output arm at composition time; never inspect process settings."""
    workspace.harness_pack = harness_pack


async def guarded_suite(
    workspace: "Workspace",
    tree: "Tree",
    *,
    python: Path,
    plugin_dir: Path,
    flags: tuple[str, ...],
    git_env: Mapping[str, str],
    timeout_s: float,
    case_ids: tuple[str, ...],
    spawn: Spawn,
    measured_targets: tuple[CallTarget, ...] | None = None,
) -> SuiteRun:
    """Caller retains main/worktree/lease through capture, namespace death and restoration."""
    state = workspace.integrity
    snapshot = await offload(partial(take_snapshot, tree.git_identity, state, timeout_s))
    runner = spawn
    try:
        before = await offload(partial(file_stamp, tree.root))
        tested_tree, tested_commit = await capture_test_tree(
            tree.root,
            workspace.tmp_dir,
            env=git_env,
            flags=flags,
            timeout_s=timeout_s,
        )
        try:
            run = await run_suite(
                python=python,
                plugin_dir=plugin_dir,
                runtime_root=workspace.runtime_root,
                tree_root=tree.root,
                timeout_s=timeout_s,
                redact=workspace.redact,
                spawn=runner,
                case_ids=case_ids,
                harness_pack=workspace.harness_pack,
                measured_targets=measured_targets,
            )
        except (SuiteInterrupted, SuiteUnavailable) as error:
            problems = await offload(partial(file_problems, tree.root, before))
            error.run = replace(
                error.run,
                tested_tree=tested_tree,
                tested_commit=tested_commit,
                problems=(*error.run.problems, *problems),
            )
            raise
        problems = await offload(partial(file_problems, tree.root, before))
        return replace(
            run,
            tested_tree=tested_tree,
            tested_commit=tested_commit,
            problems=(*run.problems, *problems),
        )
    finally:
        if isinstance(runner, IsolatedRunner) and not runner.cleanup_complete:
            error = IntegrityError(
                "suite namespace cleanup is unverified", ".git", code="main_suspect"
            )
            state.suspend(error, uncertain=True)
            raise error
        # offload keeps the lease through repeated cancellation; finish sets its latches in
        # the worker thread before any cancellation can propagate and release these locks.
        await offload(partial(finish_snapshot, snapshot, state))
