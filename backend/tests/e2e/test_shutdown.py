"""Shutdown on the live app: it ends the run, reaps the venv build, waits for a merge in flight,
closes the store and frees the runtime lock."""

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
from live_helpers import (
    DEADLINE_S,
    SERVER_EVENT,
    FakeUv,
    GatedModel,
    is_type,
    live_server,
    live_settings,
)

from oversight.app import TEMPLATE
from oversight.changes import ChangeManager
from oversight.events import StoreClosedError, read_store
from oversight.workspace import Tree, Workspace
from oversight_contracts.events import ErrorReport

pytestmark = pytest.mark.anyio


async def lock_is_free(runtime: Path) -> bool:
    workspace = Workspace(runtime, TEMPLATE, pytest_pin="9.1.1")
    await workspace.acquire_runtime()
    workspace.release_runtime()
    return True


async def test_shutdown_mid_run_ends_the_run_closes_the_store_and_frees_the_lock(
    tmp_path: Path,
) -> None:
    model = GatedModel()
    settings = live_settings(tmp_path)
    async with asyncio.timeout(DEADLINE_S), live_server(settings, lambda _: model) as s:
        assert (await s.client.post("/agents", json={"task": "t"})).status_code == 201
        await model.entered.wait()
        await s.stop()
        assert model.cancelled.is_set()
        assert s.live._runs == {}  # pyright: ignore[reportPrivateUsage]
        with pytest.raises(StoreClosedError):
            await s.store.publish(
                ErrorReport(
                    source="server", message="late", agent_id=None, change_id=None, fatal=False
                )
            )
        assert await lock_is_free(settings.runtime_dir)
        # The ended change can still be read from the log; nothing after the store closed.
        logged = [SERVER_EVENT.validate_json(e.envelope).type for e in read_store(s.store.path)]
        assert logged[:2] == ["run.started", "graph.snapshot"]


async def test_shutdown_reaps_a_blocked_venv_build_before_the_store_closes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = live_settings(tmp_path, demo_python=None)
    async with (
        FakeUv(tmp_path, monkeypatch) as uv,
        asyncio.timeout(DEADLINE_S),
        live_server(settings) as s,
    ):
        pid = await uv.pids.get()
        store = s.store
        alive_at_close: list[bool] = []
        real_close = store.close

        async def close() -> None:
            alive_at_close.append(await asyncio.to_thread(os.path.exists, f"/proc/{pid}"))
            await real_close()

        monkeypatch.setattr(store, "close", close)
        await s.stop()
    assert alive_at_close == [False]
    assert not await asyncio.to_thread(os.path.exists, f"/proc/{pid}")


async def test_shutdown_waits_for_a_merge_in_flight_and_logs_its_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    merging, closing, gate = asyncio.Event(), asyncio.Event(), asyncio.Event()
    real_git, real_aclose = Workspace.git, ChangeManager.aclose

    async def held_git(self: Workspace, tree: Tree, *args: str, **kwargs: Any) -> Any:
        if args[:1] == ("merge",):
            merging.set()
            await gate.wait()
        return await real_git(self, tree, *args, **kwargs)

    async def watched_aclose(self: ChangeManager) -> None:
        closing.set()
        await real_aclose(self)

    monkeypatch.setattr(Workspace, "git", held_git)
    monkeypatch.setattr(ChangeManager, "aclose", watched_aclose)
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        change = (await s.client.post("/agents", json={"task": "t"})).json()["change_id"]
        await s.until(is_type("agent.status", state="ready_for_review"))
        assert (await s.client.post(f"/changes/{change}/approve")).status_code == 202
        await merging.wait()
        stopping = asyncio.create_task(s.stop())
        await closing.wait()  # the shutdown reached the manager with the merge still held
        gate.set()
        await stopping
        logged = [SERVER_EVENT.validate_json(e.envelope) for e in read_store(s.store.path)]
    kinds = [e.type for e in logged]
    resolved = next(e for e in logged if e.type == "change.resolved")
    assert resolved.payload.merge == "merged"
    assert kinds.index("change.resolved") < kinds.index("graph.delta")
    assert kinds[-1] == "agent.status" and logged[-1].payload.state == "done"
