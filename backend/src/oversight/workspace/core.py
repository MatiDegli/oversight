"""The runtime workspace and its trees.

`Workspace` owns `.runtime/`: the main repository, one git worktree per agent, the demo repo's
virtual environment and the temp directory. `Tree` is main or one agent's worktree. Merge and
discard decisions belong to the change manager; this module gives it safe primitives.
"""

import fcntl
import os
import re
import shutil
from collections.abc import AsyncGenerator, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path

from oversight.limits import bounded_text
from oversight.workspace.errors import (
    LockOrderError,
    UnsafeDeletion,
    WorkspaceBusy,
    WorkspaceError,
)
from oversight.workspace.files import (
    DIR_MODE,
    FileRead,
    copy_template,
    ensure_plain_dir,
    list_files,
    read_file,
    registered_worktree,
    remove_tree,
    resolve_in,
    valid_agent_id,
    validate_runtime_root,
    verify_tree_root,
    write_file,
)
from oversight.workspace.isolation import refuse_orphans
from oversight.workspace.locks import Locks, offload
from oversight.workspace.ranges import FileRange, read_range
from oversight.workspace.runner import ProcessResult, Spawn, run_process
from oversight.workspace.suite import SuiteInterrupted, SuiteRun, SuiteUnavailable, run_suite
from oversight.workspace.test_tree import capture_test_tree
from oversight_contracts.common import AgentId

PLUGIN_DIR = Path(__file__).resolve().parent / "report_plugin"
INITIAL_DATE = "2026-10-26T00:00:00+00:00"
INITIAL_MESSAGE = "Initial workspace"
CACHE_EXCLUDES = "__pycache__/\n*.pyc\n.pytest_cache/\n.mypy_cache/\n.ruff_cache/\n.venv/\n"
GIT_FLAGS = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "core.autocrlf=false",
    "-c",
    "core.symlinks=false",
)
FORBIDDEN_GIT = frozenset({"stash", "rebase"})
EXCLUDE_NEWER = "14 days"
"""The repository's release-age rule (pyproject.toml, `tool.uv.exclude-newer`); `--no-config` drops
it, so the demo venv's install passes it explicitly."""
UV_PASSTHROUGH = frozenset(
    {
        # Only what uv needs to find its cache and Pythons and to reach the network, by name. A
        # prefix would also let UV_OVERRIDE, UV_EXCLUDE or UV_CONSTRAINT change what gets installed.
        # The index variables say where packages come from, which a machine behind a mirror needs;
        # they choose a source and never a version, a constraint or an exclusion. Not allowed:
        # UV_EXTRA_INDEX_URL, UV_INDEX_STRATEGY, UV_FIND_LINKS and UV_NO_INDEX.
        "UV_INDEX_URL",
        "UV_DEFAULT_INDEX",
        "UV_INDEX",
        "UV_CACHE_DIR",
        "UV_PYTHON_INSTALL_DIR",
        "UV_PYTHON_CACHE_DIR",
        "UV_PYTHON_DOWNLOADS",
        "UV_PYTHON_PREFERENCE",
        "UV_LINK_MODE",
        "UV_NATIVE_TLS",
        "UV_HTTP_TIMEOUT",
        "UV_OFFLINE",
        "HOME",
        "XDG_CACHE_HOME",
        "XDG_DATA_HOME",
        "XDG_CONFIG_HOME",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "no_proxy",
    }
)


@dataclass(frozen=True)
class Author:
    name: str
    email: str


OVERSIGHT_AUTHOR = Author("Oversight", "oversight@oversight.invalid")


