"""Independent reviewed-code Linux boundary; never selected by the running application.

The caller owns the handle until stop_verified() completes, even following natural exit.
Namespace and pidfds are borrowed witnesses, never passed to edited code. Production
preparation, publication, durable ownership and gateway handoff are separate obligations.
"""

import asyncio
import contextlib
import ctypes
import fcntl
import json
import math
import os
import secrets
import select
import signal
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, cast

from oversight.workspace.isolation import open_pidfd
from oversight.workspace.preview_boundary_entry import (
    SYSTEM_DIRECTORIES,
    SYSTEM_FILES,
    TOOL_TARGETS,
    manifest_digest,
)
from oversight.workspace.runner import ProcessCollector, child_env, wait_done

ENTRY = Path(__file__).with_name("preview_boundary_entry.py").resolve()
# A wheel lives under python/Lib/site-packages; exclude the actual installed payload.
# Keep this bottom-layer module independent of settings (which imports the workspace facade).
_payload_root = Path(os.environ.get("OVERSIGHT_APP_ROOT", Path(__file__).resolve().parents[4]))
if not _payload_root.is_absolute():
    raise ValueError("OVERSIGHT_APP_ROOT must be absolute")
REPO_ROOT = _payload_root.resolve()


class BoundaryUnavailable(OSError):
    """Required isolation, authenticated entry or verified death is unavailable; fail closed."""


@dataclass(frozen=True)
class ReadOnlyMount:
    source: Path
    target: str
    role: str
    digest: str


class RootInputs(Protocol):
    @property
    def tree(self) -> Path: ...
    @property
    def state(self) -> Path: ...
    @property
    def scratch(self) -> Path: ...
    @property
    def readonly(self) -> tuple[ReadOnlyMount, ...]: ...
    @property
    def exclusions(self) -> tuple[Path, ...]: ...


@dataclass(frozen=True)
class BoundaryConfig:
    argv: tuple[str, ...]
    cwd: str = "/T"
    env: tuple[tuple[str, str], ...] = ()
    launch_timeout_s: float = 10.0
    lifetime_s: float = 900.0
    cleanup_timeout_s: float = 5.0
    output_cap: int = 262_144


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start_time: int


@dataclass(frozen=True)
class NamespaceIdentity:
    device: int
    inode: int


class BoundaryHandle(Protocol):
    @property
    def processes(self) -> Mapping[str, ProcessIdentity]: ...
    @property
    def pidfds(self) -> Mapping[str, int]: ...
    @property
    def namespace_fds(self) -> Mapping[str, int]: ...
    @property
    def u0(self) -> NamespaceIdentity: ...
    @property
    def n0(self) -> NamespaceIdentity: ...
    @property
    def u1(self) -> NamespaceIdentity: ...
    @property
    def pid(self) -> NamespaceIdentity: ...
    @property
    def mount(self) -> NamespaceIdentity: ...
    @property
    def stdout(self) -> str: ...
    @property
    def stderr(self) -> str: ...
    @property
    def returncode(self) -> int | None: ...
    @property
    def verified_dead(self) -> bool: ...
    async def wait(self) -> int: ...
    async def stop_verified(self) -> None: ...


def _canonical(path: Path) -> Path:
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise BoundaryUnavailable("noncanonical boundary input")
    return path


def _overlap(left: Path, right: Path) -> bool:
    return left.is_relative_to(right) or right.is_relative_to(left)


