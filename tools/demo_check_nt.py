"""Console-attached Windows supervision; only public W1 workspace primitives are used.

The outer kill-on-close handle is deliberately never closed during this process's life.
Shutdown requires the observed CRT SIGBREAK exit code and the completed lifespan log.
Linux-injected tests exercise policy, not the NT kernel.
"""

import argparse
import asyncio
import contextlib
import ctypes
import errno
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import smoke
from demo_check_io import (
    OVERALL_MARGIN,
    PER_RUN_DEADLINE,
    RUNS,
    START_AGENT_DEADLINE,
    START_DEADLINE,
    STOP_DEADLINE,
    OverallClock,
)
from oversight.workspace import (
    assign_job,
    close_job,
    create_kill_job,
    job_pids,
    resume_suspended,
    run_process,
    terminate_job,
)
from oversight_contracts.commands import GraphResponse

BACKEND_MARKER = "NT backend pid: "
BACKEND_BOOTSTRAP = (
    "import os,runpy,sys;"
    f"print({BACKEND_MARKER!r}+str(os.getpid()),flush=True);"
    "sys.argv[0]='oversight';runpy.run_module('oversight',run_name='__main__')"
)
CREATE_FLAGS = 0x00000004 | 0x00000200  # CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP
CTRL_BREAK = 1  # CTRL_BREAK_EVENT; no CREATE_NO_WINDOW and no signal handler in the server.
NT_GRACEFUL_EXITS = (3,)  # Uvicorn re-raises SIGBREAK through the CRT after lifespan shutdown.
POLL = 0.02


@dataclass(frozen=True)
class Jobs:
    """Inject public job operations without importing private kernel types."""

    create: Callable[[], int] = create_kill_job
    assign: Callable[[int, int], None] = assign_job
    resume: Callable[[int], None] = resume_suspended
    pids: Callable[[int], tuple[int, ...]] = job_pids
    terminate: Callable[[int], None] = terminate_job
    close: Callable[[int], None] = close_job


JOBS = Jobs()
_outer: int | None = None


def retain_outer(jobs: Jobs = JOBS) -> int:
    """Refuse incompatible ancestors before launching; never expose ownership of the handle."""
    global _outer
    if _outer is None:
        job = jobs.create()
        try:
            jobs.assign(job, os.getpid())
        except BaseException:
            jobs.close(job)  # no process was assigned; safe to close this failed job
            raise
        _outer = job  # non-inherited W1 handle; OS teardown alone releases it
    return _outer


async def settled[T](operation: Awaitable[T]) -> T:
    """Retain cleanup task/handles even if the caller is cancelled repeatedly."""
    task = asyncio.ensure_future(operation)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return result


async def wait_members(
    job: int, jobs: Jobs, expected: tuple[int, ...] = (), deadline_s: float = STOP_DEADLINE
) -> None:
    end = time.monotonic() + max(deadline_s, 0)
    while True:
        members = jobs.pids(job)
        if set(members) == set(expected):
            return
        if time.monotonic() >= end:
            raise smoke.SmokeError(f"job {job} still contains {members}; expected {expected}")
        await asyncio.sleep(POLL)


