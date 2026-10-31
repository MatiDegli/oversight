"""Human controls over HTTP: admission, lifecycle and schema-valid message events."""

# Tests inspect registries only to hold or identify otherwise transient lifecycle phases.
# pyright: reportPrivateUsage=false

import asyncio
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from decision_helpers import observed_body
from live_helpers import (
    DEADLINE_S,
    LiveServer,
    error_code,
    is_type,
    live_server,
    live_settings,
    meta_settings,
    start,
)
from revise_helpers import HeldRequests, opened, ready, request, states

from oversight.agents import (
    FAKE_MODEL,
    MUSE_SPARK,
    TOOL_NAMES,
    LedgerState,
    Message,
    ModelReply,
    ScriptedModel,
    ToolSchema,
    parse_script,
)
from oversight.app import LiveRun
from oversight.changes import ChangeManager
from oversight_contracts.events import ModelProfile

pytestmark = pytest.mark.anyio
AGENT = "/agents/agent-1"
ACTIONS = ("message", "pause", "resume")


class HeldScript(ScriptedModel):
    """Each fake reply needs a permit; arrival is observable without polling or sleeps."""

    def __init__(self, profile: ModelProfile = FAKE_MODEL) -> None:
        import json

        turns = [
            {
                "tool_calls": [{"name": name, "arguments": args}],
                "usage": {"input_tokens": 1, "output_tokens": 1, "cached_tokens": 0},
            }
            for name, args in [
                ("read_file", {"path": "shop/pricing.py"}),
                ("finish", {"summary": "Done."}),
                ("read_file", {"path": "shop/pricing.py"}),
                ("finish", {"summary": "Revised."}),
            ]
        ]
        super().__init__(parse_script(json.dumps({"task": "t", "turns": turns})), profile)
        self.entered: asyncio.Queue[None] = asyncio.Queue()
        self.permits = asyncio.Semaphore(0)

    async def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolSchema]
    ) -> ModelReply:
        self.entered.put_nowait(None)
        await self.permits.acquire()
        return await super().complete(messages, tools)


async def control(s: LiveServer, action: str, text: str = "Private instruction.") -> Any:
    body = {"text": text} if action == "message" else None
    return await s.client.post(f"{AGENT}/{action}", json=body)


def accepted(response: Any) -> None:
    assert (response.status_code, response.content) == (202, b""), response.text


async def pause(s: LiveServer, model: HeldScript) -> None:
    accepted(await control(s, "pause"))
    model.permits.release()
    await s.until(is_type("agent.status", state="paused"))


async def test_working_pending_and_paused_resume_order_and_persisted_text(tmp_path: Path) -> None:
    model = HeldScript()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda *_: model) as s,
    ):
        await start(s)
        await model.entered.get()
        assert error_code(await control(s, "resume")) == (409, "wrong_state")
        accepted(await control(s, "message", "  Private first.\n"))
        accepted(await control(s, "pause"))
        accepted(await control(s, "pause"))
        accepted(await control(s, "resume"))  # cancels the pending pause
        assert "paused" not in states(s.events())
        accepted(await control(s, "pause"))
        accepted(await control(s, "message", "Private pending."))
        model.permits.release()
        await s.until(is_type("agent.status", state="paused"))
        accepted(await control(s, "pause"))
        accepted(await control(s, "message", "Private second. 😀"))
        manager = s.live._manager
        assert manager is not None and manager._agents["agent-1"].state == "working"
        assert s.live._slots == {"A": "agent-1"}
        # A paused gate holds neither lock. Review decisions and writes still need ready.
        async with asyncio.timeout(2), s.live.workspace.locks(main=True, agent_id="agent-1"):
            assert manager._agents["agent-1"].tree.root.exists()
        for action in ("approve", "reject", "request-changes"):
            body = {**observed_body(s), "comment": "Please revise."}
            if action != "reject":
                body["revision"] = max(1, body["revision"])
            response = await s.client.post(f"/changes/change-1/{action}", json=body)
            assert error_code(response) == (409, "wrong_state")
        file = (
            await s.client.get("/files", params={"path": "shop/pricing.py", "agent": "agent-1"})
        ).json()
        response = await s.client.put(
            "/files",
            json={
                "path": "shop/pricing.py",
                "agent_id": "agent-1",
                "content": file["content"],
                "base_hash": file["hash"],
            },
        )
        assert error_code(response) == (409, "wrong_state")
        accepted(await control(s, "resume"))
        await model.entered.get()
        model.permits.release()
        await ready(s)
        assert states(s.events()) == [
            "starting",
            "working",
            "paused",
            "working",
            "ready_for_review",
        ]
        assert [m["content"] for m in model.requests[1][-3:]] == [
            "  Private first.\n",
            "Private pending.",
            "Private second. 😀",
        ]
        sent = [e for e in s.events() if e.type == "agent.message"]
        assert [e.payload.text for e in sent] == [
            "  Private first.\n",
            "Private pending.",
            "Private second. 😀",
        ]
        assert max(e.seq for e in sent) < max(e.seq for e in s.events() if e.type == "agent.step")
        assert s.live._controls == {}
        for action in ACTIONS:
            assert error_code(await control(s, action)) == (409, "wrong_state")


