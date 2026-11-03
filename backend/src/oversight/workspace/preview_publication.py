"""Verified, bounded byte publication. No served asset is reopened through a build path.

The caller owns the supervisor-only destination parent and must keep it inaccessible to
candidate code. Publication pins the complete input inventory, verifies it after copying,
and transfers only fresh read-only output descriptors. Nothing is returned on failure.
"""

import asyncio
import contextlib
import ctypes
import hashlib
import os
import re
import stat
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO
from urllib.parse import unquote_to_bytes


@dataclass(frozen=True)
class OutputBudgets:
    file_bytes: int = 16 * 1024 * 1024
    total_bytes: int = 256 * 1024 * 1024
    files: int = 10_000
    entries: int = 20_000
    seconds: float = 30
    path_bytes: int = 4096

    def __post_init__(self) -> None:
        if any(
            type(value) is not int or value < 0
            for value in (
                self.file_bytes,
                self.total_bytes,
                self.files,
                self.entries,
                self.path_bytes,
            )
        ):
            raise ValueError("invalid output budget")
        if not 0 < self.seconds <= 3600:
            raise ValueError("invalid publication deadline")


def _name(name: str) -> None:
    name.encode("utf-8", errors="strict")
    stem = name.split(".", 1)[0].upper()
    if (
        not name
        or name in {".", ".."}
        or name[-1:] in {" ", "."}
        or any(ord(c) < 32 or ord(c) == 127 or c in '\\/:<>"|?*%' for c in name)
        or stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
        or re.fullmatch(r"(?:COM|LPT)[0-9¹²³]", stem)
    ):
        raise ValueError("ambiguous output name")


def request_path(target: str) -> str:
    """Decode one origin-form path; residual escapes and encoded separators are refused."""
    raw = target.split("?", 1)[0]
    if not raw.startswith("/") or raw.startswith("//") or "#" in target:
        raise ValueError("origin-form path required")
    if re.search(r"%(?![0-9a-fA-F]{2})|%(?:2f|5c)", raw, re.I):
        raise ValueError("ambiguous path encoding")
    value = unquote_to_bytes(raw).decode("utf-8", errors="strict")[1:]
    if not value:
        return "index.html"
    for part in value.split("/"):
        _name(part)
    return value


class _How(ctypes.Structure):
    _fields_ = [("flags", ctypes.c_uint64), ("mode", ctypes.c_uint64), ("resolve", ctypes.c_uint64)]


def _linux_open(parent: int, name: str, *, anchor: bool = False) -> int:
    if sys.platform != "linux":
        raise OSError("Linux openat2 requires Linux")
    if os.uname().machine not in {"x86_64", "aarch64", "riscv64"}:
        raise OSError("openat2 syscall number is unproven on this architecture")
    how = _How(os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK | os.O_NOFOLLOW, 0, 0x04 | 0x08)
    if not anchor:
        how.resolve |= 0x01  # RESOLVE_NO_XDEV, including bind mounts.
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.syscall(
        ctypes.c_long(437),
        ctypes.c_int(parent),
        ctypes.c_char_p(os.fsencode(name)),
        ctypes.byref(how),
        ctypes.c_size_t(ctypes.sizeof(how)),
    )
    if result < 0:
        code = ctypes.get_errno()
        raise OSError(code, "openat2 refused publication input")
    return int(result)


class _Unicode(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_ushort),
        ("maximum", ctypes.c_ushort),
        ("buffer", ctypes.c_void_p),
    ]


class _Attributes(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_ulong),
        ("root", ctypes.c_void_p),
        ("name", ctypes.POINTER(_Unicode)),
        ("attributes", ctypes.c_ulong),
        ("security", ctypes.c_void_p),
        ("qos", ctypes.c_void_p),
    ]


class _IoStatus(ctypes.Structure):
    _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_size_t)]


