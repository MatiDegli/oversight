"""Review saves through the live API, with only the scripted fake model."""

import asyncio
from pathlib import Path

import httpx
import pytest
from live_helpers import (
    DEADLINE_S,
    FAKE_SCRIPT,
    LiveServer,
    Stepped,
    error_code,
    is_type,
    live_server,
    live_settings,
    start,
)

from oversight.agents import load_script
from oversight.workspace import Tree, WorkspaceError, content_hash
from oversight_contracts.commands import AgentCreated, FileContent, FileSaved

pytestmark = pytest.mark.anyio
PRICING = "shop/pricing.py"


async def read(s: LiveServer, path: str, agent: str | None = None) -> FileContent:
    response = await s.client.get(
        "/files", params={"path": path, **({"agent": agent} if agent else {})}
    )
    assert response.status_code == 200, response.text
    return FileContent.model_validate(response.json())


async def put(s: LiveServer, path: str, content: str, base: str, agent: str) -> httpx.Response:
    return await s.client.put(
        "/files",
        json={
            "path": path,
            "content": content,
            "base_hash": base,
            "agent_id": agent,
        },
    )


async def reviewed(s: LiveServer) -> AgentCreated:
    created = AgentCreated.model_validate((await start(s)).json())
    await s.until(is_type("agent.status", agent_id=created.agent_id, state="ready_for_review"))
    return created


async def test_review_save_is_attributed_to_user_and_reproposed(tmp_path: Path) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        created = await reviewed(s)
        before = await read(s, PRICING, created.agent_id)
        main = await read(s, PRICING)
        cursor = s.events()[-1].seq
        content = before.content + "# reviewed by the person\n"
        response = await put(s, PRICING, content, before.hash, created.agent_id)
        assert response.status_code == 200, response.text
        saved = FileSaved.model_validate(response.json())
        assert saved.path == PRICING and saved.hash == content_hash(content.encode())
        events = await s.until(
            is_type("change.proposed", change_id=created.change_id), after=cursor
        )
        updated = next(e.payload for e in events if e.type == "change.updated")
        assert updated.author == "user" and updated.file.modified == content
        proposal = events[-1].payload
        assert proposal.revision == 2 and proposal.tests.outcome == "passed"
        assert (await read(s, PRICING, created.agent_id)).hash == saved.hash
        assert await read(s, PRICING) == main


async def test_review_save_errors_preserve_the_file(tmp_path: Path) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        created = await reviewed(s)
        before = await read(s, PRICING, created.agent_id)
        cases = [
            (PRICING, "0" * 64, created.agent_id, 409, "stale_file"),
            ("../outside.py", before.hash, created.agent_id, 422, "invalid_path"),
            (PRICING, before.hash, "agent-999", 404, "not_found"),
        ]
        cursor = s.events()[-1].seq
        for path, base, agent, status, code in cases:
            response = await put(s, path, "# mine\n", base, agent)
            assert error_code(response) == (status, code)
        assert await read(s, PRICING, created.agent_id) == before
        assert s.events()[-1].seq == cursor


async def test_failed_review_save_answers_internal_and_preserves_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        created = await reviewed(s)
        before = await read(s, PRICING, created.agent_id)
        main = await read(s, PRICING)
        cursor = s.events()[-1].seq
        calls = 0

        async def failed_write(
            tree: Tree, path: str, content: str, *, expected_hash: str | None
        ) -> str:
            nonlocal calls
            assert tree.agent_id == created.agent_id
            assert (path, content, expected_hash) == (PRICING, "# mine\n", before.hash)
            calls += 1
            raise WorkspaceError("The review file could not be written")

        monkeypatch.setattr(Tree, "write_text", failed_write)
        response = await put(s, PRICING, "# mine\n", before.hash, created.agent_id)

        assert calls == 1
        assert error_code(response) == (500, "internal")
        assert response.json()["error"]["message"] == "The review file could not be written"
        assert await read(s, PRICING, created.agent_id) == before
        assert await read(s, PRICING) == main
        assert s.events()[-1].seq == cursor


async def test_missing_review_file_creates_no_directories(tmp_path: Path) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        created = await reviewed(s)
        before = await read(s, PRICING, created.agent_id)
        path = "a/b/c/missing.py"
        response = await put(s, path, "# mine\n", before.hash, created.agent_id)

        assert error_code(response) == (409, "stale_file")
        assert response.json()["error"]["message"] == f"{path} no longer exists"
        assert not (s.live.workspace.runtime_root / "worktrees" / created.agent_id / "a").exists()
        assert await read(s, PRICING, created.agent_id) == before


async def test_review_save_while_working_is_wrong_state(tmp_path: Path) -> None:
    model = Stepped(load_script(FAKE_SCRIPT))
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: model) as s,
    ):
        created = AgentCreated.model_validate((await start(s)).json())
        await s.until(is_type("agent.status", agent_id=created.agent_id, state="working"))
        before = await read(s, PRICING, created.agent_id)
        response = await put(s, PRICING, "# mine\n", before.hash, created.agent_id)
        assert error_code(response) == (409, "wrong_state")
        assert await read(s, PRICING, created.agent_id) == before
