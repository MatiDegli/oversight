"""Real, temporary boundary inputs; no production preparation or supervisor imports."""

import asyncio
import json
import os
import shutil
import struct
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oversight.workspace.preview_boundary import BoundaryConfig, ReadOnlyMount
from oversight.workspace.preview_boundary_entry import (
    SYSTEM_DIRECTORIES,
    SYSTEM_FILES,
    manifest_digest,
)


@dataclass(frozen=True)
class Inputs:
    tree: Path
    state: Path
    scratch: Path
    readonly: tuple[ReadOnlyMount, ...]
    exclusions: tuple[Path, ...]


def prepared(directory: Path) -> Inputs:
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("tree", "state", "scratch", "python/bin", "python/lib", "deps"):
        (directory / name).mkdir(parents=True, exist_ok=True)
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    shutil.copy2(Path(sys.base_prefix) / "bin" / version, directory / "python/bin" / version)
    (directory / "python/bin/python").symlink_to(version)
    shutil.copytree(
        sysconfig.get_path("stdlib"),
        directory / "python/lib" / version,
        ignore=shutil.ignore_patterns("__pycache__", "config-*", "sitecustomize.py"),
        symlinks=True,
    )
    # Standalone builds resolve libpython through $ORIGIN/../lib inside the boundary.
    for library in Path(sysconfig.get_config_var("LIBDIR")).glob(f"lib{version}*.so*"):
        shutil.copy2(library, directory / "python/lib" / library.name)
    (directory / "deps/verified.txt").write_text("temporary dependency fixture\n")
    mounts = [ReadOnlyMount(directory / "python", "/opt/python", "python", "")]
    mounts += [ReadOnlyMount(directory / "deps", "/opt/deps", "dependencies", "")]
    # These three executable fixtures certify mounting, never an actual uv/node/pnpm build.
    for role in ("uv", "node", "pnpm"):
        path = directory / role
        path.mkdir()
        shutil.copy2("/usr/bin/true", path / role)
        mounts.append(ReadOnlyMount(path, "/opt/" + role, role, ""))
    for target in (*SYSTEM_FILES, *SYSTEM_DIRECTORIES):
        mounts.append(ReadOnlyMount(Path(target).resolve(strict=True), target, "system", ""))
    mounts = [ReadOnlyMount(m.source, m.target, m.role, manifest_digest(m.source)) for m in mounts]
    return Inputs(directory / "tree", directory / "state", directory / "scratch", tuple(mounts), ())


def config(code: str, **options: Any) -> BoundaryConfig:
    return BoundaryConfig(
        argv=("/opt/python/bin/python", "-I", "-S", "-c", code),
        launch_timeout_s=15,
        lifetime_s=30,
        **options,
    )


async def result(handle: Any) -> dict[str, Any]:
    await handle.wait()
    assert handle.returncode == 0, handle.stderr
    return json.loads(handle.stdout)


def fd_snapshot() -> set[int]:
    result: set[int] = set()
    for name in Path("/proc/self/fd").iterdir():
        try:
            os.fstat(int(name.name))
        except OSError:
            continue
        result.add(int(name.name))
    return result