@dataclass
class Server:
    job: int
    log: Path
    jobs: Jobs
    child: subprocess.Popen[Any] | None = None

    async def dispose(self) -> None:
        """Retain the handle until both launcher and all descendants are gone.

        A failed empty-job query/wait leaves the handle open until process teardown.
        Cleanup failure is never a successful shutdown or permission to remove scratch.
        """
        try:
            self.jobs.terminate(self.job)
        finally:
            if self.child is not None:
                if self.child.poll() is None:
                    self.child.kill()  # also covers assignment failure before membership
                await asyncio.to_thread(self.child.wait, timeout=STOP_DEADLINE)
        await wait_members(self.job, self.jobs)
        self.jobs.close(self.job)

    async def stop(
        self, deadline_s: float = STOP_DEADLINE, send: Callable[[int, int], None] = os.kill
    ) -> None:
        assert self.child is not None
        try:
            if self.child.poll() is not None:
                raise smoke.SmokeError("server exited before CTRL_BREAK")
            send(self.child.pid, CTRL_BREAK)
            end = time.monotonic() + max(deadline_s, 0)
            while self.child.poll() is None:
                if time.monotonic() >= end:
                    raise smoke.SmokeError("CTRL_BREAK leader deadline exceeded")
                await asyncio.sleep(POLL)
            if time.monotonic() >= end:
                raise smoke.SmokeError("CTRL_BREAK leader completion observed after deadline")
            await wait_members(self.job, self.jobs, deadline_s=deadline_s)
            text = self.log.read_text(encoding="utf-8", errors="replace")
            code = self.child.returncode
            print(f"NT shutdown: loop={type(asyncio.get_running_loop()).__name__} code={code}")
            if code not in NT_GRACEFUL_EXITS or "Application shutdown complete" not in text:
                raise smoke.SmokeError(f"NT shutdown log/code verdict failed: code={code}\n{text}")
        except BaseException:
            await settled(self.dispose())
            raise
        self.jobs.close(self.job)


async def start_server(
    argv: list[str],
    env: dict[str, str],
    log: Path,
    *,
    jobs: Jobs = JOBS,
    spawn: Callable[..., subprocess.Popen[Any]] = subprocess.Popen,
) -> Server:
    """Assign before resume, killing/reaping even a child refused by an ancestor job."""
    server = Server(jobs.create(), log, jobs)
    try:
        with log.open("wb") as output:
            server.child = spawn(
                argv,
                cwd=smoke.REPO_ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_FLAGS,
            )
        jobs.assign(server.job, server.child.pid)
        jobs.resume(server.child.pid)
        return server
    except BaseException:
        await settled(server.dispose())
        raise


class Locking(Protocol):
    @property
    def LK_NBLCK(self) -> int: ...

    @property
    def LK_UNLCK(self) -> int: ...

    def locking(self, fd: int, mode: int, size: int, /) -> object: ...


async def probe_lock(
    runtime: Path, deadline_s: float = STOP_DEADLINE, *, api: Locking | None = None
) -> None:
    if api is None:
        if sys.platform != "win32":
            raise smoke.SmokeError("lock probe requires native Windows byte locking")
        import msvcrt

        api = msvcrt
    fd = os.open(runtime / "server.lock", os.O_RDWR)
    try:
        os.set_inheritable(fd, False)
        end = time.monotonic() + deadline_s
        while True:
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                api.locking(fd, api.LK_NBLCK, 1)
                break
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EDEADLK}:
                    raise
                if time.monotonic() >= end:
                    raise smoke.SmokeError("runtime lock probe deadline exceeded") from error
                await asyncio.sleep(POLL)
        os.lseek(fd, 0, os.SEEK_SET)
        api.locking(fd, api.LK_UNLCK, 1)
    finally:
        os.close(fd)


def cleanup(scratch: Path) -> None:
    """Bounded sharing retries; read-only files may be removed, locked paths must fail."""

    def writable_remove(
        operation: Callable[[str], object], path: str, error: BaseException
    ) -> None:
        if not isinstance(error, PermissionError):
            raise error
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        operation(path)

    for delay in (0.02, 0.04, 0.08, 0.16, 0.32, 0.32, None):
        try:
            shutil.rmtree(scratch, onexc=writable_remove)
            return
        except OSError as error:
            if getattr(error, "winerror", None) not in {5, 32, 33} or delay is None:
                raise
            time.sleep(delay)


def evidence_dir() -> Path:
    parent = os.environ.get("OVERSIGHT_NT_EVIDENCE_DIR")
    if parent:
        Path(parent).mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="oversight-nt-evidence-", dir=parent))