def _environment(config: BoundaryConfig) -> dict[str, str]:
    # No ambient PATH, HOME, Python hooks, preload, provider credentials or WSL channels.
    allowed = {"LANG", "LC_ALL", "TZ", "PYTHONUNBUFFERED", "PYTHONDONTWRITEBYTECODE"}
    extra = dict(config.env)
    if len(extra) != len(config.env) or set(extra) - allowed:
        raise BoundaryUnavailable("unsupported boundary environment")
    env = {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "TZ": "UTC"} | extra
    return env | {
        "PATH": "/opt/python/bin:/opt/uv:/opt/node:/opt/pnpm:/usr/bin",
        "HOME": "/state/home",
        "TMPDIR": "/tmp",
        "XDG_CACHE_HOME": "/state/cache",
        "UV_OFFLINE": "1",
        "UV_NO_SYNC": "1",
        "UV_PROJECT_ENVIRONMENT": "/opt/deps",
        "PYTHONDONTWRITEBYTECODE": "1",
    }


def _validate(config: BoundaryConfig, roots: RootInputs) -> None:
    if sys.platform != "linux" or os.uname().machine != "x86_64":
        raise BoundaryUnavailable("reviewed boundary requires Linux x86-64")
    if not config.argv or len(config.argv) > 128 or any(not a or "\0" in a for a in config.argv):
        raise BoundaryUnavailable("invalid boundary argv")
    if (
        any(
            not math.isfinite(t) or t <= 0
            for t in (config.launch_timeout_s, config.lifetime_s, config.cleanup_timeout_s)
        )
        or type(config.output_cap) is not int
        or config.output_cap < 0
    ):
        raise BoundaryUnavailable("invalid boundary limits")
    _environment(config)
    if config.argv[0] not in (
        "/opt/python/bin/python",
        "/opt/uv/uv",
        "/opt/node/node",
        "/opt/pnpm/pnpm",
    ):
        raise BoundaryUnavailable("unapproved boundary executable")
    cwd = PurePosixPath(config.cwd)
    if (
        str(cwd) != config.cwd
        or ".." in cwd.parts
        or not (cwd.is_relative_to("/T") or cwd.is_relative_to("/state"))
    ):
        raise BoundaryUnavailable("invalid boundary cwd")
    tree, state, scratch = (_canonical(p) for p in (roots.tree, roots.state, roots.scratch))
    if any(not p.is_dir() for p in (tree, state, scratch)) or any(
        _overlap(a, b) for a, b in ((tree, state), (tree, scratch), (state, scratch))
    ):
        raise BoundaryUnavailable("boundary roots overlap")
    excluded = tuple(_canonical(p) for p in roots.exclusions)
    excluded += (REPO_ROOT, Path("/mnt"), Path("/run"))
    targets: set[str] = set()
    roles: set[str] = set()
    for item in roots.readonly:
        source = _canonical(item.source)
        if item.target in targets:
            raise BoundaryUnavailable("duplicate boundary mount")
        targets.add(item.target)
        if item.role == "system":
            if not (
                (item.target in SYSTEM_FILES and source.is_file())
                or (item.target in SYSTEM_DIRECTORIES and source.is_dir())
            ):
                raise BoundaryUnavailable("unapproved system manifest")
            # System content must be the selected, resolved system file, never T content.
            if source != Path(item.target).resolve(strict=True):
                raise BoundaryUnavailable("system manifest source mismatch")
        else:
            dependency = item.role == "dependencies" and (
                item.target == "/opt/deps"
                or (
                    str(PurePosixPath(item.target)) == item.target
                    and ".." not in PurePosixPath(item.target).parts
                    and PurePosixPath(item.target).is_relative_to("/opt/deps")
                )
            )
            if not dependency and (
                TOOL_TARGETS.get(item.role) != item.target or item.role in roles
            ):
                raise BoundaryUnavailable("unapproved tool manifest")
            roles.add(item.role)
            if not source.is_dir():
                raise BoundaryUnavailable("tool manifest is not a directory")
        if any(
            _overlap(source, p) for p in (tree, state, scratch, *excluded)
        ) or Path.home().is_relative_to(source):
            raise BoundaryUnavailable("excluded manifest source")
        if len(item.digest) != 64 or manifest_digest(source) != item.digest:
            raise BoundaryUnavailable("boundary manifest mismatch")
    required = set(
        (
            *SYSTEM_FILES,
            *SYSTEM_DIRECTORIES,
            *(v for k, v in TOOL_TARGETS.items() if k != "dependencies"),
        )
    )
    if roles != set(TOOL_TARGETS) or not required <= targets:
        raise BoundaryUnavailable("incomplete boundary manifest")
    ordered = sorted(targets)
    if any(PurePosixPath(b).is_relative_to(a) for a in ordered for b in ordered if a != b):
        raise BoundaryUnavailable("incomplete boundary manifest")