@pytest.mark.parametrize("stop_before_start", [False, True])
async def test_registered_start_queues_and_pauses_before_first_model_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stop_before_start: bool
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    original = LiveRun._run

    async def held(self: LiveRun, *args: Any) -> None:
        entered.set()
        await release.wait()
        await original(self, *args)

    monkeypatch.setattr(LiveRun, "_run", held)
    model = HeldScript()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda *_: model) as s,
        opened(release),
    ):
        await start(s)
        await entered.wait()
        assert error_code(await control(s, "resume")) == (409, "wrong_state")
        accepted(await control(s, "message"))
        accepted(await control(s, "pause"))
        accepted(await control(s, "resume"))
        accepted(await control(s, "pause"))
        if stop_before_start:
            accepted(await control(s, "stop"))
            await s.until(is_type("change.resolved", reason="stopped"))
            assert states(s.events()) == ["stopped"]
            assert s.live._controls == {} and model.requests == []
            return
        release.set()
        await s.until(is_type("agent.status", state="paused"))
        assert model.entered.empty() and model.requests == []
        accepted(await control(s, "stop"))
        await s.until(is_type("change.resolved", reason="stopped"))
        assert states(s.events()) == ["starting", "working", "paused", "stopped"]
        assert s.live._controls == {} and model.requests == []


async def test_stop_paused_never_wakes_and_precedes_rejected_resolution(tmp_path: Path) -> None:
    model = HeldScript()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda *_: model) as s,
    ):
        await start(s)
        await model.entered.get()
        await pause(s, model)
        accepted(await control(s, "message", "Do not deliver this."))
        accepted(await control(s, "stop"))
        await s.until(is_type("change.resolved", reason="stopped"))
        events = s.events()  # Every envelope is validated by LiveServer.events.
        assert states(events) == ["starting", "working", "paused", "stopped"]
        assert events[-2].payload.state == "stopped"
        assert events[-1].payload.decision == "rejected"
        assert not any(e.type in {"change.proposed", "agent.step"} for e in events)
        assert [e.payload.text for e in events if e.type == "agent.message"] == [
            "Do not deliver this."
        ]
        assert s.live._controls == {} and s.live._slots == {"A": None}
        for action in (*ACTIONS, "stop"):
            assert error_code(await control(s, action)) == (409, "wrong_state")


