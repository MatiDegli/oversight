"""GET /files, PUT /files and POST /graph/rescan over a real server on the live app, with the
scripted model: the routes, their error table and their state checks. The manager's own behavior,
cancels and git failures included, is in tests/changes/test_main_edit.py."""

import asyncio
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
from decision_helpers import observed_body
from fastapi.testclient import TestClient
from live_helpers import (
    DEADLINE_S,
    LiveServer,
    error_code,
    is_type,
    live_server,
    live_settings,
    start,
)

from oversight.app import ReplayConfig, create_app
from oversight.changes import ChangeManager, WrongState
from oversight.settings import REPO_ROOT
from oversight.workspace import ProcessResult, content_hash
from oversight_contracts.commands import (
    AgentCreated,
    ErrorResponse,
    FileContent,
    FileSaved,
    GraphResponse,
)

pytestmark = pytest.mark.anyio

PATH = "shop/inventory.py"
PRICING = "shop/pricing.py"
"""The file the scripted agent A documents."""


async def read(s: LiveServer, path: str, agent: str | None = None) -> FileContent:
    params = {"path": path} | ({"agent": agent} if agent else {})
    response = await s.client.get("/files", params=params)
    assert response.status_code == 200, response.text
    return FileContent.model_validate(response.json())


async def put(s: LiveServer, path: str, content: str, base_hash: str) -> httpx.Response:
    body = {"path": path, "content": content, "base_hash": base_hash}
    return await s.client.put("/files", json=body)


async def reviewed(s: LiveServer) -> AgentCreated:
    """Start agent A and wait until its change is ready for review."""
    created = AgentCreated.model_validate((await start(s)).json())
    await s.until(is_type("agent.status", agent_id=created.agent_id, state="ready_for_review"))
    return created


async def test_get_and_save_a_file_on_main_round_trips(tmp_path: Path) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        before = await read(s, PATH)
        assert before.agent_id is None and before.hash == content_hash(before.content.encode())

        saved = await put(s, PATH, before.content + "# noted\n", before.hash)

        assert saved.status_code == 200
        saved_body = FileSaved.model_validate(saved.json())
        after = await read(s, PATH)
        assert after.content == before.content + "# noted\n" and after.hash == saved_body.hash
        *_, edit = await s.until(is_type("graph.delta", cause="user_edit"))
        graph = GraphResponse.model_validate((await s.client.get("/graph")).json())
        assert (graph.graph.commit, graph.graph.version) == (
            edit.payload.commit,
            edit.payload.to_version,
        )
        stale = await put(s, PATH, "x = 1\n", before.hash)  # the hash it started from is gone
        assert error_code(stale) == (409, "stale_file")
        assert (await read(s, PATH)).hash == saved_body.hash


async def test_get_file_for_both_targets(tmp_path: Path) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        created = AgentCreated.model_validate((await start(s)).json())
        await s.until(is_type("change.updated", agent_id=created.agent_id))

        main = await read(s, PRICING)
        agent = await read(s, PRICING, agent=created.agent_id)

        assert (main.agent_id, agent.agent_id) == (None, created.agent_id)
        assert "Take the coupon's percent off" in agent.content
        assert "Take the coupon's percent off" not in main.content
        assert agent.hash != main.hash


async def test_get_file_for_a_closed_agent_is_not_found(tmp_path: Path) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        created = await reviewed(s)
        assert (await read(s, PRICING, agent=created.agent_id)).agent_id == created.agent_id
        assert (
            await s.client.post(
                f"/changes/{created.change_id}/reject", json=observed_body(s, created.change_id)
            )
        ).status_code == 202
        await s.until(is_type("agent.status", agent_id=created.agent_id, state="done"))
        await read(s, PATH)  # waits for main's lock, which the close-out holds through its cleanup

        for agent in (created.agent_id, "agent-9", "../x"):
            gone = await s.client.get("/files", params={"path": PRICING, "agent": agent})
            assert error_code(gone) == (404, "not_found"), agent


async def test_get_refusals_and_misses(tmp_path: Path) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        os.symlink(tmp_path, s.live.workspace.main.root / "link")
        cases = [
            ("../x", 422, "invalid_path"),
            ("/etc/passwd", 422, "invalid_path"),
            ("a/.git/config", 422, "invalid_path"),
            ("link/x", 422, "invalid_path"),  # a symlinked directory
            ("shop", 422, "invalid_path"),  # a directory
            (f"{PATH}/x", 422, "invalid_path"),  # through a file
            ("shop/missing.py", 404, "not_found"),
        ]
        for path, status, code in cases:
            response = await s.client.get("/files", params={"path": path})
            assert error_code(response) == (status, code), path
            assert str(tmp_path) not in response.text, path
        assert error_code(await s.client.get("/files")) == (422, "invalid_request")


