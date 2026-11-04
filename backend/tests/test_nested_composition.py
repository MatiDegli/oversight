"""In-process nested application admission, role playback and local preview refusal."""

import asyncio
import json
import shutil
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI

from oversight.app import LiveRun
from oversight.nested_clients import AGENTS_LABEL, SCRIPTED_ACTIONS_NOTICE
from oversight.nested_entry import compose_app, parse_config
from oversight.nested_runtime import NestedConfig, create_nested_app
from oversight.settings import REPO_ROOT
from oversight.workspace import PreviewError

pytestmark = pytest.mark.anyio


def inputs(tmp_path: Path) -> list[str]:
    tree, state = tmp_path / "tree", tmp_path / "preview/state"
    shutil.copytree(REPO_ROOT / "demo-repo", tree / "demo-repo")
    scripts = tree / "fixtures/fake-model"
    scripts.mkdir(parents=True)
    shutil.copyfile(
        REPO_ROOT / "fixtures/fake-model/demo-preview-self-nested.json",
        scripts / "demo-preview-self-nested.json",
    )
    layouts = tree / "fixtures/layouts"
    layouts.mkdir()
    shutil.copyfile(REPO_ROOT / "fixtures/layouts/demo-repo.json", layouts / "demo-repo.json")
    (state / "dist").mkdir(parents=True)
    (state / "dist/index.html").write_text("<html>prepared nested assets</html>")
    # A source witness absent from the development template.
    pricing = tree / "demo-repo/shop/pricing.py"
    pricing.write_text(pricing.read_text() + "\n# disposable source witness\n")
    return [
        "--tree",
        str(tree),
        "--state",
        str(state),
        "--demo-python",
        sys.executable,
        "--api-port",
        "18101",
        "--public-port",
        "18101",
    ]


@asynccontextmanager
async def session(
    config: NestedConfig,
) -> AsyncIterator[tuple[FastAPI, LiveRun, httpx.AsyncClient]]:
    app = create_nested_app(config, app_factory=compose_app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:18101"
        ) as client,
    ):
        yield app, app.state.live, client


async def event(live: LiveRun, kind: str, **fields: object) -> dict[str, Any]:
    async with asyncio.timeout(40):
        async for stored in live.store.subscribe(0):
            payload = json.loads(stored.envelope)["payload"]
            if stored.type == kind and all(payload.get(k) == v for k, v in fields.items()):
                return payload
    raise AssertionError("event store closed")


def test_explicit_entry_and_static_api(tmp_path: Path) -> None:
    config = parse_config(inputs(tmp_path))
    app = create_nested_app(config, app_factory=compose_app)
    assert app.state.settings.runtime_dir == config.preview_dir / "state/.runtime"
    assert app.state.settings.vite_port is None
    assert config.frontend_dist == config.preview_dir / "state/dist"
    assert config.coder_script == config.lead_script
    with pytest.raises(SystemExit):
        parse_config([])
    argv = inputs(tmp_path / "other")
    argv[-1] = "18102"
    with pytest.raises(ValueError, match="one listener"):
        create_nested_app(parse_config(argv), app_factory=compose_app)


@pytest.mark.parametrize("model", [None, "fake", "fake-b"])
@pytest.mark.parametrize("text", ["Add a coupon", "Completely unrelated entered text"])
async def test_lead_proposes_then_queue_uses_distinct_coder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: str | None, text: str
) -> None:
    import oversight.nested_runtime as runtime

    witnesses: dict[str, Mock] = {}
    for role in ("coder", "lead"):
        original = getattr(runtime, f"make_{role}_factory")

        def observed(script, *, policy, role=role, original=original):
            witnesses[role] = Mock(wraps=original(script, policy=policy))
            return witnesses[role]

        monkeypatch.setattr(runtime, f"make_{role}_factory", observed)
    async with session(parse_config(inputs(tmp_path))) as (_, live, client):
        body = {"task": text} | ({"model": model} if model else {})
        response = await client.post("/leads", json=body)
        assert response.status_code == 201, response.text
        reply = await event(live, "lead.reply")
        await event(live, "lead.status", state="idle")
        assert AGENTS_LABEL in reply["text"] and SCRIPTED_ACTIONS_NOTICE in reply["text"]
        assert witnesses["lead"].call_count == 1 and witnesses["coder"].call_count == 0
        assert witnesses["lead"].call_args.args[0].id == (model or "fake")
        assert not list((live.workspace.runtime_root / "worktrees").glob("*"))
        assert "NESTED10" not in (live.workspace.main.root / "shop/pricing.py").read_text()
        plan = next(iter(live.plans.plans.values()))
        response = await client.post(
            "/queue", json={"plan_id": plan.plan_id, "plan_revision": plan.revision}
        )
        assert response.status_code == 201, response.text
        status = await event(live, "agent.status", state="ready_for_review")
        assert witnesses["coder"].call_count == 1 and witnesses["lead"].call_count == 1
        tree = live.workspace.worktree(status["agent_id"]).root
        content = (tree / "shop/pricing.py").read_text()
        assert '"NESTED10": 10' in content and "disposable source witness" in content
        proposal = await event(live, "change.proposed")
        assert proposal["tests"]["outcome"] == "passed"
        assert {f["path"] for f in proposal["files"]} == {
            "shop/pricing.py",
            "tests/test_pricing.py",
        }
        assert live.workspace.main.root.is_relative_to(live.workspace.runtime_root)


