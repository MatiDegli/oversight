"""Capture the tested working tree with a temporary index; never disturb staging."""

from collections.abc import Mapping, Sequence
from pathlib import Path
from uuid import uuid4

from oversight.workspace.errors import WorkspaceError
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

    async def git(*args: str) -> str:
        result = await run_process(
            ["git", *flags, *args],
            cwd=root,
            timeout_s=timeout_s,
            env=dict(env) | {"GIT_INDEX_FILE": str(index)},
        )
        if result.returncode != 0 or result.timed_out:
            raise WorkspaceError("could not capture the tested Git tree")
        return result.stdout.strip()

    try:
        await git("read-tree", "HEAD")
        await git("add", "-A")
        tree = await git("write-tree")
        committed = await git("rev-parse", "HEAD^{tree}")
        commit = await git("rev-parse", "HEAD") if tree == committed else None
        return tree, commit
    finally:
        await offload(lambda: index.unlink(missing_ok=True))
        await offload(lambda: index.with_suffix(".lock").unlink(missing_ok=True))
