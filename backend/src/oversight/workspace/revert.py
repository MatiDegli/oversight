"""Private inverse candidates and a single CAS effect, composed by the change service.

No ref is touched until apply_inverse. Expectations live only in this process;
settlement accepts only a candidate prepared by this workspace and its owner.
"""

import asyncio
import os
import re
import stat
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
from typing import Literal
from weakref import WeakKeyDictionary

from oversight.workspace.core import Tree, Workspace
from oversight.workspace.errors import InvalidPath, WorkspaceError
from oversight.workspace.files import ensure_plain_dir, remove_tree, valid_agent_id
from oversight.workspace.integrity import lease_until, remaining
from oversight.workspace.locks import offload
from oversight.workspace.suite import SuiteRun

CLEANUP_TIMEOUT_S = 5
SETTLEMENT_ATTEMPTS = 3
OID = re.compile(r"[0-9a-f]{40}\Z")
type InverseClassification = Literal["untouched", "landed", "unknown"]


class InverseError(WorkspaceError):
    """A candidate or its caller is invalid; no publication was attempted."""


@dataclass(frozen=True)
class InverseOutcome:
    kind: Literal["landed", "main_moved", "unknown"]
    main_commit: str | None = None


@dataclass(frozen=True)
class _Expected:
    agent_id: str
    main_commit: str
    inverse_tree: str
    inverse_commit: str
    message: str


# Values contain no Workspace/Tree reference: releasing a Workspace drops its ledger.
_candidates: WeakKeyDictionary[Workspace, dict[str, _Expected]] = WeakKeyDictionary()


class _InverseTree(Tree):
    def verify_root(self) -> None:
        runtime = self._workspace.runtime_root
        if self.root.parent != runtime / "tmp" or not re.fullmatch(
            r"revert-[0-9a-f]{32}", self.root.name
        ):
            raise InvalidPath("invalid inverse checkout root")
        for path in (runtime, self.root.parent, self.root):
            if not stat.S_ISDIR(path.lstat().st_mode) or os.path.realpath(path) != str(path):
                raise InvalidPath("inverse checkout path is missing or linked")


@dataclass
class InverseCandidate:
    tree: Tree
    main_commit: str
    inverse_tree: str
    inverse_commit: str
    cleaned: bool = False


def _require_locks(workspace: Workspace, agent_id: str) -> None:
    if not valid_agent_id(agent_id):
        raise InverseError("invalid original agent id")
    if not (
        workspace.operations.holds(main=True) and workspace.operations.holds(agent_id=agent_id)
    ):
        raise InverseError("inverse requires main and original agent locks in the caller task")


def _expected(workspace: Workspace, h: str, c: str, t: str) -> _Expected:
    expected = _candidates.get(workspace, {}).get(c)
    if expected is None or (expected.main_commit, expected.inverse_tree) != (h, t):
        raise InverseError("inverse candidate was not prepared by this workspace")
    _require_locks(workspace, expected.agent_id)
    return expected


async def _cleanup(workspace: Workspace, item: InverseCandidate) -> bool:
    """Retry interrupted cleanup in the owner task and report deferred cancellation."""
    cancelled = False
    root = item.tree.root
    for step in range(3):
        deadline = time.monotonic() + CLEANUP_TIMEOUT_S
        for _ in range(SETTLEMENT_ATTEMPTS):
            try:
                if step == 0:
                    await workspace.git(
                        workspace.main,
                        "worktree",
                        "remove",
                        "--force",
                        str(root),
                        timeout_s=remaining(deadline),
                    )
                elif step == 1:
                    await offload(partial(remove_tree, workspace.runtime_root, root))
                else:
                    await workspace.git(
                        workspace.main,
                        "worktree",
                        "prune",
                        "--expire=now",
                        timeout_s=remaining(deadline),
                    )
                break
            except asyncio.CancelledError:
                cancelled = True
            except (OSError, WorkspaceError):
                break  # Continue the filesystem fallback and pruning even if Git failed.
    deadline = time.monotonic() + CLEANUP_TIMEOUT_S
    for _ in range(SETTLEMENT_ATTEMPTS):
        try:
            listing = await workspace.git_ok(
                workspace.main, "worktree", "list", "--porcelain", timeout_s=remaining(deadline)
            )
            item.cleaned = await offload(
                lambda listing=listing: (
                    not os.path.lexists(root)
                    and not os.path.lexists(item.tree.git_identity.directory)
                    and f"worktree {root}\n" not in listing
                )
            )
            break
        except asyncio.CancelledError:
            cancelled = True
        except (OSError, WorkspaceError):
            break
    if item.cleaned:
        workspace.git_identities.pop(root, None)
    return cancelled


