"""An uncommitted message cannot discard a finish that completed while its write was held."""

import asyncio

import pytest
from agent_helpers import Recorder, make_run, script

from oversight.events import EventPayload
from oversight.workspace import Tree, Workspace
from oversight_contracts.events import AgentStep

pytestmark = pytest.mark.anyio


async def test_finish_survives_a_failed_in_flight_message_write(
    small_tree: tuple[Workspace, Tree], monkeypatch: pytest.MonkeyPatch
) -> None:
    model = script([("finish", {"summary": "Done."})], [("finish", {"summary": "Extra call."})])
    recorder = Recorder()
    run = make_run(small_tree, model, recorder)
    step_entered, step_release = asyncio.Event(), asyncio.Event()
    write_entered, write_release, settling = asyncio.Event(), asyncio.Event(), asyncio.Event()
    wait_for_admission = run.control.wait_for_admission

    async def publish(payload: EventPayload) -> None:
        if isinstance(payload, AgentStep) and payload.step == 1:
            step_entered.set()
            await step_release.wait()
        await recorder.publish(payload)

    async def record() -> None:
        write_entered.set()
        await write_release.wait()
        raise OSError("synthetic disk failure")

    async def observe_settlement() -> None:
        settling.set()
        await wait_for_admission()

    monkeypatch.setattr(run, "_publish", publish)
    monkeypatch.setattr(run.control, "wait_for_admission", observe_settlement)
    async with asyncio.timeout(5):
        drive = asyncio.create_task(run.run())
        message = None
        try:
            await step_entered.wait()
            message = asyncio.create_task(run.control.record_message("Please reconsider.", record))
            await write_entered.wait()
            step_release.set()
            await settling.wait()
            assert not drive.done() and len(model.requests) == 1
            write_release.set()
            with pytest.raises(OSError, match="synthetic disk failure"):
                await message
            end = await drive
            assert end.summary == "Done." and end.steps == 1
            assert end.state == "ready_for_review" and len(model.requests) == 1
            assert not run.control.pending_messages
            assert recorder.of("AgentMessageSent") == []
        finally:
            step_release.set()
            write_release.set()
            await asyncio.gather(
                drive, *([message] if message is not None else []), return_exceptions=True
            )