def _identity(pid: int) -> ProcessIdentity:
    text = Path(f"/proc/{pid}/stat").read_text()
    return ProcessIdentity(pid, int(text.rsplit(")", 1)[1].split()[19]))


def _child(pid: int) -> int:
    children = Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
    if len(children) != 1:
        raise BoundaryUnavailable("ambiguous boundary child")
    return int(children[0])


def _ns(fd: int) -> NamespaceIdentity:
    info = os.fstat(fd)
    return NamespaceIdentity(info.st_dev, info.st_ino)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BoundaryUnavailable("duplicate entry evidence")
        result[key] = value
    return result


async def _line(fd: int) -> bytes:
    pipe = os.fdopen(fd, "rb", buffering=0)
    transport: asyncio.ReadTransport | None = None
    try:
        reader = asyncio.StreamReader(limit=4096)
        protocol = asyncio.StreamReaderProtocol(reader)
        transport, _ = await asyncio.get_running_loop().connect_read_pipe(lambda: protocol, pipe)
        return await reader.readuntil(b"\n")
    finally:
        if transport is not None:
            transport.close()
        else:
            pipe.close()


async def _settle(task: asyncio.Task[Any]) -> Any:
    while not task.done():
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(task)
    return task.result()


class _Handle:
    def __init__(self, config: BoundaryConfig) -> None:
        self.config = config
        self.transport: asyncio.SubprocessTransport | None = None
        self.collector = ProcessCollector(config.output_cap)
        self.processes: dict[str, ProcessIdentity] = {}
        self.pidfds: dict[str, int] = {}
        self.namespace_fds: dict[str, int] = {}
        self.identities: dict[str, NamespaceIdentity] = {}
        self.verified_dead = False
        self.owner = -1
        self.stage: Path | None = None
        self._cleanup: asyncio.Task[None] | None = None
        self._watcher: asyncio.Task[None] | None = None
        self._code: int | None = None
        self.nonce = ""
        self.deadline = time.monotonic() + config.lifetime_s

    @property
    def u0(self) -> NamespaceIdentity:
        return self.identities["u0"]

    @property
    def n0(self) -> NamespaceIdentity:
        return self.identities["n0"]

    @property
    def u1(self) -> NamespaceIdentity:
        return self.identities["u1"]

    @property
    def pid(self) -> NamespaceIdentity:
        return self.identities["pid"]

    @property
    def mount(self) -> NamespaceIdentity:
        return self.identities["mount"]

    @property
    def stdout(self) -> str:
        return self.collector.stdout.text()

    @property
    def stderr(self) -> str:
        return self.collector.stderr.text()

    @property
    def returncode(self) -> int | None:
        return self._code

    def authenticate(self, raw: bytes, nonce: str) -> None:
        if self.transport is None:
            raise BoundaryUnavailable("missing boundary launcher")
        decoded: object = json.loads(raw, object_pairs_hook=_pairs)
        if not isinstance(decoded, dict):
            raise BoundaryUnavailable("incomplete boundary evidence")
        evidence = cast(dict[str, Any], decoded)
        if set(evidence) != {
            "nonce",
            "pid",
            "outer",
            "inner",
            "procdev",
        }:
            raise BoundaryUnavailable("incomplete boundary evidence")
        if evidence["nonce"] != nonce or type(evidence["pid"]) is not int or evidence["pid"] != 1:
            raise BoundaryUnavailable("invalid boundary nonce or PID")
        outer = self.transport.get_pid()
        setup = _child(outer)
        init = _child(setup)
        for key, pid in (("outer", outer), ("setup", setup), ("init", init)):
            before = _identity(pid)
            if key in self.processes and before != self.processes[key]:
                raise BoundaryUnavailable("stale boundary process")
            if key not in self.pidfds:
                self.pidfds[key] = open_pidfd(pid)
            self.processes[key] = _identity(pid)
            if before != self.processes[key]:
                raise BoundaryUnavailable("stale boundary process")
        for key, pid, name in (
            ("u0", outer, "user"),
            ("n0", outer, "net"),
            ("p0", setup, "pid"),
            ("u1", init, "user"),
            ("pid", init, "pid"),
            ("mount", init, "mnt"),
        ):
            fd = os.open(f"/proc/{pid}/ns/{name}", os.O_RDONLY | os.O_CLOEXEC)
            self.namespace_fds[key] = fd
            self.identities[key] = _ns(fd)
        self._check_topology(evidence, outer, setup, init)
        if any(select.select([fd], [], [], 0)[0] for fd in self.pidfds.values()):
            raise BoundaryUnavailable("boundary process already exited")

    def _check_topology(self, evidence: dict[str, Any], outer: int, setup: int, init: int) -> None:
        for kind, pid in (("outer", outer), ("inner", init)):
            expected = {
                name: os.readlink(f"/proc/{pid}/ns/{name}")
                for name in ("user", "net", "pid", "mnt")
            }
            if evidence[kind] != expected:
                raise BoundaryUnavailable("namespace evidence mismatch")
        if any(
            evidence["inner"][n] == os.readlink("/proc/self/ns/" + n)
            for n in ("user", "net", "pid", "mnt")
        ):
            raise BoundaryUnavailable("inherited host namespace")
        if (
            self.u0 == self.u1
            or evidence["outer"]["net"] != evidence["inner"]["net"]
            or evidence["outer"]["pid"] != os.readlink("/proc/self/ns/pid")
            or os.readlink(f"/proc/{setup}/ns/user") != evidence["inner"]["user"]
            or self.identities["p0"] == self.pid
            or os.readlink(f"/proc/{setup}/ns/pid") == evidence["outer"]["pid"]
            or os.readlink(f"/proc/{setup}/ns/net") != evidence["outer"]["net"]
            or os.readlink(f"/proc/{setup}/ns/mnt") != evidence["inner"]["mnt"]
        ):
            raise BoundaryUnavailable("invalid boundary topology")
        for key, owner in (("n0", self.u0), ("p0", self.u0), ("pid", self.u1), ("mount", self.u1)):
            fd = fcntl.ioctl(self.namespace_fds[key], 0xB701)  # NS_GET_USERNS
            try:
                if _ns(fd) != owner:
                    raise BoundaryUnavailable("incorrect namespace owner")
            finally:
                os.close(fd)
        for key, parent in (("u1", self.u0), ("pid", self.identities["p0"])):
            fd = fcntl.ioctl(self.namespace_fds[key], 0xB702)  # NS_GET_PARENT
            try:
                if _ns(fd) != parent:
                    raise BoundaryUnavailable("incorrect namespace parent")
            finally:
                os.close(fd)
        self._check_process_state(setup, init)
        self._check_root(evidence, outer, init)
        if _child(outer) != setup or _child(setup) != init:
            raise BoundaryUnavailable("boundary process changed")

    def _check_process_state(self, setup: int, init: int) -> None:
        status = dict(
            line.split(":", 1) for line in Path(f"/proc/{init}/status").read_text().splitlines()
        )
        setup_status = dict(
            line.split(":", 1) for line in Path(f"/proc/{setup}/status").read_text().splitlines()
        )
        pids = status["NSpid"].split()
        if (
            len(pids) != 3
            or pids[0] != str(init)
            or int(pids[1]) <= 1
            or pids[2] != "1"
            or setup_status["NSpid"].split() != [str(setup), "1"]
            or any(
                int(status[key].strip(), 16)
                for key in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb")
            )
            or status["NoNewPrivs"].strip() != "1"
            or status["Seccomp"].strip() != "2"
            or int(status["Seccomp_filters"]) != _filter_count() + 1
        ):
            raise BoundaryUnavailable("unsafe boundary process state")

    def _check_root(self, evidence: dict[str, Any], outer: int, init: int) -> None:
        if type(evidence["procdev"]) is not int or evidence["procdev"] == os.stat("/proc").st_dev:
            raise BoundaryUnavailable("host proc inherited")
        root = Path(f"/proc/{init}/root")
        if (
            os.stat(root / "proc").st_dev != evidence["procdev"]
            or os.stat(root).st_dev == os.stat("/").st_dev
            or os.path.lexists(root / ".oldroot")
            or os.readlink(root / "proc/1/ns/pid") != evidence["inner"]["pid"]
        ):
            raise BoundaryUnavailable("root or proc evidence mismatch")
        anchor = Path(f"/proc/{outer}/root/proc")
        anchor_dev = os.stat(anchor).st_dev
        if (
            anchor_dev in (evidence["procdev"], os.stat("/proc").st_dev)
            or os.readlink(anchor / "1/ns/pid") != f"pid:[{self.identities['p0'].inode}]"
        ):
            raise BoundaryUnavailable("invalid boundary proc anchor")
        rows = [r.split() for r in Path(f"/proc/{init}/mountinfo").read_text().splitlines()]
        procs = [r for r in rows if r[r.index("-") + 1] == "proc"]
        expected = {f"{os.major(dev)}:{os.minor(dev)}" for dev in (anchor_dev, evidence["procdev"])}
        if (
            len(procs) != 2
            or {r[2] for r in procs} != expected
            or any(
                r[3:5] != ["/", "/proc"]
                or not {"nosuid", "nodev", "noexec"} <= set(r[5].split(","))
                for r in procs
            )
        ):
            raise BoundaryUnavailable("missing or unsafe boundary proc mounts")

    async def monitor(self) -> None:
        try:
            await wait_done(self.collector.exited, max(0, self.deadline - time.monotonic()))
        finally:
            await self.stop_verified()

    def start_monitor(self) -> None:
        self._watcher = asyncio.create_task(self.monitor())

    async def wait(self) -> int:
        if self._watcher is None:
            raise BoundaryUnavailable("boundary did not enter")
        try:
            await asyncio.shield(self._watcher)
        except asyncio.CancelledError:
            await self.stop_verified()
            raise
        if self._code is None:
            raise BoundaryUnavailable("boundary exit is unknown")
        return self._code

    async def stop_verified(self) -> None:
        if self._cleanup is None:
            self._cleanup = asyncio.create_task(self._stop())
        try:
            await asyncio.shield(self._cleanup)
        except asyncio.CancelledError:
            await _settle(self._cleanup)
            raise

    async def _stop(self) -> None:
        try:
            async with asyncio.timeout(self.config.cleanup_timeout_s):
                await self._kill_and_verify()
            self.verified_dead = True
        except Exception as error:
            raise BoundaryUnavailable("boundary death is unverified") from error
        finally:
            if self.owner >= 0:
                os.close(self.owner)
                self.owner = -1
            for descriptors in (self.pidfds, self.namespace_fds):
                for fd in descriptors.values():
                    os.close(fd)
                descriptors.clear()
            if self.transport is not None:
                self.transport.close()
            if self.stage is not None:
                self.stage.rmdir()
                self.stage = None

    async def _kill_and_verify(self) -> None:
        if self.transport is None:
            return
        # Killing pinned PID 1 makes the kernel kill and reap every namespace member.
        if "init" in self.pidfds:
            with contextlib.suppress(ProcessLookupError):
                _signal_fd(self.pidfds["init"])
        else:
            with contextlib.suppress(ProcessLookupError):
                self._kill_launcher()
        if not await wait_done(self.collector.exited, self.config.cleanup_timeout_s * 0.7):
            for fd in self.pidfds.values():
                with contextlib.suppress(ProcessLookupError):
                    _signal_fd(fd)
            with contextlib.suppress(ProcessLookupError):
                self._kill_launcher()
            if not await wait_done(self.collector.exited, self.config.cleanup_timeout_s * 0.2):
                raise BoundaryUnavailable("launcher not reaped")
        self._code = self.transport.get_returncode()
        for fd in self.pidfds.values():
            await _dead(fd)
        for _ in range(1000):
            if not await asyncio.to_thread(_still_present, tuple(self.processes.values())):
                break
            await asyncio.sleep(0.01)
        else:
            raise BoundaryUnavailable("owned processes were not reaped")
        if not await wait_done(self.collector.closed, self.config.cleanup_timeout_s * 0.1):
            raise BoundaryUnavailable("boundary pipes not drained")

    def _kill_launcher(self) -> None:
        if "outer" in self.pidfds:
            _signal_fd(self.pidfds["outer"])
        elif self.transport is not None:
            # Only a failure to acquire the initial pidfd can reach this fallback.
            self.transport.kill()