@asynccontextmanager
async def prepare_inverse(
    workspace: Workspace,
    *,
    agent_id: str,
    main_commit: str,
    inverse_tree: str,
    message: str,
    timeout_s: float,
) -> AsyncGenerator[InverseCandidate]:
    """Create unreachable C(T, parent H), yield its detached checkout, verify cleanup.

    Keep the yielded handle after exit: cleaned is set only after directory, admin
    directory and worktree registration have all been verified absent.
    """
    _require_locks(workspace, agent_id)
    if (
        not all(OID.fullmatch(value) for value in (main_commit, inverse_tree))
        or not message.strip()
    ):
        raise InverseError("inverse requires full object ids and a nonempty message")
    deadline = time.monotonic() + timeout_s
    async with lease_until(workspace.git_lease(), deadline):

        async def git(*args: str) -> str:
            return (
                await workspace.git_ok(workspace.main, *args, timeout_s=remaining(deadline))
            ).strip()

        if await git("rev-parse", f"{main_commit}^{{tree}}") == inverse_tree:
            raise InverseError("empty inverse")
        if await git("cat-file", "-t", inverse_tree) != "tree":
            raise InverseError("inverse target is not a tree")
        commit = (
            await workspace.git_ok(
                workspace.main,
                "commit-tree",
                inverse_tree,
                "-p",
                main_commit,
                "-m",
                message,
                author=workspace.user_author(),
                timeout_s=remaining(deadline),
            )
        ).strip()
    expected = _Expected(
        agent_id,
        main_commit,
        inverse_tree,
        commit,
        message if message.endswith("\n") else message + "\n",
    )
    ledger = _candidates.setdefault(workspace, {})
    if commit in ledger and ledger[commit] != expected:
        raise InverseError("inverse commit already belongs to another agent")
    ledger[commit] = expected
    root = workspace.tmp_dir / f"revert-{uuid.uuid4().hex}"
    item = InverseCandidate(
        _InverseTree(workspace, root, agent_id, "", main_commit), main_commit, inverse_tree, commit
    )
    body_error: BaseException | None = None
    try:
        await offload(partial(ensure_plain_dir, workspace.runtime_root, root.parent))
        await workspace.git_ok(
            workspace.main,
            "worktree",
            "add",
            "--detach",
            str(root),
            commit,
            timeout_s=remaining(deadline),
        )
        await offload(item.tree.git_identity.verify_registration)
        yield item
    except BaseException as error:
        body_error = error
        raise
    finally:
        if await _cleanup(workspace, item) and not isinstance(body_error, asyncio.CancelledError):
            raise asyncio.CancelledError from body_error


def inverse_suite_problem(run: SuiteRun, *, tree: str, cleaned: bool) -> str | None:
    """Judge a real SuiteRun obtained with isolation='required', after checkout cleanup."""
    checks = (
        (run.result.outcome == "passed", "inverse suite outcome is not passed"),
        (run.result.passed >= 1, "inverse suite has no passed tests"),
        (run.result.failed == 0, "inverse suite has failed tests"),
        (run.result.skipped == 0, "inverse suite has skipped tests"),
        (not run.problems, "inverse suite has report or integrity problems"),
        (run.tested_tree == tree, "inverse suite tested tree differs"),
        (cleaned, "inverse checkout cleanup is unverified"),
    )
    return next((reason for accepted, reason in checks if not accepted), None)


