"""Pre-Git filesystem guards. No Git process is used to discover repository identity."""

import asyncio
import hashlib
import json
import os
import stat
import time
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from oversight.workspace.core import Tree, Workspace

from oversight.limits import SizeLimitError, bounded_text
from oversight.workspace.errors import WorkspaceError
from oversight.workspace.locks import offload
from oversight.workspace.runner import ProcessResult, Spawn

# Only exact invocations known to use objects/metadata may bypass the filesystem walk.
# In particular diff-index and diff-files can enter a nested repository and run filters.
OBJECT_COMMANDS = frozenset({"rev-parse", "rev-list", "merge-base", "write-tree"})


def skips_scan(args: tuple[str, ...]) -> bool:
    return (
        args[0] in OBJECT_COMMANDS
        or args == ("ls-files", "-s", "-z")
        or (len(args) == 3 and args[0] == "cat-file" and args[1] in {"-p", "-e", "-t", "-s"})
    )


class IntegrityError(WorkspaceError):
    """A fail-closed refusal with a reason safe to embed in an event's reason field."""

    def __init__(self, reason: str, path: str, *, code: str = "integrity_invalid") -> None:
        self.code = code
        # Escape delimiters as well as controls: a filename cannot forge another code field.
        shown = json.dumps(path[:240], ensure_ascii=True).replace(";", "\\u003b")
        super().__init__(f"code={code}; reason={reason}: {shown}")


def remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise IntegrityError("Git integrity deadline exceeded", ".", code="git_failed")
    return left


@dataclass(frozen=True)
class GitIdentity:
    root: Path
    directory: Path
    common: Path

    def verify_root_entry(self) -> None:
        """The walk exemption requires the registered kind and exact linked-tree pointer."""
        entry = self.root / ".git"
        try:
            mode = entry.lstat().st_mode
            valid = (
                stat.S_ISDIR(mode)
                if self.directory == self.common
                else stat.S_ISREG(mode) and bounded_text(entry) == f"gitdir: {self.directory}\n"
            )
            if not valid:
                raise IntegrityError("Git root entry differs from registration", ".git")
        except (OSError, UnicodeError, SizeLimitError) as error:
            raise IntegrityError("unreadable registered Git root entry", ".git") from error

    def verify_registration(self) -> None:
        """Check the newly server-created checkout once; never discover pins from its pointer."""
        try:
            if self.directory == self.common:
                valid = self.directory.is_dir()
            else:
                valid = (
                    bounded_text(self.root / ".git") == f"gitdir: {self.directory}\n"
                    and bounded_text(self.directory / "gitdir") == f"{self.root}/.git\n"
                )
            if not valid or os.path.realpath(self.directory) != str(self.directory):
                raise IntegrityError("unexpected Git registration", ".git")
        except OSError as error:
            raise IntegrityError("unreadable Git registration", ".git") from error

    def pins(self) -> dict[str, str]:
        return {
            "GIT_DIR": str(self.directory),
            "GIT_COMMON_DIR": str(self.common),
            "GIT_WORK_TREE": str(self.root),
            "GIT_NO_REPLACE_OBJECTS": "1",
        }


def scan_nested_git(identity: GitIdentity, deadline: float, *, initializing: bool = False) -> None:
    """Walk every directory (including ignored files), without following symbolic links.

    The sole exemption is the registered root's literal .git entry of the registered kind
    and, for linked trees, exact pointer content. Only init may start without that entry.
    """
    root = identity.root
    try:
        if not stat.S_ISDIR(root.lstat().st_mode) or os.path.realpath(root) != str(root):
            raise IntegrityError("linked Git scan root", ".")
    except OSError as error:
        raise IntegrityError("unreadable Git scan root", ".") from error
    pending = [root]
    while pending:
        remaining(deadline)
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    remaining(deadline)
                    path = Path(entry.path)
                    relative = path.relative_to(root).as_posix()
                    if entry.name.casefold() == ".git":
                        if path != root / ".git" or entry.is_symlink():
                            raise IntegrityError("nested or linked Git entry", relative)
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path)
        except OSError as error:
            raise IntegrityError(
                f"Git scan failed ({type(error).__name__})",
                directory.relative_to(root).as_posix(),
            ) from error
    if not initializing or os.path.lexists(root / ".git"):
        identity.verify_root_entry()
    remaining(deadline)