class Tree:
    """Main, or one agent's worktree. Created by `Workspace`, never by callers."""

    def __init__(
        self,
        workspace: "Workspace",
        root: Path,
        agent_id: AgentId | None,
        branch: str,
        base_commit: str | None,
    ) -> None:
        self._workspace = workspace
        self.root = root
        self.agent_id = agent_id
        self.branch = branch
        self.base_commit = base_commit

    def verify_root(self) -> None:
        """`InvalidPath` unless the root is this tree's own plain directory, with no link to it."""
        verify_tree_root(self._workspace.runtime_root, self.root)

    @asynccontextmanager
    async def section(self, *, locked: bool) -> AsyncGenerator[None]:
        """Count an operation, so `reset` can see it, take this tree's lock if asked, and check that
        the root is still the directory it was made as."""
        with self._workspace.operations.track():
            if locked:
                async with self._workspace.locks(
                    main=self.agent_id is None, agent_id=self.agent_id
                ):
                    await offload(self.verify_root)
                    yield
            else:
                await offload(self.verify_root)
                yield

    def resolve(self, path: str) -> Path:
        """The path under this tree's root, or `InvalidPath`."""
        self.verify_root()
        return resolve_in(self.root, path)

    async def read_text(self, path: str) -> FileRead:
        async with self.section(locked=self.agent_id is not None):
            return await offload(partial(read_file, self.root, path))

    async def read_range(self, path: str, first: int, last: int, chars: int) -> FileRange:
        async with self.section(locked=self.agent_id is not None):
            return await offload(partial(read_range, self.root, path, first, last, chars))

    async def write_text(self, path: str, content: str, *, expected_hash: str | None) -> str:
        """Write under the tree's lock; return the new hash. `StaleFile` if the file is not the one
        `expected_hash` names (`None` requires that it not exist)."""
        async with self.section(locked=True):
            tmp_dir = self._workspace.tmp_dir
            return await offload(
                partial(write_file, self.root, tmp_dir, path, content, expected_hash)
            )

    async def list_files(self) -> list[str]:
        async with self.section(locked=self.agent_id is not None):
            return await offload(partial(list_files, self.root))

    async def head(self) -> str:
        async with self.section(locked=True):
            return (await self._workspace.git_ok(self, "rev-parse", "HEAD")).strip()

    async def commit_all(self, message: str, author: Author) -> str | None:
        """Commit every change, as `author`; return the new HEAD, or None if the tree is clean."""
        async with self.section(locked=True):
            git = self._workspace.git_ok
            await git(self, "add", "-A")
            if not (await git(self, "status", "--porcelain")).strip():
                return None
            await git(self, "commit", "-q", "-m", message, author=author)
            return (await git(self, "rev-parse", "HEAD")).strip()


