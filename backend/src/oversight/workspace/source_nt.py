"""Internal NT source inputs, never a public selector or a reset/copy entry point.

Future wiring must preflight before constructors, then hold the runtime lock/server Job,
guard the previous runtime and disclose trust before calling source Git. Staging must
validate this inventory again before reset. See DECISIONS.md and docs/workspace-source.md.
"""

import hashlib
import ntpath
import os
import re
import stat
import sys
import time
from collections.abc import Callable, Generator
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from oversight.workspace._platform import (
    case_sensitive_directory,
    is_link_like,
    preflight_placement,
    stream_names,
    verify_sentinel,
)
from oversight.workspace.files import (
    CACHE_DIRS,
    nt_plain_parents,
    open_nt,
    resolve_in,
    split_relative,
)
from oversight.workspace.locks import offload
from oversight.workspace.metadata_io import nt_token
from oversight.workspace.runner import Spawn, run_process
from oversight.workspace.secret_paths import is_secret_path
from oversight.workspace.source import BOLTONS, SourceProfile

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
    expected = (
        ntpath.normcase(ntpath.join(runtime, profile.checkout_dir))
        if profile is not None and profile == BOLTONS
        else None
    )
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
    inspect: Callable[[Path], None] | None = None,
) -> NtSource:
    """Lexical checks for ALL placements precede any injected filesystem operation."""
    source, runtime, ide, venv = map(absolute_drive, (source, runtime, ide, venv))
    overlaps(source, runtime)
    for path in (*projections(NtSource(Path(), Path(), source, runtime)), ide, venv):
        short_path(path)
    for path in (ide, runtime, source, venv):
        preflight(path)
        if inspect is not None or sys.platform == "win32":
            (inspect or existing_parents)(bind(path))
    return NtSource(bind(source), bind(runtime), source, runtime)


def existing_parents(
    path: Path, *, sensitive: Callable[[Path], bool] = case_sensitive_directory
) -> None:
    for parent in (*reversed(path.parents), path):
        try:
            nt_plain_parents(parent)
            if sensitive(parent):
                raise refuse("case-sensitive placement ancestor")
        except FileNotFoundError:
            break


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
    if any(p.casefold() == ".gitmodules" or p.upper() == ".GITMODULES" for p in parts):
        raise refuse("submodule metadata")
    if any(any(c in '<>"|?*' for c in part) for part in parts):
        raise refuse("unsafe selected component")
    return not (
        is_secret_path(path, nt=True)
        or any(p.casefold() in CACHE_DIRS | {"node_modules"} for p in parts)
        or path.casefold().endswith(".pyc")
    )


def repository(root: Path, ancestors: tuple[Path, ...]) -> None:
    nt_plain_parents(root / ".git")  # A gitdir file, linked worktree or junction cannot pass.
    if os.path.lexists(root / ".git/commondir"):
        raise refuse("linked worktree/common directory")
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
    if not absolute_drive(git).lower().endswith(".exe"):
        raise refuse("absolute Git executable required")
    sentinel = source.runtime / "server.lock"
    await offload(lambda: verify_sentinel(sentinel, lock_fd))
    for path in projections(source):
        short_path(path)
    await offload(
        lambda: repository(
            source.root,
            tuple(source.root.parents) if ancestors is None else ancestors,
        )
    )
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
        remaining(deadline, clock)
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
        remaining(deadline, clock)
        if selected(path):
            paths.add(path)
    ignored = records(
        await query("ls-files", "--others", "--ignored", "--exclude-standard", "--directory", "-z")
    )
    for ignored_path in ignored:
        split_relative(ignored_path.removesuffix("/"), nt=True)
        remaining(deadline, clock)
    path_set(tuple(paths))
    for path in projections(source, tuple(paths)):
        short_path(path)
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


def projections(source: NtSource, paths: tuple[str, ...] = ()) -> tuple[str, ...]:
    # Current layout: core.create_worktree, test_tree.capture_test_tree,
    # probes.base_probe_checkout and suite.run_suite. Longest valid agent ID is 10 chars.
    runtime, token = source.runtime_path, "0" * 32
    trees = ("workspace", "worktrees/agent-9999", f"tmp/base-probe-{token}")
    result = [
        ntpath.join(base, path)
        for base in (source.source_path, *(ntpath.join(runtime, tree) for tree in trees))
        for path in (*paths, ".git")
    ]
    result.extend(
        ntpath.join(runtime, "tmp", suffix)
        for suffix in (
            f"suite-index-{token}.lock",
            f".write-{token}",
            *(token + suffix for suffix in (".jsonl", ".xml", ".calls-input", ".calls")),
        )
    )
    result.extend(
        ntpath.join(base, ".git", suffix)
        for base in (source.source_path, ntpath.join(runtime, "workspace"))
        for suffix in ("index.lock", "objects/00/" + "0" * 62)
    )
    result.extend(
        ntpath.join(runtime, "workspace/.git/worktrees", name, "index.lock")
        for name in ("agent-9999", f"base-probe-{token}")
    )
    return tuple(result)


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


