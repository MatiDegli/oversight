"""One exported fake run and an explicit golden for Python and future frontend attribution."""

# The watcher is held working so timing and admission budgets cannot hide attribution.
# pyright: reportPrivateUsage=false

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from backend.tests.api.wake_helpers import ControlledClock, Models, ready, spin
from backend.tests.events.test_lead_wake import plan, status, suite_execution
from live_helpers import DEADLINE_S, live_server, two_fakes

from oversight.events import EventStore, export_run, is_lead_wake_trigger, read_jsonl
from oversight.runtime_lead_wakes import LeadWatcher
from oversight_contracts.events import ServerEvent

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures/events"
RECORDING = FIXTURES / "recorded-lead-wakes.jsonl"
EXPECTED = FIXTURES / "lead-wakes.expected.json"


async def _record(tmp_path: Path) -> None:
    clock, expected = ControlledClock(), []
    async with live_server(two_fakes(tmp_path), Models()) as s:
        await ready(s, clock)

        async def publish(event: ServerEvent, lead_id: str | None = None) -> None:
            row = await s.store.publish(event.payload)
            if lead_id:
                expected.append({"lead_id": lead_id, "trigger_seq": row.seq})

        await publish(plan())
        await publish(plan(plan_id="plan-2", started_by="agent-9"))
        await publish(status())
        await publish(suite_execution(), "agent-1")
        # Reassign after the failed suite: it must retain the earlier status's plan.
        await publish(status(plan_id="plan-2"))
        await publish(suite_execution(outcome="timeout"), "agent-9")
        await publish(status(state="halted", plan_id="plan-2"), "agent-9")
        await publish(status(plan_id=None))
        await publish(suite_execution())
        await publish(status(state="halted", plan_id=None))
        await publish(status(plan_id="plan-1"))
        await publish(suite_execution(phase="proposal"))
        await publish(suite_execution(outcome="passed"))
        await publish(suite_execution(outcome="error"), "agent-1")
        await publish(status(state="halted"), "agent-1")
        # A proposed plan is waiting for Approve: notice-only.
        await publish(
            plan(
                2,
                state="proposed",
                subtasks=[dict(task="Work", zone=dict(node_ids=[], paths=[]), state="waiting")],
            )
        )
        await publish(plan(3, state="completed"), "agent-1")
        await publish(plan(4, state="completed"))
        await publish(plan(5, plan_id="plan-2", started_by="agent-9", state="failed"), "agent-9")
        await s.live.leads.close()
        path = s.store.path
    export_run(path, RECORDING)
    EXPECTED.write_text(json.dumps(expected, indent=2) + "\n", encoding="utf-8")


@pytest.mark.anyio
async def test_recorded_wake_fixture_matches_predicate_and_watcher(tmp_path: Path):
    async with asyncio.timeout(DEADLINE_S):
        if os.environ.get("OVERSIGHT_RECORD_WAKE_FIXTURE") == "1":
            await _record(tmp_path / "recording")
        rows = read_jsonl(RECORDING)
        expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
        leads = ("agent-1", "agent-9")
        prefix, found = {}, []
        for event in rows:
            prefix[event.seq] = event
            for lead_id in leads:
                if is_lead_wake_trigger(prefix, event.seq, lead_id):
                    found.append({"lead_id": lead_id, "trigger_seq": event.seq})
        assert found == expected
        async with EventStore(
            tmp_path / "replay.sqlite", "run-20261102-170000", mode="replay"
        ) as store:
            # No copying or hand editing of recording envelopes: replay their payloads in order.
            for event in rows:
                if event.type != "run.started":
                    await store.publish(event.payload, ts=event.ts)
                else:
                    await store.publish(
                        event.payload.model_copy(update={"run_id": store.run_id}), ts=event.ts
                    )
            actual = []
            for lead_id in leads:
                clock = ControlledClock()
                lead: Any = SimpleNamespace(
                    status=SimpleNamespace(agent_id=lead_id),
                    through_seq=None,
                    phase="working",
                    stopping=False,
                )
                live = SimpleNamespace(store=store, _redactor=str, _closing=False)
                service: Any = SimpleNamespace(live=live, wake_clock=clock, _closing=False)
                watcher = LeadWatcher(service, lead)
                task = asyncio.create_task(watcher.run())
                await spin(lambda watcher=watcher: watcher.cursor == store.last_seq)
                notice = watcher.take()
                assert notice is not None
                actual.extend({"lead_id": lead_id, "trigger_seq": seq} for seq in notice.triggers)
                assert watcher.take() is None
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                assert watcher.subscription is not None and watcher.subscription.done()
                assert not clock.waiters
            assert sorted(actual, key=lambda row: row["trigger_seq"]) == expected
