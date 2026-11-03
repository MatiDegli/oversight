"""Actual EventStore membership, sink installation and commit-before-return behavior."""

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import cast

import pytest

from oversight.api.commands import Commands
from oversight.api.preview_publication import install_preview_publication
from oversight.api.routes import create_router
from oversight.events import EventRejected, EventStore
from oversight.workspace import PreviewManager, PreviewStatusRecord
from oversight_contracts.previews import PreviewStatus

from .test_preview_resolution import EVENTS, RUN, seed

pytestmark = pytest.mark.anyio
RECORD = PreviewStatusRecord(
    "preview-" + "a" * 32, RUN, "agent-1", "change-1", 1, "head", "starting"
)


async def test_sink_installed_during_router_construction_before_startup_and_awaits_sql(
    tmp_path, monkeypatch
):
    installed = []
    manager = SimpleNamespace(set_status_sink=installed.append)
    store = EventStore(tmp_path / "run.sqlite", RUN)
    create_router(store, "live", cast(Commands, SimpleNamespace(previews=manager)))
    assert len(installed) == 1 and store.last_seq == 0
    await store.start()
    try:
        await seed(store)
        entered, release = asyncio.Event(), asyncio.Event()
        original = store._commit

        async def gated(batch):
            entered.set()
            await release.wait()
            await original(batch)

        monkeypatch.setattr(store, "_commit", gated)
        before = store.last_seq
        pending = asyncio.create_task(installed[0](RECORD))
        await asyncio.wait_for(entered.wait(), 5)
        assert not pending.done() and store.last_seq == before
        release.set()
        await asyncio.wait_for(pending, 5)
        assert store.last_seq == before + 1
        payload = store.committed_events()[-1].payload.model_dump()
        assert payload == PreviewStatus(**RECORD.__dict__).model_dump()
        assert payload["reason"] is None and not {"url", "lease_token", "output"} & payload.keys()
    finally:
        await store.close()


@pytest.mark.parametrize(
    "field,value",
    [
        ("run_id", "run-20261103-000001"),
        ("agent_id", "agent-9"),
        ("change_id", "change-2"),
        ("revision", 2),
        ("head_commit", "stale"),
    ],
)
async def test_status_requires_run_coding_membership_and_exact_pinned_proposal(
    tmp_path, field, value
):
    async with EventStore(tmp_path / "run.sqlite", RUN) as store:
        await seed(store)
        before = store.committed_events()
        with pytest.raises(EventRejected):
            await store.publish(PreviewStatus(**replace(RECORD, **{field: value}).__dict__))
        assert store.committed_events() == before


async def test_terminal_facts_after_resolution_keep_pin_and_reject_unowned_status(tmp_path):
    async with EventStore(tmp_path / "run.sqlite", RUN) as store:
        await seed(store)
        installed = []
        install_preview_publication(
            store, cast(PreviewManager, SimpleNamespace(set_status_sink=installed.append))
        )
        sink = installed[0]
        await sink(RECORD)
        await sink(replace(RECORD, state="ready"))
        await store.publish(EVENTS[12].payload.model_copy(update={"decided_by": "person"}))
        before = store.committed_events()
        with pytest.raises(EventRejected):
            await sink(replace(RECORD, state="ready"))
        assert store.committed_events() == before
        await sink(replace(RECORD, state="stopping", reason="mutation"))
        await sink(replace(RECORD, state="stopped", reason="mutation"))
        with pytest.raises(EventRejected):
            await sink(
                replace(
                    RECORD, preview_id="preview-" + "b" * 32, state="failed", reason="launch_failed"
                )
            )
        states = [e.payload.state for e in store.committed_events() if e.type == "preview.status"]
        assert states == ["starting", "ready", "stopping", "stopped"]