@pytest.mark.parametrize(
    "state", ["ready", "unproposed", "conflict", "broken", "deciding", "closing", "closed"]
)
async def test_manager_state_table_refuses_controls_and_preserves_stop_eligibility(
    tmp_path: Path, state: str
) -> None:
    model = HeldScript()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda *_: model) as s,
    ):
        await start(s)
        await model.entered.get()
        manager = s.live._manager
        assert manager is not None
        rec = manager._agents["agent-1"]
        rec.state = state  # type: ignore[assignment]
        try:
            for action in ACTIONS:
                assert error_code(await control(s, action)) == (409, "wrong_state"), (state, action)
            if state in {"deciding", "closing", "closed"}:
                assert error_code(await control(s, "stop")) == (409, "wrong_state")
            else:
                accepted(await control(s, "stop"))
                await s.until(is_type("change.resolved", reason="stopped"))
        finally:
            if rec.state == state:
                rec.state = "working"


@pytest.mark.parametrize("halt", [False, True], ids=["proposal-finalizing", "halt-finalizing"])
async def test_finalizing_closed_control_refuses_until_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, halt: bool
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    original = ChangeManager.proposed

    async def held(self: ChangeManager, *args: Any, **kwargs: Any) -> bool:
        entered.set()
        await release.wait()
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(ChangeManager, "proposed", held)
    if halt:
        from oversight.app import LIMITS

        monkeypatch.setattr("oversight.app.LIMITS", LIMITS.model_copy(update={"max_steps": 1}))
    model = HeldScript()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda *_: model) as s,
        opened(release),
    ):
        await start(s)
        model.permits.release()
        if not halt:
            model.permits.release()
        await entered.wait()
        for action in ACTIONS:
            assert error_code(await control(s, action)) == (409, "wrong_state")
        accepted(await control(s, "stop"))
        await s.until(is_type("change.resolved", reason="stopped"))


@pytest.mark.parametrize(
    "body",
    [{}, {"text": 3}, {"text": ""}, {"text": " \n\t"}, {"text": "x" * 4097}, {"text": "😀" * 2049}],
)
async def test_bad_message_body_blank_and_single_message_limits(tmp_path: Path, body: Any) -> None:
    model = HeldScript()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda *_: model) as s,
    ):
        await start(s)
        await model.entered.get()
        assert error_code(await s.client.post(f"{AGENT}/message", json=body)) == (
            422,
            "invalid_request",
        )
        assert error_code(await s.client.post(f"{AGENT}/message", content=b"not json")) == (
            422,
            "invalid_request",
        )
        assert error_code(
            await s.client.post(
                f"{AGENT}/message",
                content=b'{"text":"\\ud800"}',
                headers={"content-type": "application/json"},
            )
        ) == (422, "invalid_request")
        assert not s.live._controls["agent-1"].control.pending_messages
        assert not any(e.type == "agent.message" for e in s.events())


@pytest.mark.parametrize("limit", ["queue", "lifetime"])
async def test_queue_and_lifetime_limits_are_atomic_and_never_replenish(
    tmp_path: Path, limit: str
) -> None:
    model = HeldScript()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda *_: model) as s,
    ):
        await start(s)
        await model.entered.get()
        texts = ["q"] * 32 if limit == "queue" else ["😀" * 1024, "x" * 4096]
        for text in texts:
            accepted(await control(s, "message", text))
        assert error_code(await control(s, "message", "x")) == (422, "invalid_request")
        model.permits.release()
        await model.entered.get()
        if limit == "queue":
            accepted(await control(s, "message", "x" * 4096))
            accepted(await control(s, "message", "x" * 4064))  # 32 + 4096 + 4064 = 8192
            assert error_code(await control(s, "message", "x")) == (422, "invalid_request")
            return
        assert error_code(await control(s, "message", "x")) == (422, "invalid_request")
        model.permits.release()
        await ready(s)
        accepted(await request(s))
        await model.entered.get()
        assert error_code(await control(s, "message", "x")) == (422, "invalid_request")


