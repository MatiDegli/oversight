"""Suite metadata snapshots, verified restitution and session-long authority suspension."""

import configparser
import os
import re
import stat
import sys
import time
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from oversight.workspace.integrity import GitIdentity, IntegrityError
from oversight.workspace.metadata_io import (
    Budget,
    Entry,
    collect,
    directory,
    make_directory,
    nt_guard,
    remove_directory,
    replace_file,
)

GUARD_SECONDS = 10.0


@dataclass
class IntegrityState:
    """Monotonic latches owned by Workspace; only a new server session clears them.

    C3 can consult rules_suspended without disabling the isolated runner. Uncertainty also
    blocks every server Git, including approval recovery: it must never guess or retry an effect.
    """

    rules_suspended: bool = False
    main_suspect: bool = False
    reason: str | None = None

    def suspend(self, error: IntegrityError, *, uncertain: bool) -> None:
        self.rules_suspended = True
        self.main_suspect |= uncertain
        self.reason = str(error)

    def before_git(self) -> None:
        if self.main_suspect:
            raise IntegrityError(
                "Git metadata is unverifiable until restart", ".git", code="main_suspect"
            )


def _anchor(path: Path, deadline: float, *, file: bool = False) -> tuple[int, int]:
    if sys.platform == "win32":
        info = nt_guard(path, deadline)
        if not (
            stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            if file
            else stat.S_ISDIR(info.st_mode)
        ):
            raise IntegrityError("invalid Git snapshot anchor", str(path))
        return info.st_dev, info.st_ino
    with directory(path, deadline) as fd:
        assert isinstance(fd, int)
        info = os.fstat(fd)
        return info.st_dev, info.st_ino


def _unquote(value: str) -> str:
    """Git config quoting: unescape quotes/backslashes, retaining lone backslashes literally."""
    result: list[str] = []
    quoted = False
    escaped = False
    escapes = {"n": "\n", "t": "\t", "b": "\b", '"': '"', "\\": "\\"}
    for char in value:
        if escaped:
            result.append(escapes.get(char, "\\" + char))
            escaped = False
        elif char == '"':
            quoted = not quoted
        elif quoted and char == "\\":
            escaped = True
        else:
            result.append(char)
    if quoted or escaped:
        raise ValueError("unterminated Git config quote")
    return "".join(result)


def _hooks_paths(config: bytes, root: Path, *, runtime: Path | None = None) -> tuple[Path, ...]:
    """Read only simple local Git config, never ask Git to expand an include or a path.

    Server-created configs have no includes. Unknown/include syntax cannot be snapshotted
    completely, so it fails closed before executing the suite rather than guessing its effects.
    """
    parser = configparser.RawConfigParser(strict=False)
    parser.read_string(config.decode("utf-8"))
    paths: list[Path] = []
    for section in parser.sections():
        if section.lower().startswith(("include", "includeif")):
            raise IntegrityError("Git config includes cannot be snapshotted", "config")
        if section.lower() != "core":
            continue
        for key, value in parser.items(section):
            if key.lower() != "hookspath":
                continue
            value = value.strip()
            if '"' in value:
                value = _unquote(value)
            if not value or value.startswith("~") or "\0" in value:
                raise IntegrityError("unsupported Git hooksPath", "config")
            if value == "/dev/null":
                continue
            if sys.platform == "win32":
                if (
                    runtime is None
                    or value.endswith(("/", "\\"))
                    or any(part in {".", ".."} for part in value.replace("\\", "/").split("/"))
                    or PureWindowsPath(value) != PureWindowsPath(runtime / "server.lock/hooks")
                ):
                    raise IntegrityError("unsupported NT Git hooksPath", "config")
                # Git's exact pinned child is below a held-open regular file. Never probe it.
                continue
            path = Path(os.path.abspath(root / value))
            if path != Path("/dev/null"):
                paths.append(path)
    return tuple(paths)


def _is_ref(path: Path, common: Path) -> bool:
    name = path.name
    if sys.platform == "win32":
        name = name.upper()
        if name == "PACKED-REFS":
            name = "packed-refs"
    return name in {
        "HEAD",
        "ORIG_HEAD",
        "MERGE_HEAD",
        "AUTO_MERGE",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "BISECT_HEAD",
        "packed-refs",
    } or path.is_relative_to(common / "refs")


def _sanitize_ref(path: Path, entry: Entry | None, common: Path) -> None:
    if path.is_relative_to(common / "refs") and not _ref_name(
        os.fsencode(path.relative_to(common))
    ):
        raise IntegrityError("unsafe ref name for restoration", str(path))
    if entry is None:
        return
    data = entry.data
    if data is None:
        raise IntegrityError("non-file ref for restoration", str(path))
    if (path.name.casefold() if sys.platform == "win32" else path.name) == "packed-refs":
        valid = all(
            row.startswith(b"#")
            or re.fullmatch(rb"\^[0-9a-f]{40}(?:[0-9a-f]{24})?", row)
            or (
                re.fullmatch(rb"[0-9a-f]{40}(?:[0-9a-f]{24})? refs/[A-Za-z0-9_./-]+", row)
                and _ref_name(row.split(b" ", 1)[1])
            )
            for row in data.splitlines()
        )
    else:
        valid = bool(re.fullmatch(rb"[0-9a-f]{40}(?:[0-9a-f]{24})?\n", data)) or (
            data.startswith(b"ref: ") and data.endswith(b"\n") and _ref_name(data[5:-1])
        )
    if not valid:
        raise IntegrityError("unsafe ref contents for restoration", str(path))