async def test_put_refusals_write_nothing(tmp_path: Path) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        current = await read(s, PATH)
        shown = len(s.events())
        cases = [
            ("../x.py", current.hash, 422, "invalid_path"),
            ("/etc/passwd", current.hash, 422, "invalid_path"),
            ("a/.git/config", current.hash, 422, "invalid_path"),
            ("shop", current.hash, 422, "invalid_path"),
            ("shop/missing.py", current.hash, 409, "stale_file"),  # a save never creates a file
            (PATH, "0" * 64, 409, "stale_file"),
        ]
        for path, base, status, code in cases:
            response = await put(s, path, "x = 1\n", base)
            assert error_code(response) == (status, code), path
            assert str(tmp_path) not in response.text, path
        body = {"path": PATH, "content": 3, "base_hash": current.hash}
        assert error_code(await s.client.put("/files", json=body)) == (422, "invalid_request")

        assert await read(s, PATH) == current
        assert len(s.events()) == shown
        assert not (s.live.workspace.main.root / "shop" / "missing.py").exists()


async def test_put_on_a_suspect_main_is_wrong_state_and_get_still_reads(tmp_path: Path) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        current = await read(s, PATH)
        manager = s.live._manager  # pyright: ignore[reportPrivateUsage]
        assert manager is not None
        manager._suspect = True  # pyright: ignore[reportPrivateUsage]

        response = await put(s, PATH, "x = 1\n", current.hash)

        assert error_code(response) == (409, "wrong_state")
        assert await read(s, PATH) == current


async def test_a_failed_commit_answers_internal_and_leaves_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        current = await read(s, PATH)
        real = s.live.workspace.git

        async def git(tree: Any, *args: str, **kwargs: Any) -> ProcessResult:
            if args[0] == "commit":
                return ProcessResult(1, "", "fatal: an armed test failure", False, False, 0)
            return await real(tree, *args, **kwargs)

        monkeypatch.setattr(s.live.workspace, "git", git)
        response = await put(s, PATH, current.content + "# lost\n", current.hash)

        assert error_code(response) == (500, "internal")
        assert f"saving {PATH} failed" in response.json()["error"]["message"]
        assert str(tmp_path) not in response.text and "Traceback" not in response.text
        assert await read(s, PATH) == current
        [error] = [e for e in s.events() if e.type == "error"]
        assert (error.payload.source, error.payload.fatal) == ("git", False)


async def test_rescan_answers_202_and_publishes_only_what_moved(tmp_path: Path) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        shown = len(s.events())
        quiet = await s.client.post("/graph/rescan")
        assert (quiet.status_code, quiet.content) == (202, b"")
        assert len(s.events()) == shown  # nothing moved, nothing sent

        workspace = s.live.workspace
        current = await read(s, PATH)
        async with workspace.locks(main=True):  # a commit the server did not make
            await workspace.main.write_text(
                PATH,
                current.content + "\n\ndef spare() -> int:\n    return 1\n",
                expected_hash=current.hash,
            )
            await workspace.git_ok(workspace.main, "commit", "-qam", "Add spare")
        moved = await s.client.post("/graph/rescan")

        assert (moved.status_code, moved.content) == (202, b"")
        *_, rescan = await s.until(is_type("graph.delta", cause="rescan"))
        assert [n.id for n in rescan.payload.nodes_added] == ["shop.inventory:spare"]
        graph = GraphResponse.model_validate((await s.client.get("/graph")).json())
        assert graph.graph.commit == rescan.payload.commit


async def test_a_rescan_that_raises_answers_internal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        manager = s.live._manager  # pyright: ignore[reportPrivateUsage]
        assert manager is not None

        def scan(commit: str) -> None:
            raise RuntimeError("a scan that raises")

        monkeypatch.setattr(manager._main_graph, "scan", scan)  # pyright: ignore[reportPrivateUsage]
        workspace = s.live.workspace
        current = await read(s, PATH)
        async with workspace.locks(main=True):
            await workspace.main.write_text(
                PATH, current.content + "# moved\n", expected_hash=current.hash
            )
            await workspace.git_ok(workspace.main, "commit", "-qam", "Move main")

        response = await s.client.post("/graph/rescan")

        assert error_code(response) == (500, "internal")
        assert "rescanning failed (RuntimeError)" in response.json()["error"]["message"]
        [error] = [e for e in s.events() if e.type == "error"]
        assert (error.payload.source, error.payload.fatal) == ("graph", False)


async def test_a_start_refused_by_open_is_wrong_state_and_frees_the_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_open = ChangeManager.open

    async def refused(self: ChangeManager, *args: Any, **kwargs: Any) -> Any:
        raise WrongState("Main's state is unknown; nothing was started")

    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        monkeypatch.setattr(ChangeManager, "open", refused)
        response = await start(s)
        assert error_code(response) == (409, "wrong_state")
        assert ErrorResponse.model_validate(response.json()).error.message.endswith(
            "nothing was started"
        )
        monkeypatch.setattr(ChangeManager, "open", real_open)
        assert (await start(s)).status_code == 201  # the slot is free


def test_a_replay_answers_501_to_the_file_routes_without_reading_the_body(tmp_path: Path) -> None:
    log = REPO_ROOT / "fixtures" / "events" / "handwritten-level1.jsonl"
    client = TestClient(
        create_app(live_settings(tmp_path), ReplayConfig(log=log)), base_url="http://127.0.0.1:8710"
    )
    assert error_code(client.get("/files")) == (501, "not_implemented")  # no path asked
    assert error_code(client.put("/files", content=b"not json")) == (501, "not_implemented")
    assert error_code(client.post("/graph/rescan", content=b"not json")) == (501, "not_implemented")