class Workspace:
    def __init__(
        self,
        runtime_root: Path,
        template: Path,
        *,
        pytest_pin: str,
        demo_python: Path | None = None,
    ) -> None:
        validate_runtime_root(runtime_root)
        if not shutil.rmtree.avoids_symlink_attacks:
            raise UnsafeDeletion("this platform's rmtree follows symbolic links; refusing to run")
        self._root = runtime_root
        self._template = template
        self._pytest_pin = pytest_pin
        self._demo_python = demo_python or runtime_root / "demo-venv" / "bin" / "python"
        self._demo_python_given = demo_python is not None
        self._locks = Locks()
        self._main = Tree(self, runtime_root / "workspace", None, "main", None)
        self._trees: dict[str, Tree] = {}
        self._lock_fd: int | None = None
        self._reset_done = False

    # --- Accessors --------------------------------------------------------------------------

    @property
    def main(self) -> Tree:
        return self._main

    @property
    def operations(self) -> Locks:
        return self._locks

    @property
    def runtime_root(self) -> Path:
        return self._root

    @property
    def tmp_dir(self) -> Path:
        return self._root / "tmp"

    def worktree(self, agent_id: AgentId) -> Tree:
        try:
            return self._trees[agent_id]
        except KeyError:
            raise WorkspaceError(f"{agent_id} has no worktree") from None

    def locks(
        self, *, main: bool = False, agent_id: AgentId | None = None
    ) -> AbstractAsyncContextManager[None]:
        """Main first, then the agent's. Anything else raises `LockOrderError`."""
        return self._locks.acquire(main=main, agent_id=agent_id)

    @staticmethod
    def user_author() -> Author:
        return Author("User", "user@oversight.invalid")

    @staticmethod
    def agent_author(agent_id: AgentId) -> Author:
        return Author(agent_id, f"{agent_id}@oversight.invalid")

    def redact(self, text: str) -> str:
        """Remove the runtime directory's location from text that may reach an event."""
        root = re.escape(str(self._root))
        text = re.sub(rf"{root}/(?:workspace|worktrees/agent-[1-9][0-9]*)(?:/|(?![\w-]))", "", text)
        return text.replace(f"{self._root}/", ".runtime/").replace(str(self._root), ".runtime")

    # --- Processes and git ------------------------------------------------------------------

    def _git_env(self, author: Author | None, date: str | None) -> dict[str, str]:
        who = author or OVERSIGHT_AUTHOR
        env = {
            "HOME": str(self._root / "tmp" / "home"),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            # The runtime directory sits inside the IDE's own repository: never look above it.
            "GIT_CEILING_DIRECTORIES": str(self._root),
            "GIT_AUTHOR_NAME": who.name,
            "GIT_AUTHOR_EMAIL": who.email,
            "GIT_COMMITTER_NAME": who.name,
            "GIT_COMMITTER_EMAIL": who.email,
        }
        if date:
            env |= {"GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
        return env

    async def git(
        self,
        tree: Tree,
        *args: str,
        timeout_s: float = 30,
        author: Author | None = None,
        date: str | None = None,
    ) -> ProcessResult:
        """Run git in `tree`. The caller holds the tree's lock. `args` start with the subcommand:
        the only global options are the ones this module adds, so a caller cannot move the git
        directory or the work tree, or define an alias. `stash` and `rebase` never spawn."""
        if not self._locks.holds(main=tree.agent_id is None, agent_id=tree.agent_id):
            raise LockOrderError("git runs only while the caller holds the tree's lock")
        if not args or args[0].startswith("-"):
            raise WorkspaceError("a git option before the subcommand is not allowed")
        if args[0] in FORBIDDEN_GIT:
            raise WorkspaceError(f"git {args[0]} is not allowed")
        await offload(tree.verify_root)
        return await run_process(
            ["git", *GIT_FLAGS, *args],
            cwd=tree.root,
            timeout_s=timeout_s,
            env=self._git_env(author, date),
        )

    async def git_ok(
        self,
        tree: Tree,
        *args: str,
        timeout_s: float = 30,
        author: Author | None = None,
        date: str | None = None,
    ) -> str:
        """Like `git`, but failure or truncated output raises `WorkspaceError`; returns stdout."""
        result = await self.git(tree, *args, timeout_s=timeout_s, author=author, date=date)
        if result.truncated:
            raise WorkspaceError(f"git {args[0]} failed: output was truncated")
        if result.returncode != 0:
            detail = self.redact(result.stderr.strip())[-500:]
            raise WorkspaceError(f"git {args[0]} failed: {detail}")
        return result.stdout

    async def _run_ok(
        self, argv: Sequence[str], *, cwd: Path, timeout_s: float, env: Mapping[str, str]
    ) -> None:
        result = await run_process(argv, cwd=cwd, timeout_s=timeout_s, env=env)
        if result.returncode != 0:
            raise WorkspaceError(f"{argv[0]} failed: {self.redact(result.stderr.strip())[-500:]}")

    # --- Startup ----------------------------------------------------------------------------

    def _acquire_sync(self) -> None:
        self._root.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
        fd = os.open(self._root / "server.lock", os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise WorkspaceBusy(
                "another Oversight server is using this runtime directory"
            ) from None
        self._lock_fd = fd

    async def acquire_runtime(self) -> None:
        """Take the runtime directory's lock, so a second server fails; children do not hold it."""
        if self._lock_fd is None:
            await offload(self._acquire_sync)
            try:
                refuse_orphans(self._root)
            except BaseException:
                self.release_runtime()
                raise

    def release_runtime(self) -> None:
        if self._lock_fd is not None:
            os.close(self._lock_fd)
            self._lock_fd = None

    def _populate(self) -> None:
        (self._root / "tmp" / "home").mkdir(mode=DIR_MODE, parents=True)
        copy_template(self._template, self._main.root)

    def _write_excludes(self) -> None:
        info = self._main.root / ".git" / "info"
        info.mkdir(parents=True, exist_ok=True)
        (info / "exclude").write_text(CACHE_EXCLUDES, encoding="utf-8")

    async def reset(self) -> str:
        """Start clean: delete worktrees, main and tmp, copy the template, and make the one initial
        commit, with a fixed author and date so its hash is the same in every take. Server start
        only; returns main's base commit. Runs, ledger and demo venv are kept."""
        if self._lock_fd is None:
            raise WorkspaceBusy("reset needs the runtime lock: call acquire_runtime first")
        if not self._locks.idle:
            raise WorkspaceBusy("an operation is in progress")
        if self._reset_done:
            raise WorkspaceBusy("reset already ran; it is for server start only")
        with self._locks.track():
            async with self._locks.acquire(main=True):
                for name in ("worktrees", "workspace", "tmp"):
                    await offload(partial(remove_tree, self._root, self._root / name))
                await offload(self._populate)
                git = self.git_ok
                await git(self._main, "init", "-q", "-b", "main", "--template=")
                await offload(self._write_excludes)
                await git(self._main, "add", "-A")
                await git(self._main, "commit", "-q", "-m", INITIAL_MESSAGE, date=INITIAL_DATE)
                commit = (await git(self._main, "rev-parse", "HEAD")).strip()
                self._trees.clear()
                self._reset_done = True
                return commit

    def _uv_env(self) -> dict[str, str]:
        """uv needs its cache, its Python installs and the network settings, which HOME and the
        allowlist would hide: the names in `UV_PASSTHROUGH`, nothing else. No API key is there."""
        return {key: value for key, value in os.environ.items() if key in UV_PASSTHROUGH}

    async def ensure_demo_venv(self) -> None:
        """Reuse `demo-venv/` if its stamp names the pinned pytest and its python runs; otherwise
        rebuild it. The stamp is written last, so a half-built venv is never reused."""
        if self._demo_python_given:
            return
        venv = self._root / "demo-venv"
        stamp = venv / "oversight-stamp"
        want = f"pytest=={self._pytest_pin}"

        def read_stamp() -> str | None:
            try:
                return bounded_text(stamp).strip()
            except FileNotFoundError:
                return None

        if await offload(read_stamp) == want:
            try:
                probe = await run_process(
                    [str(self._demo_python), "-c", "import pytest"],
                    cwd=self._root,
                    timeout_s=60,
                    env={"HOME": str(self._root / "tmp" / "home")},
                )
            except OSError:
                probe = None  # no interpreter, or a symlink to one that is gone: rebuild
            if probe is not None and probe.returncode == 0:
                return
        await offload(partial(remove_tree, self._root, venv))
        env = self._uv_env()
        await self._run_ok(
            ["uv", "--no-config", "venv", "--quiet", "--python", "3.12", str(venv)],
            cwd=self._root,
            timeout_s=120,
            env=env,
        )
        await self._run_ok(
            [
                "uv",
                "--no-config",
                "pip",
                "install",
                "--quiet",
                "--exclude-newer",
                EXCLUDE_NEWER,
                "--python",
                str(self._demo_python),
                want,
            ],
            cwd=self._root,
            timeout_s=300,
            env=env,
        )
        await offload(lambda: stamp.write_text(want + "\n", encoding="utf-8"))

    # --- Worktrees --------------------------------------------------------------------------

    async def create_worktree(self, agent_id: AgentId) -> Tree:
        """A worktree and branch named `agent_id`, from main's HEAD, which becomes `base_commit`."""
        if not valid_agent_id(agent_id):
            raise WorkspaceError(f"not a valid agent id: {agent_id!r}")
        target = self._root / "worktrees" / agent_id
        with self._locks.track():
            async with self._locks.acquire(main=True, agent_id=agent_id):
                if agent_id in self._trees or await offload(lambda: os.path.lexists(target)):
                    raise WorkspaceError(f"{agent_id} already has a worktree")
                head = await self._main.head()  # also refuses a main root that is not plain
                await offload(partial(ensure_plain_dir, self._root, target.parent))
                add = ("worktree", "add", "-q", "-b", agent_id, str(target), head)
                await self.git_ok(self._main, *add)
                tree = Tree(self, target, agent_id, agent_id, head)
                self._trees[agent_id] = tree
                return tree

    async def _remove_worktree(
        self, agent_id: AgentId, *, merged: bool, delete_branch: bool
    ) -> None:
        with self._locks.track():
            async with self._locks.acquire(main=True, agent_id=agent_id):
                # Every guard runs before git is invoked; a refusal keeps files, commits, branch.
                guard = partial(registered_worktree, self._root, self._main.root, agent_id)
                target = await offload(guard)
                tree = self._trees.get(agent_id) or Tree(self, target, agent_id, agent_id, None)
                git = self.git_ok
                if merged:
                    status = await git(tree, "status", "--porcelain", "--untracked-files=all")
                    if status.strip():
                        raise UnsafeDeletion(f"{agent_id}'s worktree has uncommitted changes")
                    check = ("merge-base", "--is-ancestor", agent_id, "main")
                    if (await self.git(self._main, *check)).returncode != 0:
                        raise UnsafeDeletion(f"{agent_id}'s branch is not merged into main")
                    await git(self._main, "worktree", "remove", str(target))
                else:
                    await git(self._main, "worktree", "remove", "--force", "--force", str(target))
                await offload(partial(remove_tree, self._root, target))
                await git(self._main, "worktree", "prune")
                if delete_branch:
                    await git(self._main, "branch", "-d" if merged else "-D", agent_id)
                self._trees.pop(agent_id, None)

    async def discard_worktree(self, agent_id: AgentId, *, delete_branch: bool) -> None:
        """The user rejected the change: remove the worktree whatever it holds."""
        await self._remove_worktree(agent_id, merged=False, delete_branch=delete_branch)

    async def cleanup_merged_worktree(self, agent_id: AgentId, *, delete_branch: bool) -> None:
        """After an approved merge: remove the worktree only if it is clean and its branch is in
        main, else `UnsafeDeletion`."""
        await self._remove_worktree(agent_id, merged=True, delete_branch=delete_branch)

    # --- Tests ------------------------------------------------------------------------------

    async def run_tests(
        self,
        tree: Tree,
        *,
        timeout_s: float,
        case_ids: tuple[str, ...] = (),
        spawn: Spawn = run_process,
    ) -> SuiteRun:
        """Run the demo repo's tests in `tree`, under its lock, in the demo venv."""
        async with tree.section(locked=True):
            await offload(lambda: self.tmp_dir.mkdir(mode=DIR_MODE, parents=True, exist_ok=True))
            tested_tree, tested_commit = await capture_test_tree(
                tree.root,
                self.tmp_dir,
                env=self._git_env(None, None),
                flags=GIT_FLAGS,
                timeout_s=timeout_s,
            )
            try:
                run = await run_suite(
                    python=self._demo_python,
                    plugin_dir=PLUGIN_DIR,
                    runtime_root=self._root,
                    tree_root=tree.root,
                    timeout_s=timeout_s,
                    redact=self.redact,
                    spawn=spawn,
                    case_ids=case_ids,
                )
            except (SuiteInterrupted, SuiteUnavailable) as error:
                error.run = replace(error.run, tested_tree=tested_tree, tested_commit=tested_commit)
                raise
            return replace(run, tested_tree=tested_tree, tested_commit=tested_commit)


__all__ = ["Author", "Tree", "Workspace"]
