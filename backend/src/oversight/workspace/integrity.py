"""Pre-Git filesystem guards. No Git process is used to discover repository identity."""

import asyncio
import hashlib
import json
import os
import re
import stat
import sys
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
from oversight.workspace._platform import executable, is_link_like
from oversight.workspace.errors import WorkspaceError
from oversight.workspace.files import gitdir_matches, nt_ambiguous, nt_plain_parents, open_nt
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
        if sys.platform == "win32":
            self._verify_nt(registration=False)
            return
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
        if sys.platform == "win32":
            self._verify_nt(registration=True)
            return
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
        except (OSError, UnicodeError, SizeLimitError) as error:
            raise IntegrityError("unreadable Git registration", ".git") from error

    def _verify_nt(self, *, registration: bool) -> None:
        try:
            nt_plain_parents(self.root)
            nt_plain_parents(self.directory)
            nt_plain_parents(self.common)
            if self.directory == self.common:
                nt_plain_parents(self.root / ".git")
                return
            pointer = _plain_bytes(self.root / ".git", 4096, time.monotonic() + 5).decode("utf-8")
            valid = gitdir_matches(pointer, self.directory, pointer=True)
            if registration:
                named = _plain_bytes(self.directory / "gitdir", 4096, time.monotonic() + 5)
                valid = valid and gitdir_matches(named.decode("utf-8"), self.root / ".git")
            if not valid:
                raise IntegrityError("unexpected Git registration", ".git")
        except (OSError, WorkspaceError, UnicodeError) as error:
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
        if sys.platform == "win32":
            nt_plain_parents(root)
        if not stat.S_ISDIR(root.lstat().st_mode) or os.path.realpath(root) != str(root):
            raise IntegrityError("linked Git scan root", ".")
    except OSError as error:
        raise IntegrityError("unreadable Git scan root", ".") from error
    pending = [root]
    while pending:
        remaining(deadline)
        directory = pending.pop()
        try:
            if sys.platform == "win32":
                nt_plain_parents(directory)
            with os.scandir(directory) as entries:
                for entry in entries:
                    remaining(deadline)
                    path = Path(entry.path)
                    relative = path.relative_to(root).as_posix()
                    if entry.name.casefold() == ".git" or (
                        sys.platform == "win32" and entry.name.upper() == ".GIT"
                    ):
                        if (
                            path != root / ".git"
                            or is_link_like(entry)
                            or (
                                sys.platform == "win32"
                                and (entry.name != ".git" or is_link_like(entry, nt=True))
                            )
                        ):
                            raise IntegrityError("nested or linked Git entry", relative)
                        continue
                    if sys.platform == "win32" and nt_ambiguous(entry.name):
                        raise IntegrityError("ambiguous Windows entry", relative)
                    if sys.platform == "win32" and is_link_like(entry, nt=True):
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
        binary = "git"
        if sys.platform == "win32":
            binary = await offload(lambda: executable("git"))
        result = await spawn(
            [binary, *flags, args[0], *submodules, *args[1:]],
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
        if any(
            part.casefold() == ".gitmodules"
            or (sys.platform == "win32" and part.upper() == ".GITMODULES")
            for part in path.split("/")
        ):
            raise IntegrityError("Git modules are forbidden", path)


def veto_gitmodules(root: Path, deadline: float) -> None:
    """Also reject untracked/ignored .gitmodules, before add can stage it."""
    if sys.platform == "win32":
        nt_plain_parents(root)
    pending = [root]
    while pending:
        remaining(deadline)
        directory = pending.pop()
        try:
            if sys.platform == "win32":
                nt_plain_parents(directory)
            with os.scandir(directory) as entries:
                for entry in entries:
                    remaining(deadline)
                    path = Path(entry.path)
                    if entry.name.casefold() == ".gitmodules" or (
                        sys.platform == "win32" and entry.name.upper() == ".GITMODULES"
                    ):
                        raise IntegrityError(
                            "Git modules are forbidden", str(path.relative_to(root))
                        )
                    if sys.platform == "win32" and is_link_like(entry, nt=True):
                        continue
                    if entry.name.casefold() != ".git" and entry.is_dir(follow_symlinks=False):
                        pending.append(path)
        except OSError as error:
            raise IntegrityError(
                "Git modules scan failed", str(directory.relative_to(root))
            ) from error


def _plain_bytes(path: Path, limit: int, deadline: float) -> bytes:
    """Read bounded regular metadata without following the final symlink or opening a FIFO."""
    if sys.platform == "win32":
        remaining(deadline)
        try:
            with open_nt(path) as handle:
                if os.fstat(handle.fileno()).st_size > limit:
                    raise IntegrityError("invalid merge metadata", path.name)
                data = handle.read(limit + 1)
            if len(data) > limit:
                raise IntegrityError("oversized merge metadata", path.name)
        except IntegrityError:
            raise
        except (OSError, WorkspaceError) as error:
            raise IntegrityError("unreadable merge metadata", path.name) from error
        remaining(deadline)
        return data
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


BYTE_OVERRIDE = b"* -text\n"
DISABLED_GLOBAL_ATTRIBUTES = "info/disabled-global-attributes"


def _policy_bytes(path: Path, shown: str, deadline: float) -> bytes | None:
    # Local import: metadata_io already uses IntegrityError and remaining from this module.
    from oversight.workspace.metadata_io import Budget, read_entry

    try:
        entry = read_entry(path, Budget(deadline))
        if entry is not None and entry.data is None:
            raise IntegrityError("attribute policy must be a regular file", shown)
        return None if entry is None else entry.data
    except (OSError, WorkspaceError) as error:
        raise IntegrityError(
            "unsafe attribute policy; remove links or restore a plain file", shown
        ) from error


def _veto_attributes(data: bytes, relative: str) -> None:
    if b"\0" in data:
        raise IntegrityError("NUL in attribute policy; restore a plain attributes file", relative)
    # Git's attributes are ASCII names even when patterns contain arbitrary filename bytes.
    # Inspect all tokens conservatively; reject custom macro definitions rather than interpret
    # recursion, quoting or precedence differently from Git. The built-in binary macro is safe.
    # Git ends lines only at LF and treats CR as a blank, alongside space and tab.
    for line in data.removeprefix(b"\xef\xbb\xbf").split(b"\n"):
        line = line.strip(b" \t\r")
        if not line or line.startswith(b"#"):
            continue
        if line.startswith(b'"'):
            raise IntegrityError(
                "quoted attribute patterns unsupported; use an unquoted pattern", relative
            )
        tokens = re.split(rb"[ \t\r]+", line)
        if tokens[0].startswith(b"[attr]"):
            raise IntegrityError(
                "attribute macros unsupported; expand the macro explicitly", relative
            )
        for token in tokens[1:]:
            name = token.lstrip(b"-!").split(b"=", 1)[0]
            if name in {b"filter", b"working-tree-encoding", b"ident"}:
                raise IntegrityError(
                    "attribute transform forbidden; remove filter, encoding or ident", relative
                )


def validate_source_byte_policy(
    root: Path, common: Path, manifest: Sequence[str], deadline: float
) -> None:
    """Before copying/reset: validate explicit, already selected source entries, without Git.

    The selector supplies the registered source common directory and complete copy manifest;
    excluded contents are never walked. No source config, index or working file is changed.
    """
    from oversight.workspace.files import split_relative
    from oversight.workspace.metadata_io import Budget

    budget = Budget(deadline)
    info = _policy_bytes(common / "info" / "attributes", ".git/info/attributes", deadline)
    if info:
        raise IntegrityError(
            "source info attributes unsupported; move policy into .gitattributes",
            ".git/info/attributes",
        )
    for relative in manifest:
        budget.charge(0)
        parts = split_relative(relative, nt=True)
        if parts[-1].casefold() == ".gitattributes":
            data = _policy_bytes(root.joinpath(*parts), relative, deadline)
            if data is None:
                raise IntegrityError(
                    "source attribute entry disappeared; retry selection", relative
                )
            budget.charge(len(data))
            _veto_attributes(data, relative)


def install_byte_override(identity: GitIdentity, deadline: float) -> None:
    """Init-only installation, before add; an existing entry is never repaired or replaced."""
    from oversight.workspace.metadata_io import Entry, replace_file

    path = identity.common / "info" / "attributes"
    if _policy_bytes(path, ".git/info/attributes", deadline) is not None:
        raise IntegrityError(
            "unexpected runtime attributes; restart with a fresh runtime", ".git/info/attributes"
        )
    replace_file(path, None, Entry(0o600, BYTE_OVERRIDE), deadline, cas=False)
    verify_byte_override(identity, deadline)


def verify_byte_override(identity: GitIdentity, deadline: float) -> None:
    # Pin the global layer away from suite-writable HOME, and refuse any created entry.
    if (
        _policy_bytes(
            identity.common / DISABLED_GLOBAL_ATTRIBUTES,
            f".git/{DISABLED_GLOBAL_ATTRIBUTES}",
            deadline,
        )
        is not None
    ):
        raise IntegrityError(
            "global attributes entry must be absent; restart with a fresh runtime",
            f".git/{DISABLED_GLOBAL_ATTRIBUTES}",
        )
    if (
        _policy_bytes(identity.common / "info" / "attributes", ".git/info/attributes", deadline)
        != BYTE_OVERRIDE
    ):
        raise IntegrityError(
            "runtime attributes changed or missing; restart with a fresh runtime",
            ".git/info/attributes",
        )


def verify_tree_byte_policy(identity: GitIdentity, deadline: float) -> None:
    """Recheck the relevant runtime tree before every Git spawn, including object-only queries."""
    from oversight.workspace.metadata_io import Budget, directory

    budget = Budget(deadline)
    pending = [identity.root]
    while pending:
        parent = pending.pop()
        with directory(parent, deadline) as fd, os.scandir(fd) as entries:
            for entry in entries:
                budget.charge(0)
                if entry.name.casefold() == ".git":
                    continue
                path = parent / entry.name
                if entry.name.casefold() == ".gitattributes":
                    relative = path.relative_to(identity.root).as_posix()
                    data = _policy_bytes(path, relative, deadline)
                    if data is None:
                        raise IntegrityError(
                            "attribute entry disappeared; retry operation", relative
                        )
                    budget.charge(len(data))
                    _veto_attributes(data, relative)
                elif not is_link_like(entry, nt=True) and entry.is_dir(follow_symlinks=False):
                    pending.append(path)