@pytest.mark.parametrize("pending", [False, True], ids=["paused", "pending"])
@pytest.mark.parametrize("refusal", ["setup", "main", "spending"])
async def test_resume_refusals_preserve_actual_or_pending_pause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pending: bool, refusal: str
) -> None:
    model = HeldScript(MUSE_SPARK)
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(meta_settings(tmp_path), lambda *_: model) as s,
    ):
        await start(s)
        await model.entered.get()
        accepted(await control(s, "pause"))
        if not pending:
            model.permits.release()
            await s.until(is_type("agent.status", state="paused"))
        live = s.live
        current = live._controls["agent-1"].control
        if refusal == "setup":
            monkeypatch.setattr(live, "_settings", replace(live._settings, meta_api_key=None))
            expected = (503, "not_configured")
        elif refusal == "main":
            live.workspace.integrity.main_suspect = True
            expected = (409, "wrong_state")
        else:
            monkeypatch.setattr(live.ledger, "state", LedgerState(spent_usd=149, unknown=()))
            expected = (403, "spend_brake")
        assert error_code(await control(s, "resume")) == expected
        assert current.pause_requested and current.paused is (not pending)
        assert states(s.events()).count("working") == 1
        accepted(await control(s, "stop"))
        await s.until(is_type("change.resolved", reason="stopped"))


async def test_revision_preparation_refuses_then_pauses_and_restores_revising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    model = HeldScript()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda *_: model) as s,
    ):
        await start(s)
        model.permits.release()
        model.permits.release()
        first = await ready(s)
        async with HeldRequests(s.store, monkeypatch) as held:
            accepted(await request(s))
            await held.at["agent-1"].wait()
            for action in (*ACTIONS, "stop"):
                assert error_code(await control(s, action)) == (409, "wrong_state")
            held.open("agent-1")
        await model.entered.get()  # First run's two calls were already observed in ready.
        await model.entered.get()
        await model.entered.get()
        assert error_code(await control(s, "resume")) == (409, "wrong_state")
        accepted(await control(s, "message"))
        accepted(await control(s, "pause"))
        model.permits.release()
        await s.until(is_type("agent.status", state="paused"), first[-1].seq)
        accepted(await control(s, "resume"))
        await model.entered.get()
        model.permits.release()
        events = await ready(s, after=first[-1].seq)
        assert states(events) == ["revising", "paused", "revising", "ready_for_review"]
        assert s.live._controls == {}


async def test_unknown_agent_and_lead_have_no_coding_controls(tmp_path: Path) -> None:
    async with asyncio.timeout(DEADLINE_S), live_server(live_settings(tmp_path)) as s:
        for action in (*ACTIONS, "stop"):
            for target in ("agent-999", "lead-1"):
                response = await s.client.post(f"/agents/{target}/{action}", json={"text": "t"})
                assert error_code(response) == (404, "not_found")
        for action in ACTIONS:
            assert error_code(
                await s.client.post(f"/leads/lead-1/{action}", json={"text": "t"})
            ) == (404, "not_found")
        assert set(ACTIONS).isdisjoint(TOOL_NAMES)


@pytest.mark.parametrize("action", ACTIONS)
def test_replay_does_not_read_control_bodies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    from fastapi.testclient import TestClient
    from starlette.requests import Request

    from oversight.app import ReplayConfig, create_app
    from oversight.settings import REPO_ROOT

    async def unread(_self: Request) -> bytes:
        raise AssertionError("a replay must refuse before reading a command body")

    monkeypatch.setattr(Request, "body", unread)
    log = REPO_ROOT / "fixtures/events/handwritten-level1.jsonl"
    app = create_app(live_settings(tmp_path), ReplayConfig(log=log))
    with TestClient(app, base_url="http://127.0.0.1:8710") as client:
        assert error_code(client.post(f"{AGENT}/{action}", content=b"invalid json")) == (
            501,
            "not_implemented",
        )


async def test_shutdown_paused_closes_controls_without_waking(tmp_path: Path) -> None:
    model = HeldScript()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda *_: model) as s,
    ):
        await start(s)
        await model.entered.get()
        await pause(s, model)
        current = s.live._controls["agent-1"].control
        await s.stop()
        assert current.closed and not current.paused
        assert s.live._controls == {} and s.live._runs == {}
        assert states(s.events()) == ["starting", "working", "paused"]
        assert model.entered.empty()
