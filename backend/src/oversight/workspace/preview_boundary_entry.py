"""Trusted Linux entry, executed by absolute path with -I -S, before any tree code.

Only this child forks. U0 constructs the root and owns N0; U1 inherits locked mounts.
No product imports, shell, host listener, credentials, or worktree-supplied bootstrap code.
"""

import ctypes
import errno
import fcntl
import hashlib
import json
import os
import platform
import select
import signal
import stat
import struct
import sys
import time
from pathlib import Path
from typing import Any

# The exact supported system manifest (glibc x86-64). Other layouts fail closed.
SYSTEM_FILES = (
    "/usr/bin/unshare",
    "/usr/bin/env",
    "/usr/bin/dash",
    "/usr/bin/sh",
    "/usr/bin/git",
    "/lib64/ld-linux-x86-64.so.2",
    *(
        "/usr/lib/x86_64-linux-gnu/" + name
        for name in (
            "ld-linux-x86-64.so.2",
            "libc.so.6",
            "libm.so.6",
            "libz.so.1",
            "libexpat.so.1",
            "libffi.so.8",
            "libssl.so.3",
            "libcrypto.so.3",
            "libbz2.so.1.0",
            "liblzma.so.5",
            "libsqlite3.so.0",
            "libreadline.so.8",
            "libtinfo.so.6",
            "libnss_dns.so.2",
            "libnss_files.so.2",
            "libpthread.so.0",
            "libdl.so.2",
            "librt.so.1",
            "libuuid.so.1",
            "libutil.so.1",
            "libselinux.so.1",
            "libpcre2-8.so.0",
            "libstdc++.so.6",
            "libgcc_s.so.1",
            "libresolv.so.2",
        )
    ),
    "/etc/nsswitch.conf",
    "/etc/ld.so.cache",
    "/etc/hosts",
    "/etc/localtime",
    "/usr/share/zoneinfo/Etc/UTC",
)
SYSTEM_DIRECTORIES = ("/usr/lib/locale/C.utf8", "/usr/share/git-core/templates")
TOOL_TARGETS = {
    "python": "/opt/python",
    "uv": "/opt/uv",
    "node": "/opt/node",
    "pnpm": "/opt/pnpm",
    "dependencies": "/opt/deps",
}
USER, NET, MOUNT, PID = 0x10000000, 0x40000000, 0x20000, 0x20000000
BIND, REC, PRIVATE, REMOUNT, RDONLY = 4096, 16384, 1 << 18, 32, 1
NOSUID, NODEV, NOEXEC = 2, 4, 8
NOATIME, NODIRATIME, RELATIME = 1024, 2048, 1 << 21
STRICTATIME = 1 << 24
LIBC = ctypes.CDLL(None, use_errno=True)


def checked(value: int, operation: str) -> int:
    if value < 0:
        raise OSError(ctypes.get_errno(), operation)
    return value


def mount(source: str | None, target: str, kind: str | None, flags: int, data: str = "") -> None:
    checked(
        LIBC.mount(
            source.encode() if source is not None else None,
            target.encode(),
            kind.encode() if kind is not None else None,
            ctypes.c_ulong(flags),
            data.encode(),
        ),
        "mount " + target,
    )


