"""Accepted messages commit masked history before the model can receive original text."""

# These tests hold the composition's store boundary to expose the admission race.
# pyright: reportPrivateUsage=false

import asyncio
from pathlib import Path

import pytest
from backend.tests.api.test_agent_controls import HeldScript, accepted, control
from live_helpers import DEADLINE_S, error_code, is_type, live_server, live_settings, start

from oversight.events import EventPayload, StoredEvent, read_store
from oversight.redaction import Redactor
from oversight_contracts.events import AgentMessageSent

pytestmark = pytest.mark.anyio


async def test_message_masks_both_configured_keys_but_delivers_original(tmp_path: Path) -> None:
    # Synthetic, test-only material, assembled here rather than a stored credential fixture.
    keys = ["test-only-provider-" + name * 2 for name in ("first", "second")]
    plain = "Please check plain prose, checkout-slug and field_name."
    shaped = ["Bearer " + "a1" * 12, "sk-" + "b2" * 12, "c3" * 12]
    fragments = [key[-8:] for key in keys]
    text = " ".join([plain, *shaped, *fragments])
    model = HeldScript()
    settings = live_settings(tmp_path, meta_api_key=keys[0], baseten_api_key=keys[1])
    async with asyncio.timeout(DEADLINE_S), live_server(settings, lambda *_: model) as s:
        await start(s)
        await model.entered.get()
        accepted(await control(s, "message", text))
        event = next(e for e in s.events() if e.type == "agent.message")
        assert event.payload.text == plain + " " + " ".join(["[redacted]"] * 5)
        assert Redactor(*keys)(event.payload.text) == event.payload.text
        assert any(e.envelope == event.model_dump_json() for e in read_store(s.store.path))
        model.permits.release()
        await model.entered.get()
        model.permits.release()
        events = await s.until(is_type("agent.step", step=2))
        assert model.requests[1][-1] == {"role": "user", "content": text}
        assert events[-1].seq > event.seq


@pytest.mark.parametrize("fail", [False, True])
async def test_pending_commit_blocks_delivery_and_failure_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    model = HeldScript()
    entered, release, gate_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(live_settings(tmp_path), lambda *_: model) as s,
    ):
        await start(s)
        await model.entered.get()
        original = s.store.publish
        run_control = s.live._controls["agent-1"].control
        gate = run_control.gate

        async def held(payload: EventPayload, *, ts: str | None = None) -> StoredEvent:
            if isinstance(payload, AgentMessageSent):
                entered.set()
                await release.wait()
                if fail:
                    raise OSError("synthetic disk failure")
            return await original(payload, ts=ts)

        async def observed_gate(*args, **kwargs):
            gate_entered.set()
            return await gate(*args, **kwargs)

        monkeypatch.setattr(s.store, "publish", held)
        monkeypatch.setattr(run_control, "gate", observed_gate)
        request = asyncio.create_task(control(s, "message", "Please reconsider."))
        try:
            await entered.wait()
            assert not run_control.claim_finish()
            model.permits.release()
            await gate_entered.wait()
            assert model.entered.empty()
            assert not request.done()
            release.set()
            response = await request
            if fail:
                assert error_code(response) == (500, "internal")
            else:
                accepted(response)
            await model.entered.get()
            recorded = [e for e in s.events() if e.type == "agent.message"]
            assert len(recorded) == (0 if fail else 1)
            if fail:
                monkeypatch.setattr(s.store, "publish", original)
                # A failed write spent neither lifetime bytes nor a queue slot.
                accepted(await control(s, "message", "x" * 4096))
                accepted(await control(s, "message", "y" * 4096))
            model.permits.release()
            events = await s.until(is_type("agent.step", step=2))
            messages = [m["content"] for m in model.requests[1] if m["role"] == "user"]
            assert ("Please reconsider." in messages) is (not fail)
            if recorded:
                assert recorded[0].seq < events[-1].seq
        finally:
            release.set()
            await request