def harmless_pe() -> bytes:
    """Valid x86-64 PE32+ console image: entry returns 0; no imports or host effects."""
    image = bytearray(1024)
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, 0x80)
    image[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", image, 0x84, 0x8664, 1, 0, 0, 0, 240, 0x22)
    optional = 0x98
    struct.pack_into(
        "<HBBIIIIIQ", image, optional, 0x20B, 14, 0, 512, 0, 0, 0x1000, 0x1000, 0x140000000
    )
    struct.pack_into("<II", image, optional + 32, 0x1000, 512)
    struct.pack_into("<HHHHHH", image, optional + 40, 6, 0, 0, 0, 6, 0)
    struct.pack_into("<IIIHH", image, optional + 56, 0x2000, 512, 0, 3, 0x160)
    struct.pack_into("<QQQQII", image, optional + 72, 0x100000, 0x1000, 0x100000, 0x1000, 0, 16)
    section = optional + 240
    image[section : section + 8] = b".text\0\0\0"
    struct.pack_into("<IIIIIIHHI", image, section + 8, 3, 0x1000, 512, 512, 0, 0, 0, 0, 0x60000020)
    image[512:515] = b"\x31\xc0\xc3"
    return bytes(image)


async def eventually_file(path: Path) -> str:
    async with asyncio.timeout(5):
        for _ in range(500):
            if await asyncio.to_thread(path.exists):
                return await asyncio.to_thread(path.read_text)
            await asyncio.sleep(0.01)
    raise AssertionError("missing child witness")


# Fork only inside the payload's already authenticated PID namespace, never in pytest.
FORK_EXEC = r"""
def execute(argv):
    child = os.fork()
    if child == 0:
        try:
            os.execv(argv[0], argv)
        except OSError as error:
            os._exit(min(255, error.errno + 100))
    return os.waitstatus_to_exitcode(os.waitpid(child, 0)[1])
"""

# Actual x86-64 io_uring socket/connect requests, with a linked connection timeout.
# When the inherited filter works, the attempt stops at io_uring_setup with EPERM.
IO_URING_VSOCK = r"""
import ctypes, errno, mmap, os, socket, struct

def uring_vsock():
    libc = ctypes.CDLL(None, use_errno=True)
    params = ctypes.create_string_buffer(120)
    ring = libc.syscall(425, 2, ctypes.byref(params))
    if ring < 0:
        return dict(setup=ctypes.get_errno())
    sq_entries, cq_entries = struct.unpack_from('II', params)
    sq_head, sq_tail, sq_mask, _, _, _, sq_array = struct.unpack_from('7I', params, 40)
    cq_head, cq_tail, cq_mask, _, _, cq_cqes = struct.unpack_from('6I', params, 80)
    with mmap.mmap(ring, sq_array + sq_entries * 4, offset=0) as sq, \
         mmap.mmap(ring, cq_cqes + cq_entries * 16, offset=0x8000000) as cq, \
         mmap.mmap(ring, sq_entries * 64, offset=0x10000000) as sqes:
        def word(memory, offset):
            return struct.unpack_from('I', memory, offset)[0]

        def submit(*requests):
            tail = word(sq, sq_tail)
            for index, request in enumerate(requests):
                slot = (tail + index) & word(sq, sq_mask)
                sqes[slot * 64:(slot + 1) * 64] = request
                struct.pack_into('I', sq, sq_array + slot * 4, slot)
            struct.pack_into('I', sq, sq_tail, tail + len(requests))
            rc = libc.syscall(426, ring, len(requests), len(requests), 1, 0, 0)
            assert rc == len(requests), (rc, ctypes.get_errno())
            head = word(cq, cq_head)
            assert word(cq, cq_tail) - head == len(requests)
            results = {}
            for index in range(len(requests)):
                slot = (head + index) & word(cq, cq_mask)
                token, value = struct.unpack_from('Qi', cq, cq_cqes + slot * 16)
                results[token] = value
            struct.pack_into('I', cq, cq_head, head + len(requests))
            return results[1]

        def request(opcode, fd, off=0, addr=0, length=0, flags=0, token=1):
            sqe = bytearray(64)
            struct.pack_into('BBHiQQIIQ', sqe, 0,
                opcode, flags, 0, fd, off, addr, length, 0, token)
            return sqe

        # IORING_OP_SOCKET takes domain in fd, type in off and protocol in len.
        fd = submit(request(45, socket.AF_VSOCK, socket.SOCK_STREAM | socket.SOCK_NONBLOCK))
        results = dict(setup=0, socket=fd)
        if fd >= 0:
            try:
                address = ctypes.create_string_buffer(struct.pack('HHIII', 40, 0, 1234, 2, 0))
                deadline = (ctypes.c_int64 * 2)(0, 250_000_000)
                results['connect'] = submit(
                    request(16, fd, ctypes.sizeof(address) - 1,
                        ctypes.addressof(address), flags=4),
                    request(15, -1, addr=ctypes.addressof(deadline), length=1, token=2))
            finally:
                os.close(fd)
    os.close(ring)
    return results
"""


def errno_tagged_entry(path: Path, trusted: Path) -> Path:
    """Tag emitted ERRNO actions so an ambient EPERM cannot mask a missing deny rule.

    Linux uses the newest filter's data for equal-priority ERRNO actions. Only the
    errno changes: the production BPF predicates and actual kernel calls stay intact.
    """
    path.write_text(
        """
import ctypes, errno
from pathlib import Path
namespace = {'__name__': 'trusted_entry'}
"""
        + f"trusted = Path({str(trusted)!r})\n"
        + "exec(compile(trusted.read_text(), str(trusted), 'exec'), namespace)\n"
        + """
libc = namespace['LIBC']
original = libc.prctl
class Filter(ctypes.Structure):
    _fields_ = [('code', ctypes.c_ushort), ('jt', ctypes.c_ubyte),
               ('jf', ctypes.c_ubyte), ('k', ctypes.c_uint)]
class Program(ctypes.Structure):
    _fields_ = [('length', ctypes.c_ushort), ('filters', ctypes.POINTER(Filter))]
def tagged_prctl(option, *args):
    if option == 22:
        program = ctypes.cast(args[1], ctypes.POINTER(Program)).contents
        for index in range(program.length):
            instruction = program.filters[index]
            if instruction.code == 0x06 and instruction.k & 0xffff0000 == 0x50000:
                instruction.k = 0x50000 | errno.ENOTRECOVERABLE
    return original(option, *args)
libc.prctl = tagged_prctl
namespace['main']()
"""
    )
    return path


NESTED = r"""
import ctypes, errno, json, os, pathlib, socket
libc = ctypes.CDLL(None, use_errno=True)
errors = {}
for name in ('/opt/deps', '/etc/hosts', '/'):
    rc = libc.mount(None, name.encode(), None, 4096 | 32, None)
    assert rc == -1, name
    errors[name] = ctypes.get_errno()
for name in ('/opt/deps/verified.txt', '/etc/hosts'):
    try: os.open(name, os.O_WRONLY)
    except OSError as e: errors[name] = e.errno
    else: raise AssertionError(name)
try:
    s = socket.socket(socket.AF_VSOCK)
    s.connect((2, 1234))
except OSError as e:
    errors['vsock'] = e.errno
else: raise AssertionError('vsock')
assert libc.prctl(38, 0, 0, 0, 0) == -1
assert ctypes.get_errno() == errno.EINVAL
pathlib.Path('/state/nested.json').write_text(json.dumps(dict(
    ids={n: os.readlink('/proc/self/ns/'+n) for n in ('user','net','pid','mnt')},
    errors=errors, pid=os.getpid(), status=pathlib.Path('/proc/self/status').read_text())))
"""

DESCENDANTS = r"""
import os, pathlib, signal, time
child = os.fork()
if child == 0:
    os.setsid()
    child = os.fork()
    if child:
        os._exit(0)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    pathlib.Path('/state/detached').write_text(str(os.getpid()))
    while True: time.sleep(1)
os.waitpid(child, 0)
pathlib.Path('/state/reaped').write_text('ready')
while True: time.sleep(1)
"""


def released_descendants(exit_code: int, wait_s: int = 20) -> str:
    return (
        DESCENDANTS.rsplit("while True: time.sleep(1)", 1)[0]
        + "signal.signal(signal.SIGALRM, lambda *_: os._exit(9))\n"
        + f"signal.alarm({wait_s})\n"
        + "while not pathlib.Path('/state/release').exists(): time.sleep(0.01)\n"
        + f"os._exit({exit_code})\n"
    )


def descendants(pid: int) -> list[int]:
    found: list[int] = []
    pending = [pid]
    while pending:
        parent = pending.pop()
        try:
            text = Path(f"/proc/{parent}/task/{parent}/children").read_text()
        except FileNotFoundError:
            continue
        children = [int(item) for item in text.split()]
        found.extend(children)
        pending.extend(children)
    return found


def all_reaped(pids: list[int]) -> bool:
    return all(not Path(f"/proc/{pid}").exists() for pid in pids)