async def run_once(
    number: int,
    logs: Path,
    runtime: Path,
    first: GraphResponse | None,
    clock: OverallClock,
    *,
    demo: bool = True,
) -> GraphResponse | None:
    import demo_check

    port = smoke.free_port()
    base = smoke.base_url(port)
    log = logs / f"server-{number}.log"
    server = await start_server(
        [sys.executable, "-m", "oversight", *smoke.BACKEND_ARGS],
        demo_check.server_env(port, runtime),
        log,
    )
    assert server.child is not None
    try:
        clock.set_phase(f"run {number}: wait for /health")
        await settled(
            asyncio.to_thread(
                smoke.wait_for_health,
                base,
                clock.bounded(START_DEADLINE),
                server.child,
            )
        )
        if demo:
            first = await settled(
                asyncio.to_thread(
                    demo_check.serve_and_decide,
                    base,
                    first,
                    clock,
                    number,
                    runtime,
                )
            )
        else:
            await settled(
                asyncio.to_thread(smoke.wait_for_graph, base, clock.bounded(START_DEADLINE))
            )
    finally:
        await settled(server.stop(max(0, min(STOP_DEADLINE, clock.remaining()))))
    await wait_members(retain_outer(), JOBS, (os.getpid(),))
    await probe_lock(runtime)
    clock.check()
    print(f"NT {'demo-check' if demo else 'smoke'}: run {number}: ok")
    return first


async def run_checks(
    runs: int = RUNS, *, demo: bool = True, hard_kill: bool = False, own_fixtures: bool = False
) -> None:
    import demo_check

    if demo and not (demo_check.DIST / "index.html").is_file():
        raise smoke.SmokeError("build frontend/dist first with pnpm -C frontend build")
    outer = retain_outer()
    scratch = Path(tempfile.mkdtemp(prefix="oversight-nt-"))
    runtime = (await asyncio.to_thread(scratch.resolve)) / ".runtime"
    logs = evidence_dir()
    print(f"NT evidence: {logs}; runtime: {runtime}", flush=True)
    clock = OverallClock(
        deadline_s=(runs + (3 if own_fixtures else 0)) * PER_RUN_DEADLINE + OVERALL_MARGIN
    )
    first: GraphResponse | None = None
    try:
        if hard_kill:
            await hard_kill_check(runtime, logs, clock)
        for number in range(1, runs + 1):
            first = await run_once(number, logs, runtime, first, clock, demo=demo)
        if own_fixtures:
            await run_own_checks(logs, clock)
        clock.set_phase("cleanup")
        await wait_members(outer, JOBS, (os.getpid(),))
        await settled(asyncio.to_thread(cleanup, scratch))
    except BaseException:
        print(f"NT failure: retained runtime {runtime} and logs {logs}", file=sys.stderr)
        raise
    print(f"NT check: ok: {runs} clean starts, no credits; logs {logs}")


def process_image(pid: int) -> str:
    """Inspect exact job member images, including launchers; never command-line matching."""
    if sys.platform != "win32":
        raise OSError("process image query requires Windows")
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.restype = ctypes.c_void_p
    api.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    api.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_uint32),
    ]
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    process = api.OpenProcess(0x1000, False, pid)
    if not process:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = ctypes.c_uint32(len(buffer))
        if not api.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return buffer.value
    finally:
        api.CloseHandle(process)


def backend_pid(server: Server) -> int:
    """The trusted bootstrap identifies the interpreter behind any virtualenv launcher."""
    text = server.log.read_text(encoding="utf-8", errors="replace")
    markers = [
        line.removeprefix(BACKEND_MARKER)
        for line in text.splitlines()
        if line.startswith(BACKEND_MARKER)
    ]
    if len(markers) != 1 or not markers[0].isdecimal():
        raise smoke.SmokeError("missing or ambiguous backend PID evidence")
    pid = int(markers[0])
    if pid == os.getpid() or pid not in server.jobs.pids(server.job):
        raise smoke.SmokeError("backend PID is not a supervised job member")
    return pid


def kill_backend(pid: int) -> None:
    if sys.platform != "win32":
        raise smoke.SmokeError("forced backend death requires Windows")
    os.kill(pid, 15)  # Windows os.kill with SIGTERM uses TerminateProcess, not console delivery.


