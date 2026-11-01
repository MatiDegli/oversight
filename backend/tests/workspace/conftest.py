"""Shared helpers for the workspace tests: the event loop backend, FIFO gates, process checks."""

import asyncio
import ctypes
import errno
import os
import select
import sys
from collections.abc import Awaitable, Callable, Iterator
from pathlib import Path

import pytest

from oversight.workspace import Workspace, run_process
from oversight.workspace.core import EXCLUDE_NEWER, UV_PASSTHROUGH

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


class Gate:
    """A one-shot handshake between a test and a child process, over a FIFO. The child blocks in
    `child_wait` until the test calls `open_gate`; the child's `child_signal` ends `wait_signal`."""

    def __init__(self, directory: Path, name: str) -> None:
        if sys.platform == "win32":
            pytest.skip("POSIX FIFO gate")
        self.go = directory / f"{name}.go"
        self.signal = directory / f"{name}.signal"
        os.mkfifo(self.go)
        os.mkfifo(self.signal)

    @property
    def child_wait(self) -> str:
        return f"open({str(self.go)!r}).read()"

    @property
    def child_signal(self) -> str:
        return f"open({str(self.signal)!r}, 'w').write(str(os.getpid()))"

    async def _exchange(self, path: Path, *, writing: bool, timeout_s: float) -> str:
        if sys.platform == "win32":
            pytest.skip("POSIX FIFO gate")
        loop = asyncio.get_running_loop()
        opened = asyncio.Event()
        aborted = False

        def exchange() -> str:
            fd = os.open(path, os.O_WRONLY if writing else os.O_RDONLY)
            loop.call_soon_threadsafe(opened.set)
            try:
                if writing:
                    if not aborted:
                        os.write(fd, b"go")
                    return ""
                chunks: list[bytes] = []
                while not aborted:
                    if not select.select([fd], [], [], 0.05)[0]:
                        continue
                    chunk = os.read(fd, 4096)
                    if not chunk:
                        break
                    chunks.append(chunk)
                return b"".join(chunks).decode()
            finally:
                os.close(fd)

        worker = asyncio.create_task(asyncio.to_thread(exchange))
        try:
            async with asyncio.timeout(timeout_s):
                return await asyncio.shield(worker)
        except (TimeoutError, asyncio.CancelledError) as error:
            aborted = True

            async def release() -> None:
                if sys.platform == "win32":
                    pytest.skip("POSIX FIFO gate")
                while not worker.done():
                    try:
                        fd = os.open(
                            path, (os.O_RDONLY if writing else os.O_WRONLY) | os.O_NONBLOCK
                        )
                    except OSError as error:
                        if error.errno != errno.ENXIO:
                            raise
                        await asyncio.sleep(0.001)
                        continue
                    ready = asyncio.create_task(opened.wait())
                    try:
                        # Keep the peer open until the worker has actually opened its end.
                        await asyncio.wait({ready, worker}, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        os.close(fd)
                        ready.cancel()
                        await asyncio.gather(ready, return_exceptions=True)
                    break
                await asyncio.gather(worker, return_exceptions=True)

            cleanup = asyncio.create_task(release())
            cancelled = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    cancelled = True
            cleanup.result()
            if cancelled and not isinstance(error, asyncio.CancelledError):
                raise asyncio.CancelledError from error
            raise

    async def open_gate(self, timeout_s: float = 60) -> None:
        await self._exchange(self.go, writing=True, timeout_s=timeout_s)

    async def wait_signal(self, timeout_s: float = 60) -> str:
        return await self._exchange(self.signal, writing=False, timeout_s=timeout_s)


@pytest.fixture
def gate_factory(tmp_path: Path) -> Callable[[str], Gate]:
    def make(name: str) -> Gate:
        return Gate(tmp_path, name)

    return make


def _pidfd_open(pid: int) -> int | None:
    """None when the process is already gone. Some Python builds lack os.pidfd_open, so it falls
    back to the system call, number 434 on every architecture Linux gained it on."""
    if sys.platform == "win32":
        pytest.skip("Linux pidfd proof")
    if hasattr(os, "pidfd_open"):
        try:
            return os.pidfd_open(pid)
        except ProcessLookupError:
            return None
    libc = ctypes.CDLL(None, use_errno=True)
    fd = int(libc.syscall(434, pid, 0))
    if fd >= 0:
        return fd
    if ctypes.get_errno() == errno.ESRCH:
        return None
    raise OSError(ctypes.get_errno(), "pidfd_open failed")


def _wait_gone(pid: int, deadline_s: float = 10.0) -> None:
    """Wait on a pidfd, not on a sleep: it is readable once the process has terminated."""
    fd = _pidfd_open(pid)
    if fd is None:
        return
    try:
        ready, _, _ = select.select([fd], [], [], deadline_s)
        assert ready, f"process {pid} is still alive"
    finally:
        os.close(fd)


@pytest.fixture
def wait_gone() -> Callable[[int], None]:
    return _wait_gone


TEMPLATE_FILES = {
    "pyproject.toml": "[tool.pytest.ini_options]\ntestpaths = ['tests']\npythonpath = ['.']\n",
    "shop/__init__.py": "",
    "shop/mod.py": "def value() -> int:\n    return 1\n",
    "tests/test_mod.py": (
        "from shop.mod import value\n\n\ndef test_value() -> None:\n    assert value() == 1\n"
    ),
}


def write_tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root


@pytest.fixture
def template(tmp_path: Path) -> Path:
    """A small template, with junk the copy must skip."""
    root = write_tree(tmp_path / "template", TEMPLATE_FILES)
    write_tree(root, {"shop/__pycache__/mod.cpython-312.pyc": "x", ".venv/bin/python": "x"})
    return root


MakeWorkspace = Callable[..., Workspace]


@pytest.fixture
def make_workspace(tmp_path: Path, template: Path) -> Iterator[MakeWorkspace]:
    """Build a Workspace on a fresh `.runtime`; the runtime locks are released at the end."""
    created: list[Workspace] = []

    def make(
        name: str = "rt", *, source: Path | None = None, demo_python: Path | None = None
    ) -> Workspace:
        root = tmp_path / name / ".runtime"
        root.parent.mkdir(exist_ok=True)
        workspace = Workspace(root, source or template, pytest_pin="9.1.1", demo_python=demo_python)
        created.append(workspace)
        return workspace

    yield make
    for workspace in created:
        workspace.release_runtime()


@pytest.fixture
async def workspace(anyio_backend: str, make_workspace: MakeWorkspace) -> Workspace:
    """A workspace that is started: runtime lock taken, reset done."""
    started = make_workspace()
    await started.acquire_runtime()
    await started.reset()
    return started


GitOut = Callable[..., Awaitable[str]]


@pytest.fixture
def git_out(tmp_path: Path) -> GitOut:
    """Run git for inspection, outside the workspace's own runner, in a clean environment."""

    async def run(cwd: Path, *args: str, check: bool = True) -> str:
        env = {"HOME": str(tmp_path), "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1"}
        result = await run_process(["git", *args], cwd=cwd, timeout_s=30, env=env)
        if check:
            assert result.returncode == 0, result.stderr
        return result.stdout

    return run


@pytest.fixture(scope="session")
def demo_python(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The demo repo's environment, built once per session from tools/demo-venv-requirements.txt."""
    root = tmp_path_factory.mktemp("demo-venv-build")
    venv = root / "venv"
    python = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    env = {k: v for k, v in os.environ.items() if k in UV_PASSTHROUGH}
    requirements = REPO_ROOT / "tools" / "demo-venv-requirements.txt"

    async def build() -> None:
        uv = ["uv", "--no-config"]
        install = [*uv, "pip", "install", "--quiet", "--exclude-newer", EXCLUDE_NEWER]
        steps = [
            [*uv, "venv", "--quiet", "--python", "3.12", str(venv)],
            [*install, "--python", str(python), "-r", str(requirements)],
        ]
        for argv in steps:
            result = await run_process(argv, cwd=root, timeout_s=300, env=env)
            assert result.returncode == 0, result.stderr

    asyncio.run(build())
    return python


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def write_tree_files() -> Callable[[Path, dict[str, str]], Path]:
    return write_tree