def _windows_open(parent: int | None, name: str) -> int:
    """NT root-relative opens, no reparse traversal, no write/delete sharing.

    OBJ_DONT_REPARSE rejects intermediate reparses on the absolute anchor. Subsequent
    single-component opens use pinned parents; FILE_OPEN_REPARSE_POINT plus attribute
    verification rejects the final component too. All handles become owned CRT fds.
    """
    if sys.platform != "win32":
        raise OSError("Windows reparse verification requires native Windows")
    import msvcrt

    dll = ctypes.WinDLL("ntdll")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = dll.NtCreateFile
    create.restype = ctypes.c_long
    create.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_ulong,
        ctypes.POINTER(_Attributes),
        ctypes.POINTER(_IoStatus),
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_void_p,
        ctypes.c_ulong,
    ]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    text = ctypes.create_unicode_buffer(name)
    encoded = name.encode("utf-16-le")
    if len(encoded) > 65532:
        raise ValueError("Windows publication path is too long")
    unicode = _Unicode(len(encoded), len(encoded) + 2, ctypes.cast(text, ctypes.c_void_p))
    root = None if parent is None else msvcrt.get_osfhandle(parent)
    attributes = _Attributes(ctypes.sizeof(_Attributes), root, ctypes.pointer(unicode), 0x1040)
    handle, status = ctypes.c_void_p(), _IoStatus()
    # FILE_GENERIC_READ, FILE_SHARE_READ, FILE_OPEN, synchronous + reparse-point open.
    result = create(
        ctypes.byref(handle),
        0x120089,
        ctypes.byref(attributes),
        ctypes.byref(status),
        None,
        0,
        1,
        1,
        0x200020,
        None,
        0,
    )
    if result < 0 or handle.value is None:
        raise OSError(f"NtCreateFile refused publication input: NTSTATUS {result & 0xFFFFFFFF:#x}")
    try:
        fd = msvcrt.open_osfhandle(handle.value, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        kernel.CloseHandle(handle)
        raise
    try:
        attributes = getattr(os.fstat(fd), "st_file_attributes", None)
        if not isinstance(attributes, int):
            raise OSError("Windows reparse attributes are unavailable")
        if attributes & 0x400:
            raise ValueError("reparse publication input")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open(parent: int, name: str) -> int:
    if sys.platform == "linux":
        return _linux_open(parent, name)
    if sys.platform == "win32":
        return _windows_open(parent, name)
    raise OSError("verified publication is unavailable on this platform")


def _anchor(path: Path) -> int:
    if sys.platform == "linux":
        with contextlib.ExitStack() as stack:
            root = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            stack.callback(os.close, root)
            return _linux_open(root, str(path.absolute()).lstrip("/"), anchor=True)
    if sys.platform == "win32":
        return _windows_open(None, "\\??\\" + str(path.absolute()))
    raise OSError("verified publication is unavailable on this platform")


def _identity(info: os.stat_result) -> tuple[int, ...]:
    if info.st_ino <= 0:
        raise OSError("publication file identity is unavailable")
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


@dataclass
class _Pinned:
    fd: int
    parent: int
    name: str
    relative: str
    path: Path
    identity: tuple[int, ...]
    names: tuple[str, ...] | None


def _names(fd: int, path: Path, limit: int) -> tuple[str, ...]:
    # On NT, pinned handles deny replacement. Enumeration supplies names only: all content
    # access still uses NT root-relative handles, followed by inventory verification.
    with os.scandir(path if sys.platform == "win32" else fd) as entries:
        names: list[str] = []
        for entry in entries:
            if len(names) >= limit:
                raise ValueError("publication entry budget exceeded")
            _name(entry.name)
            names.append(entry.name)
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("case-ambiguous publication names")
    return tuple(sorted(names))


def _inventory(
    root: int, source: Path, budgets: OutputBudgets, stack: contextlib.ExitStack, deadline: float
) -> list[_Pinned]:
    pending = [(root, source, "", -1, "")]
    pinned: list[_Pinned] = []
    count = total = 0
    while pending:
        if len(pinned) >= budgets.entries:
            raise ValueError("publication entry budget exceeded")
        _deadline(deadline)
        fd, path, relative, parent, component = pending.pop()
        before = _identity(os.fstat(fd))
        names = _names(fd, path, budgets.entries - len(pinned))
        if _identity(os.fstat(fd)) != before:
            raise ValueError("publication directory changed during enumeration")
        pinned.append(_Pinned(fd, parent, component, relative, path, before, names))
        for name in names:
            _deadline(deadline)
            if len(pinned) + len(pending) >= budgets.entries:
                raise ValueError("publication entry budget exceeded")
            rel = f"{relative}/{name}".lstrip("/")
            if len(rel.encode("utf-8")) > budgets.path_bytes:
                raise ValueError("publication path budget exceeded")
            child = _open(fd, name)
            stack.callback(os.close, child)
            info = os.fstat(child)
            if stat.S_ISDIR(info.st_mode):
                pending.append((child, path / name, rel, fd, name))
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                count, total = count + 1, total + info.st_size
                if (
                    count > budgets.files
                    or total > budgets.total_bytes
                    or info.st_size > budgets.file_bytes
                ):
                    raise ValueError("publication output budget exceeded")
                pinned.append(_Pinned(child, fd, name, rel, path / name, _identity(info), None))
            else:
                raise ValueError("publication requires regular single-link files")
    return pinned


def _deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise TimeoutError("publication deadline exceeded")


def _verify(pinned: list[_Pinned], budgets: OutputBudgets, deadline: float) -> None:
    for item in pinned:
        _deadline(deadline)
        if _identity(os.fstat(item.fd)) != item.identity:
            raise ValueError("publication input changed")
        if item.names is not None and _names(item.fd, item.path, budgets.entries) != item.names:
            raise ValueError("publication inventory changed")
        if item.parent >= 0:
            fd = _open(item.parent, item.name)
            try:
                if _identity(os.fstat(fd)) != item.identity:
                    raise ValueError("publication input replaced")
            finally:
                os.close(fd)


@dataclass
class _Outputs:
    streams: dict[str, BinaryIO] = field(default_factory=lambda: dict[str, BinaryIO]())
    closed: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)