def _ref_name(name: bytes) -> bool:
    parts = name.split(b"/")
    return (
        len(parts) >= 2
        and parts[0] == b"refs"
        and all(
            re.fullmatch(rb"[A-Za-z0-9_][A-Za-z0-9_.-]*", part)
            and b".." not in part
            and not part.endswith((b".lock", b"."))
            for part in parts
        )
    )


@dataclass(frozen=True)
class MetadataSnapshot:
    identity: GitIdentity
    roots: tuple[Path, ...]
    anchors: dict[Path, tuple[int, int]]
    entries: dict[Path, Entry]
    skip: frozenset[Path]
    sentinel: Path | None = None

    @classmethod
    def take(cls, identity: GitIdentity, deadline: float) -> "MetadataSnapshot":
        common = identity.common
        # Capture all admin directories as well as main's own real index/merge state. The
        # object database is content-addressed data, except its executable traversal inputs.
        roots = [common, common / "objects/info"]
        if identity.root / ".git" != common:
            roots.append(identity.root / ".git")
        skip = frozenset({common / "objects"})
        entries: dict[Path, Entry] = {}
        budget = Budget(deadline)
        for path in roots:
            collect(path, budget, entries, skip=skip)
        for path, entry in tuple(entries.items()):
            name = path.name.upper() if sys.platform == "win32" else path.name
            configs = (
                {"CONFIG", "CONFIG.WORKTREE"}
                if sys.platform == "win32"
                else {"config", "config.worktree"}
            )
            if name in configs and entry.data is not None:
                for root in {identity.root, common.parent}:
                    for hooks in _hooks_paths(entry.data, root, runtime=common.parent.parent):
                        if hooks not in roots and not hooks.is_relative_to(common):
                            roots.append(hooks)
                            collect(hooks, budget, entries, skip=skip)
        protected = {common, identity.directory, identity.root, common / "objects"}
        sentinel: Path | None = None
        anchors = {path: _anchor(path, deadline) for path in protected}
        if sys.platform == "win32":
            sentinel = common.parent.parent / "server.lock"
            anchors[sentinel] = _anchor(sentinel, deadline, file=True)
        snapshot = cls(identity, tuple(roots), anchors, entries, skip, sentinel)
        snapshot._no_locks(entries)
        return snapshot

    def _no_locks(self, entries: dict[Path, Entry]) -> None:
        for path in entries:
            name = path.name.casefold() if sys.platform == "win32" else path.name
            if name.endswith(".lock"):
                raise IntegrityError("unknown Git metadata lock", str(path))

    def _read(self, deadline: float) -> dict[Path, Entry]:
        for path, token in self.anchors.items():
            if _anchor(path, deadline, file=path == self.sentinel) != token:
                raise IntegrityError("Git admin directory identity changed", str(path))
        entries: dict[Path, Entry] = {}
        budget = Budget(deadline)
        for root in self.roots:
            collect(root, budget, entries, skip=self.skip)
        self._no_locks(entries)
        return entries

    def restore(self, observed: dict[Path, Entry], deadline: float) -> None:
        """Restore once, then reread everything. No retry, reset or lock theft."""
        for path in sorted(self.entries, key=lambda p: len(p.parts)):
            wanted = self.entries[path]
            actual = observed.get(path)
            if wanted.data is None and actual != wanted:
                if actual is not None:
                    raise IntegrityError("Git metadata directory was replaced", str(path))
                make_directory(path, wanted.mode, deadline)
        for path in sorted(self.entries.keys() | observed.keys()):
            wanted, actual = self.entries.get(path), observed.get(path)
            if wanted == actual or (wanted is not None and wanted.data is None):
                continue
            if actual is not None and actual.data is None:
                if wanted is not None:
                    raise IntegrityError("Git metadata file was replaced by directory", str(path))
                continue
            cas = _is_ref(path, self.identity.common)
            if cas:
                _sanitize_ref(path, wanted, self.identity.common)
            name = path.name.casefold() if sys.platform == "win32" else path.name
            replace_file(path, actual, wanted, deadline, cas=cas or name == "index")
        for path in sorted(observed, key=lambda p: len(p.parts), reverse=True):
            if path not in self.entries and observed[path].data is None:
                remove_directory(path, deadline)
        if self._read(deadline) != self.entries:
            raise IntegrityError("Git metadata restoration did not verify", ".git")

    def finish(self, state: IntegrityState, deadline: float) -> None:
        """Runs only after the runner's namespace cleanup, still inside the shared lease."""
        try:
            observed = self._read(deadline)
            if observed == self.entries:
                return
            error = IntegrityError(
                "suite changed Git metadata; rules suspended until restart", ".git"
            )
            state.suspend(error, uncertain=False)
            self.restore(observed, deadline)
        except (OSError, IntegrityError) as cause:
            error = IntegrityError(
                "Git metadata restoration is unverifiable", ".git", code="main_suspect"
            )
            state.suspend(error, uncertain=True)
            raise error from cause
        raise error


def take_snapshot(
    identity: GitIdentity, state: IntegrityState, timeout_s: float
) -> MetadataSnapshot:
    state.before_git()
    try:
        return MetadataSnapshot.take(identity, time.monotonic() + min(timeout_s, GUARD_SECONDS))
    except (OSError, IntegrityError, ValueError, configparser.Error) as cause:
        error = IntegrityError("Git metadata snapshot is unverifiable", ".git", code="main_suspect")
        state.suspend(error, uncertain=True)
        raise error from cause


def finish_snapshot(snapshot: MetadataSnapshot, state: IntegrityState) -> None:
    snapshot.finish(state, time.monotonic() + GUARD_SECONDS)
