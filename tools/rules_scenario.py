"""Fake-only activation proof through HTTP and committed SSE; no authority injection."""

import asyncio
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from pydantic import TypeAdapter

from oversight.events import parse_needs_you
from oversight.settings import REPO_ROOT, Settings
from oversight_contracts.events import ServerEvent

EVENT = TypeAdapter(ServerEvent)
SCRIPTS = REPO_ROOT / "fixtures/fake-model"
DEADLINE = 60


def settings(runtime: Path) -> Settings:
    """Explicit settings never load .env, provider credentials or an existing runtime."""
    runtime.parent.mkdir(parents=True, exist_ok=True)
    reviewer = runtime.parent / "rules-reviewer.json"
    reviewer.write_text((SCRIPTS / "rules-reviewer.json").read_text())
    return Settings(
        runtime_dir=runtime,
        agent_model="fake",
        fake_script=SCRIPTS / "rules-author.json",
        fake_script_b=SCRIPTS / "rules-protected.json",
        max_agents=2,
        cross_review=True,
        review_model="fake-b",
        reviewer_fake_b_script=reviewer,
        budget_usd=1,
        demo_python=Path(sys.executable),
    )


class Events:
    """Bounded, schema-validated SSE, including events committed before subscription."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.seen: list[Any] = []
        self.queue: asyncio.Queue[Any] = asyncio.Queue()
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
            async with self.client.stream("GET", "/events") as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data:"):
                        await self.queue.put(EVENT.validate_json(line[5:].strip()))
        except Exception as error:
            await self.queue.put(error)

    async def wait(self, kind: str, *, after: int = 0, **fields: object) -> Any:
        def matches(event: Any) -> bool:
            return (
                event.seq > after
                and event.type == kind
                and all(getattr(event.payload, key) == value for key, value in fields.items())
            )

        return await self.match(matches)

    async def match(self, predicate: Callable[[Any], bool]) -> Any:
        async with asyncio.timeout(DEADLINE):
            for event in self.seen:
                if predicate(event):
                    return event
            while True:
                event = await self.queue.get()
                if isinstance(event, Exception):
                    raise event
                assert event.seq == len(self.seen) + 1
                self.seen.append(event)
                if predicate(event):
                    return event


async def select(client: httpx.AsyncClient, events: Events) -> None:
    assert (await client.get("/merge-mode")).json() == {"mode": "ask_me", "revision": 0}
    response = await client.put("/merge-mode", json={"mode": "merge_when_safe", "revision": 0})
    assert response.status_code == 200, response.text
    assert response.json() == {"mode": "merge_when_safe", "revision": 1}
    await events.wait("merge_mode.changed", mode="merge_when_safe", revision=1)


async def merge(client: httpx.AsyncClient, events: Events) -> Any:
    response = await client.post("/agents", json={"task": "Document apply_coupon.", "slot": "A"})
    assert response.status_code == 201, response.text
    proposal = await events.wait("change.proposed")
    reviewed = await events.wait("change.reviewed", change_id=proposal.payload.change_id)
    assert reviewed.payload.verdict == "clean"
    resolution = await events.wait("change.resolved", change_id=proposal.payload.change_id)
    result = resolution.payload
    assert result.decided_by == "rules" and result.merge == "merged"
    assert result.assessment is not None and len(result.assessment.rules) == 9
    assert all(rule.result == "pass" and rule.evidence for rule in result.assessment.rules)
    assert any(e.type == "change.assessed" and e.payload == result.assessment for e in events.seen)
    assert any(e.type == "tests.executed" and e.seq > reviewed.seq for e in events.seen)
    await events.wait("graph.delta", after=resolution.seq, commit=result.merge_commit)
    assert len([e for e in events.seen if e.type == "change.resolved"]) == 1
    content = (await client.get("/files", params={"path": "shop/pricing.py"})).json()["content"]
    assert "Take the coupon's percent" in content
    return resolution


async def revert(client: httpx.AsyncClient, events: Events, resolution: Any) -> Any:
    result = resolution.payload
    response = await client.post(
        f"/changes/{result.change_id}/revert",
        json={
            "resolution_seq": resolution.seq,
            "merge_commit": result.merge_commit,
            "main_commit": result.merge_commit,
        },
    )
    assert response.status_code == 202 and response.content == b"", response.text
    outcome = await events.wait("revert.resolved", after=resolution.seq)
    assert outcome.payload.result == "reverted"
    assert outcome.payload.revert_commit != result.merge_commit
    await events.wait("graph.delta", after=outcome.seq, commit=outcome.payload.revert_commit)
    content = (await client.get("/files", params={"path": "shop/pricing.py"})).json()["content"]
    assert "Take the coupon's percent" not in content
    return outcome


async def needs_you(client: httpx.AsyncClient, events: Events, reviewer: Path) -> Any:
    # Change only the next scripted model input, after the previous review has settled.
    # Review parsing, accounting and receipt authority all remain on the production path.
    await asyncio.to_thread(
        reviewer.write_text, (SCRIPTS / "rules-unavailable-reviewer.json").read_text()
    )
    before = (await client.get("/graph")).json()["graph"]["commit"]
    after = events.seen[-1].seq
    response = await client.post(
        "/agents", json={"task": "Document the package name.", "slot": "B"}
    )
    assert response.status_code == 201, response.text
    proposal = await events.wait("change.proposed", after=after)
    notice = await events.match(
        lambda e: (
            e.seq > proposal.seq
            and e.type == "error"
            and e.payload.change_id == proposal.payload.change_id
            and parse_needs_you(e.payload.message) is not None
        )
    )
    note = parse_needs_you(notice.payload.message)
    assert note is not None and note.code == "review_invalid"
    assert (note.revision, note.head) == (proposal.payload.revision, proposal.payload.head_commit)
    assert "verdict" in note.reason.lower()
    assert (await client.get("/graph")).json()["graph"]["commit"] == before
    assert not any(
        e.type == "change.resolved" and e.payload.change_id == proposal.payload.change_id
        for e in events.seen
    )
    return notice
