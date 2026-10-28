"""Ephemeral detached checkouts; callers hold main and the originating agent's locks."""

import os
import stat
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from functools import partial

from oversight.workspace.core import Tree, Workspace
from oversight.workspace.errors import InvalidPath
from oversight.workspace.files import ensure_plain_dir, remove_tree
from oversight.workspace.locks import offload

CLEANUP_TIMEOUT_S = 5


class _ProbeTree(Tree):
    """A private tree handle that cannot be returned by an agent workspace lookup."""

    def verify_root(self) -> None:
        runtime = self._workspace.runtime_root
        if self.root.parent != runtime / "tmp" or not self.root.name.startswith("base-probe-"):
            raise InvalidPath("invalid probe checkout root")
        for path in (runtime, self.root.parent, self.root):
            if not stat.S_ISDIR(path.lstat().st_mode) or os.path.realpath(path) != str(path):
                raise InvalidPath("probe checkout path is missing or linked")


@asynccontextmanager
async def base_probe_checkout(
    workspace: Workspace,
    *,
    agent: Tree,
    base: str,
    head: str,
    paths: tuple[str, ...],
    timeout_s: float,
) -> AsyncGenerator[Tree]:
    """Overlay test/support paths from head, including deletions, onto base.

    Even a partially successful worktree add is cleaned in finally. Cleanup uses
    two five-second Git deadlines and a symlink-safe filesystem fallback. Cleanup
    runs in the task that owns the locks, including on interruption.
    """
    root = workspace.tmp_dir / f"base-probe-{uuid.uuid4().hex}"
    tree = _ProbeTree(workspace, root, agent.agent_id, "", base)
    try:
        await offload(partial(ensure_plain_dir, workspace.runtime_root, root.parent))
        await workspace.git_ok(
            workspace.main, "worktree", "add", "--detach", str(root), base, timeout_s=timeout_s
        )
        await offload(tree.git_identity.verify_registration)
        for path in paths:
            tree.resolve(path)  # Validate every target, including parents, before checkout.
        await workspace.git_ok(
            tree,
            "restore",
            "--source",
            head,
            "--staged",
            "--worktree",
            "--",
            *paths,
            timeout_s=timeout_s,
        )
        yield tree
    finally:
        try:
            await workspace.git(
                workspace.main,
                "worktree",
                "remove",
                "--force",
                str(root),
                timeout_s=CLEANUP_TIMEOUT_S,
            )
        finally:
            await offload(partial(remove_tree, workspace.runtime_root, root))
            await workspace.git(
                workspace.main, "worktree", "prune", "--expire=now", timeout_s=CLEANUP_TIMEOUT_S
            )
