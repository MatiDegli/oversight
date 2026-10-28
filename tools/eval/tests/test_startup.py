"""Preflight distinguishes the optional startup notice from activity and failures."""

import json

import pytest
from live_helpers import live_server, live_settings  # pyright: ignore[reportMissingImports]
from tools.eval.session import Session
from tools.eval.tasks import BenchError
from tools.eval.transport import API, Events

from oversight.workspace import OPEN_NOTICE, capability
from oversight_contracts.events import ErrorReport


@pytest.mark.anyio
@pytest.mark.parametrize("isolated", [False, True])
async def test_preflight_preserves_startup_notice_without_requiring_isolation(
    tmp_path, monkeypatch, isolated
):
    async def check(runtime):
        if not isolated:
            raise OSError("test host has no user namespaces")

    monkeypatch.setattr(capability, "check_effect", check)
    async with live_server(live_settings(tmp_path)) as server:
        evidence = tmp_path / "events.jsonl"
        api = API(str(server.client.base_url))
        events = Events(api, server.store.run_id, evidence)
        try:
            await Session(api, events).preflight("fake")
            assert events.seq == len(server.events()) == (2 if isolated else 3)
            raw = [json.loads(line)["event"] for line in evidence.read_text().splitlines()]
            assert raw == [event.model_dump(mode="json") for event in server.events()]
        finally:
            await events.close()
            await api.client.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "overrides",
    [
        {"source": "server"},
        {"fatal": True},
        {"agent_id": "agent-1"},
        {"change_id": "change-1"},
        {"message": "A different tests error"},
        {},  # Even an identical notice is not allowed after the startup slot.
    ],
)
async def test_preflight_still_rejects_errors_after_startup(tmp_path, monkeypatch, overrides):
    async def check(runtime):
        if not overrides:
            raise OSError("test host has no user namespaces")

    monkeypatch.setattr(capability, "check_effect", check)
    async with live_server(live_settings(tmp_path)) as server:
        await server.store.publish(
            ErrorReport(
                **(
                    dict(
                        source="tests",
                        message=OPEN_NOTICE + " Reason: another warning",
                        agent_id=None,
                        change_id=None,
                        fatal=False,
                    )
                    | overrides
                )
            )
        )
        api = API(str(server.client.base_url))
        events = Events(api, server.store.run_id, tmp_path / "events.jsonl")
        try:
            with pytest.raises(BenchError, match="occupied"):
                await Session(api, events).preflight("fake")
        finally:
            await events.close()
            await api.client.aclose()
