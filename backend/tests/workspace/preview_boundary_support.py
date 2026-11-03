"""Real, temporary boundary inputs; no production preparation or supervisor imports."""

import asyncio
import json
import os
import shutil
import struct
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
    shutil.copy2("/usr/bin/python3.12", directory / "python/bin/python3.12")
    (directory / "python/bin/python").symlink_to("python3.12")
    shutil.copytree(
        "/usr/lib/python3.12",
        directory / "python/lib/python3.12",
        ignore=shutil.ignore_patterns("__pycache__", "config-*", "sitecustomize.py"),
        symlinks=True,
    )
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
while True: time.sleep(1)
"""


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
