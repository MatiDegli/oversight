"""Typed OS boundaries for runtime ownership and native Windows process lifetime."""

import ctypes
import errno
import logging
import ntpath
import os
import re
import stat
import sys
import threading
import time
from collections.abc import Callable, Sequence
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
                os.close(fd)
                raise WorkspaceBusy(
                    "another Oversight server is using this runtime directory"
                ) from None
    except BaseException:
        if nt:
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
    QueryDosDeviceW: Callable[..., int]
    GetVolumeInformationW: Callable[..., int]
    GetDriveTypeW: Callable[..., int]


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
        "GetDriveTypeW": (_DWORD, [ctypes.c_wchar_p]),
        "QueryDosDeviceW": (_DWORD, [ctypes.c_wchar_p, ctypes.c_wchar_p, _DWORD]),
        "GetVolumeInformationW": (
            _DWORD,
            [
                ctypes.c_wchar_p,
                ctypes.c_wchar_p,
                _DWORD,
                _HANDLE,
                _HANDLE,
                _HANDLE,
                ctypes.c_wchar_p,
                _DWORD,
            ],
        ),
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
            if entry.size < 16:
                raise OSError("incomplete thread snapshot")
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


def is_link_like(entry: os.DirEntry[str] | os.stat_result, *, nt: bool | None = None) -> bool:
    windows = sys.platform == "win32" if nt is None else nt
    if hasattr(entry, "is_symlink"):
        child = cast(os.DirEntry[str], entry)
        if not windows:
            return child.is_symlink()
        if child.is_symlink() or getattr(child, "is_junction", lambda: False)():
            return True
        info = child.stat(follow_symlinks=False)
    else:
        info = cast(os.stat_result, entry)
    return stat.S_ISLNK(info.st_mode) or (
        windows and bool(getattr(info, "st_file_attributes", 0) & 0x400)
    )


def retry_nt(operation: Callable[[], None], *, nt: bool) -> None:
    first: OSError | None = None
    for delay in (0.02, 0.04, 0.08, 0.16, 0.32, 0.32, None):
        try:
            operation()
            return
        except OSError as error:
            if not nt or getattr(error, "winerror", None) not in {5, 32, 33}:
                raise
            first = first or error
            if delay is None:
                raise first from None
            time.sleep(delay)


def replace_retry(source: Path, destination: Path, *, nt: bool | None = None) -> None:
    retry_nt(
        lambda: os.replace(source, destination), nt=sys.platform == "win32" if nt is None else nt
    )


def unlink_retry(path: Path, *, missing_ok: bool = False, nt: bool | None = None) -> None:
    retry_nt(
        lambda: path.unlink(missing_ok=missing_ok), nt=sys.platform == "win32" if nt is None else nt
    )


def executable(name: str, *, path: str | None = None) -> str:
    if sys.platform != "win32":
        return name
    # shutil.which on Windows can implicitly search cwd, even with an explicit PATH.
    for directory in (os.environ.get("PATH", "") if path is None else path).split(os.pathsep):
        directory = directory.strip('"')
        if not os.path.isabs(directory):
            continue
        candidate = Path(directory) / (name + ".exe")
        if candidate.is_file():
            return str(candidate.absolute())
    raise FileNotFoundError(f"{name}.exe is required on an explicit absolute PATH")


def verify_sentinel(path: Path, fd: int) -> None:
    opened, current = os.fstat(fd), path.lstat()
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_nlink != 1
        or is_link_like(current)
        or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise WorkspaceBusy("runtime lock sentinel changed or is not a plain file")


def volume_info(drive: str, *, kernel: Kernel | None = None) -> tuple[int, str, str]:
    api = kernel or kernel32()
    device, filesystem = ctypes.create_unicode_buffer(32768), ctypes.create_unicode_buffer(32)
    if not api.QueryDosDeviceW(drive, device, len(device)):
        raise _failure(api, "QueryDosDeviceW")
    if not api.GetVolumeInformationW(
        drive + "\\", None, 0, None, None, None, filesystem, len(filesystem)
    ):
        raise _failure(api, "GetVolumeInformationW")
    return api.GetDriveTypeW(drive + "\\"), filesystem.value, device.value