@dataclass(frozen=True)
class PublishedBuild:
    paths: tuple[str, ...]
    total_bytes: int
    directory: Path
    _sizes: tuple[int, ...] = field(repr=False)
    _outputs: _Outputs = field(repr=False, compare=False)

    def size(self, path: str) -> int:
        if self._outputs.closed:
            raise ValueError("publication is closed")
        return self._sizes[self.paths.index(path)]

    def read(self, path: str, offset: int = 0, size: int = 65536) -> bytes:
        with self._outputs.lock:
            if self._outputs.closed or offset < 0 or not 0 <= size <= 16 * 1024 * 1024:
                raise ValueError("invalid publication read")
            stream = self._outputs.streams[path]
            if sys.platform != "win32":
                return os.pread(stream.fileno(), size, offset)
            stream.seek(offset)
            return stream.read(size)

    def close(self) -> None:
        with self._outputs.lock:
            if not self._outputs.closed:
                self._outputs.closed = True
                for stream in self._outputs.streams.values():
                    stream.close()
                for path in self.directory.iterdir():
                    if sys.platform == "win32":
                        path.chmod(0o600)
                    path.unlink()
                self.directory.rmdir()


def _copy(item: _Pinned, output: BinaryIO, deadline: float) -> None:
    remaining = item.identity[4]
    digest = hashlib.sha256()
    while remaining:
        _deadline(deadline)
        chunk = os.read(item.fd, min(65536, remaining))
        if not chunk:
            raise ValueError("publication file truncated")
        output.write(chunk)
        digest.update(chunk)
        remaining -= len(chunk)
    if os.read(item.fd, 1):
        raise ValueError("publication file grew")
    output.flush()
    output.seek(0)
    copied = hashlib.sha256()
    while chunk := output.read(65536):
        _deadline(deadline)
        copied.update(chunk)
    if copied.digest() != digest.digest():
        raise ValueError("publication byte verification failed")


def _publish(source: Path, destination: Path, budgets: OutputBudgets) -> PublishedBuild:
    deadline = time.monotonic() + budgets.seconds
    source, destination = source.absolute(), destination.absolute()
    if source.resolve() in (destination.resolve(), *destination.resolve().parents):
        raise ValueError("publication destination must be outside tested tree")
    with contextlib.ExitStack() as stack:
        root = _anchor(source)
        stack.callback(os.close, root)
        if not stat.S_ISDIR(os.fstat(root).st_mode):
            raise ValueError("publication root must be a directory")
        pinned = _inventory(root, source, budgets, stack, deadline)
        outputs = _Outputs()
        files = [item for item in pinned if item.names is None]
        build = PublishedBuild(
            tuple(item.relative for item in files),
            sum(item.identity[4] for item in files),
            destination,
            tuple(item.identity[4] for item in files),
            outputs,
        )
        destination.mkdir(mode=0o700)  # Exclusive fresh supervisor-owned output directory.
        try:
            published_root = _anchor(destination)
            stack.callback(os.close, published_root)
            for index, item in enumerate(files):
                _deadline(deadline)
                path = destination / str(index)
                with path.open("x+b") as output:
                    _copy(item, output, deadline)
                    copied_identity = _identity(os.fstat(output.fileno()))
                fd = _open(published_root, str(index))
                try:
                    # NT may finalize write timestamps when the writer closes. The
                    # private output must retain device/inode/type/link count/byte size.
                    if _identity(os.fstat(fd))[:5] != copied_identity[:5]:
                        raise ValueError("published output identity changed")
                    outputs.streams[item.relative] = os.fdopen(fd, "rb")
                    fd = -1
                finally:
                    if fd >= 0:
                        os.close(fd)
                path.chmod(0o400)
            _verify(pinned, budgets, deadline)
            current = _anchor(source)
            try:
                if _identity(os.fstat(current)) != pinned[0].identity:
                    raise ValueError("publication root replaced")
            finally:
                os.close(current)
            _deadline(deadline)
            return build
        except BaseException:
            build.close()
            raise


DEFAULT_OUTPUT_BUDGETS = OutputBudgets()


async def publish_build(
    source: Path, destination: Path, *, budgets: OutputBudgets = DEFAULT_OUTPUT_BUDGETS
) -> PublishedBuild:
    """Caller transfers ownership of returned handles; cancellation closes eventual output.

    File IO runs off the event loop. Cancellation waits for the bounded copy worker before
    returning, so no unpublished output or descriptors can outlive this operation.
    """
    task = asyncio.create_task(asyncio.to_thread(_publish, source, destination, budgets))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.shield(task)
        if not task.cancelled() and task.exception() is None:
            cleanup = asyncio.create_task(asyncio.to_thread(task.result().close))
            while not cleanup.done():
                with contextlib.suppress(asyncio.CancelledError):
                    await asyncio.shield(cleanup)
            cleanup.result()
        raise