def manifest_digest(source: Path) -> str:
    """Hash an exact no-follow inventory, including modes, links, names and file bytes.

    No secret or symlink target is read. Special, privileged and multiply linked files
    are refused; prepared distributions must use private regular copies.
    """
    digest = hashlib.sha256()
    pending = [(source, ".")]
    while pending:
        path, relative = pending.pop()
        before = path.lstat()
        if before.st_mode & (stat.S_ISUID | stat.S_ISGID):
            raise ValueError("privileged manifest entry")
        record: list[Any] = [relative, stat.S_IMODE(before.st_mode)]
        if stat.S_ISLNK(before.st_mode):
            record += ["link", os.readlink(path)]
        elif stat.S_ISDIR(before.st_mode):
            record += ["directory"]
            pending.extend(
                (p, p.name if relative == "." else relative + "/" + p.name)
                for p in sorted(path.iterdir(), reverse=True)
            )
        elif stat.S_ISREG(before.st_mode) and before.st_nlink == 1:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
            try:
                opened = os.fstat(fd)
                if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise ValueError("manifest entry changed")
                content = hashlib.sha256()
                while block := os.read(fd, 1024 * 1024):
                    content.update(block)
                record += ["file", before.st_size, content.hexdigest()]
                try:
                    capabilities = os.getxattr(fd, "security.capability")
                except OSError as error:
                    if error.errno not in (errno.ENODATA, errno.ENOTSUP):
                        raise
                else:
                    if capabilities:
                        raise ValueError("privileged manifest entry")
            finally:
                os.close(fd)
        else:
            raise ValueError("unsafe manifest entry")
        after = path.lstat()
        if (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_nlink,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_nlink,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise ValueError("manifest entry changed")
        digest.update(json.dumps(record, ensure_ascii=True, separators=(",", ":")).encode())
        digest.update(b"\n")
    return digest.hexdigest()


def parent_death(parent: int) -> None:
    checked(LIBC.prctl(1, signal.SIGKILL, 0, 0, 0), "PR_SET_PDEATHSIG")
    if os.getppid() != parent:
        raise RuntimeError("owner disappeared")


def enter_user(procfd: int | None = None, mapped_id: int = 0) -> None:
    uid, gid = os.getuid(), os.getgid()
    checked(LIBC.unshare(USER), "unshare user")
    for name, value in (
        ("setgroups", "deny"),
        ("uid_map", f"{mapped_id} {uid} 1\n"),
        ("gid_map", f"{mapped_id} {gid} 1\n"),
    ):
        path = f"{os.getpid()}/{name}" if procfd is not None else "/proc/self/" + name
        fd = os.open(path, os.O_WRONLY | os.O_CLOEXEC, dir_fd=procfd)
        try:
            os.write(fd, value.encode())
        finally:
            os.close(fd)
    # Permit same-user external identity inspection; payload cannot see the observer PID.
    checked(LIBC.prctl(4, 1, 0, 0, 0), "PR_SET_DUMPABLE")


def loopback() -> None:
    import socket

    with socket.socket() as control:
        request = struct.pack("16sH22x", b"lo", 0)
        flags = struct.unpack("16sH22x", fcntl.ioctl(control, 0x8913, request))[1]
        fcntl.ioctl(control, 0x8914, struct.pack("16sH22x", b"lo", flags | 1))


def close_except(keep: set[int], procfd: int | None = None) -> None:
    path = "self/fd" if procfd is not None else "/proc/self/fd"
    directory = os.open(path, os.O_RDONLY | os.O_DIRECTORY, dir_fd=procfd)
    try:
        names = os.listdir(directory)
    finally:
        os.close(directory)
    for name in names:
        fd = int(name)
        if fd > 2 and fd not in keep:
            try:
                os.close(fd)
            except OSError as error:
                if error.errno != errno.EBADF:
                    raise


def bind(source: str, destination: Path, *, readonly: bool, device: bool = False) -> None:
    if Path(source).is_dir():
        destination.mkdir(parents=True, exist_ok=True)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.touch()
    # A less-privileged mount namespace cannot split an inherited locked mount tree.
    # Recursive bind works there; refuse source submounts rather than importing them.
    resolved = Path(source).resolve(strict=True)
    for row in Path("/proc/self/mountinfo").read_text().splitlines():
        escaped = row.split()[4]
        for code, character in (("\\040", " "), ("\\011", "\t"), ("\\012", "\n"), ("\\134", "\\")):
            escaped = escaped.replace(code, character)
        point = Path(escaped)
        if point != resolved and point.is_relative_to(resolved):
            raise ValueError("submount in boundary input")
    mount(source, str(destination), None, BIND | REC)
    flags = BIND | REMOUNT | NOSUID
    # Inspect the inherited mount itself: user namespaces lock its source restrictions.
    inherited = os.statvfs(destination).f_flag
    for source_flag, mount_flag in (
        (os.ST_RDONLY, RDONLY),
        (os.ST_NOSUID, NOSUID),
        (os.ST_NODEV, NODEV),
        (os.ST_NOEXEC, NOEXEC),
        (os.ST_NOATIME, NOATIME),
        (os.ST_NODIRATIME, NODIRATIME),
        (os.ST_RELATIME, RELATIME),
    ):
        if inherited & source_flag:
            flags |= mount_flag
    # An explicit nodiratime without noatime/relatime must not default to relatime.
    if not inherited & (os.ST_NOATIME | os.ST_RELATIME):
        flags |= STRICTATIME
    if not device:
        flags |= NODEV
    if readonly:
        flags |= RDONLY
    mount(None, str(destination), None, flags)


def install_root(config: dict[str, Any]) -> None:
    root = Path(config["root"])
    mount(None, "/", None, REC | PRIVATE)
    mount("tmpfs", str(root), "tmpfs", NOSUID | NODEV, "mode=755,size=32m")
    for name in (
        "T",
        "state",
        "tmp",
        "dev",
        "dev/shm",
        "proc",
        "opt",
        "usr/bin",
        "usr/lib",
        "usr/share",
        "lib",
        "lib64",
        "etc",
        ".oldroot",
        ".binfmt",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / "bin").symlink_to("usr/bin")
    (root / "lib").rmdir()
    (root / "lib").symlink_to("usr/lib")
    (root / "usr/lib64").symlink_to("/lib64")
    # A new, disabled per-user-namespace registry shadows inherited WSL binfmt handlers.
    mount("binfmt_misc", str(root / ".binfmt"), "binfmt_misc", NOSUID | NODEV | NOEXEC)
    (root / ".binfmt/status").write_text("0")
    checked(LIBC.umount2(str(root / ".binfmt").encode(), 2), "detach binfmt registry")
    (root / ".binfmt").rmdir()
    bind(f"/proc/self/fd/{config['tree_fd']}", root / "T", readonly=False)
    bind(f"/proc/self/fd/{config['state_fd']}", root / "state", readonly=False)
    for item in config["mounts"]:
        source = f"/proc/self/fd/{item['fd']}"
        if manifest_digest(Path(source).resolve(strict=True)) != item["digest"]:
            raise ValueError("manifest changed before mount")
        bind(source, root / item["target"].lstrip("/"), readonly=True)
    for name in ("null", "zero", "random", "urandom"):
        bind("/dev/" + name, root / "dev" / name, readonly=True, device=True)
    for name, target in (
        ("fd", "/proc/self/fd"),
        ("stdin", "/proc/self/fd/0"),
        ("stdout", "/proc/self/fd/1"),
        ("stderr", "/proc/self/fd/2"),
    ):
        (root / "dev" / name).symlink_to(target)
    for name, mode in (("tmp", "1777"), ("dev/shm", "1777")):
        mount("tmpfs", str(root / name), "tmpfs", NOSUID | NODEV, "mode=" + mode)
    os.chdir(root)
    checked(LIBC.syscall(155, b".", b".oldroot"), "pivot_root")
    os.chdir("/")
    checked(LIBC.umount2(b"/.oldroot", 2), "detach old root")
    Path("/.oldroot").rmdir()
    mount(None, "/", None, REMOUNT | RDONLY | NOSUID | NODEV)
    # Resolve interpreter links entirely inside the detached root, before any T code.
    for item in config["mounts"]:
        path = Path(item["target"])
        if path.is_dir():
            for directory, dirs, files in os.walk(path, followlinks=False):
                for name in (*dirs, *files):
                    entry = Path(directory) / name
                    if entry.is_symlink():
                        approved_link(entry.resolve(strict=True))
    for path in ("/opt/python/bin/python", "/opt/uv/uv", "/opt/node/node", "/opt/pnpm/pnpm"):
        if not os.access(path, os.X_OK) or not Path(path).resolve(strict=True).is_file():
            raise ValueError("missing prepared executable")
        approved_link(Path(path).resolve(strict=True))
        with Path(path).open("rb") as file:
            first = file.read(256)
        if first.startswith(b"#!"):
            interpreter = first.split(b"\n", 1)[0][2:].strip().split()[0].decode()
            if not interpreter.startswith("/") or not os.access(interpreter, os.X_OK):
                raise ValueError("unresolved interpreter")
            approved_link(Path(interpreter).resolve(strict=True))


def approved_link(path: Path) -> None:
    if str(path) not in SYSTEM_FILES and not any(
        path.is_relative_to(target) for target in (*TOOL_TARGETS.values(), *SYSTEM_DIRECTORIES)
    ):
        raise ValueError("prepared link resolves outside read-only manifest")


def deny_vsock() -> None:
    """Deny direct/io_uring vsock and foreign ABIs; preserve nested user unshare."""
    if platform.machine() != "x86_64":
        raise OSError(errno.ENOTSUP, "unsupported seccomp architecture")
    checked(LIBC.prctl(38, 1, 0, 0, 0), "PR_SET_NO_NEW_PRIVS")
    # Seccomp cannot inspect io_uring SQEs, whose socket/connect operations bypass
    # socket(2)'s domain check. Deny setup/enter/register before any descendant exists.
    # BPF: architecture, syscall number, then socket/socketpair domain argument.
    instructions = [
        (0x20, 0, 0, 4),
        (0x15, 1, 0, 0xC000003E),
        (0x06, 0, 0, 0x80000000),
        (0x20, 0, 0, 0),
        (0x45, 0, 1, 0x40000000),
        (0x06, 0, 0, 0x50000 | errno.EPERM),
        (0x15, 0, 1, 425),  # io_uring_setup
        (0x06, 0, 0, 0x50000 | errno.EPERM),
        (0x15, 0, 1, 426),  # io_uring_enter
        (0x06, 0, 0, 0x50000 | errno.EPERM),
        (0x15, 0, 1, 427),  # io_uring_register
        (0x06, 0, 0, 0x50000 | errno.EPERM),
        (0x15, 2, 0, 41),
        (0x15, 1, 0, 53),
        (0x06, 0, 0, 0x7FFF0000),
        (0x20, 0, 0, 16),
        (0x15, 0, 1, 40),
        (0x06, 0, 0, 0x50000 | errno.EPERM),
        (0x06, 0, 0, 0x7FFF0000),
    ]

    class Filter(ctypes.Structure):
        _fields_ = [
            ("code", ctypes.c_ushort),
            ("jt", ctypes.c_ubyte),
            ("jf", ctypes.c_ubyte),
            ("k", ctypes.c_uint),
        ]

    class Program(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ushort), ("filters", ctypes.POINTER(Filter))]

    filters = (Filter * len(instructions))(*(Filter(*i) for i in instructions))
    program = Program(len(instructions), filters)
    checked(LIBC.prctl(22, 2, ctypes.byref(program), 0, 0), "install seccomp")