async def _git(workspace: Workspace, deadline: float, *args: str) -> str:
    return await workspace.git_ok(workspace.main, *args, timeout_s=remaining(deadline))


async def _head(workspace: Workspace, deadline: float) -> str:
    attached = await workspace.git(
        workspace.main, "symbolic-ref", "HEAD", timeout_s=remaining(deadline)
    )
    if (
        attached.returncode != 0
        or attached.timed_out
        or attached.truncated
        or attached.stdout.strip() != "refs/heads/main"
    ):
        raise WorkspaceError("main must be attached and readable")
    ref = (await _git(workspace, deadline, "rev-parse", "refs/heads/main")).strip()
    if (
        not OID.fullmatch(ref)
        or (await _git(workspace, deadline, "rev-parse", "HEAD")).strip() != ref
    ):
        raise WorkspaceError("main ref and HEAD differ")
    return ref


async def _verify_commit(workspace: Workspace, expected: _Expected, deadline: float) -> None:
    fields = (
        await _git(
            workspace,
            deadline,
            "show",
            "-s",
            "--format=%T%x00%P%x00%an%x00%ae%x00%cn%x00%ce%x00%B",
            expected.inverse_commit,
        )
    ).split("\0", 6)
    author = workspace.user_author()
    wanted = [
        expected.inverse_tree,
        expected.main_commit,
        author.name,
        author.email,
        author.name,
        author.email,
        expected.message + "\n",
    ]
    if fields != wanted:
        raise InverseError("inverse commit tree, parent, identities or message differ")


async def _checkout_tree(workspace: Workspace, deadline: float) -> str:
    # Refuse flags that hide tracked modifications from diff-files.
    entries = await _git(workspace, deadline, "ls-files", "-v", "-z")
    if any(entry and not entry.startswith("H ") for entry in entries.split("\0")):
        raise InverseError("inverse index has hidden or unmerged entries")
    if await _git(workspace, deadline, "diff-files", "--name-only", "-z"):
        raise InverseError("inverse checkout is dirty")
    if await _git(workspace, deadline, "ls-files", "--others", "--exclude-standard", "-z"):
        raise InverseError("inverse checkout has untracked files")
    return (await _git(workspace, deadline, "write-tree")).strip()


async def _classify(
    workspace: Workspace, expected: _Expected, deadline: float
) -> InverseClassification:
    head = await _head(workspace, deadline)
    await _verify_commit(workspace, expected, deadline)
    h_tree = (
        await _git(workspace, deadline, "rev-parse", f"{expected.main_commit}^{{tree}}")
    ).strip()
    index = await _checkout_tree(workspace, deadline)
    if head == expected.main_commit:
        return (
            "untouched"
            if index == h_tree and await _head(workspace, deadline) == head
            else "unknown"
        )
    if head != expected.inverse_commit:
        return "unknown"
    if index == h_tree:
        # Only finish a verified H checkout. Inconsistent/partially dirty evidence
        # is unknown: never overwrite foreign edits or roll the published ref back.
        await _git(
            workspace,
            deadline,
            "read-tree",
            "-m",
            "-u",
            expected.main_commit,
            expected.inverse_commit,
        )
    elif index != expected.inverse_tree:
        return "unknown"
    if (
        await _checkout_tree(workspace, deadline) == expected.inverse_tree
        and await _head(workspace, deadline) == expected.inverse_commit
    ):
        return "landed"
    return "unknown"


async def classify_inverse(
    workspace: Workspace,
    *,
    main_commit: str,
    inverse_commit: str,
    inverse_tree: str,
    timeout_s: float,
) -> InverseClassification:
    """Settle only H/C from Git facts, with finite retries and no second CAS.

    Cancellation can raise after bounded cleanup without reaping the Git child.
    An interrupted update-ref may therefore publish while settlement is reading
    evidence: unknown is required unless the ref and checkout are both verified.
    Even untouched is only an observation, not proof that a live child cannot land.
    Repeated cancellation is deferred in this owner task until settlement ends.
    """
    expected = _expected(workspace, main_commit, inverse_commit, inverse_tree)
    deadline, cancelled = time.monotonic() + timeout_s, False
    result: InverseClassification = "unknown"
    for _ in range(SETTLEMENT_ATTEMPTS):
        try:
            async with lease_until(workspace.git_lease(), deadline):
                result = await _classify(workspace, expected, deadline)
            break
        except asyncio.CancelledError:
            cancelled = True
        except (OSError, WorkspaceError, TimeoutError):
            continue
    if cancelled:
        raise asyncio.CancelledError
    return result


