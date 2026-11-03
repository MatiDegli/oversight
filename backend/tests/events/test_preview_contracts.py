"""Fake preview facts register and consume sequence numbers without runtime behavior."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from pydantic import TypeAdapter

from oversight.events import EventRejected, EventStore, fold_usage, project_views
from oversight.events.store import EVENT_TYPES, EventPayload
from oversight.events.view_resolution import capture_view_resolver, resolve_view_references
from oversight_contracts.events import PreviewStatusEvent, ServerEvent
from oversight_contracts.previews import PreviewStatus
from oversight_contracts.views import LeadView, PreviewView

ROOT = Path(__file__).resolve().parents[3]
REFERENCE_CASES = json.loads((ROOT / "fixtures/views/reference-cases.json").read_text())
EVENTS = TypeAdapter(list[ServerEvent]).validate_python(REFERENCE_CASES["events"])
STATUS = PreviewStatus(
    preview_id="preview-" + "a" * 32,
    run_id="run-20261103-000000",
    agent_id="agent-1",
    change_id="change-1",
    revision=1,
    head_commit="c1",
    state="starting",
)


def test_payload_registration_and_envelope_without_private_runtime_facts() -> None:
    assert EVENT_TYPES[PreviewStatus] == "preview.status"
    assert TypeAdapter(EventPayload).validate_json(STATUS.model_dump_json()) == STATUS
    event = PreviewStatusEvent(seq=1, ts=EVENTS[0].ts, type="preview.status", payload=STATUS)
    assert TypeAdapter(ServerEvent).validate_json(event.model_dump_json()) == event
    assert set(event.payload.model_dump()) == {
        "preview_id",
        "run_id",
        "agent_id",
        "change_id",
        "revision",
        "head_commit",
        "state",
        "reason",
    }


@pytest.mark.parametrize("prefix", [0, 1, 6, 15])
def test_inert_preview_refuses_resolution_without_affecting_inventory(prefix: int) -> None:
    events = EVENTS[:prefix]
    projection = project_views(events)
    request = LeadView(
        agent_id="agent-9",
        through_seq=prefix,
        view=PreviewView(kind="preview", agent_id="agent-1"),
    )
    assert projection.resolve(request.view) == []
    assert projection.accepts(request.view) is False
    assert resolve_view_references(events, request) == []
    resolver = capture_view_resolver({event.seq: event for event in events}, prefix)
    assert resolver(request) is False
    status = PreviewStatusEvent(
        seq=prefix + 1, ts=EVENTS[0].ts, type="preview.status", payload=STATUS
    )
    assert project_views([*events, status]).inventory() == projection.inventory()


@pytest.mark.parametrize("mode", ["live", "replay"])
@pytest.mark.anyio
async def test_fake_status_commits_and_keeps_inert_reader_in_both_modes(
    tmp_path: Path, mode: Literal["live", "replay"]
) -> None:
    # This proves registration and committed-prefix bookkeeping, not candidate membership.
    async with EventStore(tmp_path / "run.sqlite", STATUS.run_id, mode=mode) as store:
        await store.publish(EVENTS[0].payload.model_copy(update={"run_id": STATUS.run_id}))
        before = store.committed_events()
        published = await store.publish(STATUS)
        assert published.type == "preview.status" and published.seq == 2
        committed = store.committed_events()
        assert committed[-1].type == "preview.status"
        assert committed[-1].payload.model_dump()["reason"] is None
        assert committed[:-1] == before
        request = LeadView(
            agent_id="agent-9",
            through_seq=2,
            view=PreviewView(kind="preview", agent_id="agent-1"),
        )
        assert not store.view_resolver(2)(request)
        assert resolve_view_references(committed, request) == []
        # PR1's horizon refusal and zero append remain intact.
        with pytest.raises(EventRejected):
            await store.publish(request.model_copy(update={"through_seq": 100}))
        assert store.committed_events() == committed


def test_preview_status_advances_seq_without_accounting_observations() -> None:
    before = fold_usage(EVENTS)
    status = PreviewStatusEvent(
        seq=len(EVENTS) + 1, ts=EVENTS[0].ts, type="preview.status", payload=STATUS
    )
    after = fold_usage([*EVENTS, status])
    assert after == replace(before, through_seq=status.seq)
    assert fold_usage([status.model_copy(update={"seq": 1})]) == replace(
        fold_usage([]), through_seq=1
    )
    with pytest.raises(ValueError, match="seq"):
        fold_usage([status])
