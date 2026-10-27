"""The built UI served by FastAPI: the SPA fallback, the API's precedence, cache headers, and no
index.html for an API 404."""

import contextlib
import re
from collections.abc import AsyncIterator, MutableMapping
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from live_helpers import live_server, live_settings

from oversight.api import add_static, create_router, install_error_handlers
from oversight.api.static import API_PATHS
from oversight.app import ReplayConfig, create_app
from oversight.events import EventStore
from oversight.settings import REPO_ROOT, Settings
from oversight_contracts.commands import ErrorResponse, GraphResponse
from oversight_contracts.graph import GraphSnapshot

pytestmark = pytest.mark.anyio
INDEX_HTML = "<!doctype html><title>Oversight</title><div id=root></div>"
SECRET = "outside the build"
EMPTY_GRAPH = GraphSnapshot(
    version="v0", commit="c0", nodes=[], edges=[], parse_errors=[], positions={}
)
REPLAY_LOG = REPO_ROOT / "fixtures" / "events" / "handwritten-level1.jsonl"


@pytest.fixture
def dist(tmp_path: Path) -> Path:
    """A build in miniature: index.html, a hashed script and style sheet, and a plain file. A file
    one level above it holds a secret, and a link inside it points there."""
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX_HTML)
    (root / "assets" / "index-abc123.js").write_text("export {};")
    (root / "assets" / "index-abc123.css").write_text("body{}")
    (root / "robots.txt").write_text("User-agent: *")
    (tmp_path / "secret.txt").write_text(SECRET)
    (root / "assets" / "link.txt").symlink_to(tmp_path / "secret.txt")
    return root


@contextlib.asynccontextmanager
async def ui_app(tmp_path: Path, dist: Path) -> AsyncIterator[tuple[FastAPI, httpx.AsyncClient]]:
    """The real router over a real store with a graph, then the UI, behind an in-process client."""
    async with EventStore(
        tmp_path / "run-20261027-140312.sqlite", "run-20261027-140312", mode="replay"
    ) as store:
        await store.publish(EMPTY_GRAPH)
        app = FastAPI()
        app.include_router(create_router(store, "live"))
        install_error_handlers(app)
        assert add_static(app, dist)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield app, client