async def _dead(fd: int) -> None:
    loop = asyncio.get_running_loop()
    ready: asyncio.Future[None] = loop.create_future()
    loop.add_reader(fd, lambda: ready.set_result(None) if not ready.done() else None)
    try:
        await ready
    finally:
        loop.remove_reader(fd)


def _still_present(identities: tuple[ProcessIdentity, ...]) -> bool:
    for identity in identities:
        try:
            if _identity(identity.pid) == identity:
                return True
        except FileNotFoundError:
            pass
    return False


def _filter_count() -> int:
    status = Path("/proc/self/status").read_text().splitlines()
    return int(next(row.split(":", 1)[1] for row in status if row.startswith("Seccomp_filters:")))


def _signal_fd(fd: int) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.syscall(424, fd, signal.SIGKILL, 0, 0) < 0:
        raise OSError(ctypes.get_errno(), "pidfd_send_signal")


def _configuration(
    config: BoundaryConfig, roots: RootInputs, owner: _Handle, fds: list[int]
) -> int:
    owner.stage = Path(tempfile.mkdtemp(prefix="root-", dir=roots.scratch))

    def pin(path: Path) -> int:
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW)
        fds.append(fd)
        return fd

    owner.nonce = secrets.token_hex(32)
    data = {
        "tree_fd": pin(roots.tree),
        "state_fd": pin(roots.state),
        "root": str(owner.stage),
        "nonce": owner.nonce,
        "argv": config.argv,
        "cwd": config.cwd,
        "env": _environment(config),
        "lifetime_s": config.lifetime_s,
        "deadline": owner.deadline,
        "mounts": [
            dict(fd=pin(m.source), target=m.target, digest=m.digest) for m in roots.readonly
        ],
    }
    libc = ctypes.CDLL(None, use_errno=True)
    fd = int(libc.syscall(319, b"preview-boundary", 3))  # memfd_create, CLOEXEC|ALLOW_SEALING
    if fd < 0:
        raise OSError(ctypes.get_errno(), "memfd_create")
    fds.append(fd)
    encoded = json.dumps(data, separators=(",", ":")).encode()
    if len(encoded) >= 1024 * 1024:
        raise BoundaryUnavailable("boundary configuration is too large")
    os.write(fd, encoded)
    os.lseek(fd, 0, os.SEEK_SET)
    fcntl.fcntl(fd, 1033, 15)  # F_ADD_SEALS: SEAL|SHRINK|GROW|WRITE
    return fd


