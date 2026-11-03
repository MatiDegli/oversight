"""Read-only POSIX selection and independently verified, disposable COPY handoffs.

These leaves grant no runtime reset or execution authority. The caller must hold its runtime
lock and satisfy trust/prior-runtime policy before inspection. All placements must already
exist; this module creates only fresh staging children, never the original or runtime root.
"""

import hashlib
import json
import os
import re
import stat
import time
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from oversight.workspace.locks import offload
from oversight.workspace.runner import ProcessResult, Spawn, run_process
from oversight.workspace.secret_paths import is_secret_path
from oversight.workspace.source_config import check_source_config

__all__ = [
    "FileEntry",
    "Inventory",
    "PosixSource",
    "Selection",
    "StagedSource",
    "Witness",
    "cleanup_stage",
    "inventory",
    "manifest_bytes",
    "prepare_source",
    "stage_source",
    "validate_selection",
    "verify_stage",
]

PHASE_SECONDS = 10
MAX_GIT = 8 * 1024 * 1024
MAX_PATHS = 16384
MAX_FILE = 4 * 1024 * 1024
MAX_TOTAL = 128 * 1024 * 1024
# Filesystem root ownership is the host trust anchor, including mapped user namespaces.
ROOT_UID = os.stat("/").st_uid
EXCLUDED = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".pyright",
        ".tox",
        ".nox",
        ".cache",
        ".runtime",
        "dist",
        "build",
        ".next",
        "htmlcov",
        ".coverage",
        "coverage",
    }
)


def refuse(reason: str) -> OSError:
    return OSError("POSIX source refused: " + reason)


def remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise refuse("phase deadline exceeded")
    return left


def absolute(path: Path) -> Path:
    raw = str(path)
    if (
        not raw.startswith("/")
        or raw.startswith("//")
        or any(p in {".", ".."} for p in raw.split("/"))
        or any(ord(c) < 32 for c in raw)
    ):
        raise refuse("canonical absolute POSIX path required")
    if path != Path("/"):
        relative(raw[1:])
    return path


def relative(path: str) -> tuple[str, ...]:
    parts = tuple(path.split("/"))
    if (
        len(path.encode("utf-8", "surrogatepass")) > 4096
        or len(parts) > 64
        or any(
            not p
            or p in {".", ".."}
            or len(p.encode("utf-8", "surrogatepass")) > 255
            or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in p)
            for p in parts
        )
    ):
        raise refuse("unsafe relative path")
    return parts


def directory_names(fd: int, deadline: float) -> tuple[str, ...]:
    names: list[str] = []
    with os.scandir(fd) as entries:
        for item in entries:
            remaining(deadline)
            if len(names) >= MAX_PATHS:
                raise refuse("directory entry budget exceeded")
            names.append(item.name)
    return tuple(sorted(names))


def identity(info: os.stat_result) -> tuple[int, ...]:
    return info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid


