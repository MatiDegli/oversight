"""Opt-in, fake-only two-proposal preview story. Never starts/configures a backend.

The public preview contracts are absent on base 36a0d45 (contracts 1.17.0).
A/B must supply a contract-backed PreviewClient binding before real-C acceptance.
No replacement preview schema, event or lifecycle implementation lives here. The
binding owns wire validation, private lease-token storage and verified-stop waits.

Example once that approved binding exists:
  uv run --frozen python tools/preview_scenario.py --backend http://127.0.0.1:8710 \
    --preview-client approved_module:factory --dedicated-fake-c
This is an integration prerequisite, not evidence of C/browser acceptance.
"""

import argparse
import asyncio
import importlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

import httpx
from pydantic import TypeAdapter

from oversight.agents import parse_script
from oversight_contracts.events import ServerEvent

SCRIPT = Path(__file__).resolve().parents[1] / "fixtures/fake-model/demo-preview-coupon.json"
EVENT = TypeAdapter(ServerEvent)
REQUEST_SECONDS = 5
WAIT_SECONDS = 30
SCENARIO_SECONDS = 120


class ScenarioError(RuntimeError):
    """Fixed diagnostics only; never echo response bodies, URLs or private tokens."""


def require(condition: object, message: str) -> None:
    if not condition:
        raise ScenarioError(message)


def loopback_url(url: str, *, root: bool = False) -> str:
    try:
        parts = urlsplit(url)
        require(
            parts.scheme == "http"
            and parts.hostname == "127.0.0.1"
            and parts.port is not None
            and 1 <= parts.port <= 65535
            and parts.username is None
            and parts.password is None
            and not parts.query
            and not parts.fragment,
            "Expected explicit loopback HTTP URL",
        )
        if root:
            require(parts.path in ("", "/"), "Expected backend origin without a path")
        return url.rstrip("/") if root else url
    except ValueError:
        raise ScenarioError("Invalid loopback URL") from None


@dataclass(frozen=True)
class Candidate:
    """Local evidence from an existing committed change.proposed, not an API type."""

    run_id: str
    agent_id: str
    change_id: str
    revision: int
    head_commit: str

    def opening(self) -> dict[str, object]:
        return dict(
            run_id=self.run_id,
            agent_id=self.agent_id,
            revision=self.revision,
            head_commit=self.head_commit,
        )

    def decision(self) -> dict[str, object]:
        return dict(revision=self.revision, head_commit=self.head_commit)


class PreviewClient(Protocol):
    """Internal consumer seam pending A's approved wire types, not a public contract.

    open sends POST /previews with Candidate.opening(), privately retains the lease,
    and rejects responses for any other identity/revision/head. ready GETs the
    returned ready URL (never an event-derived URL), awaits confirmed C readiness,
    fences the same identity, and returns the candidate's loopback document URL.
    renew uses POST /previews/{id}/lease. stop uses POST /previews/{id}/stop and
    returns ONLY after public evidence of verified termination. On uncertainty it
    raises; the scenario then refuses revisions, reopening and server Git commands.
    The binding must retain cleanup ownership even if opening's response is stale.
    Tokens never appear in repr, diagnostics, URLs or evidence.
    """

    async def open(self, candidate: Candidate) -> None: ...
    async def ready(self, candidate: Candidate) -> str: ...
    async def renew(self) -> None: ...
    async def stop(self) -> None: ...


class Events:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.seen: list[Any] = []
        self.queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=2048)
        self.task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> "Events":
        self.task = asyncio.create_task(self._read())
        return self

    async def __aexit__(self, *args: object) -> None:
        assert self.task is not None
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    async def _read(self) -> None:
        try:
            async with self.client.stream("GET", "/events", timeout=None) as response:
                require(response.status_code == 200, "Event stream unavailable")
                async for line in response.aiter_lines():
                    require(len(line) <= 1_000_000, "Event exceeds scenario bound")
                    if line.startswith("data:"):
                        await self.queue.put(EVENT.validate_json(line[5:].strip()))
        except Exception:
            await self.queue.put(ScenarioError("Event stream failed"))

    async def wait(self, kind: str, *, after: int = 0, **fields: object) -> Any:
        def matches(event: Any) -> bool:
            return (
                event.seq > after
                and event.type == kind
                and all(getattr(event.payload, key, None) == value for key, value in fields.items())
            )

        async with asyncio.timeout(WAIT_SECONDS):
            for event in self.seen:
                if matches(event):
                    return event
            while True:
                event = await self.queue.get()
                if isinstance(event, Exception):
                    raise event
                require(event.seq == len(self.seen) + 1, "Event sequence is stale or incomplete")
                require(len(self.seen) < 10_000, "Scenario event bound exceeded")
                self.seen.append(event)
                if matches(event):
                    return event