async def hard_kill_check(runtime: Path, logs: Path, clock: OverallClock) -> None:
    """Kill the backend while a cold uv build is observed inside its retained job."""
    import demo_check

    if (runtime / "demo-venv").exists():
        raise smoke.SmokeError("hard-kill proof requires a cold disposable demo venv")
    port = smoke.free_port()
    base = smoke.base_url(port)
    server = await start_server(
        [sys.executable, "-c", BACKEND_BOOTSTRAP, *smoke.BACKEND_ARGS],
        demo_check.server_env(port, runtime),
        logs / "hard-kill.log",
    )
    assert server.child is not None
    request_task: asyncio.Task[Any] | None = None
    closed = False
    try:
        await settled(
            asyncio.to_thread(
                smoke.wait_for_health,
                base,
                clock.bounded(START_DEADLINE),
                server.child,
            )
        )
        clock.set_phase("hard kill: cold uv member")
        request_task = asyncio.create_task(asyncio.to_thread(demo_check.start_agent, base, clock))
        end = time.monotonic() + clock.bounded(START_AGENT_DEADLINE)
        while True:
            uv_members: list[int] = []
            for pid in server.jobs.pids(server.job):
                try:
                    if Path(process_image(pid)).name.casefold() == "uv.exe":
                        uv_members.append(pid)
                except OSError as error:
                    if getattr(error, "winerror", None) not in {87, 1168}:  # member just exited
                        raise
            if uv_members:
                break
            if request_task.done():
                await request_task
                raise smoke.SmokeError("cold venv finished without observing a uv job member")
            if time.monotonic() >= end:
                raise smoke.SmokeError("cold uv member deadline exceeded")
            await asyncio.sleep(POLL)
        pid = backend_pid(server)
        print(f"NT cold uv members: {uv_members}; killing backend {pid}", flush=True)
        kill_backend(pid)
        await asyncio.to_thread(server.child.wait, timeout=STOP_DEADLINE)
        # No terminate_job on the successful proof: backend's lifetime job must kill uv itself.
        await wait_members(server.job, server.jobs)
        server.jobs.close(server.job)
        closed = True
        await wait_members(retain_outer(), JOBS, (os.getpid(),))
        await probe_lock(runtime)
        print("NT backend death: empty retained server job; runtime lock released")
    finally:
        try:
            if not closed:
                await settled(server.dispose())
        finally:
            if request_task is not None:
                with contextlib.suppress(smoke.SmokeError):
                    await settled(request_task)


@dataclass(frozen=True)
class OwnFixture:
    source: Path
    runtime: Path
    module: str
    script: Path


_disposable_sources: set[Path] = set()


async def fixture_git(root: Path, *args: str) -> str:
    result = await run_process(
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            *args,
        ],
        cwd=root,
        timeout_s=30,
    )
    if result.returncode != 0 or result.timed_out or result.truncated:
        raise smoke.SmokeError("disposable fixture Git failed: " + result.stderr[-500:])
    return result.stdout.strip()


def prepare_fixture_git_sentinel(source: Path, module: str) -> None:
    """Make accidental source Git refreshes observable without running any filter."""
    scratch = source.parent
    attributes = scratch / "sentinel.attributes"
    attributes.write_bytes(b"* filter=sentinel\n")
    config = source / ".git/config"
    config.write_bytes(
        config.read_bytes()
        + (
            f'\n[core]\n worktree = "{(scratch / "misleading-worktree").as_posix()}"\n'
            f' attributesfile = "{attributes.as_posix()}"\n'
            '[filter "sentinel"]\n clean = echo executed > FILTER_SENTINEL\n'
        ).encode()
    )
    selected = source / module
    before = selected.stat()
    selected.write_bytes(selected.read_bytes())
    # Force an index stat mismatch even with coarse timestamps, preserving clone parity.
    os.utime(selected, ns=(before.st_atime_ns, before.st_mtime_ns + 5_000_000_000))


def check_fixture_filters(scratch: Path) -> None:
    if any(scratch.rglob("FILTER_SENTINEL")):
        raise smoke.SmokeError("source clean filter executed in a disposable fixture")