def drop_capabilities() -> None:
    checked(LIBC.prctl(28, 3, 0, 0, 0), "lock NOROOT securebit")
    for capability in range(64):
        value = LIBC.prctl(24, capability, 0, 0, 0)
        if value < 0 and ctypes.get_errno() != errno.EINVAL:
            checked(value, "drop capability bounding set")
    checked(LIBC.prctl(47, 4, 0, 0, 0), "clear ambient capabilities")
    header = (ctypes.c_uint * 2)(0x20080522, 0)
    data = (ctypes.c_uint * 6)()
    checked(LIBC.capset(ctypes.byref(header), ctypes.byref(data)), "drop capabilities")


def init(config: dict[str, Any], report: int, ack: int, outer: dict[str, str]) -> None:
    assert os.getpid() == 1
    mount("proc", "/proc", "proc", NOSUID | NODEV | NOEXEC)
    close_except({report, ack})
    drop_capabilities()
    names = {name: os.readlink("/proc/self/ns/" + name) for name in ("user", "net", "pid", "mnt")}
    evidence = dict(
        nonce=config["nonce"], pid=1, outer=outer, inner=names, procdev=os.stat("/proc").st_dev
    )
    os.write(report, json.dumps(evidence, separators=(",", ":")).encode() + b"\n")
    os.close(report)
    authorized = os.read(ack, 2)
    os.close(ack)
    if authorized != b"1":
        raise RuntimeError("entry not authorized")
    if time.monotonic() >= config["deadline"]:
        raise RuntimeError("boundary deadline expired")
    os.chdir(config["cwd"])
    os.execve(config["argv"][0], config["argv"], config["env"])