class Document(HTMLParser):
    def __init__(self, text: str):
        super().__init__()
        self.nonce = ""
        self.total = -1
        self.forms = 0
        self.feed(text)
        self.validate()

    def validate(self) -> None:
        require(
            self.forms == 1 and bool(re.fullmatch(r"[0-9a-f]{64}", self.nonce)),
            "Invalid candidate form",
        )

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        fields = dict(attrs)
        require(
            tag not in ("script", "style", "iframe", "link", "base", "object", "embed"),
            "Unexpected active or external document content",
        )
        require(
            not any(key == "style" or key.startswith("on") or key == "src" for key in fields),
            "Unexpected document attribute",
        )
        if tag == "form":
            self.forms += 1
            require(
                fields.get("action") == "/cart" and fields.get("method") == "post",
                "Unexpected form destination",
            )
        if tag == "input" and fields.get("name") == "nonce":
            require(fields.get("type") == "hidden" and not self.nonce, "Invalid form nonce")
            self.nonce = fields.get("value") or ""
        if tag == "output":
            value = fields.get("data-total-cents") or ""
            require(bool(re.fullmatch(r"[0-9]{1,9}", value)), "Invalid integer-cent total")
            self.total = int(value)


def document(response: httpx.Response, origin: str) -> Document:
    require(response.status_code == 200, "Candidate request failed")
    require(
        response.headers.get("content-security-policy")
        == ("default-src 'none'; form-action 'self'; base-uri 'none'; frame-ancestors " + origin),
        "Candidate CSP differs",
    )
    require(
        response.headers.get("referrer-policy") == "no-referrer"
        and response.headers.get("cache-control") == "no-store",
        "Candidate policy differs",
    )
    require(
        "set-cookie" not in response.headers
        and "location" not in response.headers
        and not any(key.startswith("access-control") for key in response.headers),
        "Candidate attempted cookies, redirect or CORS",
    )
    return Document(response.text)


