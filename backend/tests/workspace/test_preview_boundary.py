"""Behavioral Linux gates. Namespace refusal is a failure, never a qualifying skip."""

import asyncio
import dataclasses
import errno
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from oversight.workspace import preview_boundary as boundary
from oversight.workspace import preview_boundary_entry as entry
from oversight.workspace.runner import child_env

from .preview_boundary_support import Inputs, config, prepared, result

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("readonly,device", [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize(
    "source_flags,mount_flags",
    [
        (0, 1 << 24),
        (os.ST_RDONLY, 1 | (1 << 24)),
        (os.ST_NOSUID, 2 | (1 << 24)),
        (os.ST_NODEV, 4 | (1 << 24)),
        (os.ST_NOEXEC, 8 | (1 << 24)),
        (os.ST_NOATIME, 1024),
        (os.ST_NODIRATIME, 2048 | (1 << 24)),
        (os.ST_RELATIME, 1 << 21),
        (os.ST_NOATIME | os.ST_NODIRATIME, 1024 | 2048),
        (os.ST_NODIRATIME | os.ST_RELATIME, 2048 | (1 << 21)),
        (os.ST_NOSUID | os.ST_NOEXEC | os.ST_RELATIME, 2 | 8 | (1 << 21)),
    ],
)
async def test_bind_preserves_source_mount_flags(
    tmp_path, monkeypatch, readonly, device, source_flags, mount_flags
):
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.touch()
    calls = []
    monkeypatch.setattr(entry, "mount", lambda *args: calls.append(args))

    def statvfs(path):
        assert path == destination
        assert calls == [(str(source), str(destination), None, entry.BIND | entry.REC)]
        return SimpleNamespace(f_flag=source_flags)

    monkeypatch.setattr(os, "statvfs", statvfs)
    entry.bind(str(source), destination, readonly=readonly, device=device)
    added = entry.NOSUID | (entry.RDONLY if readonly else 0) | (0 if device else entry.NODEV)
    assert calls[-1] == (
        None,
        str(destination),
        None,
        entry.BIND | entry.REMOUNT | added | mount_flags,
    )


@pytest.fixture(scope="module")
def roots(tmp_path_factory: pytest.TempPathFactory) -> Inputs:
    inputs = prepared(tmp_path_factory.mktemp("boundary"))
    print("temporary manifests", [(m.role, m.target, m.digest) for m in inputs.readonly])
    return inputs


async def test_boundary_root_allowlist_and_locked_mounts(roots: Inputs) -> None:
    handle = await boundary.launch_boundary(
        config(r"""
import ctypes, json, os, pathlib
libc = ctypes.CDLL(None, use_errno=True)
errors = {}
for name in ('/etc/hosts', '/opt/deps/verified.txt', '/opt/python/bin/python'):
    try:
        fd = os.open(name, os.O_WRONLY)
    except OSError as e:
        errors[name] = e.errno
    else:
        os.close(fd)
        raise AssertionError(name)
assert libc.mount(None, b'/opt/deps', None, 32 | 4096, None) == -1
errors['remount'] = ctypes.get_errno()
for name in ('/T/allowed', '/state/allowed', '/tmp/allowed', '/dev/shm/allowed'):
    pathlib.Path(name).write_text('yes')
assert not os.path.lexists('/.oldroot')
assert not os.path.lexists('/proc/self/root/.oldroot')
print(json.dumps(dict(errors=errors, root=sorted(os.listdir('/')),
    mounts=pathlib.Path('/proc/self/mountinfo').read_text())))
"""),
        roots,
    )
    data = await result(handle)
    assert set(data["errors"].values()) <= {errno.EROFS, errno.EPERM, errno.EACCES, errno.ETXTBSY}
    assert set(data["root"]) == {
        "T",
        "bin",
        "state",
        "tmp",
        "dev",
        "proc",
        "opt",
        "usr",
        "lib",
        "lib64",
        "etc",
    }
    assert " - proc proc " in data["mounts"]
    rows = [row.split() for row in data["mounts"].splitlines()]
    assert {row[4] for row in rows if "rw" in row[5].split(",")} == {
        "/T",
        "/state",
        "/tmp",
        "/dev/shm",
        "/proc",
    }
    assert not any(
        item.startswith(("shared:", "master:")) for row in rows for item in row[6 : row.index("-")]
    )
    bad = dataclasses.replace(
        roots,
        readonly=(dataclasses.replace(roots.readonly[0], digest="0" * 64), *roots.readonly[1:]),
    )
    with pytest.raises(boundary.BoundaryUnavailable, match="manifest"):
        await boundary.launch_boundary(config("raise AssertionError('executed')"), bad)


async def test_boundary_topology_and_authenticated_entry(
    roots: Inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    witnessed = {}
    original = boundary._Handle.authenticate

    def observe(self: Any, raw: bytes, nonce: str) -> None:
        original(self, raw, nonce)
        witnessed.update(
            processes={k: dataclasses.asdict(v) for k, v in self.processes.items()},
            namespaces={k: dataclasses.asdict(v) for k, v in self.identities.items()},
            pidfds=dict(self.pidfds),
            namespace_fds=dict(self.namespace_fds),
        )

    monkeypatch.setattr(boundary._Handle, "authenticate", observe)
    handle = await boundary.launch_boundary(
        config(r"""
import json, os, pathlib
fds = {}
for p in pathlib.Path('/proc/self/fd').iterdir():
    try: fds[p.name] = os.readlink(p)
    except OSError: pass
print(json.dumps(dict(pid=os.getpid(), fds=fds,
    status=pathlib.Path('/proc/self/status').read_text(),
    ids={n: os.readlink('/proc/self/ns/'+n) for n in ('user','net','pid','mnt')})))
"""),
        roots,
    )
    data = await result(handle)
    assert data["pid"] == 1
    assert set(data["fds"]) == {"0", "1", "2"}
    for cap in ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"):
        assert f"{cap}:\t0000000000000000" in data["status"]
    assert "NoNewPrivs:\t1" in data["status"]
    assert "Seccomp:\t2" in data["status"]
    assert handle.u0 != handle.u1
    assert handle.n0.inode == int(data["ids"]["net"].split("[")[1][:-1])
    assert handle.pid.inode == int(data["ids"]["pid"].split("[")[1][:-1])
    assert handle.mount.inode == int(data["ids"]["mnt"].split("[")[1][:-1])
    print("authenticated witnesses", witnessed, "payload descriptors", data["fds"])


async def test_boundary_p0_proc_anchor_and_descriptor_closure(
    roots: Inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fcntl

    original = boundary._Handle.authenticate
    witnessed = False

    def observe(self: Any, raw: bytes, nonce: str) -> None:
        nonlocal witnessed
        original(self, raw, nonce)
        assert set(self.processes) == {"outer", "setup", "init"}
        outer, setup, init = (self.processes[k].pid for k in ("outer", "setup", "init"))
        status = Path(f"/proc/{init}/status").read_text().splitlines()
        pids = next(r.split()[1:] for r in status if r.startswith("NSpid:"))
        assert len(pids) == 3 and pids[0] == str(init) and pids[2] == "1"
        assert int(pids[1]) > 1
        anchor = Path(f"/proc/{outer}/root/proc")
        assert (anchor / "1/status").read_text().split("NSpid:")[1].splitlines()[0].split() == ["1"]
        assert os.readlink(anchor / "1/ns/pid") == os.readlink(f"/proc/{setup}/ns/pid")
        assert os.readlink(anchor / f"{pids[1]}/ns/pid") == f"pid:[{self.pid.inode}]"
        fd = fcntl.ioctl(self.namespace_fds["p0"], 0xB701)
        try:
            assert boundary._ns(fd) == self.u0
        finally:
            os.close(fd)
        anchor_dev = os.stat(anchor).st_dev
        inner_dev = os.stat(f"/proc/{init}/root/proc").st_dev
        host_dev = os.stat("/proc").st_dev
        assert len({anchor_dev, inner_dev, host_dev}) == 3
        rows = [r.split() for r in Path(f"/proc/{init}/mountinfo").read_text().splitlines()]
        procs = [r for r in rows if r[r.index("-") + 1] == "proc"]
        assert len(procs) == 2
        assert {r[2] for r in procs} == {
            f"{os.major(dev)}:{os.minor(dev)}" for dev in (anchor_dev, inner_dev)
        }
        assert all(r[3:5] == ["/", "/proc"] for r in procs)
        assert all({"nosuid", "nodev", "noexec"} <= set(r[5].split(",")) for r in procs)
        for pid in (outer, setup, init):
            assert all(os.stat(fd).st_dev != host_dev for fd in Path(f"/proc/{pid}/fd").iterdir())
        witnessed = True

    monkeypatch.setattr(boundary._Handle, "authenticate", observe)
    handle = await boundary.launch_boundary(config("pass"), roots)
    await handle.wait()
    assert handle.returncode == 0 and witnessed


async def test_boundary_anchor_mount_failure_refuses_before_payload(
    roots: Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    modified = tmp_path / "entry.py"
    modified.write_text(
        "from pathlib import Path\n"
        "namespace = {'__name__': 'trusted_entry'}\n"
        f"exec(compile(Path({str(boundary.ENTRY)!r}).read_text(), 'entry', 'exec'), namespace)\n"
        "original = namespace['mount']\n"
        "def mount(source, target, kind, flags, data=''):\n"
        "    if kind == 'proc' and target != '/proc':\n"
        "        raise PermissionError(1, 'anchor mount denied')\n"
        "    return original(source, target, kind, flags, data)\n"
        "namespace['mount'] = mount\n"
        "namespace['main']()\n"
    )
    monkeypatch.setattr(boundary, "ENTRY", modified)
    marker = roots.state / "anchor-must-not-execute"
    with pytest.raises(boundary.BoundaryUnavailable, match="anchor mount denied"):
        await boundary.launch_boundary(
            config("from pathlib import Path; Path('/state/anchor-must-not-execute').touch()"),
            roots,
        )
    assert not marker.exists()


async def test_boundary_exclusions_aliases_and_canary(roots: Inputs, tmp_path: Path) -> None:
    import os

    parent = tmp_path / "parent"
    parent.mkdir()
    canary = parent / ".env"
    canary.write_text("CANARY_BOUNDARY_ONLY=not-a-credential\n")
    original = parent / "original"
    original.mkdir()
    (original / "private").write_text("original sentinel")
    (roots.tree / "absolute-canary").symlink_to(canary)
    (roots.tree / "relative-canary").symlink_to("../../" + str(canary).lstrip("/"))
    fd = os.open(canary, os.O_RDONLY)
    os.set_inheritable(fd, True)
    paths = [
        str(canary),
        str(original),
        str(parent),
        str(Path.home()),
        str(boundary.REPO_ROOT / ".env"),
        "/mnt",
        "/run/user/" + str(os.getuid()),
        "/run/WSL",
        "/init",
        "/T/absolute-canary",
        "/T/relative-canary",
        "/proc/self/root" + str(canary),
        f"/proc/{os.getpid()}/root" + str(canary),
        f"/proc/{os.getpid()}/fd/{fd}",
    ]
    try:
        handle = await boundary.launch_boundary(
            config(f"""
import ctypes, json, os
errors = {{}}
for name in {paths!r}:
    try: os.stat(name)
    except OSError as e: errors[name] = e.errno
    else: raise AssertionError(name)
try: os.fstat({fd})
except OSError as e: errors['inherited_fd'] = e.errno
else: raise AssertionError('inherited_fd')
libc=ctypes.CDLL(None,use_errno=True)
for key, call in [('chroot', lambda: libc.chroot(b'/')),
                  ('pivot', lambda: libc.syscall(155,b'/',b'/tmp')),
                  ('mount', lambda: libc.mount(b'/',b'/tmp',None,4096,None)),
                  ('detach', lambda: libc.umount2(b'/',2))]:
    assert call() == -1, key
    errors[key] = ctypes.get_errno()
print(json.dumps(errors))
"""),
            dataclasses.replace(roots, exclusions=(parent,)),
        )
        data = await result(handle)
    finally:
        os.close(fd)
        (roots.tree / "absolute-canary").unlink()
        (roots.tree / "relative-canary").unlink()
    assert data["inherited_fd"] == errno.EBADF
    assert set(data.values()) <= {
        errno.ENOENT,
        errno.ENOTDIR,
        errno.EBADF,
        errno.EPERM,
        errno.EACCES,
    }
    assert canary.read_text() == "CANARY_BOUNDARY_ONLY=not-a-credential\n"


async def test_boundary_network_denies_host_parent_and_provider(roots: Inputs) -> None:
    import socket

    loop = asyncio.get_running_loop()
    # DNS only on the host: never send HTTP, TLS, API credentials or a provider request.
    providers = {}
    for name in ("api.meta.ai", "inference.baseten.co"):
        addresses = await asyncio.wait_for(
            loop.getaddrinfo(name, 443, family=socket.AF_INET, type=socket.SOCK_STREAM), 10
        )
        providers[name] = addresses[0][4][0]
    host = socket.socket()
    host.bind(("127.0.0.1", 0))
    host.listen()
    port = host.getsockname()[1]
    try:
        handle = await boundary.launch_boundary(
            config(f"""
import errno, json, socket
results = {{}}
for label, address in [('parent', ('127.0.0.1',{port})),
                       ('parent_api', ('127.0.0.1',8710)),
                       ('outside', ('1.1.1.1',443)),
                       *[(name, (ip,443)) for name, ip in {providers!r}.items()]]:
    with socket.socket() as s:
        s.settimeout(1)
        error = s.connect_ex(address)
        assert error != 0, label
        results[label] = dict(address=address, error=error)
with socket.socket() as listener:
    listener.bind(('127.0.0.1',0)); listener.listen()
    with socket.create_connection(listener.getsockname(),1) as client:
        accepted, _ = listener.accept()
        with accepted:
            client.sendall(b'private-loopback')
            assert accepted.recv(64) == b'private-loopback'
print(json.dumps(results))
"""),
            roots,
        )
        data = await result(handle)
    finally:
        host.close()
    assert data["parent"]["error"] == errno.ECONNREFUSED
    assert data["parent_api"]["error"] == errno.ECONNREFUSED
    assert data["outside"]["error"] == errno.ENETUNREACH
    assert all(data[name]["error"] == errno.ENETUNREACH for name in providers)
    print("real connection results", data)


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("tagged", [False, True])
async def test_boundary_io_uring_vsock_refused(
    roots: Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nested: bool, tagged: bool
) -> None:
    from .preview_boundary_support import FORK_EXEC, IO_URING_VSOCK, errno_tagged_entry

    if tagged:
        monkeypatch.setattr(
            boundary, "ENTRY", errno_tagged_entry(tmp_path / "entry.py", boundary.ENTRY)
        )
    payload = (
        IO_URING_VSOCK
        + r"""
import json
# Ordinary socket creation and nested unshare must remain available.
with socket.socket(socket.AF_INET): pass
results = uring_vsock()
libc = ctypes.CDLL(None, use_errno=True)
for name, number in (('enter', 426), ('register', 427)):
    assert libc.syscall(number, -1, 0, 0, 0, 0, 0) == -1
    results[name] = ctypes.get_errno()
print(json.dumps(results), flush=True)
"""
    )
    if nested:
        payload = (
            "import os\n"
            + FORK_EXEC
            + f"code = execute(['/usr/bin/unshare', '--user', '--map-root-user', "
            f"'/opt/python/bin/python', '-I', '-S', '-c', {payload!r}])\n"
            "assert code == 0, code\n"
        )
    data = await result(await boundary.launch_boundary(config(payload), roots))
    denied = errno.ENOTRECOVERABLE if tagged else errno.EPERM
    assert data == dict(setup=denied, enter=denied, register=denied)


async def test_boundary_exclusion_alias_refused(roots: Inputs, tmp_path: Path) -> None:
    alias = tmp_path / "excluded-source-alias"
    alias.symlink_to(roots.readonly[0].source, target_is_directory=True)
    modified = dataclasses.replace(roots, exclusions=(alias,))
    with pytest.raises(boundary.BoundaryUnavailable, match="noncanonical boundary input"):
        handle = await boundary.launch_boundary(config("pass"), modified)
        await handle.stop_verified()


async def test_boundary_wsl_host_channels_refused(
    roots: Inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    from oversight.workspace.runner import run_process

    from .preview_boundary_support import FORK_EXEC, harmless_pe

    pe = roots.tree / "harmless.exe"
    pe.write_bytes(harmless_pe())
    pe.chmod(0o755)
    identified = await run_process(
        ["/usr/bin/file", str(pe)],
        cwd=roots.tree,
        env={"PATH": "/usr/bin:/bin"},
        timeout_s=5,
        output_cap=2000,
    )
    assert identified.returncode == 0 and "PE32+ executable (console) x86-64" in identified.stdout
    monkeypatch.setenv("WSL_INTEROP", "/run/WSL/never-pass")
    handle = await boundary.launch_boundary(
        config(
            FORK_EXEC
            + r"""
import ctypes, errno, json, os, socket
assert 'WSL_INTEROP' not in os.environ
libc=ctypes.CDLL(None,use_errno=True)
errors={}
for syscall in (41,53):
    pair=(ctypes.c_int*2)()
    rc=libc.syscall(syscall,40,1,0,ctypes.byref(pair))
    assert rc == -1
    errors[str(syscall)]=ctypes.get_errno()
try:
    with socket.socket(socket.AF_VSOCK) as s: s.connect((2,1234))
except OSError as e: errors['cid2']=e.errno
else: raise AssertionError('vsock CID 2')
errors['pe']=execute(['/T/harmless.exe'])
print(json.dumps(dict(errors=errors, env=dict(os.environ))))
"""
        ),
        roots,
    )
    data = await result(handle)
    assert data["errors"] == {
        "41": errno.EPERM,
        "53": errno.EPERM,
        "cid2": errno.EPERM,
        "pe": 100 + errno.ENOEXEC,
    }
    assert data["env"]["HOME"] == "/state/home"
    assert data["env"]["UV_OFFLINE"] == data["env"]["UV_NO_SYNC"] == "1"
    assert not any("KEY" in k or "WSL" in k for k in data["env"])
    print("PE witness", identified.stdout.strip(), "channel errors", data["errors"])


async def test_boundary_nested_unshare_under_filter(roots: Inputs) -> None:
    from .preview_boundary_support import FORK_EXEC, NESTED

    handle = await boundary.launch_boundary(
        config(
            FORK_EXEC
            + f"""
import json, os, pathlib
argv=['/usr/bin/unshare','--user','--map-root-user','--mount','--pid','--fork',
      '--kill-child=SIGKILL','--mount-proc','/opt/python/bin/python','-I','-S','-c',{NESTED!r}]
code=execute(argv)
assert code == 0, code
print(pathlib.Path('/state/nested.json').read_text())
"""
        ),
        roots,
    )
    data = await result(handle)
    assert data["pid"] == 1
    assert data["ids"]["net"] == f"net:[{handle.n0.inode}]"
    assert data["ids"]["user"] != f"user:[{handle.u1.inode}]"
    assert data["ids"]["pid"] != f"pid:[{handle.pid.inode}]"
    assert data["ids"]["mnt"] != f"mnt:[{handle.mount.inode}]"
    assert data["errors"]["vsock"] == errno.EPERM
    assert set(data["errors"].values()) <= {errno.EPERM, errno.EROFS, errno.EACCES}
    print("nested namespace witness", data)


@pytest.mark.parametrize(
    "mode", ["stop", "deadline", "owner_eof", "outer_death", "p0_death", "normal", "nonzero"]
)
async def test_boundary_stop_verified_kills_all_descendants(roots: Inputs, mode: str) -> None:
    import os
    import select
    import signal

    from oversight.workspace.isolation import open_pidfd

    from .preview_boundary_support import DESCENDANTS, all_reaped, descendants, eventually_file

    for name in ("detached", "reaped", "release"):
        await asyncio.to_thread((roots.state / name).unlink, missing_ok=True)
    settings = config(DESCENDANTS)
    if mode == "deadline":
        settings = dataclasses.replace(settings, lifetime_s=3)
    if mode in {"normal", "nonzero"}:
        settings = config(
            DESCENDANTS.rsplit("while True: time.sleep(1)", 1)[0]
            + "signal.alarm(20)\n"
            + "while not pathlib.Path('/state/release').exists(): time.sleep(0.01)\n"
            + f"os._exit({0 if mode == 'normal' else 7})\n"
        )
    handle = await boundary.launch_boundary(settings, roots)
    await eventually_file(roots.state / "detached")
    await eventually_file(roots.state / "reaped")
    identities = list(handle.processes.values())
    members = await asyncio.to_thread(descendants, handle.processes["init"].pid)
    assert members, "a detached descendant must exist before stop"
    observed = [identity.pid for identity in identities] + members
    independent = [open_pidfd(pid) for pid in observed]
    owned = [*handle.pidfds.values(), *handle.namespace_fds.values()]
    try:
        if mode in {"normal", "nonzero"}:
            await asyncio.to_thread((roots.state / "release").touch)
        if mode == "stop":
            await handle.stop_verified()
        elif mode == "owner_eof":
            owned_handle: Any = handle
            os.close(owned_handle.owner)
            owned_handle.owner = -1
        elif mode == "outer_death":
            os.kill(handle.processes["outer"].pid, signal.SIGKILL)
        elif mode == "p0_death":
            os.kill(handle.processes["setup"].pid, signal.SIGKILL)
        await handle.wait()
        assert handle.verified_dead
        for fd in independent:
            poll = select.poll()
            poll.register(fd, select.POLLIN)
            assert poll.poll(0), "independent pidfd did not witness death"
        for fd in owned:
            with pytest.raises(OSError) as error:
                os.fstat(fd)
            assert error.value.errno == errno.EBADF
        assert await asyncio.to_thread(all_reaped, observed)
        if mode in {"normal", "nonzero"}:
            assert handle.returncode == (0 if mode == "normal" else 7)
        await handle.stop_verified()
    finally:
        for fd in independent:
            os.close(fd)
        await handle.stop_verified()


@pytest.mark.parametrize(
    "tamper",
    ["nonce", "pid", "outer", "inner", "procdev", "missing", "duplicate", "ambiguous", "stale"],
)
async def test_invalid_evidence_refuses_before_payload(
    roots: Inputs, monkeypatch: pytest.MonkeyPatch, tamper: str
) -> None:
    import json

    marker = roots.state / "must-not-execute"
    original = boundary._Handle.authenticate

    def forged(self: Any, raw: bytes, nonce: str) -> None:
        value = json.loads(raw)
        if tamper == "missing":
            value.pop("procdev")
        elif tamper == "duplicate":
            raw = raw.rstrip()[:-1] + b',"nonce":"forged"}\n'
        elif tamper == "ambiguous":
            monkeypatch.setattr(boundary, "_child", lambda _: 0)
        elif tamper == "stale":
            identity = self.processes["outer"]
            self.processes["outer"] = dataclasses.replace(
                identity, start_time=identity.start_time - 1
            )
        else:
            value[tamper] = True if tamper == "pid" else "forged"
        if tamper != "duplicate":
            raw = json.dumps(value).encode() + b"\n"
        original(self, raw, nonce)

    monkeypatch.setattr(boundary._Handle, "authenticate", forged)
    with pytest.raises(boundary.BoundaryUnavailable):
        await boundary.launch_boundary(
            config("from pathlib import Path; Path('/state/must-not-execute').write_text('bad')"),
            roots,
        )
    assert not await asyncio.to_thread(marker.exists)


@pytest.mark.parametrize(
    "tamper,reason",
    [
        ("p0_owner", "incorrect namespace owner"),
        ("pid_parent", "incorrect namespace parent"),
        ("u1_parent", "incorrect namespace parent"),
        ("pid_depth", "unsafe boundary process state"),
        ("setup_pid", "unsafe boundary process state"),
        ("anchor_missing", "missing or unsafe boundary proc mounts"),
        ("anchor_host", "invalid boundary proc anchor"),
        ("anchor_pid", "invalid boundary proc anchor"),
        ("anchor_flags", "missing or unsafe boundary proc mounts"),
    ],
)
async def test_invalid_anchor_evidence_refuses_before_payload(
    roots: Inputs, monkeypatch: pytest.MonkeyPatch, tamper: str, reason: str
) -> None:
    import fcntl

    original = boundary._Handle.authenticate
    ioctl, read_text, readlink, stat = fcntl.ioctl, Path.read_text, os.readlink, os.stat
    marker = roots.state / "anchor-evidence-must-not-execute"

    def forged(self: Any, raw: bytes, nonce: str) -> None:
        outer = self.transport.get_pid()
        setup = boundary._child(outer)
        init = boundary._child(setup)
        anchor = Path(f"/proc/{outer}/root/proc")

        def wrong_ioctl(fd: int, request: int) -> int:
            key, operation, replacement = {
                "p0_owner": ("p0", 0xB701, "u1"),
                "pid_parent": ("pid", 0xB702, "pid"),
                "u1_parent": ("u1", 0xB702, "u1"),
            }.get(tamper, ("", 0, ""))
            if request == operation and fd == self.namespace_fds.get(key):
                return os.dup(self.namespace_fds[replacement])
            return ioctl(fd, request)

        def wrong_text(path: Path, *args: Any, **kwargs: Any) -> str:
            text = read_text(path, *args, **kwargs)
            if (tamper == "pid_depth" and path == Path(f"/proc/{init}/status")) or (
                tamper == "setup_pid" and path == Path(f"/proc/{setup}/status")
            ):
                text = "\n".join(
                    "NSpid:\t" + (f"{init}\t1" if tamper == "pid_depth" else f"{setup}\t2")
                    if row.startswith("NSpid:")
                    else row
                    for row in text.splitlines()
                )
            if path == Path(f"/proc/{init}/mountinfo"):
                rows = text.splitlines()
                if tamper == "anchor_missing":
                    rows.remove(next(r for r in rows if " - proc " in r))
                elif tamper == "anchor_flags":
                    rows = [r.replace("nosuid,nodev,noexec", "nodev,noexec") for r in rows]
                text = "\n".join(rows)
            return text

        def wrong_link(path: Any, *args: Any, **kwargs: Any) -> str:
            if tamper == "anchor_pid" and Path(path) == anchor / "1/ns/pid":
                return readlink("/proc/self/ns/pid")
            return readlink(path, *args, **kwargs)

        def wrong_stat(path: Any, *args: Any, **kwargs: Any) -> Any:
            if tamper == "anchor_host" and Path(path) == anchor:
                return stat("/proc")
            return stat(path, *args, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(fcntl, "ioctl", wrong_ioctl)
            patch.setattr(Path, "read_text", wrong_text)
            patch.setattr(os, "readlink", wrong_link)
            patch.setattr(os, "stat", wrong_stat)
            original(self, raw, nonce)

    monkeypatch.setattr(boundary._Handle, "authenticate", forged)
    with pytest.raises(boundary.BoundaryUnavailable, match=reason):
        await boundary.launch_boundary(
            config(
                "from pathlib import Path; Path('/state/anchor-evidence-must-not-execute').touch()"
            ),
            roots,
        )
    assert not marker.exists()


@pytest.mark.parametrize("mode", ["failure", "timeout", "cancel", "cancel_spawn"])
async def test_boundary_spawn_and_descriptor_cleanup(
    roots: Inputs, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:

    from .preview_boundary_support import all_reaped, fd_snapshot

    loop = asyncio.get_running_loop()
    actual = loop.subprocess_exec
    entered, release = asyncio.Event(), asyncio.Event()
    captured: list[int] = []
    launches: list[dict[str, Any]] = []
    before = fd_snapshot()

    async def spawn(factory: Any, *argv: str, **kwargs: Any) -> Any:
        launches.append(kwargs)
        assert argv[:3] == (sys.executable, "-I", "-S")
        assert argv[3] == str(boundary.ENTRY)
        assert kwargs["cwd"] == roots.scratch
        assert kwargs["env"] == child_env({"PATH": "/usr/bin:/bin"})
        assert kwargs["stdin"] == asyncio.subprocess.DEVNULL
        assert len(kwargs["pass_fds"]) == len(roots.readonly) + 6
        assert set(kwargs["env"]) <= {"PATH", "LANG", "LC_ALL", "TZ"}
        if mode == "failure":
            entered.set()
            raise OSError(errno.EAGAIN, "injected spawn failure")
        spawned = await actual(factory, *argv, **kwargs)
        captured.append(spawned[0].get_pid())
        entered.set()
        if mode == "cancel_spawn":
            await release.wait()
        return spawned

    monkeypatch.setattr(loop, "subprocess_exec", spawn)
    original_line = boundary._line

    async def delayed_line(fd: int) -> bytes:
        raw = await original_line(fd)
        if mode == "timeout":
            await asyncio.Event().wait()
        return raw

    monkeypatch.setattr(boundary, "_line", delayed_line)
    settings = dataclasses.replace(
        config("import time; time.sleep(20)"), launch_timeout_s=1 if mode == "timeout" else 15
    )
    task = asyncio.create_task(boundary.launch_boundary(settings, roots))
    await asyncio.wait_for(entered.wait(), 10)
    if mode.startswith("cancel"):
        task.cancel()
        task.cancel()
        release.set()
    with pytest.raises(
        asyncio.CancelledError if mode.startswith("cancel") else boundary.BoundaryUnavailable
    ):
        await task
    assert launches
    assert await asyncio.to_thread(all_reaped, captured)
    await asyncio.sleep(0)
    assert fd_snapshot() == before
    assert not await asyncio.to_thread(lambda: list(roots.scratch.iterdir()))


async def test_unverified_death_raises(roots: Inputs, monkeypatch: pytest.MonkeyPatch) -> None:
    handle = await boundary.launch_boundary(config("import time; time.sleep(20)"), roots)

    async def unknown(fd: int) -> None:
        raise OSError(errno.EIO, "injected pidfd uncertainty")

    monkeypatch.setattr(boundary, "_dead", unknown)
    with pytest.raises(boundary.BoundaryUnavailable, match="unverified"):
        await handle.stop_verified()
    assert not handle.verified_dead
    with pytest.raises(boundary.BoundaryUnavailable, match="unverified"):
        await handle.wait()
    with pytest.raises(boundary.BoundaryUnavailable, match="unverified"):
        await handle.stop_verified()


async def test_locked_mounts_with_nested_cap_sys_admin(roots: Inputs) -> None:
    handle = await boundary.launch_boundary(
        config(r"""
import ctypes, errno, json, os, pathlib
libc=ctypes.CDLL(None,use_errno=True)
uid, gid = os.getuid(), os.getgid()
assert uid == gid == 1000
assert libc.unshare(0x10000000) == 0, ctypes.get_errno()
pathlib.Path('/proc/self/setgroups').write_text('deny')
pathlib.Path('/proc/self/uid_map').write_text('0 '+str(uid)+' 1\n')
pathlib.Path('/proc/self/gid_map').write_text('0 '+str(gid)+' 1\n')
assert libc.unshare(0x20000) == 0, ctypes.get_errno()
status=dict(row.split(':',1) for row in pathlib.Path('/proc/self/status').read_text().splitlines())
assert int(status['CapEff'],16) & (1<<21), status
errors={}
for name in ('/opt/deps','/opt/python','/etc/hosts','/'):
    assert libc.mount(None,name.encode(),None,4096|32|2|4,None) == -1, name
    errors[name]=ctypes.get_errno()
for name in ('/opt/deps','/opt/python','/etc/hosts','/proc'):
    assert libc.umount2(name.encode(),2) == -1, name
    errors['unmount'+name]=ctypes.get_errno()
print(json.dumps(dict(errors=errors, cap_eff=status['CapEff'].strip())))
"""),
        roots,
    )
    data = await result(handle)
    assert set(data["errors"].values()) <= {errno.EPERM, errno.EINVAL}
    print("locked mounts with nested CAP_SYS_ADMIN", data)


@pytest.mark.parametrize(
    "invalid",
    [
        "missing",
        "duplicate",
        "target",
        "system",
        "overlap",
        "symlink",
        "hardlink",
        "special",
        "privileged",
        "broken_interpreter",
        "mutable_link",
        "environment",
    ],
)
async def test_boundary_manifest_refusals(roots: Inputs, tmp_path: Path, invalid: str) -> None:
    import os

    from oversight.workspace.preview_boundary_entry import manifest_digest

    modified = roots
    settings = config("raise AssertionError('payload executed')")
    mount = roots.readonly[0]
    if invalid == "missing":
        modified = dataclasses.replace(roots, readonly=roots.readonly[1:])
    elif invalid == "duplicate":
        modified = dataclasses.replace(roots, readonly=(*roots.readonly, mount))
    elif invalid in {"target", "system"}:
        changed = dataclasses.replace(
            mount,
            target="/home" if invalid == "target" else "/etc/hosts",
            role="python" if invalid == "target" else "system",
        )
        modified = dataclasses.replace(roots, readonly=(changed, *roots.readonly[1:]))
    elif invalid == "overlap":
        modified = dataclasses.replace(roots, state=roots.tree)
    elif invalid == "environment":
        settings = dataclasses.replace(settings, env=(("WSL_INTEROP", "forbidden"),))
    else:
        directory = tmp_path / "distribution"
        directory.mkdir()
        file = directory / "unsafe"
        if invalid == "symlink":
            link = tmp_path / "linked-source"
            link.symlink_to(mount.source, target_is_directory=True)
            changed = dataclasses.replace(mount, source=link)
        else:
            if invalid == "hardlink":
                file.write_text("hardlink fixture")
                os.link(file, directory / "other")
            elif invalid == "special":
                os.mkfifo(file)
            elif invalid == "privileged":
                file.write_text("privileged fixture")
                file.chmod(0o4755)
            elif invalid == "mutable_link":
                (directory / "bin").mkdir()
                (directory / "bin/python").symlink_to("/T/mutable-python")
                (roots.tree / "mutable-python").write_text("model-controlled")
                (roots.tree / "mutable-python").chmod(0o755)
            # Deliberately malformed manifests fail even with a caller-supplied digest.
            try:
                digest = manifest_digest(directory)
            except ValueError:
                digest = "0" * 64
            changed = dataclasses.replace(mount, source=directory, digest=digest)
        modified = dataclasses.replace(roots, readonly=(changed, *roots.readonly[1:]))
    with pytest.raises(boundary.BoundaryUnavailable):
        await boundary.launch_boundary(settings, modified)


async def test_repeated_cancellation_waits_for_verified_cleanup(
    roots: Inputs, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle = await boundary.launch_boundary(config("import time; time.sleep(20)"), roots)
    actual = boundary._dead
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(fd: int) -> None:
        entered.set()
        await release.wait()
        await actual(fd)

    monkeypatch.setattr(boundary, "_dead", delayed)
    task = asyncio.create_task(handle.stop_verified())
    await asyncio.wait_for(entered.wait(), 5)
    for _ in range(3):
        task.cancel()
        await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert handle.verified_dead
    await handle.wait()
    await handle.stop_verified()


async def test_wait_cancellation_stops_boundary(roots: Inputs) -> None:
    handle = await boundary.launch_boundary(config("import time; time.sleep(20)"), roots)
    task = asyncio.create_task(handle.wait())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert handle.verified_dead
    await handle.stop_verified()


async def test_missing_kernel_isolation_refuses_launch(
    roots: Inputs, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real inherited seccomp policy forces unshare(2) to return EPERM."""
    entry = tmp_path / "denied.py"
    trusted = str(boundary.ENTRY)
    entry.write_text(
        """
import ctypes
from pathlib import Path
libc=ctypes.CDLL(None,use_errno=True)
class F(ctypes.Structure):
    _fields_=[('code',ctypes.c_ushort),('jt',ctypes.c_ubyte),('jf',ctypes.c_ubyte),('k',ctypes.c_uint)]
class P(ctypes.Structure):
    _fields_=[('n',ctypes.c_ushort),('filters',ctypes.POINTER(F))]
filters=(F*4)(F(0x20,0,0,0),F(0x15,0,1,272),F(0x06,0,0,0x50001),F(0x06,0,0,0x7fff0000))
assert libc.prctl(38,1,0,0,0) == 0
assert libc.prctl(22,2,ctypes.byref(P(4,filters)),0,0) == 0
"""
        + f"exec(compile(Path({trusted!r}).read_text(), {trusted!r}, 'exec'))\n"
    )
    monkeypatch.setattr(boundary, "ENTRY", entry)
    with pytest.raises(boundary.BoundaryUnavailable, match=r"\[Errno 1\] unshare user"):
        await boundary.launch_boundary(config("raise AssertionError('executed')"), roots)


async def test_boundary_interface_is_frozen_and_structural(roots: Inputs) -> None:
    settings = config("pass")
    for instance, name, value in ((settings, "cwd", "/"), (roots.readonly[0], "digest", "0" * 64)):
        with pytest.raises(dataclasses.FrozenInstanceError):
            setattr(instance, name, value)
    handle: boundary.BoundaryHandle = await boundary.launch_boundary(settings, roots)
    await handle.wait()
    assert handle.verified_dead


@pytest.mark.parametrize(
    "target",
    [
        "/opt/deps/../escape",
        "/opt/deps/./python",
        "/opt/deps//python",
        "/opt/deps/",
        "opt/deps/python",
        "/opt/dependency/python",
        "/T/deps",
    ],
)
def test_dependency_mount_refuses_invalid_target(roots: Inputs, target: str) -> None:
    mounts = tuple(
        dataclasses.replace(m, target=target) if m.role == "dependencies" else m
        for m in roots.readonly
    )
    with pytest.raises(boundary.BoundaryUnavailable, match="unapproved tool manifest"):
        boundary._validate(config("pass"), dataclasses.replace(roots, readonly=mounts))


@pytest.mark.parametrize("target", ["/opt/deps", "/opt/deps/nested"])
def test_dependency_mount_refuses_duplicate_or_nested_target(roots: Inputs, target: str) -> None:
    dependency = next(m for m in roots.readonly if m.role == "dependencies")
    mounts = (*roots.readonly, dataclasses.replace(dependency, target=target))
    with pytest.raises(boundary.BoundaryUnavailable, match=r"duplicate|incomplete"):
        boundary._validate(config("pass"), dataclasses.replace(roots, readonly=mounts))


@pytest.mark.parametrize("cap", [-1, True, 1.5, "1024", None])
def test_boundary_refuses_invalid_output_cap(roots: Inputs, cap: Any) -> None:
    with pytest.raises(boundary.BoundaryUnavailable, match="invalid boundary limits"):
        boundary._validate(config("pass", output_cap=cap), roots)


def test_boundary_accepts_separate_dependencies_and_raised_output_budget(roots: Inputs) -> None:
    dependency = next(m for m in roots.readonly if m.role == "dependencies")
    mounts = (
        *(m for m in roots.readonly if m is not dependency),
        dataclasses.replace(dependency, target="/opt/deps/python"),
        dataclasses.replace(dependency, target="/opt/deps/node"),
    )
    boundary._validate(
        config("pass", output_cap=2 * 1024**2), dataclasses.replace(roots, readonly=mounts)
    )
