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

from oversight.app import MAIN_LEFT, TEMPLATE
from oversight.changes import ChangeManager
from oversight.events import StoreClosedError, read_store
from oversight.workspace import ProcessResult, Tree, Workspace
from oversight_contracts.events import ErrorReport
from oversight_contracts.graph import GraphDelta

pytestmark = pytest.mark.anyio
CLOSE_OUT_TRIES = 2
"""The change manager's tries per occasion: a decision, or the drain at shutdown."""


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
        # A plain shutdown says nothing about the agent's state: only a lost ledger halts it.
        states = [
            e.payload.state
            for e in map(SERVER_EVENT.validate_json, (e.envelope for e in read_store(s.store.path)))
            if e.type == "agent.status"
        ]
        assert states == ["starting", "working"]


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


async def test_shutdown_drains_a_delta_whose_publish_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        store = s.store
        real_publish = store.publish
        refused: list[str] = []

        async def publish(payload: Any, **kwargs: Any) -> Any:
            if isinstance(payload, GraphDelta) and len(refused) < CLOSE_OUT_TRIES:
                refused.append(payload.to_version)
                raise OSError("disk full")
            return await real_publish(payload, **kwargs)

        monkeypatch.setattr(store, "publish", publish)
        change = (await s.client.post("/agents", json={"task": "t"})).json()["change_id"]
        await s.until(is_type("agent.status", state="ready_for_review"))
        assert (await s.client.post(f"/changes/{change}/approve")).status_code == 202
        await s.until(is_type("error", change_id=change))  # both tries failed: incomplete
        assert len(refused) == CLOSE_OUT_TRIES
        await s.stop()
        logged = [SERVER_EVENT.validate_json(e.envelope) for e in read_store(store.path)]
    kinds = [e.type for e in logged]
    delta = kinds.index("graph.delta")
    assert kinds.index("change.resolved") < kinds.index("error") < delta
    assert logged[delta].payload.to_version == refused[0]
    assert kinds[-1] == "agent.status" and logged[-1].payload.state == "done"


async def test_a_merge_left_half_done_holds_main_suspect_until_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_git = Workspace.git

    async def broken_git(self: Workspace, tree: Tree, *args: str, **kwargs: Any) -> Any:
        if args[:1] == ("merge",):
            if "--abort" in args:  # the abort fails without running: MERGE_HEAD stays
                return ProcessResult(128, "", "fatal: abort refused", False, False, 1)
            await real_git(self, tree, *args, **kwargs)  # merges, leaving MERGE_HEAD
            return ProcessResult(128, "", "fatal: merge reported a failure", False, False, 1)
        return await real_git(self, tree, *args, **kwargs)

    monkeypatch.setattr(Workspace, "git", broken_git)
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        change = (await s.client.post("/agents", json={"task": "t"})).json()["change_id"]
        await s.until(is_type("agent.status", state="ready_for_review"))
        assert (await s.client.post(f"/changes/{change}/approve")).status_code == 202
        await s.until(is_type("error", change_id=change))
        assert s.live._manager is not None and s.live._manager.main_suspect  # pyright: ignore[reportPrivateUsage]
        again = await s.client.post(f"/changes/{change}/approve")
        assert (again.status_code, again.json()["error"]["code"]) == (409, "wrong_state")
        # Main suspect is checked before the slot, which the broken change still holds.
        start = await s.client.post("/agents", json={"task": "t"})
        assert (start.status_code, start.json()["error"]["code"]) == (409, "wrong_state")
        assert (await s.client.post(f"/changes/{change}/reject")).status_code == 202
        await s.until(is_type("agent.status", state="done"))
        later = await s.client.post("/agents", json={"task": "t"})
        assert (later.status_code, later.json()["error"]["code"]) == (409, "wrong_state")
        assert "main's state is unknown" in later.json()["error"]["message"]
        await s.stop()
        logged = [SERVER_EVENT.validate_json(e.envelope) for e in read_store(s.store.path)]
    last = logged[-1]
    assert (last.type, last.payload.source, last.payload.fatal) == ("error", "git", True)
    assert last.payload.message == MAIN_LEFT
