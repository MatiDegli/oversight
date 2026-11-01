"""Typed OS boundaries for runtime ownership and native Windows process lifetime."""

import ctypes
import errno
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol, cast

from oversight.workspace.errors import WorkspaceBusy

if sys.platform != "win32":
    import fcntl


class Locking(Protocol):
    @property
    def LK_NBLCK(self) -> int: ...

    @property
    def LK_UNLCK(self) -> int: ...

    def locking(self, fd: int, mode: int, length: int, /) -> object: ...


def import_flags(*, nt: bool, binary: int = 0) -> tuple[int, int, int]:
    """DIRECTORY, READ and tree's directory flags; POSIX expressions stay exact."""
    if nt:
        directory = os.O_RDONLY | binary
        read = os.O_RDONLY | binary
        return directory, read, directory
    if sys.platform == "win32":
        raise OSError("POSIX flags are unavailable on Windows")
    directory = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    read = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    tree = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    return directory, read, tree


def _locking() -> Locking:
    if sys.platform == "win32":
        import msvcrt

        return msvcrt
    raise OSError("Windows locking is unavailable")


def acquire_lock(path: Path, *, msvcrt_api: Locking | None = None) -> int:
    nt = sys.platform == "win32" or msvcrt_api is not None
    if sys.platform == "win32":
        flags = os.O_RDWR | os.O_CREAT | os.O_BINARY
    elif nt:
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
    else:
        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    fd = os.open(path, flags, 0o600)
    try:
        if nt:
            os.set_inheritable(fd, False)
            os.lseek(fd, 0, os.SEEK_SET)
            api = msvcrt_api or _locking()
            try:
                api.locking(fd, api.LK_NBLCK, 1)
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EDEADLK):
                    raise
                raise WorkspaceBusy(
                    "another Oversight server is using this runtime directory"
                ) from None
        elif sys.platform != "win32":
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise WorkspaceBusy(
                    "another Oversight server is using this runtime directory"
                ) from None
    except BaseException:
        os.close(fd)
        raise
    return fd


def release_lock(fd: int, *, msvcrt_api: Locking | None = None) -> None:
    try:
        if sys.platform == "win32" or msvcrt_api is not None:
            os.lseek(fd, 0, os.SEEK_SET)
            api = msvcrt_api or _locking()
            api.locking(fd, api.LK_UNLCK, 1)
    finally:
        os.close(fd)


# Numeric/kernel32 primitives. All handles are non-inheritable (null security attributes).

NT_KILL_CODE = 0xC000013A
NT_CREATE_FLAGS = 0x00000004 | 0x08000000 | 0x00000200
_DWORD = ctypes.c_uint32
_HANDLE = ctypes.c_void_p


class Kernel(Protocol):
    CreateJobObjectW: Callable[..., int]
    SetInformationJobObject: Callable[..., int]
    OpenProcess: Callable[..., int]
    AssignProcessToJobObject: Callable[..., int]
    CreateToolhelp32Snapshot: Callable[..., int]
    Thread32First: Callable[..., int]
    Thread32Next: Callable[..., int]
    OpenThread: Callable[..., int]
    ResumeThread: Callable[..., int]
    QueryInformationJobObject: Callable[..., int]
    TerminateJobObject: Callable[..., int]
    TerminateProcess: Callable[..., int]
    CloseHandle: Callable[..., int]
    GetLastError: Callable[..., int]


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64),
        ("job_time", ctypes.c_int64),
        ("flags", _DWORD),
        ("min_working", ctypes.c_size_t),
        ("max_working", ctypes.c_size_t),
        ("active", _DWORD),
        ("affinity", ctypes.c_size_t),
        ("priority", _DWORD),
        ("scheduling", _DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits),
        ("io", ctypes.c_uint64 * 6),
        ("memory", ctypes.c_size_t * 4),
    ]


class _Thread(ctypes.Structure):
    _fields_ = [
        ("size", _DWORD),
        ("usage", _DWORD),
        ("tid", _DWORD),
        ("pid", _DWORD),
        ("priority", ctypes.c_int32),
        ("delta", ctypes.c_int32),
        ("flags", _DWORD),
    ]


def kernel32() -> Kernel:
    if sys.platform != "win32":
        raise OSError("kernel32 is Windows-only")
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures: dict[str, tuple[type, list[type]]] = {
        "CreateJobObjectW": (_HANDLE, [_HANDLE, ctypes.c_wchar_p]),
        "SetInformationJobObject": (_DWORD, [_HANDLE, _DWORD, _HANDLE, _DWORD]),
        "OpenProcess": (_HANDLE, [_DWORD, _DWORD, _DWORD]),
        "AssignProcessToJobObject": (_DWORD, [_HANDLE, _HANDLE]),
        "CreateToolhelp32Snapshot": (_HANDLE, [_DWORD, _DWORD]),
        "Thread32First": (_DWORD, [_HANDLE, _HANDLE]),
        "Thread32Next": (_DWORD, [_HANDLE, _HANDLE]),
        "OpenThread": (_HANDLE, [_DWORD, _DWORD, _DWORD]),
        "ResumeThread": (_DWORD, [_HANDLE]),
        "QueryInformationJobObject": (_DWORD, [_HANDLE, _DWORD, _HANDLE, _DWORD, _HANDLE]),
        "TerminateJobObject": (_DWORD, [_HANDLE, _DWORD]),
        "TerminateProcess": (_DWORD, [_HANDLE, _DWORD]),
        "CloseHandle": (_DWORD, [_HANDLE]),
        "GetLastError": (_DWORD, []),
    }
    for name, (result, args) in signatures.items():
        call = getattr(dll, name)
        call.restype, call.argtypes = result, args
    return cast(Kernel, dll)


