import asyncio
import json
import os
import signal
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from oversight.workspace import runner
from oversight.workspace.runner import run_process

pytestmark = pytest.mark.anyio

type GateFactory = Callable[[str], Any]
type WaitGone = Callable[[int], None]
PYTHON = sys.executable


def script(*parts: str) -> list[str]:
    lines = ["import os, sys, time, signal, subprocess", *(textwrap.dedent(p) for p in parts)]
    return [PYTHON, "-I", "-c", "\n".join(lines)]


SPAWN_GRANDCHILD = """
    grandchild = subprocess.Popen([sys.executable, "-I", "-c", "import time; time.sleep(600)"])
    print(grandchild.pid, flush=True)
"""


async def test_exit_code_and_both_streams(tmp_path: Path) -> None:
    result = await run_process(
        script("print('out'); print('err', file=sys.stderr); sys.exit(3)"),
        cwd=tmp_path,
        timeout_s=30,
    )
    assert (result.returncode, result.stdout.strip(), result.stderr.strip()) == (3, "out", "err")
    assert not result.timed_out
    assert not result.truncated


async def test_output_keeps_a_head_and_a_tail_while_the_rest_is_drained(tmp_path: Path) -> None:
    result = await run_process(
        script("sys.stdout.write('x' * 10_000_000)"), cwd=tmp_path, timeout_s=60, output_cap=1000
    )
    middle = 10_000_000 - 1000 - runner.TAIL_CAP
    assert result.returncode == 0
    assert result.stdout == "x" * 1000 + runner.omitted_marker(middle) + "x" * runner.TAIL_CAP
    assert result.truncated


async def test_an_overflow_that_fits_in_the_tail_loses_nothing(tmp_path: Path) -> None:
    result = await run_process(
        script("sys.stdout.write('a' * 1000 + 'b' * 500)"),
        cwd=tmp_path,
        timeout_s=60,
        output_cap=1000,
    )
    assert result.stdout == "a" * 1000 + "b" * 500
    assert not result.truncated


async def test_a_long_output_keeps_the_summary_at_its_end(tmp_path: Path) -> None:
    summary = "\n== 3 passed in 0.1s ==\n"
    size = runner.DEFAULT_OUTPUT_CAP + 50_000
    body = "x" * size
    result = await run_process(
        script(
            f"sys.stdout.write('x' * {size} + {summary!r})",
            f"sys.stderr.write('x' * {size} + '\\nERR END\\n')",
        ),
        cwd=tmp_path,
        timeout_s=60,
    )
    full = body + summary
    cap, tail = runner.DEFAULT_OUTPUT_CAP, runner.TAIL_CAP
    middle = runner.omitted_marker(len(full) - cap - tail)
    assert result.stdout == full[:cap] + middle + full[-tail:]
    assert result.stdout.endswith(summary)
    assert result.stderr.endswith("\nERR END\n")
    assert result.truncated


async def test_timeout_kills_the_whole_group(tmp_path: Path, wait_gone: WaitGone) -> None:
    result = await run_process(
        script(SPAWN_GRANDCHILD, "time.sleep(600)"), cwd=tmp_path, timeout_s=3
    )
    assert result.timed_out
    assert result.returncode == -signal.SIGTERM
    wait_gone(int(result.stdout.split()[0]))


async def test_timeout_escalates_to_sigkill_when_sigterm_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wait_gone: WaitGone
) -> None:
    monkeypatch.setattr(runner, "TERM_GRACE_S", 0.2)
    ignoring = "signal.signal(signal.SIGTERM, signal.SIG_IGN)"
    result = await run_process(
        script(ignoring, SPAWN_GRANDCHILD, "time.sleep(600)"), cwd=tmp_path, timeout_s=3
    )
    assert result.timed_out
    assert result.returncode == -signal.SIGKILL
    wait_gone(int(result.stdout.split()[0]))


ESCAPED_DESCENDANT = """
    holder = subprocess.Popen(
        [sys.executable, "-I", "-c", "import time; time.sleep(600)"], start_new_session=True
    )
    print(holder.pid, flush=True)
"""