async def _launch(
    config: BoundaryConfig,
    roots: RootInputs,
    abort: asyncio.Event,
    before_exec: Callable[[BoundaryHandle], Awaitable[None]] | None,
    on_ownership: Callable[[BoundaryHandle], None] | None,
) -> _Handle:
    owner = _Handle(config)
    fds: list[int] = []
    report = ack = -1
    reader: asyncio.Task[bytes] | None = None
    try:
        if on_ownership is not None:
            on_ownership(owner)
        async with asyncio.timeout(config.launch_timeout_s):
            await asyncio.to_thread(_validate, config, roots)
            config_fd = _configuration(config, roots, owner, fds)
            report, report_write = os.pipe2(os.O_CLOEXEC)
            fds.append(report_write)
            ack_read, ack = os.pipe2(os.O_CLOEXEC)
            fds.append(ack_read)
            control, owner.owner = os.pipe2(os.O_CLOEXEC)
            fds.append(control)
            command = [
                sys.executable,
                "-I",
                "-S",
                str(ENTRY),
                str(config_fd),
                str(report_write),
                str(ack_read),
                str(control),
                str(os.getpid()),
            ]
            spawning = asyncio.create_task(_spawn(owner, command, roots.scratch, tuple(fds)))
            try:
                await asyncio.shield(spawning)
            except asyncio.CancelledError:
                await _settle(spawning)
                raise
            for fd in fds:
                os.close(fd)
            fds.clear()
            reader = asyncio.create_task(_line(report))
            report = -1
            raw = await reader
            owner.authenticate(raw, owner.nonce)
            if before_exec is not None:
                await before_exec(owner)
            if abort.is_set():
                raise asyncio.CancelledError
            if time.monotonic() >= owner.deadline:
                raise BoundaryUnavailable("boundary deadline expired before execution")
            os.write(ack, b"1")
            os.close(ack)
            ack = -1
            owner.start_monitor()
            return owner
    except BaseException as error:
        await owner.stop_verified()
        if isinstance(error, asyncio.CancelledError):
            raise
        raise BoundaryUnavailable(
            f"boundary entry refused: {error}; {owner.stderr[-2000:]}"
        ) from error
    finally:
        for fd in (*fds, report, ack):
            if fd >= 0:
                os.close(fd)
        if reader is not None:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)


