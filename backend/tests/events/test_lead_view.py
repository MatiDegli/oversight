"""Shared reference parity and constant-work committed sink validation."""

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import TypeAdapter

from oversight.events import (
    MOMENT_EVENT_TYPES,
    EventRejected,
    EventStore,
    project_views,
    resolve_view_references,
)
from oversight_contracts.events import ServerEvent
from oversight_contracts.views import BehaviorView, LeadView, MomentView

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures/views/reference-cases.json"
DATA = json.loads(FIXTURE.read_text())
EVENTS = TypeAdapter(list[ServerEvent]).validate_python(DATA["events"])


@pytest.mark.parametrize("case", DATA["cases"], ids=lambda case: case["name"])
def test_shared_reference_cases(case: dict) -> None:
    found = resolve_view_references(
        EVENTS[: case["prefix"]], LeadView.model_validate(case["request"])
    )
    assert json.loads(json.dumps(found)) == case["expected"]


@pytest.mark.anyio
async def test_sink_accepts_horizon_zero_and_refuses_future_without_append(tmp_path: Path) -> None:
    async with EventStore(tmp_path / "run.sqlite", "run-20261103-000000") as store:
        view = LeadView(
            agent_id="agent-9",
            through_seq=0,
            view=BehaviorView(kind="behavior", change_id="change-1"),
        )
        assert (await store.publish(view)).seq == 1
        before = store.committed_events()
        for horizon in (2, 100):
            with pytest.raises(EventRejected):
                await store.publish(view.model_copy(update={"through_seq": horizon}))
            assert store.committed_events() == before
        event = store.committed_event(1)
        assert event is not None and event.type == "lead.view"
        assert store.committed_event(2) is None


@pytest.mark.anyio
async def test_batch_local_target_is_refused_and_next_committed_target_accepted(
    tmp_path: Path,
) -> None:
    async with EventStore(tmp_path / "run.sqlite", "run-20261103-000000") as store:
        target = EVENTS[5].payload
        view = LeadView(
            agent_id="agent-9", through_seq=1, view=MomentView(kind="moment", event_seq=1)
        )
        results = await asyncio.gather(
            store.publish(target), store.publish(view), return_exceptions=True
        )
        assert isinstance(results[1], EventRejected)
        assert store.last_seq == 1
        assert (await store.publish(view)).seq == 2
        for seq in (0, 3, 100):
            bad = view.model_dump()
            bad["view"]["event_seq"] = seq
            with pytest.raises(ValueError):
                await store.publish(LeadView.model_validate(bad))
            assert store.last_seq == 2


@pytest.mark.anyio
async def test_sink_never_scans_or_folds_prior(tmp_path: Path) -> None:
    class LookupOnly(dict):
        def __iter__(self):
            pytest.fail("sink iterated committed events")

        def values(self):
            pytest.fail("sink scanned committed events")

        def items(self):
            pytest.fail("sink scanned committed events")

    async with EventStore(tmp_path / "run.sqlite", "run-20261103-000000") as store:
        await store.publish(EVENTS[5].payload)
        store._reference_events = LookupOnly(store._reference_events)
        request = LeadView(
            agent_id="agent-9", through_seq=1, view=MomentView(kind="moment", event_seq=1)
        )
        assert (await store.publish(request)).seq == 2


def test_adapter_moment_window_is_latest_32_milestones_only() -> None:
    milestone = EVENTS[5]
    events = [milestone.model_copy(update={"seq": i}) for i in range(1, 41)]
    projection = project_views(events)
    for seq, expected in ((1, False), (8, False), (9, True), (40, True), (41, False)):
        view = LeadView(
            agent_id="agent-9", through_seq=40, view=MomentView(kind="moment", event_seq=seq)
        )
        assert projection.accepts(view.view) is expected


def test_shared_milestone_catalog() -> None:
    assert frozenset(DATA["moment_event_types"]) == MOMENT_EVENT_TYPES


@pytest.mark.anyio
async def test_missing_committed_moment_membership_appends_nothing(tmp_path: Path) -> None:
    async with EventStore(tmp_path / "run.sqlite", "run-20261103-000000") as store:
        await store.publish(EVENTS[5].payload)
        target = store.committed_event(1)
        assert target is not None
        # Exercise the membership guard even if an index were incomplete.
        store._reference_events = {2: target}
        view = LeadView(
            agent_id="agent-9", through_seq=1, view=MomentView(kind="moment", event_seq=1)
        )
        with pytest.raises(EventRejected, match="observed committed prefix"):
            await store.publish(view)
        assert store.last_seq == 1