@pytest.mark.parametrize("leader_exits", [True, False])
async def test_cleanup_is_bounded_when_a_descendant_keeps_the_pipes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wait_gone: WaitGone, leader_exits: bool
) -> None:
    monkeypatch.setattr(runner, "PIPE_DRAIN_S", 0.2)
    body = script(ESCAPED_DESCENDANT, "" if leader_exits else "time.sleep(600)")
    holder_pid: int | None = None
    try:
        async with asyncio.timeout(30):
            result = await run_process(body, cwd=tmp_path, timeout_s=3)
        holder_pid = int(result.stdout.split()[0])
        assert result.timed_out is (not leader_exits)
        assert result.returncode is not None
    finally:
        # The holder left the group on purpose, so the runner cannot reach it: stop it by its PID.
        if holder_pid is not None:
            os.kill(holder_pid, signal.SIGKILL)
            wait_gone(holder_pid)


async def test_the_group_is_reaped_after_the_parent_exits_normally(
    tmp_path: Path, wait_gone: WaitGone
) -> None:
    result = await run_process(script(SPAWN_GRANDCHILD), cwd=tmp_path, timeout_s=30)
    assert result.returncode == 0
    assert not result.timed_out
    wait_gone(int(result.stdout.split()[0]))


async def test_repeated_cancellation_ends_after_the_cleanup(
    tmp_path: Path,
    gate_factory: GateFactory,
    monkeypatch: pytest.MonkeyPatch,
    wait_gone: WaitGone,
) -> None:
    monkeypatch.setattr(runner, "TERM_GRACE_S", 0.3)
    ready = gate_factory("ready")
    body = script(
        "import json",
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)",
        SPAWN_GRANDCHILD.replace("print(grandchild.pid, flush=True)", ""),
        f"open({str(ready.signal)!r}, 'w').write(json.dumps([os.getpid(), grandchild.pid]))",
        "time.sleep(600)",
    )
    task = asyncio.create_task(run_process(body, cwd=tmp_path, timeout_s=60))
    leader, grandchild = json.loads(await ready.wait_signal())
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    finished, _ = await asyncio.wait({task}, timeout=30)
    assert finished
    assert task.cancelled()
    wait_gone(leader)
    wait_gone(grandchild)


async def test_cancel_during_launch_still_reaches_the_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wait_gone: WaitGone
) -> None:
    pids: list[int] = []
    tasks: list[asyncio.Task[object]] = []
    original = runner.ProcessCollector.connection_made

    def connection_made(self: runner.ProcessCollector, transport: asyncio.BaseTransport) -> None:
        assert isinstance(transport, asyncio.SubprocessTransport)
        pids.append(transport.get_pid())
        tasks[0].cancel()
        original(self, transport)

    monkeypatch.setattr(runner.ProcessCollector, "connection_made", connection_made)
    task = asyncio.create_task(run_process(script("time.sleep(600)"), cwd=tmp_path, timeout_s=60))
    tasks.append(task)
    finished, _ = await asyncio.wait({task}, timeout=30)
    assert finished
    assert task.cancelled()
    assert len(pids) == 1
    wait_gone(pids[0])


async def test_the_child_environment_has_no_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("META_API_KEY", "test-key")
    monkeypatch.setenv("BASETEN_API_KEY", "test-key")
    monkeypatch.setenv("OVERSIGHT_NOT_ALLOWED", "1")
    result = await run_process(
        script("import json; print(json.dumps(dict(os.environ)))"),
        cwd=tmp_path,
        timeout_s=30,
        env={"HOME": "/tmp/home"},
    )
    seen = json.loads(result.stdout)
    assert set(seen) <= {"PATH", "LANG", "LC_ALL", "TZ", "HOME"}
    assert seen["HOME"] == "/tmp/home"
    assert (seen["LANG"], seen["TZ"]) == ("C.UTF-8", "UTC")


async def test_the_child_has_no_stdin(tmp_path: Path) -> None:
    result = await run_process(script("print(repr(sys.stdin.read()))"), cwd=tmp_path, timeout_s=30)
    assert result.stdout.strip() == "''"


async def test_the_event_loop_keeps_running_while_a_child_runs(
    tmp_path: Path, gate_factory: GateFactory
) -> None:
    gate = gate_factory("loop")
    task = asyncio.create_task(
        run_process(script(gate.child_wait + "; print('released')"), cwd=tmp_path, timeout_s=60)
    )
    ticks = 0
    for _ in range(200):
        await asyncio.sleep(0)
        ticks += 1
    assert not task.done()
    await gate.open_gate()
    result = await task
    assert ticks == 200
    assert result.stdout.strip() == "released"


async def test_a_missing_executable_raises_and_leaves_nothing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        await run_process([str(tmp_path / "no-such-program")], cwd=tmp_path, timeout_s=5)
    assert sys.version_info >= (3, 12)
