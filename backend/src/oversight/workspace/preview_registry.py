"""Durable private ownership, outside reset's workspace/worktrees/tmp directories.

Only exact boot/birth/namespace identities authorize reclamation. Ambiguity quarantines;
unknown listeners are never process identities. JSON is data, never executable restoration.
"""

import asyncio
import base64
import contextlib
import ctypes
import json
import os
import re
import signal
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from oversight.workspace.files import valid_agent_id
from oversight.workspace.integrity import GitIdentity
from oversight.workspace.isolation import open_pidfd, verify_preview_namespace_death
from oversight.workspace.metadata_io import MAX_TOTAL, Entry
from oversight.workspace.preview_types import PreviewError
from oversight.workspace.snapshot import MetadataSnapshot


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    birth: str
    boot: str

    @classmethod
    def read(cls, pid: int) -> "ProcessIdentity":
        if pid <= 1:
            raise ValueError("invalid process identity")
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return cls(pid, fields[19], Path("/proc/sys/kernel/random/boot_id").read_text().strip())

    def alive(self) -> bool:
        try:
            actual = self.read(self.pid)
        except (FileNotFoundError, ProcessLookupError):
            return False
        if actual != self:
            raise PreviewError("Live preview ownership is ambiguous; restart is required.")
        try:
            state = Path(f"/proc/{self.pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):
            return False
        return state not in {"Z", "X"}


def pack_snapshot(snapshot: MetadataSnapshot) -> dict[str, Any]:
    return {
        "identity": [
            str(snapshot.identity.root),
            str(snapshot.identity.directory),
            str(snapshot.identity.common),
        ],
        "roots": list(map(str, snapshot.roots)),
        "anchors": {str(path): list(token) for path, token in snapshot.anchors.items()},
        "entries": {
            str(path): [
                entry.mode,
                None if entry.data is None else base64.b64encode(entry.data).decode("ascii"),
            ]
            for path, entry in snapshot.entries.items()
        },
        "skip": list(map(str, snapshot.skip)),
    }


def unpack_snapshot(data: dict[str, Any], runtime: Path, worktree: Path) -> MetadataSnapshot:
    common = runtime / "workspace/.git"
    expected = GitIdentity(worktree, common / "worktrees" / worktree.name, common)
    if data["identity"] != [str(expected.root), str(expected.directory), str(common)]:
        raise ValueError("snapshot identity mismatch")
    roots = [str(common), str(common / "objects/info"), str(worktree / ".git")]
    anchors = {str(path) for path in (common, expected.directory, worktree, common / "objects")}
    required = {
        str(path)
        for path in (
            common,
            common / "config",
            common / "HEAD",
            expected.directory / "HEAD",
            worktree / ".git",
        )
    }
    if (
        data["roots"][:3] != roots
        or set(data["anchors"]) != anchors
        or data["skip"] != [str(common / "objects")]
        or not required <= data["entries"].keys()
    ):
        raise ValueError("incomplete metadata snapshot")
    if any(
        len(token) != 2 or any(type(value) is not int or value < 0 for value in token)
        for token in data["anchors"].values()
    ):
        raise ValueError("invalid snapshot anchors")
    paths = [*data["roots"], *data["anchors"], *data["entries"], *data["skip"]]
    if any(not Path(p).is_absolute() or ".." in Path(p).parts for p in paths):
        raise ValueError("noncanonical snapshot path")
    return MetadataSnapshot(
        expected,
        tuple(map(Path, data["roots"])),
        {Path(p): tuple(token) for p, token in data["anchors"].items()},
        {
            Path(p): Entry(mode, None if raw is None else base64.b64decode(raw, validate=True))
            for p, (mode, raw) in data["entries"].items()
        },
        frozenset(map(Path, data["skip"])),
    )


class PreviewRegistry:
    def __init__(self, runtime: Path) -> None:
        self.runtime = runtime
        self.directory = runtime / "preview-owner"
        self.path = self.directory / "owner.json"
        self.pending = self.directory / "owner.pending"

    def prepare(self) -> None:
        try:
            self.directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        else:
            fd = os.open(self.runtime, os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
            raise ValueError("unsafe registry directory")

    def read(self) -> dict[str, Any] | None:
        if not os.path.lexists(self.directory):
            return None
        self.prepare()
        if os.path.lexists(self.pending):
            raise ValueError("partial ownership write")
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_mode & 0o077
                or info.st_uid != os.getuid()
                or info.st_size > MAX_TOTAL * 2
            ):
                raise ValueError("unsafe registry file")
            data: dict[str, Any] = json.load(stream)
        if data["version"] != 1 or data["runtime"] != str(self.runtime):
            raise ValueError("ownership runtime mismatch")
        return data

    def write(self, data: dict[str, Any]) -> None:
        self.prepare()
        payload = json.dumps(data, separators=(",", ":"), allow_nan=False).encode()
        if len(payload) > MAX_TOTAL * 2:
            raise ValueError("ownership budget exceeded")
        fd = os.open(self.pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(self.pending, self.path)
        self._sync()

    def update(self, **updates: Any) -> None:
        data = self.read()
        if data is None:
            raise ValueError("missing ownership intent")
        self.write(data | updates)

    def clear(self) -> None:
        if os.path.lexists(self.pending):
            raise ValueError("partial ownership write")
        self.path.unlink()
        self._sync()

    def _sync(self) -> None:
        fd = os.open(self.directory, os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def identity_data(pid: int) -> dict[str, Any]:
    return asdict(ProcessIdentity.read(pid))


def registered_identity(raw: dict[str, Any]) -> ProcessIdentity:
    if set(raw) != {"pid", "birth", "boot"} or type(raw["pid"]) is not int:
        raise ValueError("invalid ownership identity")
    return ProcessIdentity(**raw)


def validate_ownership(
    data: dict[str, Any],
    runtime: Path,
    fingerprint: str,
) -> MetadataSnapshot:
    """Validate all recovery authority before signaling anything or restoring metadata."""
    required = {
        "version",
        "runtime",
        "profile",
        "preview_id",
        "agent_id",
        "revision",
        "head_commit",
        "launch_status",
        "worktree",
        "port",
        "phase",
        "snapshot",
        "launch",
    }
    if not required <= data.keys() or data["profile"] != fingerprint:
        raise ValueError("incomplete ownership profile")
    if (
        data["version"] != 1
        or data["runtime"] != str(runtime)
        or not valid_agent_id(data["agent_id"])
        or re.fullmatch(r"[0-9a-f]{32}", data["preview_id"]) is None
        or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", data["head_commit"]) is None
        or type(data["revision"]) is not int
        or data["revision"] < 1
        or data["launch_status"] != ""
        or type(data["port"]) is not int
        or not 1 <= data["port"] <= 65535
    ):
        raise ValueError("invalid ownership candidate")
    worktree = Path(data["worktree"])
    if worktree != runtime / "worktrees" / data["agent_id"]:
        raise ValueError("ownership worktree mismatch")
    phases = {
        "intent": (),
        "owned": ("supervisor",),
        "entering": ("supervisor", "unshare"),
        "confirmed": ("supervisor", "unshare", "namespace", "namespaces"),
        "executing": ("supervisor", "unshare", "namespace", "namespaces"),
        "dead": (),
    }
    phase = data["phase"]
    if phase not in phases or any(not data.get(key) for key in phases[phase]):
        raise ValueError("invalid ownership phase")
    identities = [
        registered_identity(data[key])
        for key in ("supervisor", "unshare", "namespace")
        if data.get(key)
    ]
    if len(set(identity.pid for identity in identities)) != len(identities):
        raise ValueError("ambiguous ownership births")
    if phase == "intent" and identities:
        raise ValueError("unexpected intent ownership")
    if data.get("namespace") and set(data.get("namespaces", {})) != {"net", "pid", "mnt"}:
        raise ValueError("incomplete namespace ownership")
    return unpack_snapshot(data["snapshot"], runtime, worktree)


def kill_pinned(fd: int) -> None:
    """Linux pidfd_send_signal; some supported CPython builds omit the signal wrapper."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.syscall(424, fd, signal.SIGKILL, 0, 0) < 0:
        raise OSError(ctypes.get_errno(), "registered preview signaling failed")


def namespace_links(pid: int) -> dict[str, str]:
    return {name: os.readlink(f"/proc/{pid}/ns/{name}") for name in ("net", "pid", "mnt")}


async def reclaim_registered(data: dict[str, Any]) -> None:
    """Only registered births authorize a pidfd signal, never a PID/name/port search.

    A pinned confirmed namespace remains the death witness if the supervisor itself crashed.
    Incomplete identity refuses; a prior supervisor's cooperation is never required.
    """
    identities = {
        key: registered_identity(data[key])
        for key in ("supervisor", "unshare", "namespace")
        if data.get(key)
    }
    live = {key: identity for key, identity in identities.items() if identity.alive()}
    if not live:
        return
    if "unshare" in live and "namespace" not in identities:
        raise PreviewError("Live preview recovery ownership is incomplete.")
    namespace = identities.get("namespace")
    if namespace and namespace.alive():
        expected = data["namespaces"]
        if set(expected) != {"net", "pid", "mnt"} or namespace_links(namespace.pid) != expected:
            raise PreviewError("Live preview recovery ownership is ambiguous.")
    pinned: dict[str, int] = {}
    try:
        for key, identity in live.items():
            fd = open_pidfd(identity.pid)
            pinned[key] = fd
            if not identity.alive():
                continue
        # Birth checks after pinning prevent PID reuse between observation and signaling.
        for identity in live.values():
            identity.alive()
        await asyncio.sleep(0)
        for key in ("supervisor", "unshare", "namespace"):
            if key in pinned and live[key].alive():
                with contextlib.suppress(ProcessLookupError):
                    kill_pinned(pinned[key])
        # PID 1 death follows kernel teardown even for setsid descendants.
        if "namespace" in pinned:
            fd = pinned.pop("namespace")
            await verify_preview_namespace_death(fd)
        for key, fd in tuple(pinned.items()):
            pinned.pop(key)
            await verify_preview_namespace_death(fd)
    finally:
        for fd in pinned.values():
            os.close(fd)
