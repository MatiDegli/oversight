"""Bounded impact reads on committed main and the current change version, without model APIs."""

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
import pytest
from fastapi import FastAPI
from live_helpers import (
    DEADLINE_S,
    Scripts,
    error_code,
    is_type,
    live_server,
    live_settings,
    script_json,
    start,
)

from oversight.agents import FAKE_MODEL, Message, ModelReply, ToolSchema
from oversight.api import create_router, install_error_handlers
from oversight.api.commands import NOT_BUILT
from oversight.app import ReplayConfig, create_app
from oversight.events import EventStore, read_jsonl
from oversight.graph import node_cone, parse_version
from oversight.settings import REPO_ROOT
from oversight_contracts.commands import AgentCreated, ChangeView, FileContent, GraphResponse
from oversight_contracts.events import RunStartedEvent
from oversight_contracts.graph import GraphDelta, ImpactCone

pytestmark = pytest.mark.anyio
RUN = "run-20261027-140312"
LOG = REPO_ROOT / "fixtures/events/handwritten-level1.jsonl"
SOURCE = b"""def seed(): pass
def c(): seed()
def b(): seed()
def a(): seed()
def far(): a()
def beyond(): far()
def lonely(): pass
def uncertain(receiver): receiver.seed()
"""


@asynccontextmanager
async def endpoint(
    tmp_path: Path, mode: Literal["live", "replay"] = "live", *, snapshot: bool = True
) -> AsyncIterator[tuple[EventStore, httpx.AsyncClient]]:
    async with EventStore(tmp_path / "run.sqlite", RUN, mode=mode) as store:
        first = read_jsonl(LOG)[0]
        assert isinstance(first, RunStartedEvent)
        limits = first.payload.limits.model_copy(update={"cone_max_depth": 2, "cone_max_nodes": 2})
        await store.publish(first.payload.model_copy(update={"limits": limits}))
        if snapshot:
            await store.publish(parse_version({"m.py": SOURCE}, "main").graph)
        app = FastAPI()
        install_error_handlers(app)
        app.include_router(create_router(store, mode))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield store, client


def success(response: httpx.Response) -> ImpactCone:
    assert response.status_code == 200, response.text
    return ImpactCone.model_validate_json(response.content)


@pytest.mark.parametrize("mode", ["live", "replay"])
async def test_main_cone_distances_order_limits_and_omissions(
    tmp_path: Path, mode: Literal["live", "replay"]
) -> None:
    async with endpoint(tmp_path, mode) as (store, client):
        before = store.graph(), store.last_seq
        cone = success(await client.get("/impact", params={"node": "m:seed"}))
        assert cone.model_dump() == {
            "seeds": ["m:seed"],
            "entries": [{"node_id": "m:a", "distance": 1}, {"node_id": "m:b", "distance": 1}],
            "edges_followed": "exact",
            "max_depth": 2,
            "max_nodes": 2,
            "omitted_by_depth": 1,
            "omitted_by_limit": 2,
            "tests_reaching": 0,
        }
        # The query does not publish events or replace main's graph.
        assert (store.graph(), store.last_seq) == before
        farther = success(await client.get("/impact", params={"node": "m:a"}))
        assert [(e.node_id, e.distance) for e in farther.entries] == [("m:far", 1), ("m:beyond", 2)]


@pytest.mark.parametrize("depth", ["3", "999", "9" * 5000, "0002"])
async def test_depth_is_clamped_to_run_bound(tmp_path: Path, depth: str) -> None:
    async with endpoint(tmp_path) as (_, client):
        default = success(await client.get("/impact", params={"node": "m:seed"}))
        assert (
            success(await client.get("/impact", params={"node": "m:seed", "depth": depth}))
            == default
        )


async def test_smaller_depth_and_no_callers(tmp_path: Path) -> None:
    async with endpoint(tmp_path) as (_, client):
        cone = success(await client.get("/impact", params={"node": "m:a", "depth": "1"}))
        assert [(e.node_id, e.distance) for e in cone.entries] == [("m:far", 1)]
        assert (cone.max_depth, cone.omitted_by_depth, cone.omitted_by_limit) == (1, 1, 0)
        lonely = success(await client.get("/impact", params={"node": "m:lonely"}))
        assert lonely.seeds == ["m:lonely"] and lonely.entries == []
        assert (lonely.omitted_by_depth, lonely.omitted_by_limit, lonely.tests_reaching) == (
            0,
            0,
            0,
        )


async def test_impact_follows_the_same_committed_delta_as_get_graph(tmp_path: Path) -> None:
    async with endpoint(tmp_path) as (store, client):
        _, graph = store.graph()
        assert graph is not None
        await store.publish(
            GraphDelta(
                from_version=graph.version,
                to_version="next",
                commit="next",
                cause="rescan",
                nodes_added=[],
                nodes_changed=[],
                nodes_removed=["m:lonely"],
                edges_added=[],
                edges_changed=[],
                edges_removed=[],
                parse_errors=[],
            )
        )
        assert (await client.get("/graph")).json()["graph"]["version"] == "next"
        assert error_code(await client.get("/impact", params={"node": "m:lonely"})) == (
            404,
            "not_found",
        )


@pytest.mark.parametrize("mode", ["live", "replay"])
async def test_unknown_node_or_change(tmp_path: Path, mode: Literal["live", "replay"]) -> None:
    async with endpoint(tmp_path, mode) as (_, client):
        for params in ({"node": "missing"}, {"node": "m:seed", "change": "change-999"}):
            assert error_code(await client.get("/impact", params=params)) == (404, "not_found")