def synced_roots() -> tuple[str, ...]:
    roots = [
        value
        for key, value in os.environ.items()
        if key.casefold() in {"onedrive", "onedriveconsumer", "onedrivecommercial"}
    ]
    if sys.platform == "win32":
        import winreg

        # Windows registers cloud sync roots, including custom OneDrive folder names.
        location = r"Software\Microsoft\Windows\CurrentVersion\Explorer\SyncRootManager"
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                with winreg.OpenKey(hive, location) as parent:
                    for index in range(winreg.QueryInfoKey(parent)[0]):
                        provider = winreg.EnumKey(parent, index)
                        try:
                            with winreg.OpenKey(parent, provider + r"\UserSyncRoots") as key:
                                for value in range(winreg.QueryInfoKey(key)[1]):
                                    _, folder, kind = winreg.EnumValue(key, value)
                                    if kind in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                                        roots.append(os.path.expandvars(str(folder)))
                        except FileNotFoundError:
                            continue
            except FileNotFoundError:
                pass
    return tuple(roots)


def _reserved_nt_segment(part: str) -> bool:
    stem = part.partition(".")[0].rstrip(" ").upper()
    return (
        stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
        or re.fullmatch(r"(?:COM|LPT)[1-9¹²³]", stem) is not None
    )


def preflight_placement(
    path: Path | str,
    *,
    nt: bool | None = None,
    lstat: Callable[[str], os.stat_result] = os.lstat,
    query: Callable[[str], tuple[int, str, str]] = volume_info,
    synced: Sequence[str] | None = None,
    userprofile: str | None = None,
) -> None:
    """Inspect lexical inputs and existing ancestors before resolve, creation or Git.

    Demo-only check, not an ACL or filesystem sandbox. No elevation or safe.directory bypass.
    """
    if not (sys.platform == "win32" if nt is None else nt):
        return
    raw = str(path)
    drive, tail = ntpath.splitdrive(raw)
    parts = tail.replace("/", "\\").split("\\")
    if (
        not re.fullmatch(r"[A-Za-z]:", drive)
        or not tail.startswith(("/", "\\"))
        or raw.startswith(("/", "\\"))
        or any(p in {".", ".."} or _reserved_nt_segment(p) for p in parts)
        or any(":" in p or "~" in p or p.endswith((".", " ")) for p in parts if p)
        or any(any(ord(c) < 32 or c in '<>"|?*' for c in p) for p in parts)
    ):
        raise OSError(
            "Windows placement requires a short absolute local path without aliases or ADS"
        )
    canonical = ntpath.normpath(raw)
    known = synced_roots() if synced is None else synced
    if any(
        p.casefold() == "onedrive" or p.casefold().startswith("onedrive -") for p in parts
    ) or any(
        ntpath.commonpath([canonical, root]).casefold() == ntpath.normpath(root).casefold()
        for root in known
        if ntpath.splitdrive(root)[0].casefold() == drive.casefold()
    ):
        raise OSError("Windows placement must be in nonsynced folders, outside cloud sync roots")
    kind, filesystem, device = query(drive)
    if (
        kind != 3
        or filesystem.upper() != "NTFS"
        or re.fullmatch(r"\\Device\\HarddiskVolume[0-9]+", device) is None
    ):
        raise OSError(
            "Windows placement requires a fixed local NTFS drive, without SUBST or mappings"
        )
    current = drive + "\\"
    for part in ["", *(p for p in parts if p)]:
        current = ntpath.join(current, part)
        try:
            info = lstat(current)
        except FileNotFoundError:
            break
        if is_link_like(info, nt=True) or getattr(info, "st_file_attributes", 0) & (
            0x1000 | 0x40000 | 0x400000
        ):
            raise OSError("Windows placement has a reparse, offline or recall ancestor")
        if not stat.S_ISDIR(info.st_mode):
            raise OSError("Windows placement ancestors must be plain directories")
    profile = userprofile if userprofile is not None else os.environ.get("USERPROFILE", "")
    if (
        not profile
        or ntpath.splitdrive(profile)[0].casefold() != drive.casefold()
        or (
            ntpath.commonpath([canonical, profile]).casefold()
            != ntpath.normpath(profile).casefold()
        )
    ):
        logging.getLogger(__name__).warning(
            "Windows placement is outside USERPROFILE; "
            "use a short private local NTFS nonsynced folder"
        )
