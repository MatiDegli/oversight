"""Mock HTTP proves the consumer's identity fencing and retained cleanup ownership."""

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from tools.preview_http_client import factory
from tools.preview_scenario import Candidate, ScenarioError

from oversight_contracts.events import AgentStatus
from oversight_contracts.previews import PreviewStatus

pytestmark = pytest.mark.anyio
CANDIDATE = Candidate("run-20261103-000000", "agent-1", "change-1", 1, "head")
ID, TOKEN = "preview-" + "a" * 32, "A" * 43
STATUS = PreviewStatus(preview_id=ID, **CANDIDATE.__dict__, state="ready")
AGENT = AgentStatus(
    agent_id="agent-1",
    slot="A",
    change_id="change-1",
    task="Task",
    model="fake",
    state="ready_for_review",
    step=1,
    max_steps=10,
    detail=None,
)


class Wire:
    def __init__(self):
        self.calls = []
        self.agent = AGENT
        self.status = STATUS
        self.url = "http://127.0.0.1:19900/"
        self.failed = False
        self.extra = False
        self.wrong_details = False

    def __call__(self, request):
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        headers = {"cache-control": "no-store"}
        if request.url.path == "/agents/agent-1":
            return httpx.Response(200, json=self.agent.model_dump())
        if self.failed:
            return httpx.Response(500, text="secret child output", headers=headers)
        if request.url.path == "/previews":
            receipt = {"preview_id": ID, "lease_token": TOKEN}
            if self.extra:
                receipt["output"] = "secret"
            return httpx.Response(202, json=receipt, headers=headers)
        status = self.status.model_dump()
        if request.method == "GET":
            if self.wrong_details:
                status["head_commit"] = "wrong"
            return httpx.Response(
                200,
                json={"status": status, "url": self.url if status["state"] == "ready" else None},
                headers=headers,
            )
        if request.url.path.endswith("/stop"):
            status.update(state="stopped", reason="closed")
        return httpx.Response(200, json=status, headers=headers)


async def test_exact_requests_memory_only_tokens_and_verified_stop():
    wire = Wire()
    async with httpx.AsyncClient(
        base_url="http://localhost:8710", transport=httpx.MockTransport(wire), trust_env=False
    ) as client:
        previews = factory(client)
        await previews.open(CANDIDATE)
        assert await previews.ready(CANDIDATE) == wire.url
        await previews.renew()
        await previews.stop()
        await previews.stop()
    assert wire.calls[1] == ("POST", "/previews", CANDIDATE.opening())
    assert wire.calls[-2:] == [
        ("POST", f"/previews/{ID}/lease", {"lease_token": TOKEN}),
        ("POST", f"/previews/{ID}/stop", {"lease_token": TOKEN}),
    ]
    assert TOKEN not in repr(previews)
    assert all(TOKEN not in path for _, path, _ in wire.calls)


@pytest.mark.parametrize("state", ["ready_for_review", "halted"])
async def test_unresolved_terminal_proposal_can_open_and_close(state):
    wire = Wire()
    wire.agent = AGENT.model_copy(update={"state": state})
    async with httpx.AsyncClient(
        base_url="http://localhost:8710", transport=httpx.MockTransport(wire), trust_env=False
    ) as client:
        previews = factory(client)
        await previews.open(CANDIDATE)
        assert await previews.ready(CANDIDATE) == wire.url
        await previews.stop()
    assert wire.calls[1] == ("POST", "/previews", CANDIDATE.opening())
    assert wire.calls[-1] == ("POST", f"/previews/{ID}/stop", {"lease_token": TOKEN})


@pytest.mark.parametrize("state", ["ready_for_review", "halted"])
async def test_terminal_agent_with_wrong_proposal_never_starts_preview(state):
    wire = Wire()
    wire.agent = AGENT.model_copy(update={"state": state, "change_id": "change-2"})
    async with httpx.AsyncClient(
        base_url="http://localhost:8710", transport=httpx.MockTransport(wire), trust_env=False
    ) as client:
        with pytest.raises(ScenarioError, match="coding-agent identity differs"):
            await factory(client).open(CANDIDATE)
    assert all(method == "GET" for method, _, _ in wire.calls)


async def test_stale_details_still_retain_receipt_for_authenticated_cleanup():
    wire = Wire()
    wire.wrong_details = True
    async with httpx.AsyncClient(
        base_url="http://localhost:8710", transport=httpx.MockTransport(wire), trust_env=False
    ) as client:
        previews = factory(client)
        with pytest.raises(ScenarioError):
            await previews.open(CANDIDATE)
        with pytest.raises(ScenarioError):
            await previews.open(replace(CANDIDATE, revision=2))
        wire.wrong_details = False
        await previews.stop()
    assert wire.calls[-1] == ("POST", f"/previews/{ID}/stop", {"lease_token": TOKEN})


