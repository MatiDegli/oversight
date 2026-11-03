"""Private fake HTTP matrix using the landed manager and its atomic ownership/drain seam."""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from workspace.preview_fakes import harness

from oversight.api.body_limit import BodyLimit
from oversight.api.errors import install_error_handlers
from oversight.api.previews import add_preview_routes
from oversight.api.security import ApiGuard, AuthorityPolicy
from oversight.limits import MAX_BODY_BYTES
from oversight.settings import REPO_ROOT
from oversight.workspace import IntegrityError, PreviewError, Workspace
from oversight_contracts.commands import ERROR_STATUS
from oversight_contracts.previews import PreviewDetails, PreviewStatus

pytestmark = pytest.mark.anyio
ID = "preview-" + "b" * 32
TOKEN = "B" * 43


@pytest.fixture
async def preview(tmp_path):
    workspace = Workspace(
        tmp_path / ".runtime",
        REPO_ROOT / "demo-repo",
        pytest_pin="9.1.1",
    )
    await workspace.acquire_runtime()
    await workspace.reset()
    h = await harness(workspace)
    try:
        yield h
    finally:
        await h.close()
        workspace.release_runtime()


def app_for(previews=None):
    app = FastAPI()
    add_preview_routes(app.router, previews)
    app.add_middleware(BodyLimit)
    app.add_middleware(ApiGuard, policy=AuthorityPolicy(8710))
    install_error_handlers(app)
    return app


def client_for(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://127.0.0.1:8710",
        trust_env=False,
    )


def start_body(h):
    return dict(run_id="run-20261103-000000", agent_id="agent-1", revision=1, head_commit=h.head)


def error(response, code):
    assert response.status_code == ERROR_STATUS[code]
    assert response.headers["cache-control"] == "no-store"
    detail = response.json()["error"]
    assert detail["code"] == code
    assert set(detail) == (
        {"code", "message", "missing"} if code == "not_configured" else {"code", "message"}
    )
    if code == "not_configured":
        assert detail["missing"] == []
    assert (
        TOKEN not in response.text and "raw" not in response.text and "secret" not in response.text
    )


async def test_create_get_renew_stop_and_repeated_stop_require_same_token(preview):
    h = preview
    async with client_for(app_for(h.manager)) as client:
        response = await client.post("/previews", json=start_body(h))
        assert response.status_code == 202 and response.headers["cache-control"] == "no-store"
        assert set(response.json()) == {"preview_id", "lease_token"}
        preview_id, token = response.json().values()
        await h.sink.wait("ready", preview_id)
        details = await client.get(f"/previews/{preview_id}")
        data = PreviewDetails.model_validate(details.json())
        assert data.status.reason is None and data.url is not None
        assert set(details.json()) == {"status", "url"} and "lease_token" not in details.text
        count = len(h.sink.records)
        for path in [f"/previews/{preview_id}", f"/previews/{preview_id}/lease"]:
            result = await (
                client.get(path)
                if path.endswith(preview_id)
                else client.post(path, json={"lease_token": token})
            )
            assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
        assert len(h.sink.records) == count
        error(
            await client.post(f"/previews/{preview_id}/stop", json={"lease_token": TOKEN}),
            "forbidden",
        )
        for _ in range(2):
            result = await client.post(f"/previews/{preview_id}/stop", json={"lease_token": token})
            assert PreviewStatus.model_validate(result.json()).state == "stopped"
            assert h.processes[0].dead and not h.manager.has_owner
        assert len([r for r in h.sink.records if r.state == "stopped"]) == 1
        details = (await client.get(f"/previews/{preview_id}")).json()
        assert details["url"] is None and details["status"]["reason"] == "closed"
        error(
            await client.post(f"/previews/{preview_id}/lease", json={"lease_token": token}),
            "wrong_state",
        )


@pytest.mark.parametrize(
    "state,reason",
    [
        ("starting", None),
        ("ready", None),
        ("stopping", "closed"),
        ("stopped", "closed"),
        ("failed", "cleanup_unverified"),
    ],
)
async def test_explicit_projection_keeps_nulls_and_excludes_private_extras(state, reason):
    record = SimpleNamespace(
        preview_id=ID,
        run_id="run-20261103-000000",
        agent_id="agent-1",
        change_id="change-1",
        revision=1,
        head_commit="head",
        state=state,
        reason=reason,
        url="http://127.0.0.1:19800/",
        lease_token=TOKEN,
        output="secret",
    )

    async def get(_):
        return record

    async with client_for(app_for(SimpleNamespace(get=get))) as client:
        response = await client.get(f"/previews/{ID}")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["url"] == (record.url if state == "ready" else None)
    assert response.json()["status"]["reason"] == reason
    assert (
        "lease_token" not in response.text
        and "output" not in response.text
        and TOKEN not in response.text
    )


