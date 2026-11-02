"""Per-run limits reach events, agents and Revert without exposing a model setting."""

import asyncio
import sys
from pathlib import Path

import pytest
from live_helpers import DEADLINE_S, is_type, live_server, live_settings, start, two_fakes

from oversight import app as composition
from oversight.agents import ScriptedModel, parse_script
from oversight.app import LiveRun


def process_finished(pid: str) -> bool:
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z"
    except FileNotFoundError:
        return True


@pytest.mark.anyio
@pytest.mark.parametrize(
    "setting,own,test_limit,wall",
    [(75, True, 75, 600), (300, True, 300, 1200), (75, False, 60, 600)],
)
async def test_limits_are_shared_and_logged_once(
    tmp_path, monkeypatch, caplog, setting, own, test_limit, wall
):
    monkeypatch.setattr(LiveRun, "_own_source", lambda self: own)
    settings = two_fakes(tmp_path, test_timeout_s=setting)
    async with asyncio.timeout(DEADLINE_S), live_server(settings) as s:
        limits = s.events()[0].payload.limits
        assert (limits.test_timeout_s, limits.max_wall_clock_s) == (test_limit, wall)
        # Mutating the environment after startup cannot change this run.
        monkeypatch.setenv("OVERSIGHT_TEST_TIMEOUT_S", "900")
        for slot in ("A", "B"):
            assert (await start(s, slot=slot)).status_code == 201
        tasks = [agent.task for agent in s.live._agents.values()]
        assert len(tasks) == 2 and all(task.limits == limits for task in tasks)
        assert all(task.limits is s.live._limits for task in tasks)
        manager = s.live._manager
        assert manager is not None and manager._revert is not None
        assert manager._revert.service.test_timeout_s == test_limit
    text = (
        f"Test limit: {test_limit} s. Agent time limit: {wall} s per round. "
        "These settings stay fixed for this run."
    )
    assert caplog.messages.count(text) == 1
    ignore = "OVERSIGHT_TEST_TIMEOUT_S applies only to your own repositories; this run keeps 60 s."
    assert caplog.messages.count(ignore) == (0 if own else 1)


@pytest.mark.parametrize("source", ["", "boltons-d31d821"])
def test_public_sources_keep_demo_limits(tmp_path, source):
    live = LiveRun(live_settings(tmp_path, workspace_source=source, test_timeout_s=999999999))
    assert (live._limits.test_timeout_s, live._limits.max_wall_clock_s) == (60, 600)


@pytest.mark.anyio
@pytest.mark.skipif(sys.platform != "linux", reason="Linux /proc process-tree evidence")
async def test_stop_ends_a_long_suite_and_releases_locks(tmp_path: Path, monkeypatch):
    template = tmp_path / "template"
    template.mkdir()
    pid_file = tmp_path / "pids"
    (template / "test_sleep.py").write_text(
        "import os, subprocess, sys, time\n"
        "def test_sleep():\n"
        "    child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(3600)'])\n"
        f"    with open({str(pid_file)!r}, 'w') as f: f.write(f'{{os.getpid()}} {{child.pid}}')\n"
        "    time.sleep(3600)\n"
    )
    monkeypatch.setattr(composition, "TEMPLATE", template)
    monkeypatch.setattr(LiveRun, "_own_source", lambda self: True)
    script = parse_script(
        '{"task":"test","turns":[{"tool_calls":[{"name":"run_tests","arguments":{}}]}]}'
    )
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(
            live_settings(tmp_path, test_timeout_s=3600), lambda *_: ScriptedModel(script)
        ) as s,
    ):
        assert (await start(s)).status_code == 201
        # The child process signals readiness through a file, not an in-process Event.
        while not pid_file.exists() or len(pid_file.read_text().split()) != 2:  # noqa: ASYNC110
            await asyncio.sleep(0.01)
        pids = pid_file.read_text().split()
        async with asyncio.timeout(5):
            assert (await s.client.post("/agents/agent-1/stop")).status_code == 202
            await s.until(is_type("change.resolved", reason="stopped"))
            async with (
                s.live.workspace.locks(main=True, agent_id="agent-1"),
                s.live.workspace.git_lease(),
            ):
                for pid in pids:
                    assert await asyncio.to_thread(process_finished, pid)
