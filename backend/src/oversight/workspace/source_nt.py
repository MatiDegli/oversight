"""Internal NT source selection, staging and recovery; never a public selector.

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
from dataclasses import dataclass, field, replace
from pathlib import Path
from uuid import uuid4

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
from oversight.workspace.integrity import IntegrityError, validate_source_byte_policy
from oversight.workspace.locks import offload
from oversight.workspace.metadata_io import (
    MAX_FILE as MAX_METADATA_FILE,
)
from oversight.workspace.metadata_io import (
    Budget,
    Entry,
    make_directory,
    nt_token,
    read_entry,
    remove_directory,
    replace_file,
)
from oversight.workspace.metadata_io import (
    directory as safe_directory,
)
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
    metadata: tuple[tuple[str, tuple[int, ...] | None, str | None], ...] = ()


def source_metadata(
    root: Path, deadline: float, clock: Callable[[], float] = time.monotonic
) -> tuple[tuple[str, tuple[int, ...] | None, str | None], ...]:
    deadline = time.monotonic() + remaining(deadline, clock)
    result: list[tuple[str, tuple[int, ...] | None, str | None]] = []
    for name in ("HEAD", "index"):
        path = root / ".git" / name
        try:
            before = nt_token(path.lstat()) if os.path.lexists(path) else None
            entry = read_entry(path, Budget(deadline)) if before is not None else None
            after = nt_token(path.lstat()) if os.path.lexists(path) else None
        except (OSError, IntegrityError):
            raise refuse(".git/" + name + ": unsafe or changed source metadata") from None
        if before != after or (entry is not None and entry.data is None):
            raise refuse(".git/" + name + ": metadata changed or unsafe")
        result.append(
            (
                name,
                after,
                hashlib.sha256(entry.data).hexdigest()
                if entry and entry.data is not None
                else None,
            )
        )
    return tuple(result)


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
    _deadline: float | None = None,
) -> Inventory:
    """Read-only Git inventory; caller owns the prior-runtime and trust prerequisites."""
    deadline = clock() + PHASE_SECONDS if _deadline is None else _deadline
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
    metadata = await offload(lambda: source_metadata(source.root, deadline, clock))
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
    if metadata != await offload(lambda: source_metadata(source.root, deadline, clock)):
        raise refuse("HEAD/index changed during inventory")
    return Inventory(
        source,
        head.strip(),
        tuple(sorted(paths)),
        frozenset(tracked),
        len(set(ignored)),
        deadline,
        metadata,
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
        ntpath.join(runtime, f"source-stage-{token}", suffix)
        for suffix in (
            "manifest",
            f".oversight-{token}",
            *("tree/" + p for p in paths),
            *("tree/" + ntpath.join(ntpath.dirname(p), f".oversight-{token}") for p in paths),
        )
    )
    result.extend(
        ntpath.join(runtime, p)
        for p in ("own-source", "own-reset", "own-runtime", f".oversight-{token}")
    )
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
    if listing.metadata and source_metadata(root, listing.deadline, clock) != listing.metadata:
        raise refuse("HEAD/index changed before validation")
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
    if listing.metadata and source_metadata(root, listing.deadline, clock) != listing.metadata:
        raise refuse("HEAD/index changed during validation")
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


@dataclass
class StagedSource:
    selection: Selection
    root: Path
    manifest: Path
    sensitive: Callable[[Path], bool] = case_sensitive_directory
    streams: Callable[[Path], Generator[str]] = stream_names
    deadline: float = float("inf")
    owned: list[tuple[Path, tuple[int, int]]] = field(
        default_factory=lambda: list[tuple[Path, tuple[int, int]]]()
    )

    @property
    def tree(self) -> Path:
        return self.root / "tree"

    def remember(self, path: Path) -> None:
        info = path.lstat()
        self.owned.append((path, (info.st_dev, info.st_ino)))

    def cleanup(self) -> None:
        self.cleanup_owned(self.owned)

    @staticmethod
    def cleanup_owned(owned: list[tuple[Path, tuple[int, int]]]) -> None:
        """Only exact recorded inodes, never a sibling walk or general deletion override."""
        deadline = time.monotonic() + PHASE_SECONDS
        for path, identity in owned:
            try:
                with safe_directory(path, deadline):
                    info = path.lstat()
                    if (info.st_dev, info.st_ino) != identity:
                        raise refuse("owned staging directory identity changed")
            except (FileNotFoundError, NotADirectoryError):
                continue
        for path, identity in reversed(owned):
            entry = read_entry(path, Budget(deadline))
            if entry is None:
                continue
            info = path.lstat()
            if (info.st_dev, info.st_ino) != identity:
                raise refuse("owned staging identity changed; preserve for recovery")
            if entry.data is None:
                remove_directory(path, deadline)
            else:
                replace_file(path, entry, None, deadline, cas=False)


def bounded_metadata(data: bytes) -> Entry:
    if len(data) > MAX_METADATA_FILE:
        raise refuse("source manifest/reset metadata exceeds the 4 MiB metadata budget")
    return Entry(0o600, data)


def manifest_bytes(selection: Selection) -> bytes:
    return b"".join(
        p.encode("utf-8") + b"\0"
        for p in sorted(e.path for e in selection.entries if e.hash is not None)
    )


def verify_stage(stage: StagedSource, runtime: Path, deadline: float) -> None:
    deadline = min(deadline, stage.deadline)
    if (
        stage.selection.inventory.source.runtime != runtime
        or stage.root.parent != runtime
        or re.fullmatch(r"source-stage-[0-9a-f]{32}", stage.root.name) is None
        or stage.manifest != stage.root / "manifest"
    ):
        raise refuse("unowned staging handoff")
    for path, identity in stage.owned:
        entry = read_entry(path, Budget(deadline))
        info = path.lstat()
        if entry is not None and entry.data is None:
            owned_paths = {p for p, _ in stage.owned}
            if any(child not in owned_paths for child in path.iterdir()):
                raise refuse("unexpected staging entry")
        if entry is None or (info.st_dev, info.st_ino) != identity:
            raise refuse("owned staging identity changed")
    if read_entry(stage.manifest, Budget(deadline)) != Entry(
        0o600, manifest_bytes(stage.selection)
    ):
        raise refuse("staging manifest changed")
    listing = replace(
        stage.selection.inventory,
        source=replace(stage.selection.inventory.source, root=stage.tree),
        metadata=(),
        deadline=deadline,
    )
    checked = validate_selection(listing, sensitive=stage.sensitive, streams=stage.streams)
    if [(e.path, e.hash, e.size) for e in checked.entries] != [
        (e.path, e.hash, e.size) for e in stage.selection.entries
    ]:
        raise refuse("staging bytes changed")


def publish_stage(stage: StagedSource, runtime: Path) -> None:
    deadline = time.monotonic() + PHASE_SECONDS
    with safe_directory(stage.root, deadline), safe_directory(runtime, deadline):
        info = stage.tree.lstat()
        if (stage.tree, (info.st_dev, info.st_ino)) not in stage.owned:
            raise refuse("staging tree identity changed")
        os.replace(stage.tree, runtime / "workspace")


def copy_selection(stage: StagedSource, deadline: float, clock: Callable[[], float]) -> None:
    io_until = time.monotonic() + remaining(deadline, clock)
    make_directory(stage.root, 0o700, io_until)
    stage.remember(stage.root)
    make_directory(stage.tree, 0o700, io_until)
    stage.remember(stage.tree)
    for relative, _ in stage.selection.directories:
        if relative:
            make_directory(stage.tree / relative, 0o700, io_until)
            stage.remember(stage.tree / relative)
    for entry in stage.selection.entries:
        remaining(deadline, clock)
        if entry.hash is None:
            continue
        path = stage.selection.inventory.source.root / entry.path
        with open_nt(path) as handle:
            data = handle.read(MAX_FILE + 1)
            if (
                nt_token(os.fstat(handle.fileno())) != entry.identity
                or nt_token(path.lstat()) != entry.identity
                or len(data) != entry.size
                or hashlib.sha256(data).hexdigest() != entry.hash
            ):
                raise refuse(entry.path + ": changed during copy")
        target = stage.tree / entry.path
        replace_file(target, None, Entry(0o600, data), io_until, cas=False)
        stage.remember(target)
    replace_file(
        stage.manifest, None, bounded_metadata(manifest_bytes(stage.selection)), io_until, cas=False
    )
    stage.remember(stage.manifest)


async def stage_source(
    selection: Selection,
    *,
    git: str,
    lock_fd: int,
    spawn: Spawn = run_process,
    sensitive: Callable[[Path], bool] = case_sensitive_directory,
    streams: Callable[[Path], Generator[str]] = stream_names,
    clock: Callable[[], float] = time.monotonic,
    ancestors: tuple[Path, ...] | None = None,
) -> StagedSource:
    """Independent COPY deadline, including all revalidation and the final Git relisting."""
    deadline = clock() + PHASE_SECONDS
    listing = replace(selection.inventory, deadline=deadline)
    source = listing.source
    root = source.runtime / ("source-stage-" + uuid4().hex)
    stage = StagedSource(
        selection,
        root,
        root / "manifest",
        sensitive,
        streams,
        time.monotonic() + remaining(deadline, clock),
    )
    try:
        if not listing.metadata or any(token is None for _, token, _ in listing.metadata):
            raise refuse("source HEAD/index handoff missing")
        for path in projections(source, listing.paths):
            short_path(path)

        def check() -> Selection:
            return validate_selection(listing, sensitive=sensitive, streams=streams, clock=clock)

        if await offload(check) != replace(selection, inventory=listing):
            raise refuse("selection changed before copy")
        await offload(
            lambda: validate_source_byte_policy(
                source.root,
                source.root / ".git",
                tuple(e.path for e in selection.entries if e.hash is not None),
                time.monotonic() + remaining(deadline, clock),
            )
        )
        await offload(lambda: copy_selection(stage, deadline, clock))
        if await offload(check) != replace(selection, inventory=listing):
            raise refuse("selection changed during copy")
        again = await inventory(
            source,
            git=git,
            lock_fd=lock_fd,
            spawn=spawn,
            clock=clock,
            ancestors=ancestors,
            _deadline=deadline,
        )
        if replace(again, deadline=listing.deadline) != listing:
            raise refuse("HEAD/index/selection changed during copy")
        remaining(deadline, clock)
        return stage
    except (OSError, IntegrityError) as exc:
        await offload(stage.cleanup)
        if str(exc).startswith("Windows source refused:"):
            raise
        raise refuse("unsafe or changed selected input; retry selection") from None
    except BaseException:
        await offload(stage.cleanup)
        raise


def object_id(value: str, width: int | None = None) -> str:
    if (
        re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) is None
        or (width and len(value) != width)
        or not value.strip("0")
    ):
        raise refuse("invalid pinned object ID")
    return value


def metadata_bytes(path: Path, deadline: float) -> bytes | None:
    entry = read_entry(path, Budget(deadline))
    if entry is not None and entry.data is None:
        raise refuse("metadata is a directory")
    return entry.data if entry else None


def main_ref(
    runtime: Path, deadline: float, *, width: int | None = None, unborn: bool = False
) -> str | None:
    common = runtime / "workspace/.git"
    paths = (common / "HEAD", common / "refs/heads/main", common / "packed-refs")
    head, loose, packed = snapshot = tuple(metadata_bytes(p, deadline) for p in paths)
    if head != b"ref: refs/heads/main\n" and not (unborn and head is None):
        raise refuse("runtime HEAD missing or not symbolic main")
    if loose is not None and (not loose.endswith(b"\n") or loose.count(b"\n") != 1):
        raise refuse("malformed loose main ref")
    if snapshot != tuple(metadata_bytes(p, deadline) for p in paths):
        raise refuse("runtime refs changed during read")
    found: dict[str, str] = {}
    for line in (packed or b"").decode("ascii").splitlines():
        if line.startswith("#"):
            continue
        if line.startswith("^"):
            object_id(line[1:], width)
            continue
        oid, separator, name = line.partition(" ")
        object_id(oid, width)
        if separator != " " or not name.startswith("refs/") or name in found:
            raise refuse("corrupt packed refs")
        found[name] = oid
    value = (
        loose.decode("ascii").removesuffix("\n")
        if loose is not None
        else found.get("refs/heads/main")
    )
    if value is None and unborn:
        return None
    return object_id(value or "", width)


def directory_id(path: Path, deadline: float) -> str:
    with safe_directory(path, deadline):
        info = path.lstat()
        return f"{info.st_dev}:{info.st_ino}"


def state_fields(path: Path, deadline: float) -> list[str] | None:
    data = metadata_bytes(path, deadline)
    if data is None:
        return None
    if not data.endswith(b"\0"):
        raise refuse("corrupt reset state")
    return data[:-1].decode("utf-8").split("\0")


def store_fields(path: Path, fields: list[str] | None, deadline: float) -> None:
    before = read_entry(path, Budget(deadline))
    wanted = bounded_metadata(("\0".join(fields) + "\0").encode()) if fields is not None else None
    if before is not None or wanted is not None:
        replace_file(path, before, wanted, deadline, cas=False)


def identity_field(value: str) -> tuple[int, int]:
    if re.fullmatch(r"[0-9]+:[0-9]+", value) is None:
        raise refuse("corrupt identity in reset state")
    dev, ino = value.split(":")
    return int(dev), int(ino)


def ps_quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def recovery(runtime: Path, baseline: list[str], current: str) -> str:
    patch = runtime.parent / ("oversight-export-" + uuid4().hex + ".patch")
    initial, source = baseline[:2]
    return (
        f"main {current} differs from initial {initial}. Preserve the entire runtime "
        f"{ps_quote(runtime)} for uncommitted edits; export covers committed delta only. "
        f"In a private folder, run: git -C {ps_quote(runtime / 'workspace')} diff --binary "
        f"--full-index --no-ext-diff --no-textconv --no-renames "
        f"{ps_quote('--output=' + str(patch))} "
        f"{initial} main ; then git -C {ps_quote(source)} apply --check {ps_quote(patch)} ; "
        f"then explicitly git -C {ps_quote(source)} apply {ps_quote(patch)}. Source working bytes "
        "must match the imported baseline, including dirty/untracked files; otherwise preserve "
        "the patch and resolve manually. Export never applies or discards automatically. "
        f"Only to explicitly discard, set $env:OVERSIGHT_DISCARD_OWN_RUNTIME = {ps_quote(current)} "
        "in this process and retry; no .env setting is read."
    )


def journal_owned(runtime: Path, journal: list[str]) -> list[tuple[Path, tuple[int, int]]]:
    if len(journal) < 8 or (len(journal) - 8) % 2:
        raise refuse("corrupt incomplete reset")
    if journal[4] == "-":
        if len(journal) != 8:
            raise refuse("unexpected staging records")
        return []
    if re.fullmatch(r"source-stage-[0-9a-f]{32}", journal[4]) is None:
        raise refuse("unowned recovery staging")
    root = runtime / journal[4]
    owned: list[tuple[Path, tuple[int, int]]] = []
    if len(journal[8:]) // 2 > MAX_PATHS + 3:
        raise refuse("oversized staging state")
    for relative, identity in zip(journal[8::2], journal[9::2], strict=True):
        if relative:
            split_relative(relative, nt=True)
            if relative != "manifest" and relative != "tree" and not relative.startswith("tree/"):
                raise refuse("unowned recovery entry")
        owned.append((root / relative, identity_field(identity)))
    if not owned or owned[0][0] != root:
        raise refuse("missing staging root identity")
    return owned


def guard_previous(runtime: Path, *, recover: bool = False) -> bool:
    """No Git. Call before source Git at future startup, regardless of selector/provider."""
    deadline = time.monotonic() + PHASE_SECONDS
    baseline: list[str] | None = None
    verified_main: str | None = None
    try:
        baseline = state_fields(runtime / "own-source", deadline)
        journal = state_fields(runtime / "own-reset", deadline)
        marker = state_fields(runtime / "own-runtime", deadline)
        if marker not in (None, ["own-runtime-v1"]) or (
            marker and baseline is None and journal is None
        ):
            raise refuse("missing baseline or unsafe own-runtime marker")
        if baseline is None and journal is None:
            return False
        root_id = directory_id(runtime, deadline)
        if baseline is not None:
            if len(baseline) != 5 or baseline[3] != root_id or not Path(baseline[1]).is_absolute():
                raise refuse("invalid baseline identity")
            short_path(baseline[1])
            object_id(baseline[0])
            for value in baseline[2:]:
                identity_field(value)
        owned = journal_owned(runtime, journal) if journal is not None else []
        tree = runtime / "workspace"
        tree_id = directory_id(tree, deadline) if os.path.lexists(tree) else "-"
        if baseline is not None and tree_id == baseline[4]:
            current = main_ref(runtime, deadline, width=len(baseline[0]))
            if (
                current != baseline[0]
                and os.environ.get("OVERSIGHT_DISCARD_OWN_RUNTIME") != current
            ):
                raise refuse(recovery(runtime, baseline, current or ""))
            verified_main = current
        elif journal is None:
            raise refuse("baseline workspace identity changed")
        if journal is not None:
            for value in (journal[2], journal[7]):
                if value != "-":
                    object_id(value, len(baseline[0]) if baseline else None)
            if journal[4] != "-" and not Path(journal[5]).is_absolute():
                raise refuse("invalid incomplete source path")
            if journal[0] != root_id:
                raise refuse("incomplete reset runtime identity changed")
            for value in (journal[1], journal[3], journal[6]):
                if value != "-":
                    identity_field(value)
            if tree_id != "-":
                current = main_ref(runtime, deadline, unborn=tree_id == journal[3])
                expected = (
                    journal[7]
                    if journal[7] != "-"
                    else (journal[2] if tree_id == journal[1] and journal[2] != "-" else None)
                )
                # A published baseline takes precedence over a leftover journal.
                if (tree_id not in {journal[1], journal[3]} or current != expected) and (
                    baseline is None or tree_id != baseline[4] or current != verified_main
                ):
                    raise refuse("ambiguous interrupted reset; preserve runtime")
            if recover:
                StagedSource.cleanup_owned(owned)
        return True
    except (OSError, IntegrityError, ValueError) as exc:
        if str(exc).startswith("Windows source refused: main "):
            raise
        raise refuse(
            f"unsafe, missing or corrupt reset state/ref; preserve {ps_quote(runtime)} "
            "and recover its files and ownership records manually before restarting"
        ) from None


def begin_reset(runtime: Path, stage: StagedSource | None) -> None:
    deadline = time.monotonic() + PHASE_SECONDS
    previous = (
        directory_id(runtime / "workspace", deadline)
        if os.path.lexists(runtime / "workspace")
        else "-"
    )
    old = main_ref(runtime, deadline, unborn=True) if previous != "-" else None
    fields = [directory_id(runtime, deadline), previous, old or "-", "-", "-", "-", "-", "-"]
    if stage is not None:
        source = stage.selection.inventory.source.root
        fields[3:7] = [
            directory_id(stage.tree, deadline),
            stage.root.name,
            str(source),
            ":".join(str(n) for n in dict(stage.selection.directories)[""][:2]),
        ]
        for path, identity in stage.owned:
            fields.extend(
                [
                    path.relative_to(stage.root).as_posix() if path != stage.root else "",
                    f"{identity[0]}:{identity[1]}",
                ]
            )
    store_fields(runtime / "own-reset", fields, deadline)
    if stage is not None:
        store_fields(runtime / "own-runtime", ["own-runtime-v1"], deadline)


def finish_reset(runtime: Path, commit: str, stage: StagedSource | None) -> None:
    deadline = time.monotonic() + PHASE_SECONDS
    journal = state_fields(runtime / "own-reset", deadline)
    if journal is None:
        raise refuse("incomplete reset state missing")
    object_id(commit)
    if main_ref(runtime, deadline, width=len(commit)) != commit:
        raise refuse("initial ref changed before baseline publication")
    if stage is not None:
        store_fields(
            runtime / "own-source",
            [
                commit,
                journal[5],
                journal[6],
                journal[0],
                directory_id(runtime / "workspace", deadline),
            ],
            deadline,
        )
    else:
        journal[3], journal[7] = directory_id(runtime / "workspace", deadline), commit
        store_fields(runtime / "own-reset", journal, deadline)
        store_fields(runtime / "own-source", None, deadline)
        store_fields(runtime / "own-runtime", None, deadline)
    if stage is not None:
        stage.cleanup()
    store_fields(runtime / "own-reset", None, deadline)
