"""Pre-Git filesystem guards. No Git process is used to discover repository identity."""

import asyncio
import json
import os
import stat
import time
from collections.abc import AsyncGenerator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from oversight.limits import bounded_text
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


def scan_nested_git(identity: GitIdentity, deadline: float) -> None:
    """Walk every directory (including ignored files), without following symbolic links.

    The sole exemption is the registered root's literal .git entry. Its contents belong
    to the metadata barrier, not this working-tree scan. Errors never grant permission.
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
            await offload(partial(scan_nested_git, scan_identity, deadline))
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
