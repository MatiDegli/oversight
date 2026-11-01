"""Internal NT source inputs, never a public selector or a reset/copy entry point.

Future wiring must preflight before constructors, then hold the runtime lock/server Job,
guard the previous runtime and disclose trust before calling source Git. Staging must
validate this inventory again before reset. See DECISIONS.md and docs/workspace-source.md.
"""

import ntpath
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from oversight.workspace._platform import preflight_placement, verify_sentinel
from oversight.workspace.files import CACHE_DIRS, nt_plain_parents, split_relative
from oversight.workspace.locks import offload
from oversight.workspace.runner import Spawn, run_process
from oversight.workspace.secret_paths import is_secret_path
from oversight.workspace.source import SourceProfile

MAX_GIT = 8 * 1024 * 1024
PHASE_SECONDS = 10
REMEDY = "Use short private local NTFS nonsynced folders; use a separate clone for overlap."


def refuse(reason: str) -> OSError:
    return OSError("Windows source refused: " + reason)


def absolute_drive(raw: str) -> str:
    drive, tail = ntpath.splitdrive(raw)
    if not re.fullmatch(r"[A-Za-z]:", drive) or not tail.startswith(("/", "\\")):
        raise refuse("an absolute drive path is required. " + REMEDY)
    relative = tail[1:].replace("\\", "/")
    if relative:
        split_relative(relative, nt=True)
    if any(ord(c) < 32 or c in '<>"|?*' for c in raw):
        raise refuse("unsafe placement component. " + REMEDY)
    short_path(raw)
    return ntpath.normpath(raw)


def overlaps(source: str, runtime: str, *, profile: SourceProfile | None = None) -> None:
    source, runtime = ntpath.normcase(source), ntpath.normcase(runtime)
    if ntpath.splitdrive(source)[0] != ntpath.splitdrive(runtime)[0]:
        return
    common = ntpath.commonpath([source, runtime])
    expected = ntpath.normcase(ntpath.join(runtime, profile.checkout_dir)) if profile else None
    if common in {source, runtime} and source != expected:
        raise refuse("source/runtime ancestor overlap. " + REMEDY)


@dataclass(frozen=True)
class NtSource:
    root: Path
    runtime: Path
    source_path: str
    runtime_path: str


def prepare_source(
    source: str,
    runtime: str,
    ide: str,
    venv: str,
    *,
    preflight: Callable[[str], None] = preflight_placement,
    bind: Callable[[str], Path] = Path,
) -> NtSource:
    """Lexical checks for ALL placements precede any injected filesystem operation."""
    source, runtime, ide, venv = map(absolute_drive, (source, runtime, ide, venv))
    overlaps(source, runtime)
    for path in (ide, runtime, source, venv):
        preflight(path)
    return NtSource(bind(source), bind(runtime), source, runtime)


@dataclass(frozen=True)
class Inventory:
    source: NtSource
    head: str
    paths: tuple[str, ...]
    tracked: frozenset[str]
    skipped_roots: int
    deadline: float


def remaining(deadline: float, clock: Callable[[], float]) -> float:
    left = deadline - clock()
    if left < 0:
        raise refuse("10-second selection phase deadline exceeded")
    return max(left, 1e-9)


def records(text: str) -> list[str]:
    if text and (not text.endswith("\0") or "\ufffd" in text):
        raise refuse("malformed Git path output")
    result = text[:-1].split("\0") if text else []
    if any(not path for path in result):
        raise refuse("empty Git record")
    return result


def selected(path: str) -> bool:
    parts = split_relative(path, nt=True)
    if any(any(c in '<>"|?*' for c in part) for part in parts):
        raise refuse("unsafe selected component")
    return not (
        is_secret_path(path, nt=True)
        or any(p.casefold() in CACHE_DIRS | {"node_modules"} for p in parts)
        or path.casefold().endswith(".pyc")
    )


def repository(root: Path, ancestors: tuple[Path, ...]) -> None:
    nt_plain_parents(root / ".git")  # A gitdir file, linked worktree or junction cannot pass.
    for parent in ancestors:
        if any(p.name.casefold() == ".git" or p.name.upper() == ".GIT" for p in parent.iterdir()):
            raise refuse("source is nested inside another repository")