def _failure(api: Kernel, operation: str) -> OSError:
    return OSError(api.GetLastError(), operation + " failed")


def close_job(job: int, *, kernel: Kernel | None = None) -> None:
    api = kernel or kernel32()
    if not api.CloseHandle(job):
        raise _failure(api, "CloseHandle")


def create_kill_job(*, kernel: Kernel | None = None) -> int:
    api = kernel or kernel32()
    job = api.CreateJobObjectW(None, None)
    if not job:
        raise _failure(api, "CreateJobObjectW")
    limits = _ExtendedLimits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway.
    if not api.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        error = _failure(api, "SetInformationJobObject")
        close_job(job, kernel=api)
        raise error
    return job


def assign_job(job: int, pid: int, *, kernel: Kernel | None = None) -> None:
    api = kernel or kernel32()
    process = api.OpenProcess(0x0101, False, pid)  # SET_QUOTA | TERMINATE
    if not process:
        raise _failure(api, "OpenProcess")
    try:
        if not api.AssignProcessToJobObject(job, process):
            raise _failure(api, "AssignProcessToJobObject")
    finally:
        close_job(process, kernel=api)


def resume_suspended(pid: int, *, kernel: Kernel | None = None) -> None:
    api = kernel or kernel32()
    snapshot = api.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD is system-wide.
    if not snapshot or snapshot == ctypes.c_void_p(-1).value:
        raise _failure(api, "CreateToolhelp32Snapshot")
    threads: list[int] = []
    try:
        entry = _Thread()
        entry.size = ctypes.sizeof(entry)
        ok = api.Thread32First(snapshot, ctypes.byref(entry))
        while ok:
            if entry.pid == pid:
                threads.append(entry.tid)
            entry.size = ctypes.sizeof(entry)
            ok = api.Thread32Next(snapshot, ctypes.byref(entry))
        if api.GetLastError() != 18:  # ERROR_NO_MORE_FILES, not a partial enumeration.
            raise _failure(api, "Thread32 enumeration")
        if len(threads) != 1:
            raise OSError("suspended child must have exactly one thread")
        thread = api.OpenThread(2, False, threads[0])  # THREAD_SUSPEND_RESUME
        if not thread:
            raise _failure(api, "OpenThread")
        try:
            if api.ResumeThread(thread) != 1:
                raise OSError("ResumeThread did not release exactly one suspension")
        finally:
            close_job(thread, kernel=api)
    finally:
        close_job(snapshot, kernel=api)


def job_pids(job: int, *, kernel: Kernel | None = None) -> tuple[int, ...]:
    api = kernel or kernel32()
    capacity = 64
    for _ in range(12):
        data = ctypes.create_string_buffer(8 + capacity * ctypes.sizeof(ctypes.c_size_t))
        ok = api.QueryInformationJobObject(job, 3, data, ctypes.sizeof(data), None)
        assigned, count = (_DWORD * 2).from_buffer(data)
        if ok and count >= assigned and count <= capacity:
            return tuple((ctypes.c_size_t * count).from_buffer(data, 8))
        if not ok and api.GetLastError() != 234:  # ERROR_MORE_DATA
            raise _failure(api, "QueryInformationJobObject")
        capacity = max(capacity * 2, assigned)
    raise OSError("job membership did not stabilize")


def terminate_job(job: int, *, kernel: Kernel | None = None) -> None:
    api = kernel or kernel32()
    if not api.TerminateJobObject(job, NT_KILL_CODE):
        raise _failure(api, "TerminateJobObject")


def terminate_suspended(
    pid: int, *, process_handle: int | None = None, kernel: Kernel | None = None
) -> None:
    api = kernel or kernel32()
    process = process_handle or api.OpenProcess(1, False, pid)
    if not process:
        raise _failure(api, "OpenProcess for termination")
    try:
        if not api.TerminateProcess(process, NT_KILL_CODE):
            raise _failure(api, "TerminateProcess")
    finally:
        if process_handle is None:
            close_job(process, kernel=api)


_lifetime_job: int | None = None
_lifetime_lock = threading.Lock()


def ensure_lifetime_job() -> None:
    """One deliberately unclosed handle, retained until OS process teardown, never returned."""
    global _lifetime_job
    with _lifetime_lock:
        if _lifetime_job is None:
            job = create_kill_job()
            api = kernel32()
            if not api.AssignProcessToJobObject(job, -1):  # GetCurrentProcess pseudo-handle.
                error = _failure(api, "server lifetime job assignment")
                close_job(job)
                raise error
            _lifetime_job = job