async def raw_get(app: FastAPI, path: str) -> tuple[int, bytes]:
    """GET with the path exactly as given: an HTTP client would collapse `..` before sending."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [],
        "http_version": "1.1",
        "scheme": "http",
        "server": ("test", 80),
        "client": ("test", 1),
        "root_path": "",
    }
    sent: list[MutableMapping[str, Any]] = []

    async def receive() -> MutableMapping[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(message)

    await app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status, b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


def assert_api_error(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert ErrorResponse.model_validate(response.json()).error.code == code
    assert "<!doctype" not in response.text.lower()


async def test_the_root_is_index_html_and_revalidates(tmp_path: Path, dist: Path) -> None:
    async with ui_app(tmp_path, dist) as (_, client):
        for path in ("/", "/index.html"):
            response = await client.get(path)
            assert response.status_code == 200
            assert response.text == INDEX_HTML
            assert response.headers["content-type"].startswith("text/html")
            assert response.headers["cache-control"] == "no-cache"


async def test_a_client_route_falls_back_to_index_html(tmp_path: Path, dist: Path) -> None:
    async with ui_app(tmp_path, dist) as (_, client):
        # `/graphs` and `/healthy` only start like an API path: they are the UI's.
        for path in ("/review/change-1", "/graphs", "/healthy", "/a/b/c/"):
            response = await client.get(path)
            assert response.status_code == 200, path
            assert response.text == INDEX_HTML, path
            assert response.headers["cache-control"] == "no-cache", path


async def test_hashed_assets_are_cached_for_a_year_and_other_files_revalidate(
    tmp_path: Path, dist: Path
) -> None:
    async with ui_app(tmp_path, dist) as (_, client):
        script = await client.get("/assets/index-abc123.js")
        style = await client.get("/assets/index-abc123.css")
        plain = await client.get("/robots.txt")
    for asset in (script, style):
        assert asset.status_code == 200
        assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert script.headers["content-type"].startswith("text/javascript")
    assert style.headers["content-type"].startswith("text/css")
    assert plain.text == "User-agent: *"
    assert plain.headers["cache-control"] == "no-cache"


async def test_api_routes_win_over_the_ui(tmp_path: Path, dist: Path) -> None:
    async with ui_app(tmp_path, dist) as (_, client):
        graph = await client.get("/graph")
        health = await client.get("/health")
        not_built = await client.get("/files")
    assert GraphResponse.model_validate(graph.json()).graph.version == "v0"
    assert health.json()["ok"] is True
    assert_api_error(not_built, 501, "not_implemented")


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/graph/nope", 404, "not_found"),
        ("/events/nope", 404, "not_found"),
        ("/health/nope", 404, "not_found"),
        ("/impact/nope/deeper", 404, "not_found"),
        ("/agents", 422, "invalid_request"),  # a POST route asked with GET: the 405 the API maps
        ("/changes/change-1/approve", 422, "invalid_request"),
    ],
)
async def test_an_unknown_api_path_is_the_apis_error_never_index_html(
    tmp_path: Path, dist: Path, path: str, status: int, code: str
) -> None:
    async with ui_app(tmp_path, dist) as (_, client):
        assert_api_error(await client.get(path), status, code)


async def test_a_missing_file_is_not_found_not_index_html(tmp_path: Path, dist: Path) -> None:
    async with ui_app(tmp_path, dist) as (_, client):
        for path in ("/assets/missing.js", "/assets/", "/assets", "/favicon.ico"):
            assert_api_error(await client.get(path), 404, "not_found")


async def test_a_loop_of_links_is_not_found_not_a_server_error(tmp_path: Path, dist: Path) -> None:
    # Python 3.12's Path.resolve raises RuntimeError on a loop; the answer must still be a 404.
    (dist / "assets" / "self.js").symlink_to("self.js")
    (dist / "assets" / "ping.js").symlink_to("pong.js")
    (dist / "assets" / "pong.js").symlink_to("ping.js")
    async with ui_app(tmp_path, dist) as (_, client):
        for path in ("/assets/self.js", "/assets/ping.js", "/assets/pong.js"):
            assert_api_error(await client.get(path), 404, "not_found")


async def test_no_path_leaves_the_build(tmp_path: Path, dist: Path) -> None:
    async with ui_app(tmp_path, dist) as (app, client):
        # A link inside the build that points out of it.
        assert_api_error(await client.get("/assets/link.txt"), 404, "not_found")
        for path in ("/../secret.txt", "/assets/../../secret.txt", "/..", "/../dist/../secret.txt"):
            _, body = await raw_get(app, path)
            assert SECRET.encode() not in body, path
        status, _ = await raw_get(app, "/../secret.txt")
        assert status == 404


def test_without_a_build_there_is_no_ui(tmp_path: Path) -> None:
    app = FastAPI()
    assert add_static(app, tmp_path / "missing") is False
    empty = tmp_path / "empty"
    empty.mkdir()
    assert add_static(app, empty) is False
    assert len(app.routes) == len(FastAPI().routes)  # only the docs routes every app has


def test_the_api_paths_are_the_frontend_proxys_and_the_routers() -> None:
    """One list in two languages: vite.config.ts's `apiPaths` decides what Vite forwards, and
    `API_PATHS` what FastAPI never answers with the UI. Every route the router has is in both."""
    config = (REPO_ROOT / "frontend" / "vite.config.ts").read_text(encoding="utf-8")
    match = re.search(r"const apiPaths = \[(.*?)\];", config, re.DOTALL)
    assert match is not None, "vite.config.ts no longer declares apiPaths"
    proxied = {
        path.strip().strip("\"'").lstrip("/") for path in match.group(1).split(",") if path.strip()
    }
    assert proxied == API_PATHS

    store = EventStore(Path("unused.sqlite"), "run-20261027-140312", mode="replay")
    paths = {route.path for route in create_router(store, "live").routes}  # pyright: ignore[reportAttributeAccessIssue,reportUnknownVariableType,reportUnknownMemberType]
    assert {path.split("/")[1] for path in paths} == API_PATHS


async def test_a_live_server_serves_the_build_and_never_compresses(
    tmp_path: Path, dist: Path
) -> None:
    gzip = {"accept-encoding": "gzip"}
    async with live_server(live_settings(tmp_path, frontend_dist=dist)) as s:
        index = await s.client.get("/", headers=gzip)
        asset = await s.client.get("/assets/index-abc123.js", headers=gzip)
        graph = await s.client.get("/graph", headers=gzip)
        async with s.client.stream("GET", "/events", headers=gzip) as stream:
            assert stream.status_code == 200
            assert stream.headers["content-type"].startswith("text/event-stream")
            assert "content-encoding" not in stream.headers
    assert index.text == INDEX_HTML
    assert asset.text == "export {};"
    assert "content-encoding" not in asset.headers
    assert GraphResponse.model_validate(graph.json()).graph.nodes


async def test_a_live_server_without_a_build_has_no_ui(tmp_path: Path) -> None:
    async with live_server(live_settings(tmp_path)) as s:
        assert_api_error(await s.client.get("/"), 404, "not_found")


async def test_a_replay_never_serves_the_ui(tmp_path: Path, dist: Path) -> None:
    settings = Settings(runtime_dir=tmp_path / ".runtime", frontend_dist=dist)
    app = create_app(settings, ReplayConfig(log=REPLAY_LOG, speed=0))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert_api_error(await client.get("/"), 404, "not_found")