@pytest.mark.parametrize(
    "code",
    [
        "not_found",
        "forbidden",
        "invalid_request",
        "wrong_state",
        "not_configured",
        "metadata",
        "unexpected",
    ],
)
@pytest.mark.parametrize("operation", ["start", "get", "lease", "stop"])
async def test_landed_refusals_and_safe_internal_copy(code, operation):
    async def refuse(*args, **kwargs):
        if code == "metadata":
            raise IntegrityError("raw secret metadata", ".git")
        if code == "unexpected":
            raise RuntimeError("raw secret output")
        raise PreviewError("Live preview refused.", code=code)

    manager = SimpleNamespace(start=refuse, get=refuse, renew=refuse, stop=refuse)
    async with client_for(app_for(manager)) as client:
        if operation == "start":
            response = await client.post(
                "/previews",
                json={
                    "run_id": "run-20261103-000000",
                    "agent_id": "agent-1",
                    "revision": 1,
                    "head_commit": "head",
                },
            )
        elif operation == "get":
            response = await client.get(f"/previews/{ID}")
        else:
            response = await client.post(f"/previews/{ID}/{operation}", json={"lease_token": TOKEN})
    error(
        response,
        "wrong_state" if code == "metadata" else "internal" if code == "unexpected" else code,
    )


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "/previews", b'{"secret":'),
        ("POST", "/previews", b"{}"),
        ("POST", f"/previews/{ID}/stop", b"{}"),
        ("POST", f"/previews/{ID}/lease", b'{"lease_token":"bad secret"}'),
        ("GET", "/previews/bad", b""),
        ("POST", "/previews/bad/stop", b'{"lease_token":"' + TOKEN.encode() + b'"}'),
        ("POST", f"/previews/{ID}/stop", b'{"lease_token":"' + TOKEN.encode() + b'","extra":1}'),
    ],
)
async def test_validation_and_replay_precedence(method, path, body, preview):
    async with client_for(app_for(preview.manager)) as client:
        error(await client.request(method, path, content=body), "invalid_request")
    async with client_for(app_for()) as client:
        error(await client.request(method, path, content=body), "not_implemented")
    assert not preview.manager.has_owner and not preview.sink.records


@pytest.mark.parametrize("origin", ["null", "http://127.0.0.1:19800"])
async def test_guard_body_limit_precedence_and_no_store(origin):
    async with client_for(app_for()) as client:
        error(
            await client.post(
                "/previews",
                content=b"raw secret",
                headers={"Origin": origin, "Content-Length": str(MAX_BODY_BYTES + 1)},
            ),
            "forbidden",
        )
        error(await client.post("/previews", content=b"x" * (MAX_BODY_BYTES + 1)), "too_large")
        error(await client.request("DELETE", f"/previews/{ID}"), "invalid_request")
        error(await client.get("/previews/unknown/extra"), "not_found")


async def test_concurrent_admission_and_cross_preview_tokens(preview):
    h = preview
    async with client_for(app_for(h.manager)) as client:
        responses = await asyncio.gather(
            *(client.post("/previews", json=start_body(h)) for _ in range(2))
        )
        assert sorted(r.status_code for r in responses) == [202, 409]
        first = next(r.json() for r in responses if r.status_code == 202)
        await client.post(
            f"/previews/{first['preview_id']}/stop", json={"lease_token": first["lease_token"]}
        )
        second = (await client.post("/previews", json=start_body(h))).json()
        for action in ["lease", "stop"]:
            error(
                await client.post(
                    f"/previews/{second['preview_id']}/{action}",
                    json={"lease_token": first["lease_token"]},
                ),
                "forbidden",
            )
            error(
                await client.post(
                    f"/previews/{first['preview_id']}/{action}",
                    json={"lease_token": second["lease_token"]},
                ),
                "forbidden",
            )


async def test_failed_stop_preserves_quarantine_and_no_false_repeat_success(preview):
    h = preview
    async with client_for(app_for(h.manager)) as client:
        created = (await client.post("/previews", json=start_body(h))).json()
        await h.sink.wait("ready")
        process = h.processes[0]
        process.stop_failure = True
        for _ in range(2):
            error(
                await client.post(
                    f"/previews/{created['preview_id']}/stop",
                    json={"lease_token": created["lease_token"]},
                ),
                "wrong_state",
            )
        details = (await client.get(f"/previews/{created['preview_id']}")).json()
        assert (
            details["status"]["state"] == "failed"
            and details["status"]["reason"] == "cleanup_unverified"
        )
        assert h.manager.has_owner and h.workspace.integrity.main_suspect
        assert h.manager.registry.read() is not None
        assert not process.dead