@pytest.mark.parametrize("role", ["agents", "leads"])
@pytest.mark.parametrize("model", ["muse-spark", "muse-spark-open", "unknown"])
async def test_paid_and_unknown_refuse_before_effects(
    tmp_path: Path, role: str, model: str
) -> None:
    async with session(parse_config(inputs(tmp_path))) as (_, live, client):
        response = await client.post(f"/{role}", json={"task": "valid task", "model": model})
        assert response.status_code == 422, response.text
        assert "scripted" in response.text
        assert not list((live.workspace.runtime_root / "worktrees").glob("*"))
        assert not live.leads.leads


@pytest.mark.parametrize("depth", ["0", "1", "-1", "999"])
async def test_preview_refuses_before_reservation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, depth: str
) -> None:
    monkeypatch.setenv("OVERSIGHT_PREVIEW_DEPTH", depth)
    monkeypatch.setenv("OVERSIGHT_PREVIEW_ENABLED", "true")
    async with session(parse_config(inputs(tmp_path))) as (_, live, client):
        created = await client.post("/agents", json={"task": "coupon"})
        assert created.status_code == 201
        status = await event(live, "agent.status", state="ready_for_review")
        proposal = await event(live, "change.proposed")
        body: dict[str, Any] = dict(
            run_id=live.run_id,
            agent_id=status["agent_id"],
            revision=proposal["revision"],
            head_commit=proposal["head_commit"],
        )
        capabilities = Mock(side_effect=AssertionError("further preview capability created"))
        monkeypatch.setattr("oversight.workspace.preview.bind_listener", capabilities)
        monkeypatch.setattr(live.previews.registry, "write", capabilities)
        monkeypatch.setattr(live.previews, "process_factory", capabilities)
        response = await client.post("/previews", json=body)
        assert response.status_code == 503, response.text
        assert "Further managed previews are disabled" in response.text
        with pytest.raises(PreviewError, match="Further managed previews are disabled"):
            await live.previews.start(**body)
        capabilities.assert_not_called()
        assert not live.previews.registry.path.exists()
        assert not live.previews.has_owner
        assert not live.previews.profile.enabled
        assert not live.leads._preview_available()
        assert live.previews._token is None and not live.previews._records
        assert not list((live.workspace.runtime_root / "previews").glob("**/ownership*"))
        assert (await client.get("/")).text == "<html>prepared nested assets</html>"


@pytest.mark.parametrize("model", [None, "fake", "fake-b"])
async def test_direct_coder_default_and_explicit_scripted_starts(
    tmp_path: Path, model: str | None
) -> None:
    async with session(parse_config(inputs(tmp_path))) as (_, live, client):
        body = {"task": "entered text is not a script selector"} | (
            {"model": model} if model else {}
        )
        response = await client.post("/agents", json=body)
        assert response.status_code == 201, response.text
        status = await event(live, "agent.status", state="ready_for_review")
        assert status["model"] == (model or "fake")
        tree = live.workspace.worktree(status["agent_id"]).root
        assert '"NESTED10": 10' in (tree / "shop/pricing.py").read_text()
        assert "disposable source witness" in (tree / "shop/pricing.py").read_text()


async def test_server_awaits_single_loopback_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    from oversight import nested_entry

    config = parse_config(inputs(tmp_path))
    server = Mock()
    server.serve = AsyncMock()
    constructor = Mock(return_value=server)
    monkeypatch.setattr("oversight.nested_entry.uvicorn.Server", constructor)
    await nested_entry.serve(config)
    server.serve.assert_awaited_once_with()
    [serving] = constructor.call_args.args
    assert serving.host == "127.0.0.1" and serving.port == config.api_port
    assert not serving.proxy_headers