def nested_markers(root: Path, paths: set[str]) -> None:
    parents = {root}
    for path in paths:
        current = root
        for part in path.split("/")[:-1]:
            current /= part
            parents.add(current)
    for directory in parents:
        try:
            nt_plain_parents(directory)
        except FileNotFoundError:
            continue
        for child in directory.iterdir():
            if (child.name.casefold() == ".git" or child.name.upper() == ".GIT") and (
                directory != root or child.name != ".git"
            ):
                raise refuse("nested repository marker")


async def inventory(
    source: NtSource,
    *,
    git: str,
    lock_fd: int,
    spawn: Spawn = run_process,
    clock: Callable[[], float] = time.monotonic,
    ancestors: tuple[Path, ...] | None = None,
) -> Inventory:
    """Read-only Git inventory; caller owns the prior-runtime and trust prerequisites."""
    deadline = clock() + PHASE_SECONDS
    if not PureWindowsPath(git).is_absolute() or not git.lower().endswith(".exe"):
        raise refuse("absolute Git executable required")
    await offload(
        lambda: repository(
            source.root, tuple(source.root.parents) if ancestors is None else ancestors
        )
    )
    sentinel = source.runtime / "server.lock"
    flags = tuple(
        item
        for setting in (
            f"core.hooksPath={sentinel / 'hooks'}",
            "commit.gpgsign=false",
            "core.autocrlf=false",
            "core.symlinks=false",
            "core.fsmonitor=false",
            "diff.ignoreSubmodules=all",
            "core.eol=lf",
            "core.longpaths=true",
            "core.protectNTFS=true",
        )
        for item in ("-c", setting)
    )
    env = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "nul",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_DIR": str(source.root / ".git"),
        "GIT_WORK_TREE": str(source.root),
    }

    async def query(*args: str) -> str:
        await offload(lambda: verify_sentinel(sentinel, lock_fd))
        result = await spawn(
            [git, *flags, *args],
            cwd=source.root,
            env=env,
            timeout_s=remaining(deadline, clock),
            output_cap=MAX_GIT,
        )
        remaining(deadline, clock)
        if result.returncode != 0 or result.timed_out or result.truncated:
            raise refuse("failed, timed-out or truncated source Git query")
        if len((result.stdout + result.stderr).encode("utf-8")) > MAX_GIT:
            raise refuse("Git output exceeds 8 MiB")
        return result.stdout

    head = await query("rev-parse", "--verify", "HEAD")
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\n", head) is None:
        raise refuse("malformed source HEAD")
    tracked: set[str] = set()
    for row in records(await query("ls-files", "-s", "-z")):
        match = re.fullmatch(r"(100644|100755) ([0-9a-f]{40}|[0-9a-f]{64}) 0\t(.+)", row)
        if match is None:
            raise refuse("malformed index, conflict, symlink or gitlink")
        path = match[3]
        if selected(path):
            tracked.add(path)
    paths = set(tracked)
    for path in records(
        await query("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    ):
        if selected(path):
            paths.add(path)
    ignored = records(
        await query("ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z")
    )
    path_set(tuple(paths))
    await offload(lambda: nested_markers(source.root, paths))
    remaining(deadline, clock)
    return Inventory(
        source, head.strip(), tuple(sorted(paths)), frozenset(tracked), len(set(ignored)), deadline
    )


MAX_PATHS = 16384
MAX_FILE = 4 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024


def short_path(path: str) -> None:
    if len(path.encode("utf-16-le")) // 2 >= 240:
        raise refuse("path reaches 240 UTF-16 units. " + REMEDY)


def path_set(paths: tuple[str, ...]) -> set[str]:
    unique: set[str] = set()
    for path in paths:
        parts = split_relative(path, nt=True)
        unique.update("/".join(parts[:i]) for i in range(1, len(parts) + 1))
        if len(unique) > MAX_PATHS:
            raise refuse("selected paths and ancestors exceed 16,384")
    return unique


@dataclass(frozen=True)
class ValidatedEntry:
    path: str
    identity: tuple[int, ...] | None
    hash: str | None  # None denotes a working deletion, never an empty file.
    size: int


@dataclass(frozen=True)
class Selection:
    inventory: Inventory
    entries: tuple[ValidatedEntry, ...]
    directories: tuple[tuple[str, tuple[int, ...]], ...]
    total_bytes: int
