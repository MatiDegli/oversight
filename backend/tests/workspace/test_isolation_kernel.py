"""Optional real-kernel acceptance: opt in with OVERSIGHT_ISOLATION_KERNEL_TESTS=1.

The default CI path uses injected namespaces; these tests exercise the Linux teardown guarantee.
"""

import asyncio
import contextlib
import json
import os
import signal
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from oversight.workspace import IsolatedRunner, IsolationUnavailable
from oversight.workspace.runner import ProcessCollector, child_env, wait_done

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(
        os.environ.get("OVERSIGHT_ISOLATION_KERNEL_TESTS") != "1",
        reason="optional real-kernel isolation acceptance",
    ),
]


async def ready(path: Path) -> dict[str, str]:
    async with asyncio.timeout(15):
        while not path.exists():  # noqa: ASYNC110, ASYNC240 - cross-process file handshake
            await asyncio.sleep(0.01)
    return json.loads(path.read_text())  # noqa: ASYNC240 - tiny handshake report


def members(namespace: str) -> list[int]:
    found = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdecimal():
            try:
                if os.readlink(entry / "ns/pid") == namespace:
                    found.append(int(entry.name))
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                pass
    return found


def program(root: Path, code: int = 0) -> str:
    # The detached child inherits the output pipes; namespace death must still kill it.
    return f"""
import json, os, signal, subprocess, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'],
                         start_new_session=True)
with open({str(root / "ready")!r}, 'w') as report:
    report.write(json.dumps(dict(namespace=os.readlink('/proc/self/ns/pid'))))
while not os.path.exists({str(root / "release")!r}):
    time.sleep(0.01)
sys.exit({code})
"""


async def available(root: Path) -> IsolatedRunner:
    runner = IsolatedRunner(root)
    try:
        await runner.preflight()
    except IsolationUnavailable as error:
        pytest.skip(str(error))
    return runner


@pytest.mark.parametrize("finish", ["passed", "failed", "timeout", "cancelled"])
async def test_namespace_teardown_kills_detached_pipe_holder(
    tmp_path: Path, wait_gone: Callable[[int], None], finish: str
) -> None:
    runner = await available(tmp_path)
    task = asyncio.create_task(
        runner(
            [sys.executable, "-c", program(tmp_path, int(finish == "failed"))],
            cwd=tmp_path,
            timeout_s=2 if finish == "timeout" else 20,
        )
    )
    info = await ready(tmp_path / "ready")
    pids = members(info["namespace"])
    assert len(pids) == 2
    if finish == "cancelled":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        if finish != "timeout":
            (tmp_path / "release").touch()
        result = await task
        assert result.timed_out is (finish == "timeout")
        if finish != "timeout":
            assert result.returncode == int(finish == "failed")
    for pid in pids:
        wait_gone(pid)
    assert members(info["namespace"]) == []
    # A later filesystem comparison is safe only after all namespace writers have exited.
    assert json.loads((tmp_path / "ready").read_text()) == info


async def test_server_sigkill_reaches_unshare_and_detached_child(
    tmp_path: Path, wait_gone: Callable[[int], None]
) -> None:
    await available(tmp_path)
    server_code = f"""
import asyncio
import contextlib
from pathlib import Path
from oversight.workspace import IsolatedRunner
async def main():
    runner = IsolatedRunner(Path({str(tmp_path)!r}))
    await runner([{sys.executable!r}, '-c', {program(tmp_path)!r}],
                 cwd=Path({str(tmp_path)!r}), timeout_s=60)
asyncio.run(main())
"""
    collector = ProcessCollector(4096)
    transport, _ = await asyncio.get_running_loop().subprocess_exec(
        lambda: collector,
        sys.executable,
        "-I",
        "-c",
        server_code,
        env=child_env(None),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    pids: list[int] = []
    try:
        info = await ready(tmp_path / "ready")
        pids = members(info["namespace"])
        assert len(pids) == 2
        supervisor = server_children(transport.get_pid())
        assert len(supervisor) == 1
        transport.kill()  # Exact server PID; no process-group signals.
        assert await wait_done(collector.exited, 5)
        assert transport.get_returncode() == -signal.SIGKILL
        wait_gone(int(supervisor[0]))
        for pid in pids:
            wait_gone(pid)
        assert members(info["namespace"]) == []
        pids.clear()
    finally:
        if not collector.exited.done():
            transport.kill()
            await wait_done(collector.exited, 5)
        for pid in pids:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        transport.close()


def server_children(pid: int) -> list[str]:
    return Path(f"/proc/{pid}/task/{pid}/children").read_text().split()