@asynccontextmanager
async def lease_until(
    lease: AbstractAsyncContextManager[None], deadline: float
) -> AsyncGenerator[None]:
    # Bound waiting for the lease too, but leave subprocess timeout/cleanup to the runner.
    try:
        async with asyncio.timeout(remaining(deadline)):
            await lease.__aenter__()
    except TimeoutError as error:
        raise IntegrityError("Git integrity deadline exceeded", ".", code="git_failed") from error
    try:
        yield
    finally:
        await lease.__aexit__(None, None, None)


async def guarded_git(
    identity: GitIdentity,
    args: tuple[str, ...],
    *,
    scan_identity: GitIdentity,
    verify: Callable[[], None],
    lease: AbstractAsyncContextManager[None],
    flags: Sequence[str],
    env: Mapping[str, str],
    timeout_s: float,
    spawn: Spawn,
) -> ProcessResult:
    deadline = time.monotonic() + timeout_s
    async with lease_until(lease, deadline):
        await offload(verify)
        if not skips_scan(args):
            scan = partial(scan_nested_git, scan_identity, deadline)
            await offload(partial(scan, initializing=True) if args[0] == "init" else scan)
        submodules = ("--ignore-submodules=all",) if args[0] in {"status", "diff"} else ()
        result = await spawn(
            ["git", *flags, args[0], *submodules, *args[1:]],
            cwd=identity.root,
            timeout_s=remaining(deadline),
            env=env,
        )
        if result.truncated:
            raise WorkspaceError(f"git {args[0]} failed: output was truncated")
        return result