@pytest.mark.parametrize("action", ["open", "stop"])
async def test_concurrent_operations_wait_for_admission_and_preserve_cleanup(action):
    wire = Wire()
    entered, release = asyncio.Event(), asyncio.Event()

    async def delayed(request):
        if request.url.path == "/agents/agent-1":
            entered.set()
            await asyncio.wait_for(release.wait(), 5)
        return wire(request)

    async with httpx.AsyncClient(
        base_url="http://localhost:8710", transport=httpx.MockTransport(delayed), trust_env=False
    ) as client:
        previews = factory(client)
        first = asyncio.create_task(previews.open(CANDIDATE))
        await asyncio.wait_for(entered.wait(), 5)
        second = asyncio.create_task(
            previews.open(CANDIDATE) if action == "open" else previews.stop()
        )
        try:
            await asyncio.sleep(0)
            assert not second.done()
            release.set()
            await asyncio.wait_for(first, 5)
            if action == "open":
                with pytest.raises(ScenarioError, match="closure must be verified"):
                    await asyncio.wait_for(second, 5)
            else:
                await asyncio.wait_for(second, 5)
            await previews.stop()
        finally:
            release.set()
            for task in (first, second):
                if not task.done():
                    task.cancel()
            await asyncio.gather(first, second, return_exceptions=True)
    assert sum(method == "POST" and path == "/previews" for method, path, _ in wire.calls) == 1
    assert wire.calls[-1] == ("POST", f"/previews/{ID}/stop", {"lease_token": TOKEN})


@pytest.mark.parametrize(
    "failure", ["lost", "extra", "uncertain_stop", "cross_preview", "wrong_change", "bad_url"]
)
async def test_refusals_never_release_uncertain_ownership(failure):
    wire = Wire()
    wire.failed = failure == "lost"
    wire.extra = failure == "extra"
    async with httpx.AsyncClient(
        base_url="http://localhost:8710", transport=httpx.MockTransport(wire), trust_env=False
    ) as client:
        previews = factory(client)
        if failure in {"lost", "extra"}:
            with pytest.raises(ScenarioError, match="could not be verified") as error:
                await previews.open(CANDIDATE)
            assert TOKEN not in str(error.value) and "secret" not in str(error.value)
        else:
            await previews.open(CANDIDATE)
            if failure == "uncertain_stop":
                wire.status = STATUS.model_copy(
                    update={"state": "failed", "reason": "cleanup_unverified"}
                )
                # A failed response itself must never certify drainage.
                wire.failed = True
            elif failure == "cross_preview":
                wire.status = STATUS.model_copy(update={"preview_id": "preview-" + "b" * 32})
            elif failure == "wrong_change":
                wire.status = STATUS.model_copy(update={"change_id": "change-2"})
            else:
                wire.url = "https://external.invalid/"
            with pytest.raises(ScenarioError):
                await previews.ready(CANDIDATE) if failure == "bad_url" else await previews.renew()
        wire.failed = True
        with pytest.raises(ScenarioError):
            await previews.stop()
        with pytest.raises(ScenarioError):
            await previews.open(CANDIDATE)


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8710",
        "http://localhost",
        "https://localhost:8710",
        "http://user:pass@localhost:8710",
        "http://localhost:8710/?private=1",
        "http://localhost:8710/#private",
        "http://localhost:8710/graph",
    ],
)
async def test_wrong_or_unsafe_backend_parent_refuses_before_requests(url):
    wire = Wire()
    async with httpx.AsyncClient(
        base_url=url, transport=httpx.MockTransport(wire), trust_env=False
    ) as client:
        with pytest.raises(ScenarioError):
            factory(client)
    assert wire.calls == []


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:19900/",
        "http://127.0.0.1:8710/",
        "http://127.0.0.1:19900/cart",
        "http://127.0.0.1:19900/?private=1",
        "http://127.0.0.1:19900/#private",
        "http://user:pass@127.0.0.1:19900/",
        "http://127.0.0.1/",
    ],
)
async def test_wrong_or_unsafe_frame_retains_authenticated_cleanup(url):
    wire = Wire()
    wire.url = url
    async with httpx.AsyncClient(
        base_url="http://localhost:8710", transport=httpx.MockTransport(wire), trust_env=False
    ) as client:
        previews = factory(client)
        await previews.open(CANDIDATE)
        with pytest.raises(ScenarioError):
            await previews.ready(CANDIDATE)
        await previews.stop()
    assert wire.calls[-1] == ("POST", f"/previews/{ID}/stop", {"lease_token": TOKEN})
