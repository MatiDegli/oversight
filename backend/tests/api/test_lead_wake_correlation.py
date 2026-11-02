"""Runtime reply correlation stays exclusive while historical null replies remain valid."""

# Tests inspect retained digests and watcher ownership after real committed turns.
# pyright: reportPrivateUsage=false

import asyncio
from pathlib import Path

import pytest
from backend.tests.api.lead_helpers import send, start_idle
from backend.tests.api.wake_helpers import (
    ControlledClock,
    Models,
    caught_up,
    next_trigger,
    notices,
    owned,
    ready,
    spin,
    triggers,
    wake_done,
)
from backend.tests.events.test_lead_wake import plan, status
from live_helpers import DEADLINE_S, live_server, two_fakes

from oversight.runtime_lead_admission import wake

pytestmark = pytest.mark.anyio


async def test_every_later_runtime_reply_has_exactly_one_correlation(tmp_path: Path):
    factory, clock = Models(), ControlledClock()
    async with asyncio.timeout(DEADLINE_S), live_server(two_fakes(tmp_path), factory) as s:
        await ready(s, clock)
        await send(s, "A human-only turn")
        wake_seqs = await triggers(s)
        await wake_done(s, clock)
        mixed_seq = await next_trigger(s)
        await send(s, "Summarize the pending event")
        mixed_notice = owned(s).run.history.notices[-1]
        assert mixed_notice is not None and mixed_notice.triggers == (mixed_seq,)
        clock.advance(5)
        await wake(s.live.leads, owned(s))
        await caught_up(s)

        # Read committed envelopes from the real store, including every runtime reply.
        events = s.store.committed_events()
        replies = [event.payload for event in events if event.type == "lead.reply"]
        messages = [event.seq for event in events if event.type == "agent.message"]
        assert len(replies) == 4 and len(messages) == 2
        opening, human, autonomous, mixed = replies
        assert opening.answers_seq is None and opening.woken_by is None
        for reply in replies[1:]:
            assert (reply.answers_seq is not None) != (reply.woken_by is not None)
        assert human.answers_seq == messages[0] and human.woken_by is None
        assert autonomous.answers_seq is None and autonomous.woken_by == wake_seqs
        assert mixed.answers_seq == messages[1] and mixed.woken_by is None
        assert mixed.through_seq is not None and mixed.through_seq >= mixed_seq
        assert owned(s).through_seq == mixed.through_seq
        assert not owned(s).watcher.pending and owned(s).wakes == 1


async def test_concurrent_leads_keep_watchers_wake_counts_and_refusals_isolated(tmp_path: Path):
    factory, clock = Models(), ControlledClock()
    async with (
        asyncio.timeout(DEADLINE_S),
        live_server(two_fakes(tmp_path, max_leads=2, lead_wakes=1), factory) as s,
    ):
        await ready(s, clock)
        second_id = await start_idle(s)
        leads = [owned(s), s.live.leads.leads[second_id]]
        assert second_id == "agent-2" and leads[0].watcher is not leads[1].watcher
        seqs = []
        for index, lead in enumerate(leads, 1):
            await s.store.publish(
                plan(plan_id=f"plan-{index}", started_by=lead.status.agent_id).payload
            )
            seqs.append(
                (
                    await s.store.publish(
                        status(
                            agent_id=f"agent-{index + 2}",
                            plan_id=f"plan-{index}",
                            state="halted",
                        ).payload
                    )
                ).seq
            )
        await spin(lambda: all(lead.watcher.cursor >= s.store.last_seq for lead in leads))
        clock.advance(5)
        await spin(lambda: all(lead.wakes == 1 for lead in leads))
        await asyncio.gather(*(lead.task for lead in leads))
        for lead, seq in zip(leads, seqs, strict=True):
            replies = [
                e.payload
                for e in s.store.committed_events()
                if e.type == "lead.reply" and e.payload.agent_id == lead.status.agent_id
            ]
            assert len(replies) == 2 and replies[-1].woken_by == [seq]
            assert lead.turns == 2 and lead.wakes == 1 and not lead.watcher.pending
            assert lead.run.limits is lead.limits is s.live._lead_limits
        await s.store.publish(status(agent_id="agent-3", plan_id="plan-1", state="halted").payload)
        await caught_up(s)
        clock.advance(5)
        await spin(lambda: len(notices(s)) == 1)
        assert notices(s)[0].payload.agent_id == "agent-1"
        assert leads[0].watcher.pending and not leads[1].watcher.pending
        assert [(lead.turns, lead.wakes) for lead in leads] == [(2, 1), (2, 1)]
        assert all(lead.watching is not None and not lead.watching.done() for lead in leads)
