"""Capture the tested working tree with a temporary index; never disturb staging."""

import sys
import time
from collections.abc import Mapping, Sequence
from functools import partial
from pathlib import Path
from uuid import uuid4

from oversight.workspace._platform import executable, unlink_retry
from oversight.workspace.errors import WorkspaceError
from oversight.workspace.integrity import (
    GitIdentity,
    remaining,
    scan_nested_git,
    veto_gitmodules,
    veto_index,
)
from oversight.workspace.locks import offload
from oversight.workspace.runner import run_process


async def capture_test_tree(
    root: Path,
    tmp: Path,
    *,
    env: Mapping[str, str],
    flags: Sequence[str],
    timeout_s: float,
) -> tuple[str, str | None]:
    index = tmp / f"suite-index-{uuid4().hex}"
    deadline = time.monotonic() + timeout_s

    async def git(*args: str) -> str:
        binary = "git"
        if sys.platform == "win32":
            binary = await offload(lambda: executable("git"))
        result = await run_process(
            [binary, *flags, *args],
            cwd=root,
            timeout_s=remaining(deadline),
            env=dict(env) | {"GIT_INDEX_FILE": str(index)},
        )
        if result.truncated:
            raise WorkspaceError(f"git {args[0]} failed: output was truncated")
        if result.returncode != 0 or result.timed_out:
            raise WorkspaceError("could not capture the tested Git tree")
        return result.stdout

    try:
        identity = GitIdentity(root, Path(env["GIT_DIR"]), Path(env["GIT_COMMON_DIR"]))
        await offload(partial(scan_nested_git, identity, deadline))
        await git("read-tree", "HEAD")
        veto_index(await git("ls-files", "-s", "-z"))
        await offload(partial(veto_gitmodules, root, deadline))
        await git("add", "-A")
        tree = (await git("write-tree")).strip()
        committed = (await git("rev-parse", "HEAD^{tree}")).strip()
        commit = (await git("rev-parse", "HEAD")).strip() if tree == committed else None
        return tree, commit
    finally:
        if sys.platform == "win32":
            try:
                await offload(lambda: unlink_retry(index, missing_ok=True, nt=True))
            finally:
                await offload(
                    lambda: unlink_retry(index.with_suffix(".lock"), missing_ok=True, nt=True)
                )
        else:
            await offload(lambda: index.unlink(missing_ok=True))
            await offload(lambda: index.with_suffix(".lock").unlink(missing_ok=True))