async def _spawn(owner: _Handle, command: list[str], cwd: Path, fds: tuple[int, ...]) -> None:
    owner.transport, _ = await asyncio.get_running_loop().subprocess_exec(
        lambda: owner.collector,
        *command,
        cwd=cwd,
        env=child_env({"PATH": "/usr/bin:/bin"}),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        pass_fds=fds,
    )
    pid = owner.transport.get_pid()
    owner.pidfds["outer"] = open_pidfd(pid)
    owner.processes["outer"] = _identity(pid)


async def launch_boundary(
    config: BoundaryConfig,
    roots: RootInputs,
    *,
    before_exec: Callable[[BoundaryHandle], Awaitable[None]] | None = None,
    on_ownership: Callable[[BoundaryHandle], None] | None = None,
) -> BoundaryHandle:
    """Authenticate and acknowledge entry; cancellation retains ownership until cleanup.

    on_ownership retains the cleanup handle before any spawn; it grants no execution authority.
    before_exec runs after authentication and before the sole execution acknowledgement.
    A persistence/authorization failure never opens execution.
    All deadlines are seconds. A failed gate raises BoundaryUnavailable; there is no
    host fallback. FD mappings are borrowed until stop_verified()/automatic exit closes
    them. The caller must not close, transfer to T, or use them after verified death.
    """
    abort = asyncio.Event()
    pending = asyncio.create_task(_launch(config, roots, abort, before_exec, on_ownership))
    try:
        return await asyncio.shield(pending)
    except asyncio.CancelledError:
        abort.set()
        try:
            owner = await _settle(pending)
        except asyncio.CancelledError:
            raise
        else:
            await owner.stop_verified()
        raise