def validate_selection(
    listing: Inventory,
    *,
    sensitive: Callable[[Path], bool] = case_sensitive_directory,
    streams: Callable[[Path], Generator[str]] = stream_names,
    clock: Callable[[], float] = time.monotonic,
) -> Selection:
    """No writes. Recheck records during W4a2 staging; these attest only this phase."""
    root = listing.source.root
    entries: list[ValidatedEntry] = []
    directories: dict[str, tuple[int, ...]] = {}
    total = 0
    unique = path_set(listing.paths)
    for target in projections(listing.source, listing.paths):
        short_path(target)
    check_directory(root, listing.deadline, clock, sensitive, streams)
    directories[""] = nt_token(root.lstat())
    for relative in sorted(unique, key=lambda p: (p.count("/"), p)):
        remaining(listing.deadline, clock)
        path = resolve_in(root, relative, nt=True)
        try:
            info = path.lstat()
        except FileNotFoundError:
            if relative in listing.paths and relative in listing.tracked:
                entries.append(ValidatedEntry(relative, None, None, 0))
                continue
            if any(p.startswith(relative + "/") for p in listing.tracked):
                continue
            raise refuse(relative + ": untracked entry disappeared") from None
        if is_link_like(info, nt=True) or getattr(info, "st_file_attributes", 0) & (
            0x1000 | 0x40000 | 0x400000
        ):
            raise refuse(relative + ": reparse, offline or recall entry")
        if stat.S_ISDIR(info.st_mode):
            if relative in listing.paths:
                raise refuse(relative + ": selected file became a directory")
            check_directory(path, listing.deadline, clock, sensitive, streams)
            directories[relative] = nt_token(info)
            continue
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise refuse(relative + ": nonregular or multiple-link file")
        elif info.st_size > MAX_FILE:
            raise refuse(relative + ": exceeds 4 MiB; move the asset out or use smaller source")
        check_streams(path, listing.deadline, clock, streams)
        with open_nt(path) as handle:
            if nt_token(os.fstat(handle.fileno())) != nt_token(info):
                raise refuse(relative + ": changed before read")
            data = handle.read(MAX_FILE + 1)
            remaining(listing.deadline, clock)
            if len(data) > MAX_FILE or total + len(data) > MAX_TOTAL:
                raise refuse(relative + ": selected bytes exceed budget")
            if (
                nt_token(os.fstat(handle.fileno())) != nt_token(info)
                or nt_token(path.lstat()) != nt_token(info)
                or len(data) != info.st_size
            ):
                raise refuse(relative + ": changed during read")
        total += len(data)
        entries.append(
            ValidatedEntry(relative, nt_token(info), hashlib.sha256(data).hexdigest(), len(data))
        )
    for relative, token in directories.items():
        if nt_token((root / relative).lstat()) != token:
            raise refuse(relative + ": directory changed")
    remaining(listing.deadline, clock)
    return Selection(listing, tuple(entries), tuple(sorted(directories.items())), total)


def check_directory(
    path: Path,
    deadline: float,
    clock: Callable[[], float],
    sensitive: Callable[[Path], bool],
    streams: Callable[[Path], Generator[str]],
) -> None:
    nt_plain_parents(path)
    if getattr(path.lstat(), "st_file_attributes", 0) & (0x1000 | 0x40000 | 0x400000):
        raise refuse("offline or recall directory")
    if sensitive(path):
        raise refuse("case-sensitive directory")
    check_streams(path, deadline, clock, streams)
    folded: set[str] = set()
    upper: set[str] = set()
    for child in path.iterdir():
        remaining(deadline, clock)
        if child.name.casefold() in folded or child.name.upper() in upper:
            raise refuse("sibling casefold or uppercase collision")
        folded.add(child.name.casefold())
        upper.add(child.name.upper())


def check_streams(
    path: Path,
    deadline: float,
    clock: Callable[[], float],
    streams: Callable[[Path], Generator[str]],
) -> None:
    with closing(streams(path)) as names:
        for name in names:
            remaining(deadline, clock)
            if name != "::$DATA":
                raise refuse(
                    f"{path.name}: stream {name}; review first, then Unblock-File or "
                    "Remove-Item -Stream Zone.Identifier for Zone.Identifier"
                )
