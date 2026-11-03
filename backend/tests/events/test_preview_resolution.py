"""Committed preview targets derive candidate facts and preserve the lead/coding distinction."""

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from oversight.events import EventRejected, EventStore, project_views, resolve_view_references
from oversight_contracts.events import AgentStatus, ChangeProposed, LeadStatus, ServerEvent
from oversight_contracts.views import LeadView, PreviewView

ROOT = Path(__file__).resolve().parents[3]
EVENTS = TypeAdapter(list[ServerEvent]).validate_python(
    json.loads((ROOT / "fixtures/views/reference-cases.json").read_text())["events"]
)
RUN = "run-20261103-000000"


async def seed(store):
    await store.publish(EVENTS[0].payload.model_copy(update={"run_id": RUN}))
    assert isinstance(EVENTS[2].payload, AgentStatus)
    await store.publish(EVENTS[2].payload.model_copy(update={"state": "ready_for_review"}))
    await store.publish(
        LeadStatus(
            agent_id="agent-9",
            revision=1,
            task="Coordinate",
            model="fake",
            state="idle",
            step=0,
            detail=None,
        )
    )
    assert isinstance(EVENTS[5].payload, ChangeProposed)
    await store.publish(EVENTS[5].payload)
    return request(store.last_seq)


def request(through_seq):
    return LeadView(
        agent_id="agent-9",
        through_seq=through_seq,
        caption="Look at this candidate",
        view=PreviewView(kind="preview", agent_id="agent-1"),
    )


@pytest.mark.anyio
async def test_committed_current_candidate_resolves_without_advertising_inventory(tmp_path):
    async with EventStore(tmp_path / "run.sqlite", RUN) as store:
        view = await seed(store)
        events = store.committed_events()
        projection = project_views(events)
        assert projection.resolve(view.view) == ["agent-1"]
        assert store.view_resolver(store.last_seq)(view)
        assert not any("preview" in label for label, _ in projection.inventory())
        assert [
            (label, row)
            for label, row in projection.inventory(include_previews=True)
            if label == "eligible preview"
        ] == [("eligible preview", {"agent_id": "agent-1"})]
        assert "eligible preview data:" not in store.lead_status(include_views=True)[1]
        assert (
            "eligible preview data:"
            in store.lead_status(include_views=True, include_previews=True)[1]
        )
        published = await store.publish(view)
        assert published.seq == len(events) + 1
        envelope = store.committed_events()[-1]
        assert envelope.type == "lead.view"
        assert envelope.payload.view.kind == "preview"
        assert (
            envelope.payload.agent_id == "agent-9" and envelope.payload.view.agent_id == "agent-1"
        )
        assert resolve_view_references(events, view) == ["agent-1"]


@pytest.mark.anyio
@pytest.mark.parametrize("target", ["agent-2", "agent-9"])
async def test_absent_or_non_coding_target_appends_nothing(tmp_path, target):
    async with EventStore(tmp_path / "run.sqlite", RUN) as store:
        view = await seed(store)
        bad = view.model_copy(update={"view": PreviewView(kind="preview", agent_id=target)})
        before = store.committed_events()
        assert not store.view_resolver(store.last_seq)(bad)
        with pytest.raises(EventRejected):
            await store.publish(bad)
        assert store.committed_events() == before


@pytest.mark.anyio
@pytest.mark.parametrize("fact", ["revision", "resolution", "update", "membership", "malformed"])
async def test_stale_inconsistent_or_malformed_candidate_zero_append(tmp_path, fact):
    async with EventStore(tmp_path / "run.sqlite", RUN) as store:
        view = await seed(store)
        if fact in {"revision", "malformed"}:
            await store.publish(
                EVENTS[7].payload.model_copy(update={"revision": 0} if fact == "malformed" else {})
            )
        elif fact == "resolution":
            await store.publish(EVENTS[12].payload.model_copy(update={"decided_by": "person"}))
        elif fact == "update":
            await store.publish(EVENTS[3].payload)
        else:
            await store.publish(
                EVENTS[2].payload.model_copy(
                    update={"change_id": "change-2", "state": "ready_for_review"}
                )
            )
        before = store.committed_events()
        with pytest.raises(EventRejected):
            await store.publish(view)
        assert store.committed_events() == before
        if fact != "revision":
            assert resolve_view_references(before, view) == []
        else:
            current = request(store.last_seq)
            assert (await store.publish(current)).seq == len(before) + 1


@pytest.mark.anyio
async def test_future_batch_local_and_rejected_targets_never_enter_horizon(tmp_path):
    async with EventStore(tmp_path / "run.sqlite", RUN) as store:
        view = await seed(store)
        count = store.last_seq
        bad = EVENTS[7].payload.model_copy(update={"revision": "invalid"})
        with pytest.warns(UserWarning, match="Pydantic serializer warnings"):
            results = await asyncio.gather(
                store.publish(bad), store.publish(request(count + 1)), return_exceptions=True
            )
        assert all(isinstance(result, EventRejected) for result in results)
        assert store.last_seq == count
        assert (await store.publish(view)).seq == count + 1
        last = store.committed_events()[-1]
        assert last.type == "lead.view" and last.payload.caption == view.caption


@pytest.mark.anyio
async def test_resolution_accepted_in_same_batch_invalidates_old_preview_target(tmp_path):
    async with EventStore(tmp_path / "run.sqlite", RUN) as store:
        view = await seed(store)
        before = store.last_seq
        resolution = EVENTS[12].payload.model_copy(update={"decided_by": "person"})
        results = await asyncio.gather(
            store.publish(resolution), store.publish(view), return_exceptions=True
        )
        assert not isinstance(results[0], Exception) and isinstance(results[1], EventRejected)
        assert store.last_seq == before + 1
        assert not any(e.type == "lead.view" for e in store.committed_events())


@pytest.mark.anyio
async def test_preview_outer_identity_must_remain_the_lead(tmp_path):
    async with EventStore(tmp_path / "run.sqlite", RUN) as store:
        view = (await seed(store)).model_copy(update={"agent_id": "agent-1"})
        before = store.committed_events()
        assert not store.view_resolver(store.last_seq)(view)
        assert resolve_view_references(before, view) == []
        with pytest.raises(EventRejected):
            await store.publish(view)
        assert store.committed_events() == before