async def prepare_own_fixture(scratch: Path, *, layout: str = "packages/foo/src") -> OwnFixture:
    """Test setup only: production never creates/installs the person's environment."""
    source = scratch / "source"
    source.mkdir(parents=True)
    module = layout + "/sample/__init__.py"
    files = {
        module: b"def value():\n    return 41\n",
        "marker.py": b"marker = 1\n",
        "data.bin": b"\x00\xff\r\n",
        "crlf.py": b"line = 1\r\n",
        "test_value.py": (
            b"from pathlib import Path\nimport sample\n"
            b"def test_value(tmp_path):\n    assert sample.value() == 41\n"
            b"    assert Path(sample.__file__).resolve().is_relative_to(Path.cwd())\n"
            b"    assert tmp_path.is_dir()\n"
        ),
        "test_async.py": (
            b"import asyncio\nfrom pathlib import Path\nimport pytest\nimport pytest_asyncio\n"
            b"import sample\n"
            b"@pytest_asyncio.fixture\nasync def copied_module():\n"
            b"    await asyncio.sleep(0)\n    return Path(sample.__file__).resolve()\n"
            b"@pytest.mark.asyncio\nasync def test_async_copy(copied_module):\n"
            b"    await asyncio.sleep(0)\n    assert copied_module.is_relative_to(Path.cwd())\n"
        ),
        ".gitignore": b".venv/\n__pycache__/\n.pytest_cache/\n",
        "pyproject.toml": b'[tool.uv.workspace]\nmembers = ["packages/*"]\n',
    }
    for name, data in files.items():
        target = source / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    await fixture_git(source, "init", "-q", "--template=", "-b", "main")
    await fixture_git(source, "add", "-A")
    await fixture_git(source, "commit", "-qm", "Disposable own fixture")
    python = source / ".venv/Scripts/python.exe"
    for argv in (
        ["uv", "--no-config", "venv", "--python", "3.12", str(python.parent.parent)],
        [
            "uv",
            "--no-config",
            "pip",
            "install",
            "--python",
            str(python),
            "pytest==9.1.1",
            "pytest-asyncio==1.3.0",
        ],
    ):
        # source_nt.py:1080 rejects hardlinked environment files.
        result = await run_process(argv, cwd=scratch, timeout_s=300, env={"UV_LINK_MODE": "copy"})
        if result.returncode != 0 or result.timed_out:
            raise smoke.SmokeError("disposable fixture environment setup failed")
    site = source / ".venv/Lib/site-packages"
    (site / "own_fixture.pth").write_text(str(source / layout) + "\n", encoding="utf-8")
    metadata = site / "own_fixture-1.0.dist-info"
    metadata.mkdir()
    (metadata / "METADATA").write_text("Name: own-fixture\nVersion: 1.0\n", encoding="utf-8")
    (metadata / "direct_url.json").write_text(
        json.dumps({"url": source.as_uri(), "dir_info": {"editable": True}}), encoding="utf-8"
    )
    # Associate this distribution with its path mapping for static package discovery.
    (metadata / "RECORD").write_text("own_fixture.pth,,\n", encoding="utf-8")
    # Deliberately no top_level.txt: static discovery must use the translated root.
    script = scratch / "own-source-edit.json"
    script.write_text(
        (smoke.REPO_ROOT / "fixtures/fake-model/own-source-edit.json")
        .read_text(encoding="utf-8")
        .replace("packages/foo/src/sample/__init__.py", module),
        encoding="utf-8",
    )
    prepare_fixture_git_sentinel(source, module)
    (source / "crlf.py").chmod(stat.S_IREAD)
    _disposable_sources.add(source)
    return OwnFixture(source, scratch / ".runtime", module, script)


def own_fixture_env(port: int, fixture: OwnFixture) -> dict[str, str]:
    import demo_check

    return demo_check.server_env(port, fixture.runtime) | {
        "OVERSIGHT_WORKSPACE_SOURCE": str(fixture.source),
        "OVERSIGHT_FAKE_SCRIPT": str(fixture.script),
        "OVERSIGHT_TEST_TIMEOUT_S": "75",
        "OVERSIGHT_DISCARD_OWN_RUNTIME": "",  # Never inherit a person's discard authority.
    }