def reap(child: int, owner: int | None, deadline: float) -> int:
    while True:
        pid, status = os.waitpid(child, os.WNOHANG)
        if pid:
            return os.waitstatus_to_exitcode(status)
        if time.monotonic() >= deadline or (
            owner is not None and select.select([owner], [], [], 0)[0] and not os.read(owner, 1)
        ):
            os.kill(child, signal.SIGKILL)
        select.select([], [], [], 0.01)


def descendant(
    config: dict[str, Any], report: int, ack: int, outer: dict[str, str], procfd: int
) -> None:
    # The host proc descriptor exists solely in trusted setup, never in the payload.
    parent = os.getppid()
    # UID zero in U1 would make nested root mappings require CAP_SETFCAP in U1.
    # A nonzero mapped identity permits nesting while dropping every U1 capability.
    enter_user(procfd, 1000)
    parent_death(parent)
    checked(LIBC.unshare(MOUNT | PID), "unshare descendant mount/pid")
    parentfd = checked(LIBC.syscall(434, os.getpid(), 0), "pin setup parent")
    child = os.fork()
    if child == 0:
        # Namespace PID 1 observes its parent outside its PID namespace as zero.
        parent_death(0)
        if select.select([parentfd], [], [], 0)[0]:
            os._exit(125)
        os.close(parentfd)
        init(config, report, ack, outer)
        os._exit(125)
    os.close(report)
    os.close(ack)
    os.close(parentfd)
    os.close(procfd)
    code = reap(child, None, config["deadline"])
    os._exit(code if code >= 0 else 128 - code)