async def _settled_outcome(
    workspace: Workspace, expected: _Expected, timeout_s: float
) -> InverseOutcome:
    settled = await classify_inverse(
        workspace,
        main_commit=expected.main_commit,
        inverse_commit=expected.inverse_commit,
        inverse_tree=expected.inverse_tree,
        timeout_s=timeout_s,
    )
    return (
        InverseOutcome("landed", expected.inverse_commit)
        if settled == "landed"
        else InverseOutcome("unknown")
    )


async def _preflight(workspace: Workspace, expected: _Expected, deadline: float) -> str:
    head = await _head(workspace, deadline)
    if head == expected.inverse_commit:
        return head
    try:
        index = await _checkout_tree(workspace, deadline)
        head_tree = (await _git(workspace, deadline, "rev-parse", f"{head}^{{tree}}")).strip()
        if index != head_tree:
            raise InverseError("inverse index is dirty")
    except InverseError:
        # A live update-ref may have landed since the first read. Refuse only
        # after verifying that this is still a pre-publication checkout.
        head = await _head(workspace, deadline)
        if head != expected.inverse_commit:
            raise
    if head == expected.main_commit:
        await _verify_commit(workspace, expected, deadline)
    return head


async def apply_inverse(
    workspace: Workspace,
    *,
    main_commit: str,
    inverse_commit: str,
    inverse_tree: str,
    timeout_s: float,
) -> InverseOutcome:
    """Publish C once with update-ref C H, then finish H's checkout under the same lease.

    Precondition refusals raise InverseError. Unconfirmed effects return unknown;
    cancellation first settles Git facts, then propagates to the owner.
    """
    expected = _expected(workspace, main_commit, inverse_commit, inverse_tree)
    deadline = time.monotonic() + timeout_s
    async with lease_until(workspace.git_lease(), deadline):
        try:
            head = await _preflight(workspace, expected, deadline)
        except InverseError:
            raise
        except (OSError, WorkspaceError, TimeoutError):
            return await _settled_outcome(workspace, expected, timeout_s)
        if head == inverse_commit:
            return await _settled_outcome(workspace, expected, timeout_s)
        if head not in (main_commit, inverse_commit):
            return InverseOutcome("main_moved", head)
        try:
            result = await workspace.git(
                workspace.main,
                "update-ref",
                "refs/heads/main",
                inverse_commit,
                main_commit,
                timeout_s=remaining(deadline),
            )
            if result.returncode is None:
                return InverseOutcome("unknown")  # No evidence that the subprocess was reaped.
            if result.returncode != 0 or result.timed_out:
                mismatch = re.search(
                    r"is at ([0-9a-f]{40}) but expected " + main_commit, result.stderr
                )
                if (
                    not result.timed_out
                    and mismatch
                    and mismatch[1] != inverse_commit
                    and await _head(workspace, deadline) == mismatch[1]
                ):
                    return InverseOutcome("main_moved", mismatch[1])
            else:
                await _git(
                    workspace, deadline, "read-tree", "-m", "-u", main_commit, inverse_commit
                )
                if await _classify(workspace, expected, deadline) == "landed":
                    return InverseOutcome("landed", inverse_commit)
        except asyncio.CancelledError:
            try:
                await classify_inverse(
                    workspace,
                    main_commit=main_commit,
                    inverse_commit=inverse_commit,
                    inverse_tree=inverse_tree,
                    timeout_s=timeout_s,
                )
            finally:
                raise
        except (OSError, WorkspaceError, TimeoutError):
            pass
        return await _settled_outcome(workspace, expected, timeout_s)
