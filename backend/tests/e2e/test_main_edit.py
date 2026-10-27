"""A person's save on main while an agent's change waits for review, end to end: the approval
tests the combination and merges on the save's commit, and the two deltas chain."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from live_helpers import DEADLINE_S, error_code, is_type, live_server, live_settings, start

from oversight.workspace import Tree
from oversight_contracts.commands import AgentCreated, FileContent

pytestmark = pytest.mark.anyio


async def test_a_save_on_main_then_an_approval_merges_on_its_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        created = AgentCreated.model_validate((await start(s)).json())
        await s.until(is_type("agent.status", agent_id=created.agent_id, state="ready_for_review"))
        read = await s.client.get("/files", params={"path": "README.md"})
        readme = FileContent.model_validate(read.json())
        body = {"path": "README.md", "content": readme.content + "\nA note.\n"}
        saved = await s.client.put("/files", json=body | {"base_hash": readme.hash})
        assert saved.status_code == 200
        await s.until(is_type("graph.delta", cause="user_edit"))
        tested: list[str] = []
        workspace = s.live.workspace
        real = workspace.run_tests

        async def recording(tree: Tree, **kwargs: Any) -> Any:
            tested.append(tree.agent_id or "main")
            return await real(tree, **kwargs)

        monkeypatch.setattr(workspace, "run_tests", recording)

        approve = await s.client.post(f"/changes/{created.change_id}/approve")
        assert approve.status_code == 202
        await s.until(is_type("agent.status", agent_id=created.agent_id, state="done"))

        events = s.events()
        deltas = [e.payload for e in events if e.type == "graph.delta"]
        resolved = [e.payload for e in events if e.type == "change.resolved"]
        assert tested == ["main"]  # main moved under the change: the combination was tested
        assert [(r.decision, r.merge) for r in resolved] == [("approved", "merged")]
        assert [d.cause for d in deltas] == ["user_edit", "merge"]
        assert deltas[1].from_version == deltas[0].to_version
        async with workspace.locks(main=True):
            parents = await workspace.git_ok(
                workspace.main, "rev-list", "--parents", "-n", "1", str(resolved[0].merge_commit)
            )
        assert parents.split()[1] == deltas[0].commit  # its first parent is the save's commit
        assert error_code(await s.client.put("/files", json=body | {"base_hash": readme.hash})) == (
            409,
            "stale_file",
        )