class Scenario:
    def __init__(
        self,
        client: httpx.AsyncClient,
        previews: PreviewClient,
        shop: httpx.AsyncClient,
        origin: str,
    ):
        self.client, self.previews, self.shop = client, previews, shop
        self.origin = origin
        self.candidate: Candidate | None = None
        self.preview_owned = False
        self.agent_id: str | None = None
        self.change_id: str | None = None
        self.main: str | None = None
        self.run_id: str | None = None
        self.pricing: str | None = None

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        async with asyncio.timeout(REQUEST_SECONDS):
            response = await self.client.request(method, path, **kwargs)
            require(response.status_code in (200, 201, 202), "Backend request refused")
            return response.json() if response.content else None

    async def preflight(self, events: Events) -> None:
        started = await events.wait("run.started")
        payload = started.payload
        require(
            payload.models and all(model.provider == "fake" for model in payload.models),
            "Scenario refuses non-fake profiles",
        )
        require(payload.workspace == "demo-repo", "Scenario requires disposable demo workspace")
        require(
            payload.merge_mode is not None and payload.merge_mode.mode == "ask_me",
            "Scenario requires person-owned decisions",
        )
        require(
            (await self.request("GET", "/merge-mode"))["mode"] == "ask_me",
            "Scenario requires person-owned decisions",
        )
        graph = await self.request("GET", "/graph")
        health = await self.request("GET", "/health")
        require(
            health["mode"] == "live" and health["run_id"] == payload.run_id == graph["run_id"],
            "Backend run identity differs",
        )
        require(graph["graph"]["commit"] == payload.base_commit, "Backend main already changed")
        self.main, self.run_id = payload.base_commit, payload.run_id
        baseline = await self.request("GET", "/files", params={"path": "shop/pricing.py"})
        require(baseline["agent_id"] is None, "Main source identity differs")
        self.pricing = baseline["content"]

    async def proposal(self, events: Events, revision: int, after: int) -> Candidate:
        event = await events.wait("change.proposed", after=after, agent_id=self.agent_id)
        payload = event.payload
        require(
            payload.change_id == self.change_id
            and payload.revision == revision
            and payload.base_commit == self.main
            and payload.head_commit != self.main
            and payload.tests.outcome == "passed",
            "Candidate provenance differs",
        )
        require(
            self.candidate is None or self.candidate.head_commit != payload.head_commit,
            "Candidate head was reused",
        )
        candidate = Candidate(
            str(self.run_id), payload.agent_id, payload.change_id, revision, payload.head_commit
        )
        self.candidate = candidate
        return candidate

    async def stop(self) -> None:
        if self.preview_owned:
            async with asyncio.timeout(WAIT_SECONDS):
                await self.previews.stop()
            self.preview_owned = False

    async def basket(self, candidate: Candidate, expected: int) -> None:
        self.preview_owned = True  # Retain ownership even on a lost/stale open response.
        async with asyncio.timeout(WAIT_SECONDS):
            await self.previews.open(candidate)
            url = loopback_url(await self.previews.ready(candidate))
            parts = urlsplit(url)
            require(parts.path in ("", "/"), "Expected candidate document root")
            require(parts.port != self.client.base_url.port, "Preview shares backend port")
            await self.previews.renew()
            initial = document(await self.shop.get(url), self.origin)
            require(initial.total == 1500, "Initial basket differs")
            result = document(
                await self.shop.post(
                    url.rstrip("/") + "/cart",
                    data={
                        "quantity_1": "1",
                        "quantity_2": "1",
                        "coupon": "DEMO10",
                        "nonce": initial.nonce,
                    },
                    headers={"Origin": "null"},
                ),
                self.origin,
            )
            require(
                result.total == expected and result.nonce != initial.nonce,
                "DEMO10 basket or nonce differs",
            )
        await self.stop()  # Verified termination precedes every subsequent server Git operation.

    async def candidate_source(self, candidate: Candidate) -> None:
        require(not self.preview_owned and self.pricing is not None, "Preview must close first")
        content = await self.request(
            "GET",
            "/files",
            params={
                "path": "shop/pricing.py",
                "agent": candidate.agent_id,
            },
        )
        expected = self.pricing
        if candidate.revision == 2:
            assert expected is not None
            expected = expected.replace('"SPRING10": 10}', '"SPRING10": 10, "DEMO10": 10}')
        require(
            content["agent_id"] == candidate.agent_id and content["content"] == expected,
            "Candidate source differs from the scripted proposal",
        )

    async def unchanged(self) -> None:
        require(not self.preview_owned, "Preview must close first")
        source = await self.request("GET", "/files", params={"path": "shop/pricing.py"})
        require(
            source["agent_id"] is None and source["content"] == self.pricing, "Main source changed"
        )
        graph = await self.request("GET", "/graph")
        require(
            graph["run_id"] == self.run_id and graph["graph"]["commit"] == self.main,
            "Main changed during preview scenario",
        )

    async def discard(self, events: Events, *, failed: bool) -> None:
        if self.agent_id is None:
            return
        after = events.seen[-1].seq
        if failed:
            # Stop/discard snapshots the current candidate, including an in-flight revision.
            await self.request("POST", f"/agents/{self.agent_id}/stop")
        else:
            require(self.candidate is not None, "Missing candidate cleanup identity")
            assert self.candidate is not None
            await self.request(
                "POST",
                f"/changes/{self.change_id}/reject",
                json={
                    **self.candidate.decision(),
                    "comment": "Preview harness cleanup only.",
                },
            )
        await events.wait(
            "change.resolved",
            after=after,
            change_id=self.change_id,
            agent_id=self.agent_id,
            decision="rejected",
        )
        self.agent_id = None

    async def run(self, events: Events) -> tuple[int, int]:
        async with asyncio.timeout(SCENARIO_SECONDS):
            await self.preflight(events)
            complete = False
            try:
                created = await self.request(
                    "POST",
                    "/agents",
                    json={
                        "slot": "A",
                        "model": "fake",
                        "task": parse_script(SCRIPT.read_text()).task,
                    },
                )
                self.agent_id, self.change_id = created["agent_id"], created["change_id"]
                first = await self.proposal(events, 1, 0)
                await self.basket(first, 1500)
                await self.candidate_source(first)
                await self.unchanged()
                after = events.seen[-1].seq
                await self.request(
                    "POST",
                    f"/changes/{first.change_id}/request-changes",
                    json={
                        **first.decision(),
                        "comment": "Now add DEMO10 at ten percent and update its test.",
                    },
                )
                second = await self.proposal(events, 2, after)
                await self.basket(second, 1350)
                await self.candidate_source(second)
                await self.unchanged()
                complete = True
            finally:
                # Each cleanup wait is bounded; uncertainty prevents any further Git command.
                await self.stop()
                await self.discard(events, failed=not complete)
            await self.unchanged()
            return 1500, 1350


async def execute(backend: str, factory: Callable[[httpx.AsyncClient], PreviewClient]) -> None:
    origin = loopback_url(backend, root=True)
    async with (
        httpx.AsyncClient(
            base_url=origin, timeout=REQUEST_SECONDS, follow_redirects=False, trust_env=False
        ) as client,
        httpx.AsyncClient(timeout=REQUEST_SECONDS, follow_redirects=False, trust_env=False) as shop,
        Events(client) as events,
    ):
        await Scenario(client, factory(client), shop, origin).run(events)
    print("Fake preview scenario: DEMO10 1500 -> 1350 cents; candidate rejected; main unchanged.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", required=True)
    parser.add_argument(
        "--preview-client",
        required=True,
        help="Trusted A/B contract-backed binding module:factory (not yet available)",
    )
    parser.add_argument("--dedicated-fake-c", action="store_true", required=True)
    args = parser.parse_args()
    try:
        loopback_url(args.backend, root=True)
        module, name = args.preview_client.split(":", 1)
        factory = cast(
            Callable[[httpx.AsyncClient], PreviewClient],
            getattr(importlib.import_module(module), name),
        )
        asyncio.run(execute(args.backend, factory))
    except (Exception, KeyboardInterrupt):
        print("Preview scenario failed; verify approved contracts, fake C backend and cleanup.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