def own_fixture_phase(base: str, fixture: OwnFixture, clock: OverallClock) -> None:
    from demo_check_io import EventStream, expect, request
    from oversight_contracts.commands import FileContent

    stream = EventStream(base, clock)
    try:
        started = stream.wait(lambda e: e.type == "run.started", 30, "own start", after_seq=0)
        if started.payload.limits.test_timeout_s != 75:
            raise smoke.SmokeError("own fixture did not freeze 75 seconds")
        response = expect(
            request(
                base, "/agents", post={"task": "Verify disposable copy", "slot": "A"}, clock=clock
            ),
            "own agent",
            201,
        )
        who = json.loads(response.body)
        proposal = stream.wait(
            lambda e: e.type == "change.proposed",
            120,
            "own proposal",
            after_seq=started.seq,
            agent_id=who["agent_id"],
        )
        outcomes = [
            e.payload.tests.outcome
            for e in stream.seen
            if e.type == "agent.step" and e.payload.tests is not None
        ]
        if outcomes != ["failed", "passed"] or proposal.payload.tests.outcome != "passed":
            raise smoke.SmokeError("own copy did not fail then repair")
        original = FileContent.model_validate_json(
            expect(request(base, "/files?path=marker.py", clock=clock), "own marker").body
        )
        expect(
            request(
                base,
                "/files",
                put={"path": "marker.py", "content": "marker = 2\n", "base_hash": original.hash},
                clock=clock,
            ),
            "advance own main",
        )
        expect(
            request(
                base,
                f"/changes/{who['change_id']}/approve",
                post={
                    "revision": proposal.payload.revision,
                    "head_commit": proposal.payload.head_commit,
                },
                clock=clock,
            ),
            "own approve",
            202,
        )
        resolved = stream.wait(
            lambda e: e.type == "change.resolved",
            120,
            "own merge",
            after_seq=proposal.seq,
            change_id=who["change_id"],
        )
        if resolved.payload.merge != "merged":
            raise smoke.SmokeError("own combined tests did not admit merge")
        combined = [
            e for e in stream.seen if e.type == "tests.executed" and e.payload.phase == "combined"
        ]
        if not combined or combined[-1].payload.suite.outcome != "passed":
            raise smoke.SmokeError("missing combined-copy test evidence")
    finally:
        stream.close()
    print(f"NT own suites: agent failed/repaired; combined passed; {fixture.module}")


async def fixture_recovery(fixture: OwnFixture, env: dict[str, str], logs: Path) -> None:
    """Only fixtures created here may export/apply/discard automatically."""
    if fixture.source not in _disposable_sources:
        raise smoke.SmokeError("automatic recovery is restricted to disposable fixtures")
    refused = await start_server(
        [sys.executable, "-m", "oversight", "serve"], env, logs / "restart-refused.log"
    )
    try:
        assert refused.child is not None
        await asyncio.to_thread(refused.child.wait, timeout=STOP_DEADLINE)
        if "Windows source refused" not in refused.log.read_text(
            encoding="utf-8", errors="replace"
        ):
            raise smoke.SmokeError("restart did not refuse changed own runtime")
    finally:
        await settled(refused.dispose())
    await probe_lock(fixture.runtime)
    tree = fixture.runtime / "workspace"
    base = (await fixture_git(tree, "rev-list", "--max-parents=0", "HEAD")).splitlines()[0]
    (tree / "data.bin").write_bytes(b"\x00\xfe\r\n")  # Explicit fixture-only operator delta.
    await fixture_git(tree, "add", "data.bin")
    await fixture_git(tree, "commit", "-qm", "Fixture binary export proof")
    current = await fixture_git(tree, "rev-parse", "HEAD")
    patch = logs / "own.patch"
    await fixture_git(
        tree,
        "diff",
        "--binary",
        "--full-index",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
        f"--output={patch}",
        base,
        "main",
    )
    target = fixture.source.parent / "apply-target"
    await fixture_git(fixture.source, "clone", "--no-hardlinks", str(fixture.source), str(target))
    for args in (("apply", "--check", str(patch)), ("apply", str(patch))):
        await fixture_git(target, *args)
    for relative in (fixture.module, "marker.py", "data.bin"):
        if (target / relative).read_bytes() != (tree / relative).read_bytes():
            raise smoke.SmokeError("fixture export/apply byte mismatch")
    discard = await start_server(
        [sys.executable, "-m", "oversight", "serve"],
        env | {"OVERSIGHT_DISCARD_OWN_RUNTIME": current},
        logs / "discard.log",
    )
    try:
        assert discard.child is not None
        await settled(
            asyncio.to_thread(
                smoke.wait_for_health,
                smoke.base_url(int(env["OVERSIGHT_PORT"])),
                START_DEADLINE,
                discard.child,
            )
        )
    finally:
        await settled(discard.stop())
    await probe_lock(fixture.runtime)
    print(
        f"NT own recovery: refused; binary full-index export; apply/check parity; discard {current}"
    )


