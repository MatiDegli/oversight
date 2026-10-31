"""A start that fails or is cancelled after the slot is claimed gives the slot back."""

import asyncio
import contextlib
from pathlib import Path
from typing import Any

import pytest
from live_helpers import DEADLINE_S, FakeUv, live_server, live_settings

from oversight.api import ApiError
from oversight.changes import ChangeManager, WrongState
from oversight_contracts.commands import AgentCreated, ErrorResponse, StartAgent

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        (RuntimeError("prepare failed"), 500, "internal"),
        (ApiError("invalid_request", "prepare refused"), 422, "invalid_request"),
        (WrongState("prepare refused"), 409, "wrong_state"),
    ],
)
async def test_failed_prepare_ends_the_open_change_and_frees_the_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception, status: int, code: str
) -> None:
    def failed_prepare(*args: Any, **kwargs: Any) -> Any:
        raise failure

    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        with monkeypatch.context() as patch:
            patch.setattr("oversight.app._run_controls.prepare", failed_prepare)
            response = await s.client.post("/agents", json={"task": "t"})
        assert response.status_code == status
        assert ErrorResponse.model_validate(response.json()).error.code == code
        assert s.live._slots == {"A": None}  # pyright: ignore[reportPrivateUsage]
        assert s.live._runs == {}  # pyright: ignore[reportPrivateUsage]
        manager = s.live._manager  # pyright: ignore[reportPrivateUsage]
        assert manager is not None
        rec = manager._agents["agent-1"]  # pyright: ignore[reportPrivateUsage]
        assert rec.state == "unproposed" and not rec.review_ready
        [error] = [e for e in s.events() if e.type == "error"]
        assert error.payload.source == "agent" and error.payload.fatal
        assert error.payload.agent_id == "agent-1" and error.payload.change_id == "change-1"
        retried = await s.client.post("/agents", json={"task": "t"})
        assert retried.status_code == 201
        assert AgentCreated.model_validate(retried.json()).agent_id == "agent-2"


async def test_a_failed_open_answers_internal_and_the_next_start_succeeds(tmp_path: Path) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        # A directory where agent-1's worktree would go makes `open` fail.
        (tmp_path / ".runtime" / "worktrees" / "agent-1").mkdir(parents=True)
        failed = await s.client.post("/agents", json={"task": "t"})
        assert failed.status_code == 500
        assert ErrorResponse.model_validate(failed.json()).error.code == "internal"
        assert "Traceback" not in failed.text
        [error] = [e for e in s.events() if e.type == "error"]
        assert error.payload.source == "server"
        assert str(tmp_path) not in error.payload.message
        retried = await s.client.post("/agents", json={"task": "t"})
        assert retried.status_code == 201
        assert AgentCreated.model_validate(retried.json()).agent_id == "agent-2"


async def test_a_start_cancelled_while_open_waits_leaves_the_slot_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = asyncio.Event()
    real_open = ChangeManager.open

    async def held_open(self: ChangeManager, *args: Any, **kwargs: Any) -> Any:
        entered.set()
        await asyncio.Event().wait()  # never set: only a cancel ends it
        return await real_open(self, *args, **kwargs)

    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        monkeypatch.setattr(ChangeManager, "open", held_open)
        start = asyncio.create_task(s.live.start(StartAgent(task="t")))
        await entered.wait()
        start.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await start
        assert start.cancelled()
        monkeypatch.setattr(ChangeManager, "open", real_open)
        response = await s.client.post("/agents", json={"task": "t"})
        assert response.status_code == 201


async def test_a_start_cancelled_during_the_venv_build_does_not_cancel_the_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = live_settings(tmp_path, demo_python=None)
    async with (
        FakeUv(tmp_path, monkeypatch) as uv,
        asyncio.timeout(DEADLINE_S),
        live_server(settings) as s,
    ):
        await uv.pids.get()  # the build is running `uv venv`, held
        # Started eagerly, the start runs at once up to its first await: the wait for the build.
        loop = asyncio.get_running_loop()
        start = asyncio.Task(s.live.start(StartAgent(task="t")), loop=loop, eager_start=True)
        assert s.live._slots["A"] == "starting"  # pyright: ignore[reportPrivateUsage]
        start.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await start
        venv = s.live._venv  # pyright: ignore[reportPrivateUsage]
        assert venv is not None and not venv.done()
        await uv.release()
        response = await s.client.post("/agents", json={"task": "t"})
        assert response.status_code == 201
        assert venv.done() and not venv.cancelled()