def main() -> None:
    config_fd, report, ack, owner, parent = map(int, sys.argv[1:])
    parent_death(parent)
    with os.fdopen(config_fd, "rb") as file:
        config = json.loads(file.read(1024 * 1024))
    checked(LIBC.prctl(38, 1, 0, 0, 0), "PR_SET_NO_NEW_PRIVS")
    deny_vsock()
    enter_user()
    parent_death(parent)
    checked(LIBC.unshare(NET | MOUNT), "unshare outer network/mount")
    # Reopen pinned inodes in the cloned mount namespace: a bind from an fd whose
    # mount still belongs to the parent's namespace is refused by the kernel.
    for item in [config, *config["mounts"]]:
        for key in ("tree_fd", "state_fd") if item is config else ("fd",):
            old = item[key]
            info = os.fstat(old)
            new = os.open(
                os.readlink(f"/proc/self/fd/{old}"), os.O_PATH | os.O_CLOEXEC | os.O_NOFOLLOW
            )
            other = os.fstat(new)
            if (info.st_dev, info.st_ino, info.st_mode) != (
                other.st_dev,
                other.st_ino,
                other.st_mode,
            ):
                raise ValueError("pinned mount source changed")
            os.close(old)
            item[key] = new
    loopback()
    outer = {name: os.readlink("/proc/self/ns/" + name) for name in ("user", "net", "pid", "mnt")}
    procfd = os.open("/proc", os.O_PATH | os.O_CLOEXEC)
    install_root(config)
    close_except({report, ack, owner, procfd}, procfd)
    parent = os.getpid()
    child = os.fork()
    if child == 0:
        parent_death(parent)
        os.close(owner)
        descendant(config, report, ack, outer, procfd)
        os._exit(125)
    os.close(report)
    os.close(ack)
    os.close(procfd)
    code = reap(child, owner, config["deadline"])
    os.close(owner)
    sys.exit(code if code >= 0 else 128 - code)


if __name__ == "__main__":
    main()