async def test_production_replay_uses_committed_graph_but_has_no_change_version(
    tmp_path: Path,
) -> None:
    events = read_jsonl(LOG)
    first = events[0]
    assert isinstance(first, RunStartedEvent)
    proposal = next(e.payload for e in events if e.type == "change.proposed")
    app = create_app(
        live_settings(tmp_path), ReplayConfig(log=LOG, speed=0, allow_fake_provider=True)
    )
    async with asyncio.timeout(DEADLINE_S), app.router.lifespan_context(app):
        store: EventStore = app.state.store
        assert (await anext(store.subscribe(len(events) - 1))).seq == len(events)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8710"
        ) as client:
            graph = GraphResponse.model_validate((await client.get("/graph")).json())
            node = next(
                edge.dst
                for edge in graph.graph.edges
                if edge.kind == "calls" and edge.confidence == "exact"
            )
            before = store.graph(), store.last_seq
            cone = success(await client.get("/impact", params={"node": node}))
            assert cone == node_cone(
                graph.graph,
                node,
                max_depth=first.payload.limits.cone_max_depth,
                max_nodes=first.payload.limits.cone_max_nodes,
            )
            archived = await client.get(f"/changes/{proposal.change_id}")
            assert archived.status_code == 200
            assert ChangeView.model_validate(archived.json()).change_id == proposal.change_id
            assert error_code(
                await client.get("/impact", params={"node": node, "change": proposal.change_id})
            ) == (404, "not_found")
            assert (store.graph(), store.last_seq) == before


@pytest.mark.parametrize("mode", ["live", "replay"])
async def test_no_snapshot(tmp_path: Path, mode: Literal["live", "replay"]) -> None:
    async with endpoint(tmp_path, mode, snapshot=False) as (_, client):
        assert error_code(await client.get("/impact", params={"node": "m:seed"})) == (
            404,
            "not_found",
        )


@pytest.mark.parametrize("depth", ["", "0", "-1", "1.0", "1.5", "abc", "+1", " 1", "\u0661", "²"])
async def test_bad_depth_and_required_node(tmp_path: Path, depth: str) -> None:
    async with endpoint(tmp_path) as (_, client):
        assert error_code(
            await client.get("/impact", params={"node": "m:seed", "depth": depth})
        ) == (422, "invalid_request")
        assert error_code(await client.get("/impact")) == (422, "invalid_request")


@pytest.mark.parametrize("mode", ["live", "replay"])
async def test_other_reserved_routes_still_answer_501(
    tmp_path: Path, mode: Literal["live", "replay"]
) -> None:
    async with endpoint(tmp_path, mode) as (_, client):
        assert all(path != "/impact" for _, path, _ in NOT_BUILT)
        for method, path, _ in NOT_BUILT:
            path = path.replace("{agent_id}", "agent-1").replace("{change_id}", "change-1")
            path = path.replace("{queued_task_id}", "queued-1").replace("{plan_id}", "plan-1")
            assert error_code(await client.request(method, path, content=b"not json")) == (
                501,
                "not_implemented",
            )


class WorkingModel:
    """Pause after an edit so impact can be read before the model proposes anything."""

    profile = FAKE_MODEL

    def __init__(self) -> None:
        self.calls = 0
        self.waiting = asyncio.Event()
        self.finish = asyncio.Event()
        script = script_json(
            "Add a function",
            [
                {"tool_calls": [{"name": "read_file", "arguments": {"path": "shop/pricing.py"}}]},
                {
                    "tool_calls": [
                        {
                            "name": "edit_file",
                            "arguments": {
                                "path": "shop/pricing.py",
                                "old_text": "def apply_coupon(",
                                "new_text": "def added():\n    return 1\n\ndef apply_coupon(",
                            },
                        }
                    ]
                },
                {"tool_calls": [{"name": "finish", "arguments": {"summary": "Added function"}}]},
            ],
        )
        self.model = Scripts([script])(FAKE_MODEL, "A")

    async def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolSchema]
    ) -> ModelReply:
        self.calls += 1
        if self.calls == 3:
            self.waiting.set()
            await self.finish.wait()
        return await self.model.complete(messages, tools)


async def test_change_uses_current_version_before_proposal_and_after_review_edit(
    tmp_path: Path,
) -> None:
    model = WorkingModel()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda _p, _s: model) as s,
    ):
        created = AgentCreated.model_validate((await start(s)).json())
        await model.waiting.wait()
        assert not any(e.type == "change.proposed" for e in s.events())
        query = {"node": "shop.pricing:added", "change": created.change_id}
        found = success(await s.client.get("/impact", params=query))
        assert found.seeds == ["shop.pricing:added"]
        assert (found.max_depth, found.max_nodes) == (4, 40)
        assert error_code(await s.client.get("/impact", params={"node": query["node"]})) == (
            404,
            "not_found",
        )
        assert error_code(await s.client.get("/impact", params={**query, "node": "missing"})) == (
            404,
            "not_found",
        )
        assert error_code(
            await s.client.get("/impact", params={**query, "change": "change-999"})
        ) == (404, "not_found")
        model.finish.set()
        await s.until(is_type("agent.status", agent_id=created.agent_id, state="ready_for_review"))
        current = FileContent.model_validate(
            (
                await s.client.get(
                    "/files",
                    params={
                        "path": "shop/pricing.py",
                        "agent": created.agent_id,
                    },
                )
            ).json()
        )
        saved = await s.client.put(
            "/files",
            json={
                "path": current.path,
                "base_hash": current.hash,
                "agent_id": created.agent_id,
                "content": current.content + "\n\ndef newer():\n    return added()\n",
            },
        )
        assert saved.status_code == 200, saved.text
        updated = success(await s.client.get("/impact", params=query))
        assert [(e.node_id, e.distance) for e in updated.entries] == [("shop.pricing:newer", 1)]
