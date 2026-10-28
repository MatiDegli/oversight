"""The admission boundary precedes routing, git, model calls and event publication."""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import httpx
import pytest
from live_helpers import live_settings

from oversight.app import ReplayConfig, create_app
from oversight.limits import MAX_BODY_BYTES, MAX_FILE_BYTES
from oversight.settings import REPO_ROOT
from oversight_contracts.commands import ErrorResponse

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("declared", [None, "1", str(MAX_BODY_BYTES + 1)])
@pytest.mark.parametrize("extra", [0, 1])
async def test_actual_body_boundary_and_untrusted_length(tmp_path, declared, extra) -> None:
    app = create_app(live_settings(tmp_path))
    reached = AsyncMock()

    @app.post("/probe")
    async def probe():
        await reached()
        return {"ok": True}

    async def chunks() -> AsyncIterator[bytes]:
        yield b"x" * (MAX_BODY_BYTES - 1)
        yield b"x" * (1 + extra)

    headers = {"content-length": declared} if declared is not None else {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://localhost:8710"
    ) as client:
        response = await client.post("/probe", content=chunks(), headers=headers)
    refused = extra or declared == str(MAX_BODY_BYTES + 1)
    assert response.status_code == (413 if refused else 200)
    if refused:
        assert ErrorResponse.model_validate(response.json()).error.code == "too_large"
        reached.assert_not_called()
    else:
        reached.assert_awaited_once()


@pytest.mark.parametrize("replay", [False, True])
@pytest.mark.parametrize("foreign", [False, True])
async def test_declared_excess_and_guard_order_never_read_body(tmp_path, replay, foreign) -> None:
    factory = AsyncMock(side_effect=AssertionError("model reached"))
    config = (
        ReplayConfig(REPO_ROOT / "fixtures/events/handwritten-level1.jsonl") if replay else None
    )
    app = create_app(live_settings(tmp_path), config, client_factory=factory)

    async def unread() -> AsyncIterator[bytes]:
        raise AssertionError("body read before refusal")
        yield b""

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app),
        base_url=f"http://{'foreign' if foreign else 'localhost'}:8710",
    ) as client:
        response = await client.put(
            "/files", content=unread(), headers={"content-length": str(MAX_BODY_BYTES + 1)}
        )
    assert response.status_code == (403 if foreign else 413)
    assert ErrorResponse.model_validate(response.json()).error.code == (
        "forbidden" if foreign else "too_large"
    )
    factory.assert_not_called()
    assert not (tmp_path / ".runtime").exists()


async def test_file_refusals_leave_no_effects(tmp_path, monkeypatch) -> None:
    app = create_app(live_settings(tmp_path))
    async with app.router.lifespan_context(app):
        live = app.state.live
        target = live.workspace.main.root / "notes.txt"
        target.write_bytes(b"a" * MAX_FILE_BYTES)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://localhost:8710"
        ) as client:
            exact = await client.get("/files", params={"path": "notes.txt"})
            assert exact.status_code == 200
            seq = live.store.last_seq
            git = AsyncMock(side_effect=AssertionError("git reached on refusal"))
            real_git = live.workspace.git
            monkeypatch.setattr(live.workspace, "git", git)
            for content in ("a" * (MAX_FILE_BYTES + 1), "é" * (MAX_FILE_BYTES // 2 + 1)):
                response = await client.put(
                    "/files",
                    json={
                        "path": "notes.txt",
                        "content": content,
                        "base_hash": exact.json()["hash"],
                    },
                )
                assert response.status_code == 413
                assert ErrorResponse.model_validate(response.json()).error.code == "too_large"
                assert target.read_bytes() == b"a" * MAX_FILE_BYTES
                assert live.store.last_seq == seq

            async def excess_chunks() -> AsyncIterator[bytes]:
                yield b"x" * MAX_BODY_BYTES
                yield b"x"

            response = await client.put("/files", content=excess_chunks())
            assert response.status_code == 413
            assert target.read_bytes() == b"a" * MAX_FILE_BYTES
            assert live.store.last_seq == seq
            target.write_bytes(b"a" * (MAX_FILE_BYTES + 1))
            response = await client.get("/files", params={"path": "notes.txt"})
            assert response.status_code == 413
            response = await client.put(
                "/files",
                json={"path": "notes.txt", "content": "small", "base_hash": exact.json()["hash"]},
            )
            assert response.status_code == 413
            assert target.stat().st_size == MAX_FILE_BYTES + 1
            assert live.store.last_seq == seq
            git.assert_not_called()
            monkeypatch.setattr(live.workspace, "git", real_git)
            target.unlink()


async def test_replay_commands_keep_501_for_bounded_bodies(tmp_path) -> None:
    app = create_app(
        live_settings(tmp_path),
        ReplayConfig(REPO_ROOT / "fixtures/events/handwritten-level1.jsonl"),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://localhost:8710"
    ) as client:
        for path in ("/agents", "/files", "/graph/rescan"):
            response = await client.request(
                "PUT" if path == "/files" else "POST", path, content=b"x" * MAX_BODY_BYTES
            )
            assert response.status_code == 501


async def test_agent_files_obey_the_same_limit_without_events(tmp_path, monkeypatch) -> None:
    from live_helpers import is_type, live_server, start

    async with live_server(live_settings(tmp_path)) as s:
        created = (await start(s)).json()
        agent = created["agent_id"]
        await s.until(is_type("agent.status", agent_id=agent, state="ready_for_review"))
        tree = s.live.workspace.worktree(agent)
        target = tree.root / "shop/pricing.py"
        original = target.read_bytes()
        before = (
            await s.client.get("/files", params={"path": "shop/pricing.py", "agent": agent})
        ).json()
        seq = s.store.last_seq
        with monkeypatch.context() as patch:
            git = AsyncMock(side_effect=AssertionError("git reached on refusal"))
            patch.setattr(s.live.workspace, "git", git)
            response = await s.client.put(
                "/files",
                json={
                    "path": "shop/pricing.py",
                    "agent_id": agent,
                    "base_hash": before["hash"],
                    "content": "x" * (MAX_FILE_BYTES + 1),
                },
            )
            assert response.status_code == 413
            assert target.read_bytes() == original
            target.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
            response = await s.client.get(
                "/files", params={"path": "shop/pricing.py", "agent": agent}
            )
            assert response.status_code == 413
            response = await s.client.put(
                "/files",
                json={
                    "path": "shop/pricing.py",
                    "agent_id": agent,
                    "base_hash": before["hash"],
                    "content": "small",
                },
            )
            assert response.status_code == 413
            assert target.stat().st_size == MAX_FILE_BYTES + 1
            assert s.store.last_seq == seq
            git.assert_not_called()
            target.write_bytes(original)