def veto_index(output: str) -> None:
    """Interpret complete ls-files -s -z output, including every conflict stage."""
    if output and not output.endswith("\0"):
        raise IntegrityError("incomplete Git index query", ".", code="git_failed")
    for record in output.split("\0"):
        if not record:
            continue
        metadata, separator, path = record.partition("\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            raise IntegrityError("invalid Git index query", ".", code="git_failed")
        if fields[0] == "160000":
            raise IntegrityError("Git links are forbidden", path)
        if any(part.casefold() == ".gitmodules" for part in path.split("/")):
            raise IntegrityError("Git modules are forbidden", path)


def veto_gitmodules(root: Path, deadline: float) -> None:
    """Also reject untracked/ignored .gitmodules, before add can stage it."""
    pending = [root]
    while pending:
        remaining(deadline)
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    remaining(deadline)
                    path = Path(entry.path)
                    if entry.name.casefold() == ".gitmodules":
                        raise IntegrityError(
                            "Git modules are forbidden", str(path.relative_to(root))
                        )
                    if entry.name.casefold() != ".git" and entry.is_dir(follow_symlinks=False):
                        pending.append(path)
        except OSError as error:
            raise IntegrityError(
                "Git modules scan failed", str(directory.relative_to(root))
            ) from error


def _plain_bytes(path: Path, limit: int, deadline: float) -> bytes:
    """Read bounded regular metadata without following the final symlink or opening a FIFO."""
    remaining(deadline)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise IntegrityError("invalid merge metadata", path.name)
            data = handle.read(limit + 1)
        if len(data) > limit:
            raise IntegrityError("oversized merge metadata", path.name)
    except OSError as error:
        raise IntegrityError("unreadable merge metadata", path.name) from error
    remaining(deadline)
    return data


def verify_merge_metadata(identity: GitIdentity, pre: str, head: str, deadline: float) -> None:
    """Only a merge still pointing at the captured main may commit or use Git's abort."""
    identity.verify_root_entry()
    directory, common = identity.directory, identity.common
    for path in (
        directory / "index.lock",
        directory / "HEAD.lock",
        common / "refs/heads/main.lock",
    ):
        if os.path.lexists(path):
            raise IntegrityError("unknown merge lock", path.name)
    expected = {
        directory / "HEAD": b"ref: refs/heads/main\n",
        common / "refs/heads/main": f"{pre}\n".encode(),
        directory / "ORIG_HEAD": f"{pre}\n".encode(),
        directory / "MERGE_HEAD": f"{head}\n".encode(),
    }
    for path, wanted in expected.items():
        if _plain_bytes(path, 4096, deadline) != wanted:
            raise IntegrityError("merge metadata differs from preparation", path.name)


def _index_digest(identity: GitIdentity, deadline: float) -> bytes:
    # A digest is a tamper tripwire, not a restorable snapshot (C2b owns restoration).
    return hashlib.sha256(
        _plain_bytes(identity.directory / "index", 64 * 1024 * 1024, deadline)
    ).digest()


@dataclass(frozen=True)
class MergeGuard:
    identity: GitIdentity
    pre: str
    head: str
    candidate_tree: str
    index_digest: bytes

    def verify(self, deadline: float) -> None:
        verify_merge_metadata(self.identity, self.pre, self.head, deadline)
        if _index_digest(self.identity, deadline) != self.index_digest:
            raise IntegrityError("real index changed after merge preparation", "index")


async def prepare_merge_guard(
    workspace: "Workspace", tree: "Tree", *, pre: str, head: str, timeout_s: float
) -> MergeGuard:
    deadline = time.monotonic() + timeout_s
    async with lease_until(workspace.git_lease(), deadline):
        await offload(partial(verify_merge_metadata, tree.git_identity, pre, head, deadline))
        candidate = (
            await workspace.git_ok(tree, "write-tree", timeout_s=remaining(deadline))
        ).strip()
        digest = await offload(partial(_index_digest, tree.git_identity, deadline))
        return MergeGuard(tree.git_identity, pre, head, candidate, digest)


def _veto_dirty_status(output: str) -> None:
    records = iter(output.split("\0"))
    for record in records:
        if not record:
            continue
        if len(record) < 4 or record[2] != " ":
            raise IntegrityError("invalid merge status query", ".", code="git_failed")
        if record[:2] == "??" or record[1] != " ":
            raise IntegrityError("working tree or untracked files changed", record[3:])
        if record[0] in "RC" and next(records, None) is None:
            raise IntegrityError("incomplete merge status query", ".", code="git_failed")


async def verify_merge_guard(
    workspace: "Workspace",
    tree: "Tree",
    guard: MergeGuard,
    *,
    tested_tree: str | None,
    timeout_s: float,
) -> None:
    """Check raw index/markers before any refreshing query, then the exact tree and files."""
    deadline = time.monotonic() + timeout_s
    async with lease_until(workspace.git_lease(), deadline):
        await offload(partial(guard.verify, deadline))
        current = (
            await workspace.git_ok(tree, "write-tree", timeout_s=remaining(deadline))
        ).strip()
        if current != guard.candidate_tree or current != tested_tree:
            raise IntegrityError("index, tested tree and candidate tree differ", "index")
        await workspace.veto_index(tree, timeout_s=remaining(deadline))
        head = (
            await workspace.git_ok(tree, "rev-parse", "HEAD", timeout_s=remaining(deadline))
        ).strip()
        if head != guard.pre:
            raise IntegrityError("HEAD changed after merge preparation", "HEAD")
        _veto_dirty_status(
            await workspace.git_ok(
                tree,
                "status",
                "--porcelain",
                "-z",
                "--untracked-files=all",
                timeout_s=remaining(deadline),
            )
        )
        for args in (
            ("diff-files", "--name-only", "-z"),
            ("diff", "--cached", "--name-only", "-z", guard.candidate_tree),
            ("ls-files", "--others", "--exclude-standard", "-z"),
        ):
            if await workspace.git_ok(tree, *args, timeout_s=remaining(deadline)):
                raise IntegrityError("merge working tree, index or untracked files changed", ".")
        await offload(partial(guard.verify, deadline))


async def verify_merge_abort(
    workspace: "Workspace", tree: "Tree", *, pre: str, head: str, timeout_s: float
) -> None:
    deadline = time.monotonic() + timeout_s
    async with lease_until(workspace.git_lease(), deadline):
        await offload(partial(verify_merge_metadata, tree.git_identity, pre, head, deadline))