def token(info: os.stat_result) -> tuple[int, ...]:
    return (*identity(info), info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def secure(info: os.stat_result, *, sticky_ancestor: bool = False) -> None:
    if info.st_uid not in {ROOT_UID, os.geteuid()} or (
        info.st_mode & 0o022
        and not (sticky_ancestor and info.st_uid == ROOT_UID and info.st_mode & stat.S_ISVTX)
    ):
        raise refuse("unsafe ownership or writable ancestor/input")


@dataclass(frozen=True)
class Witness:
    path: Path
    identity: tuple[int, ...]


@contextmanager
def directory(path: Path) -> Generator[int]:
    """Pin every ancestor with O_NOFOLLOW; compare names again before closing descriptors."""
    absolute(path)
    descriptors: list[int] = []
    witnesses: list[Witness] = []
    current = Path("/")
    try:
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        descriptors.append(fd)
        for part in path.parts[1:]:
            secure(os.fstat(fd), sticky_ancestor=True)
            current /= part
            fd = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd
            )
            descriptors.append(fd)
            witnesses.append(Witness(current, identity(os.fstat(fd))))
        secure(os.fstat(fd), sticky_ancestor=True)
        yield fd
        for witness in witnesses:
            if identity(witness.path.lstat()) != witness.identity:
                raise refuse("directory identity changed")
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def read_file(
    path: Path, deadline: float, *, limit: int = MAX_FILE, hardlinks: bool = False
) -> tuple[bytes, tuple[int, ...]]:
    remaining(deadline)
    with directory(path.parent) as parent:
        fd = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent
        )
        try:
            before = os.fstat(fd)
            secure(before)
            if not stat.S_ISREG(before.st_mode) or (not hardlinks and before.st_nlink != 1):
                raise refuse("nonregular or multiple-link input")
            if before.st_size > limit:
                raise refuse("file byte budget exceeded")
            data = bytearray()
            while len(data) <= limit:
                remaining(deadline)
                chunk = os.read(fd, min(65536, limit + 1 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
            if (
                len(data) != before.st_size
                or token(os.fstat(fd)) != token(before)
                or (
                    token(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) != token(before)
                )
            ):
                raise refuse("file identity or bytes changed during read")
            return bytes(data), token(before)
        finally:
            os.close(fd)


def anchors(path: Path) -> tuple[Witness, ...]:
    with directory(path):
        secure(path.lstat())
        return tuple(Witness(p, identity(p.lstat())) for p in (*reversed(path.parents), path))


@dataclass(frozen=True)
class PosixSource:
    root: Path
    runtime: Path
    ide: Path
    placements: tuple[Witness, ...]


def _placement(source: Path, runtime: Path, ide: Path) -> PosixSource:
    for path in (source, runtime, ide):
        absolute(path)
    for other in (runtime, ide):
        if source.is_relative_to(other) or other.is_relative_to(source):
            raise refuse("source/runtime or running checkout ancestor overlap")
    source_chain, runtime_chain, ide_chain = (anchors(p) for p in (source, runtime, ide))
    for other in (runtime_chain, ide_chain):
        # Bind aliases preserve inode identity even when their lexical names differ.
        if source_chain[-1].identity[:2] in {w.identity[:2] for w in other} or (
            other[-1].identity[:2] in {w.identity[:2] for w in source_chain}
        ):
            raise refuse("filesystem alias overlaps source placement")
    return PosixSource(
        source, runtime, ide, tuple(dict.fromkeys((*source_chain, *runtime_chain, *ide_chain)))
    )


async def prepare_source(source: Path, runtime: Path, ide: Path) -> PosixSource:
    """No writes. Validate all three existing placements before any source inspection."""
    return await offload(lambda: _placement(source, runtime, ide))


def recheck_source(source: PosixSource) -> None:
    if _placement(source.root, source.runtime, source.ide) != source:
        raise refuse("placement changed since validation")


def recheck_tree(source: PosixSource, tree: Path) -> None:
    """A disposable test tree is a strict descendant of the validated runtime."""
    recheck_source(source)
    absolute(tree)
    if not tree.is_relative_to(source.runtime) or tree in {source.runtime, source.ide}:
        raise refuse("disposable tree must be inside the validated runtime")
    _placement(source.root, tree, source.ide)


@dataclass(frozen=True)
class FileEntry:
    path: str
    identity: tuple[int, ...] | None
    sha256: str | None
    size: int
    mode: int


def entry(root: Path, name: str, deadline: float, *, missing: bool = False) -> FileEntry:
    relative(name)
    try:
        data, stamp = read_file(root / name, deadline)
    except FileNotFoundError:
        if not missing:
            raise refuse("untracked input disappeared") from None
        return FileEntry(name, None, None, 0, 0)
    return FileEntry(name, stamp, hashlib.sha256(data).hexdigest(), len(data), stamp[2] & 0o777)


def metadata(root: Path, deadline: float) -> tuple[FileEntry, ...]:
    """Witness Git identities; hash only HEAD/index, without reading object storage.

    Entry counts and deadlines bound the walk. Working-file byte budgets do not apply to
    Git storage. Tokens retain inode, mode, owner, link count, size, mtime and ctime checks.
    """
    forbidden = {
        "commondir",
        "gitdir",
        "worktrees",
        "modules",
        "shallow",
        "info/grafts",
        "objects/info/alternates",
        "objects/info/http-alternates",
        "refs/replace",
    }
    found: list[FileEntry] = []
    pending = [root / ".git"]
    count = 0
    while pending:
        path = pending.pop()
        with directory(path) as fd:
            secure(os.fstat(fd))
            found.append(
                FileEntry(
                    str(path.relative_to(root / ".git")) if path != root / ".git" else "",
                    token(os.fstat(fd)),
                    None,
                    0,
                    0o700,
                )
            )
            for name in directory_names(fd, deadline):
                remaining(deadline)
                count += 1
                rel = str((path / name).relative_to(root / ".git"))
                relative(rel)
                if count > MAX_PATHS or rel in forbidden or name.endswith(".lock"):
                    raise refuse("unsafe or excessive Git metadata")
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    pending.append(path / name)
                else:
                    secure(info)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise refuse("nonregular or multiple-link Git metadata")
                    record = (
                        entry(root / ".git", rel, deadline)
                        if rel in {"HEAD", "index"}
                        else FileEntry(rel, token(info), None, info.st_size, info.st_mode & 0o777)
                    )
                    if record.identity != token(info):
                        raise refuse("Git metadata changed during read")
                    found.append(record)
    if not {"HEAD", "index", "config"}.issubset({e.path for e in found}):
        raise refuse("incomplete Git metadata")
    return tuple(sorted(found, key=lambda e: e.path))


def selected(path: str) -> bool:
    parts = relative(path)
    if ".gitmodules" in parts:
        raise refuse("submodule metadata")
    return not (
        is_secret_path(path, nt=False)
        or any(p in EXCLUDED or p.endswith((".egg-info", ".dist-info")) for p in parts)
        or path.endswith((".pyc", ".pyo"))
    )


def records(text: str) -> tuple[str, ...]:
    if text and (not text.endswith("\0") or "\ufffd" in text):
        raise refuse("malformed or non-UTF-8 Git output")
    rows = tuple(text[:-1].split("\0")) if text else ()
    if any(not row for row in rows) or len(rows) > MAX_PATHS:
        raise refuse("empty or excessive Git records")
    return rows


def repository_layout(source: PosixSource, deadline: float) -> None:
    recheck_source(source)
    for parent in source.root.parents:
        marker = parent / ".git"
        if os.path.lexists(marker):
            # An empty plain directory has no Git metadata and is not a repository.
            # Some managed filesystems reserve such directories against git discovery.
            with directory(marker) as fd:
                if directory_names(fd, deadline):
                    raise refuse("source is nested inside another repository")
    # Scan eligible directories, including ignored ones: a nested repository with no
    # selected files must not silently become an ordinary input after a later edit.
    pending = [source.root]
    count = 0
    while pending:
        path = pending.pop()
        with directory(path) as fd:
            for name in directory_names(fd, deadline):
                remaining(deadline)
                count += 1
                if count > MAX_PATHS:
                    raise refuse("working input entry budget exceeded")
                if name in {".git", ".gitmodules"}:
                    if path != source.root or name != ".git":
                        raise refuse("nested repository or submodule metadata")
                    continue
                rel = str((path / name).relative_to(source.root))
                if selected(rel):
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(path / name)
                    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise refuse("link, special or multiple-link working input")


@dataclass(frozen=True)
class Inventory:
    source: PosixSource
    head: str
    paths: tuple[str, ...]
    tracked: frozenset[str]
    metadata: tuple[FileEntry, ...]
    deadline: float


@dataclass(frozen=True)
class _GitQuery:
    source: PosixSource
    git: str
    spawn: Spawn
    deadline: float

    @property
    def env(self) -> dict[str, str]:
        return {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_DIR": str(self.source.root / ".git"),
            "GIT_WORK_TREE": str(self.source.root),
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_ATTR_NOSYSTEM": "1",
        }

    async def __call__(self, *args: str) -> str:
        flags = [
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.untrackedCache=false",
            "-c",
            "core.sparseCheckout=false",
        ]
        result = await self.run(
            [self.git, *flags, *args],
            cwd=self.source.runtime,
            env=self.env,
            timeout_s=remaining(self.deadline),
            output_cap=MAX_GIT,
        )
        return result.stdout

    async def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout_s: float,
        env: Mapping[str, str] | None = None,
        output_cap: int = MAX_GIT,
    ) -> ProcessResult:
        await offload(lambda: recheck_source(self.source))
        result = await self.spawn(
            argv,
            cwd=cwd,
            env=env,
            timeout_s=min(timeout_s, remaining(self.deadline)),
            output_cap=output_cap,
        )
        remaining(self.deadline)
        if (
            result.returncode != 0
            or result.timed_out
            or result.truncated
            or (len((result.stdout + result.stderr).encode()) > output_cap)
        ):
            raise refuse("failed, timed-out or truncated Git query")
        return result


async def _check_config(query: _GitQuery) -> None:
    await check_source_config(
        query.source.root / ".git",
        git=query.git,
        spawn=query.run,
        env=query.env,
        time_left=lambda: remaining(query.deadline),
        refuse=refuse,
    )
    # Git's own parser, without includes, precedes any repository inspection. Only
    # ordinary local-repository core defaults and the SHA-256 object extension pass.
    config = records(
        await query(
            "config",
            "--file",
            str(query.source.root / ".git/config"),
            "--no-includes",
            "--null",
            "--list",
        )
    )
    allowed_core = {
        "repositoryformatversion",
        "filemode",
        "bare",
        "logallrefupdates",
        "ignorecase",
        "precomposeunicode",
        "autocrlf",
        "eol",
        "protecthfs",
        "protectntfs",
        "quotepath",
    }
    for row in config:
        key, _, value = row.partition("\n")
        key = key.lower()
        if (
            "." not in key
            or (key.startswith("core.") and key[5:] not in allowed_core)
            or (
                key.startswith("extensions.")
                and (key, value) != ("extensions.objectformat", "sha256")
            )
            or key.startswith(("include.", "includeif."))
            or (key == "core.bare" and value != "false")
        ):
            raise refuse("unsafe Git configuration")


def _tracked(text: str) -> frozenset[str]:
    tracked: set[str] = set()
    seen: set[str] = set()
    for row in records(text):
        match = re.fullmatch(r"(100644|100755) ([0-9a-f]{40}|[0-9a-f]{64}) 0\t(.+)", row)
        if match is None:
            raise refuse("malformed index, conflict, link or gitlink")
        path = match[3]
        if path in seen:
            raise refuse("duplicate index record")
        seen.add(path)
        if selected(path):
            tracked.add(path)
    return frozenset(tracked)


async def inventory(
    source: PosixSource, *, git: str, spawn: Spawn = run_process, deadline: float | None = None
) -> Inventory:
    """Read-only Git queries under a single ten-second deadline and bounded outputs."""
    until = (
        min(deadline, time.monotonic() + PHASE_SECONDS)
        if deadline is not None
        else (time.monotonic() + PHASE_SECONDS)
    )
    absolute(Path(git))
    await offload(lambda: repository_layout(source, until))
    before = await offload(lambda: metadata(source.root, until))
    query = _GitQuery(source, git, spawn, until)
    await _check_config(query)
    if await offload(lambda: metadata(source.root, until)) != before:
        raise refuse("Git metadata changed before inspection")
    head = await query("rev-parse", "--verify", "HEAD")
    if not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\n", head):
        raise refuse("malformed source HEAD")
    tracked = _tracked(await query("ls-files", "-s", "-z"))
    rows = records(await query("ls-files", "-z", "--cached", "--others", "--exclude-standard"))
    if len(rows) != len(set(rows)):
        raise refuse("duplicate selected Git record")
    paths = {p for p in rows if selected(p)}
    if not tracked.issubset(paths):
        raise refuse("incomplete selected Git output")
    if await offload(lambda: metadata(source.root, until)) != before:
        raise refuse("Git metadata changed during inventory")
    return Inventory(source, head.strip(), tuple(sorted(paths)), tracked, before, until)


@dataclass(frozen=True)
class Selection:
    inventory: Inventory
    entries: tuple[FileEntry, ...]
    directories: tuple[Witness, ...]
    total_bytes: int


def _validate(listing: Inventory) -> Selection:
    source, until = listing.source, listing.deadline
    recheck_source(source)
    if metadata(source.root, until) != listing.metadata:
        raise refuse("Git metadata changed before validation")
    if any(e.path == "info/attributes" and e.size for e in listing.metadata):
        raise refuse("source info attributes are unsupported")
    directories: dict[Path, tuple[int, ...]] = {}
    entries: list[FileEntry] = []
    total = 0
    for name in listing.paths:
        if not selected(name):
            raise refuse("excluded inventory handoff")
        path = source.root / name
        # Only existing input ancestors get witnessed. Missing tracked parents denote
        # working deletions; an untracked disappearance always refuses in entry().
        current = source.root
        for part in ("", *relative(name)[:-1]):
            current = current / part if part else current
            try:
                with directory(current):
                    directories[current] = token(current.lstat())
            except FileNotFoundError:
                if name not in listing.tracked:
                    raise refuse("untracked parent disappeared") from None
                break
        item = entry(source.root, name, until, missing=name in listing.tracked)
        if item.sha256 is not None and Path(name).name == ".gitattributes":
            _attributes(read_file(source.root / name, until)[0])
        total += item.size
        if total > MAX_TOTAL or len(directories) + len(entries) >= MAX_PATHS:
            raise refuse("selected paths or bytes exceed budget")
        entries.append(item)
    for path, stamp in directories.items():
        with directory(path):
            if token(path.lstat()) != stamp:
                raise refuse("source directory changed")
    if metadata(source.root, until) != listing.metadata:
        raise refuse("Git metadata changed during validation")
    return Selection(
        listing, tuple(entries), tuple(Witness(p, t) for p, t in sorted(directories.items())), total
    )


def _attributes(data: bytes) -> None:
    if b"\0" in data:
        raise refuse("NUL in source attributes")
    for line in data.removeprefix(b"\xef\xbb\xbf").split(b"\n"):
        line = line.strip(b" \t\r")
        if not line or line.startswith(b"#"):
            continue
        fields = re.split(rb"[ \t\r]+", line)
        if (
            line.startswith(b'"')
            or fields[0].startswith(b"[attr]")
            or any(
                f.lstrip(b"-!").split(b"=", 1)[0] in {b"filter", b"ident", b"working-tree-encoding"}
                for f in fields[1:]
            )
        ):
            raise refuse("unsupported source attribute transform")


async def validate_selection(listing: Inventory) -> Selection:
    return await offload(lambda: _validate(listing))


@dataclass(frozen=True)
class StagedSource:
    selection: Selection
    root: Path
    tree: Path
    manifest: Path
    owned: tuple[Witness, ...]
    files: tuple[FileEntry, ...]


def manifest_bytes(selection: Selection) -> bytes:
    return json.dumps(
        {
            "head": selection.inventory.head,
            "entries": [[e.path, e.sha256, e.size, e.mode] for e in selection.entries],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _create(path: Path, owned: list[Witness], *, data: bytes | None = None, mode: int = 0o600):
    with directory(path.parent) as fd:
        if data is None:
            os.mkdir(path.name, mode=0o700, dir_fd=fd)
            owned.append(
                Witness(path, identity(os.stat(path.name, dir_fd=fd, follow_symlinks=False)))
            )
        else:
            child = os.open(
                path.name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                mode,
                dir_fd=fd,
            )
            try:
                owned.append(Witness(path, identity(os.fstat(child))))
                view = memoryview(data)
                while view:
                    count = os.write(child, view)
                    if count <= 0:
                        raise refuse("short staging write")
                    view = view[count:]
                os.fchmod(child, mode)
                owned[-1] = Witness(path, identity(os.fstat(child)))
            finally:
                os.close(child)


def _copy(selection: Selection, root: Path, owned: list[Witness], deadline: float) -> StagedSource:
    _create(root, owned)
    tree, manifest = root / "tree", root / "manifest"
    _create(tree, owned)
    dirs: set[str] = set()
    for item in selection.entries:
        remaining(deadline)
        if item.sha256 is None:
            continue
        data, stamp = read_file(selection.inventory.source.root / item.path, deadline)
        if stamp != item.identity or hashlib.sha256(data).hexdigest() != item.sha256:
            raise refuse("selected file changed during COPY")
        parts = relative(item.path)
        for i in range(1, len(parts)):
            name = "/".join(parts[:i])
            if name not in dirs:
                _create(tree / name, owned)
                dirs.add(name)
        _create(tree / item.path, owned, data=data, mode=0o700 if item.mode & 0o111 else 0o600)
    _create(manifest, owned, data=manifest_bytes(selection))
    files = tuple(entry(tree, e.path, deadline) for e in selection.entries if e.sha256 is not None)
    return StagedSource(selection, root, tree, manifest, tuple(owned), files)


def _owned(owned: tuple[Witness, ...], deadline: float, *, missing: bool) -> None:
    paths = {w.path for w in owned}
    for witness in owned:
        remaining(deadline)
        try:
            with directory(witness.path.parent) as fd:
                info = os.stat(witness.path.name, dir_fd=fd, follow_symlinks=False)
                if identity(info) != witness.identity:
                    raise refuse("owned staging identity changed; preserve for recovery")
                if stat.S_ISDIR(info.st_mode):
                    with directory(witness.path) as child:
                        if any(
                            witness.path / n not in paths for n in directory_names(child, deadline)
                        ):
                            raise refuse("unexpected staging entry; preserve for recovery")
        except FileNotFoundError:
            if not missing:
                raise refuse("missing staging entry") from None


def _cleanup(owned: tuple[Witness, ...]) -> None:
    deadline = time.monotonic() + PHASE_SECONDS
    _owned(owned, deadline, missing=True)  # Preflight every identity before any deletion.
    for witness in reversed(owned):
        remaining(deadline)
        try:
            with directory(witness.path.parent) as fd:
                info = os.stat(witness.path.name, dir_fd=fd, follow_symlinks=False)
                if identity(info) != witness.identity:
                    raise refuse("staging replacement during cleanup; preserve for recovery")
                if stat.S_ISDIR(info.st_mode):
                    os.rmdir(witness.path.name, dir_fd=fd)
                else:
                    os.unlink(witness.path.name, dir_fd=fd)
        except FileNotFoundError:
            continue


async def cleanup_stage(stage: StagedSource) -> None:
    """Delete exact recorded identities only; substituted or unexpected entries refuse."""

    def cleanup() -> None:
        _stage_layout(stage)
        _cleanup(stage.owned)

    await offload(cleanup)


def _stage_layout(stage: StagedSource) -> None:
    if (
        stage.root.parent != stage.selection.inventory.source.runtime
        or not re.fullmatch(r"source-stage-[0-9a-f]{32}", stage.root.name)
        or stage.tree != stage.root / "tree"
        or stage.manifest != stage.root / "manifest"
    ):
        raise refuse("unowned staging handoff")
    if (
        not stage.owned
        or stage.owned[0].path != stage.root
        or (len({w.path for w in stage.owned}) != len(stage.owned))
        or any(not w.path.is_relative_to(stage.root) for w in stage.owned)
    ):
        raise refuse("invalid staging ownership manifest")


def _verify(stage: StagedSource, deadline: float) -> None:
    recheck_source(stage.selection.inventory.source)
    _stage_layout(stage)
    _owned(stage.owned, deadline, missing=False)
    data, _ = read_file(stage.manifest, deadline)
    if data != manifest_bytes(stage.selection):
        raise refuse("staging manifest changed")
    expected = [(e.path, e.sha256, e.size) for e in stage.selection.entries if e.sha256 is not None]
    actual = tuple(entry(stage.tree, e.path, deadline) for e in stage.files)
    if actual != stage.files or [(e.path, e.sha256, e.size) for e in actual] != expected:
        raise refuse("staging bytes or identity changed")


async def verify_stage(stage: StagedSource, *, deadline: float | None = None) -> None:
    """Independently verify membership, inode identities, hashes and manifest before use."""
    until = (
        min(deadline, time.monotonic() + PHASE_SECONDS)
        if deadline is not None
        else (time.monotonic() + PHASE_SECONDS)
    )
    await offload(lambda: _verify(stage, until))


async def stage_source(
    selection: Selection, *, git: str, spawn: Spawn = run_process
) -> StagedSource:
    """Fresh staging only; recheck source selection before/after, verify before return.

    Cancellation joins filesystem work before identity-scoped cleanup. Failed cleanup raises
    and retains the remaining stage for caller recovery; it never recursively deletes a tree.
    The caller owns the successful handoff and must verify again immediately before use.
    """
    until = time.monotonic() + PHASE_SECONDS
    listing = replace(selection.inventory, deadline=until)
    expected = replace(selection, inventory=listing)
    root = listing.source.runtime / ("source-stage-" + uuid4().hex)
    owned: list[Witness] = []
    try:
        if await validate_selection(listing) != expected:
            raise refuse("selection changed before COPY")
        again = await inventory(listing.source, git=git, spawn=spawn, deadline=until)
        if replace(again, deadline=until) != listing:
            raise refuse("Git selection changed before COPY")
        stage = await offload(lambda: _copy(expected, root, owned, until))
        if await validate_selection(listing) != expected:
            raise refuse("source changed during COPY")
        again = await inventory(listing.source, git=git, spawn=spawn, deadline=until)
        if replace(again, deadline=until) != listing:
            raise refuse("Git selection changed during COPY")
        await verify_stage(stage, deadline=until)
        return stage
    except BaseException:
        await offload(lambda: _cleanup(tuple(owned)))
        raise
