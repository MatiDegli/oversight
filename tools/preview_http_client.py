"""Contract-backed, fake acceptance consumer. Lease ownership stays in this object's memory."""

import asyncio
from typing import TypeVar

import httpx
from pydantic import BaseModel
from tools.preview_scenario import Candidate, ScenarioError, loopback_url, require

from oversight_contracts.events import AgentStatus
from oversight_contracts.previews import (
    PreviewCreated,
    PreviewDetails,
    PreviewStatus,
    PreviewTokenRequest,
    StartPreview,
)

T = TypeVar("T", bound=BaseModel)


class PreviewHttpClient:
    def __init__(self, client: httpx.AsyncClient):
        # The supplied client is the scenario's no-proxy/no-redirect transport. Refuse redirects
        # again per call; never construct a client which could pick up environment credentials.
        loopback_url(str(client.base_url), root=True)
        require(not client.trust_env, "Preview acceptance refuses environment proxy discovery")
        self._client = client
        self._receipt: PreviewCreated | None = None
        self._candidate: Candidate | None = None
        self._uncertain = False

    async def _request(
        self, method: str, path: str, model: type[T], body: BaseModel | None = None
    ) -> T:
        try:
            async with asyncio.timeout(5):
                response = await self._client.request(
                    method,
                    path,
                    json=body.model_dump(mode="json") if body else None,
                    timeout=5,
                    follow_redirects=False,
                )
                require(
                    response.status_code == (202 if model is PreviewCreated else 200),
                    "Preview request refused",
                )
                require(
                    (model is AgentStatus or response.headers.get("cache-control") == "no-store"),
                    "Preview response is not private",
                )
                return model.model_validate_json(response.content)
        except Exception:
            raise ScenarioError("Preview request could not be verified") from None

    def _matches(self, status: PreviewStatus, candidate: Candidate) -> None:
        require(
            self._receipt is not None
            and status.preview_id == self._receipt.preview_id
            and (
                status.run_id,
                status.agent_id,
                status.change_id,
                status.revision,
                status.head_commit,
            )
            == (
                candidate.run_id,
                candidate.agent_id,
                candidate.change_id,
                candidate.revision,
                candidate.head_commit,
            ),
            "Preview candidate identity differs",
        )

    async def _wait_candidate(self, candidate: Candidate) -> None:
        # Proposal commitment precedes run-ended admission. Never retry a Start mutation.
        async with asyncio.timeout(30):
            while True:
                status = await self._request("GET", f"/agents/{candidate.agent_id}", AgentStatus)
                require(
                    status.agent_id == candidate.agent_id
                    and status.change_id == candidate.change_id,
                    "Preview coding-agent identity differs",
                )
                if status.state == "ready_for_review":
                    return
                require(
                    status.state in {"working", "starting", "revising"},
                    "Preview candidate is not ready",
                )
                await asyncio.sleep(0.05)

    async def open(self, candidate: Candidate) -> None:
        require(
            self._receipt is None and not self._uncertain, "Preview closure must be verified first"
        )
        request = StartPreview.model_validate(candidate.opening())
        await self._wait_candidate(candidate)
        self._uncertain = True  # A lost receipt never authorizes another start or later Git.
        self._candidate = candidate
        self._receipt = await self._request("POST", "/previews", PreviewCreated, request)
        # Retain this receipt even when the follow-up is stale: Stop still owns cleanup.
        details = await self._request(
            "GET", f"/previews/{self._receipt.preview_id}", PreviewDetails
        )
        self._matches(details.status, candidate)
        self._uncertain = False

    async def ready(self, candidate: Candidate) -> str:
        require(
            self._receipt is not None and self._candidate == candidate,
            "Preview ownership is absent",
        )
        assert self._receipt is not None
        try:
            async with asyncio.timeout(30):
                while True:
                    details = await self._request(
                        "GET", f"/previews/{self._receipt.preview_id}", PreviewDetails
                    )
                    self._matches(details.status, candidate)
                    if details.status.state == "ready":
                        require(details.url is not None, "Ready preview URL is absent")
                        url = loopback_url(str(details.url))
                        require(
                            httpx.URL(url).port != self._client.base_url.port,
                            "Preview shares backend port",
                        )
                        return url
                    require(details.status.state == "starting", "Preview did not become ready")
                    await asyncio.sleep(0.05)
        except Exception:
            raise ScenarioError("Preview readiness could not be verified") from None

    async def _token(self, action: str) -> PreviewStatus:
        require(
            self._receipt is not None and self._candidate is not None, "Preview ownership is absent"
        )
        assert self._receipt is not None and self._candidate is not None
        result = await self._request(
            "POST",
            f"/previews/{self._receipt.preview_id}/{action}",
            PreviewStatus,
            PreviewTokenRequest(lease_token=self._receipt.lease_token),
        )
        self._matches(result, self._candidate)
        return result

    async def renew(self) -> None:
        result = await self._token("lease")
        require(result.state in {"starting", "ready"}, "Preview renewal was not confirmed")

    async def stop(self) -> None:
        if self._receipt is None:
            require(not self._uncertain, "Preview closure is unverified; wait for server expiry")
            return
        self._uncertain = True
        result = await self._token("stop")
        require(
            result.state in {"stopped", "failed"} and result.reason != "cleanup_unverified",
            "Preview closure is unverified; no subsequent Git is permitted",
        )
        self._receipt = None
        self._candidate = None
        self._uncertain = False


def factory(client: httpx.AsyncClient) -> PreviewHttpClient:
    return PreviewHttpClient(client)