async def run_own_checks(logs: Path, clock: OverallClock) -> None:
    for number, layout in enumerate(("packages/foo/src", "backend/src", "packages/foo/src"), 1):
        scratch = Path(tempfile.mkdtemp(prefix="oversight-own-"))
        evidence = logs / f"own-{number}"
        evidence.mkdir()
        print(
            f"NT own fixture {number}: retained on failure at {scratch}; evidence {evidence}",
            flush=True,
        )
        fixture = await prepare_own_fixture(scratch, layout=layout)
        selected = (
            fixture.module,
            "marker.py",
            "data.bin",
            ".git/HEAD",
            ".git/index",
            ".git/config",
        )
        hashes = {
            p: hashlib.sha256((fixture.source / p).read_bytes()).hexdigest() for p in selected
        }
        env = own_fixture_env(smoke.free_port(), fixture)
        server = await start_server(
            [sys.executable, "-m", "oversight", "serve"], env, evidence / "server.log"
        )
        try:
            assert server.child is not None
            base = smoke.base_url(int(env["OVERSIGHT_PORT"]))
            await settled(
                asyncio.to_thread(smoke.wait_for_health, base, START_DEADLINE, server.child)
            )
            await settled(asyncio.to_thread(own_fixture_phase, base, fixture, clock))
        finally:
            await settled(server.stop())
        await probe_lock(fixture.runtime)
        after = {p: hashlib.sha256((fixture.source / p).read_bytes()).hexdigest() for p in selected}
        (evidence / "source-hashes.json").write_text(
            json.dumps({"before": hashes, "after": after}, indent=2), encoding="utf-8"
        )
        if hashes != after:
            raise smoke.SmokeError("server mutated original source")
        await asyncio.to_thread(check_fixture_filters, scratch)
        await fixture_recovery(fixture, env, evidence)
        if hashes != {
            p: hashlib.sha256((fixture.source / p).read_bytes()).hexdigest() for p in selected
        }:
            raise smoke.SmokeError("recovery server mutated original source")
        await asyncio.to_thread(check_fixture_filters, scratch)
        await wait_members(retain_outer(), JOBS, (os.getpid(),))
        await settled(asyncio.to_thread(cleanup, scratch))
    print("NT own: three disposable runs; no original-source apply/discard")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe-lock", type=Path, metavar="RUNTIME")
    parser.add_argument("--hard-kill-check", action="store_true")
    parser.add_argument(
        "--own-fixtures",
        action="store_true",
        help="after public enablement: three additional disposable own-source runs",
    )
    args = parser.parse_args()
    if args.probe_lock is not None and args.hard_kill_check:
        parser.error("choose --probe-lock or --hard-kill-check")
    try:
        if sys.platform != "win32":
            raise smoke.SmokeError("this driver requires native Windows")
        retain_outer()
        if args.probe_lock is not None:
            asyncio.run(probe_lock(args.probe_lock))
            print("NT lock: released")
        else:
            if args.own_fixtures:
                asyncio.run(run_checks(hard_kill=args.hard_kill_check, own_fixtures=True))
            else:
                asyncio.run(run_checks(hard_kill=args.hard_kill_check))
    except (smoke.SmokeError, OSError, subprocess.SubprocessError) as error:
        print(f"NT check: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
