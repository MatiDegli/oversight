"""Acceptance-only lead inputs, authored as fixtures rather than model suggestions."""

from tools.preview_scenario import Candidate

from oversight.events import EventStore
from oversight_contracts.events import LeadReply, LeadStatus
from oversight_contracts.views import LeadView, PreviewView

LEAD_ID = "agent-900001"
PROVENANCE = "Real local C shop / scripted fake coding model / fixture-authored lead inputs"


async def seed_preview_chat(store: EventStore, target: Candidate) -> list[int]:
    """Append validated inputs through the existing writer; never rewrite a prefix."""
    events = store.committed_events()
    if target.run_id != store.run_id or any(
        event.type == "lead.status" and event.payload.agent_id == LEAD_ID for event in events
    ):
        raise ValueError("Chat fixture requires a fresh lead in the candidate's run")
    agent = next(
        (
            e.payload
            for e in reversed(events)
            if e.type == "agent.status" and e.payload.agent_id == target.agent_id
        ),
        None,
    )
    proposal = next(
        (
            e
            for e in reversed(events)
            if e.type == "change.proposed" and e.payload.change_id == target.change_id
        ),
        None,
    )
    invalidated = proposal is not None and any(
        e.seq > proposal.seq
        and (
            (e.type == "change.updated" and e.payload.change_id == target.change_id)
            or (
                e.type == "change.resolved"
                and e.payload.change_id == target.change_id
                and e.payload.merge != "conflict"
            )
        )
        for e in events
    )
    if (
        agent is None
        or agent.change_id != target.change_id
        or agent.state not in {"ready_for_review", "halted"}
        or proposal is None
        or proposal.payload.agent_id != target.agent_id
        or proposal.payload.revision != target.revision
        or proposal.payload.head_commit != target.head_commit
        or invalidated
    ):
        raise ValueError("Chat fixture requires the exact current unresolved candidate")
    status = LeadStatus(
        agent_id=LEAD_ID,
        revision=1,
        task="Fixture-authored: try the candidate shop and review its change.",
        model="fake",
        state="working",
        step=0,
        detail="Acceptance fixture, not model-produced speech.",
    )
    started = await store.publish(status)
    request = LeadView(
        agent_id=LEAD_ID,
        through_seq=started.seq,
        caption=None,
        view=PreviewView(kind="preview", agent_id=target.agent_id),
    )
    # The writer independently validates current proposal membership and horizon.
    option = await store.publish(request)
    reply = await store.publish(
        LeadReply(
            agent_id=LEAD_ID,
            answers_seq=None,
            woken_by=None,
            text="Fixture-authored lead reply: try the shop, then review the coupon change.",
            through_seq=option.seq,
            cut=None,
        )
    )
    idle = await store.publish(status.model_copy(update={"revision": 2, "state": "idle"}))
    return [started.seq, option.seq, reply.seq, idle.seq]
