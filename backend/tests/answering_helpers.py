"""Synthetic confirmed origins; no fixture recording or real model is modified."""

from typing import Any

from oversight.events import EventStore, format_needs_you
from oversight_contracts.decisions import ChangeDecisionRef
from oversight_contracts.events import AgentStatus, ErrorReport


async def origin(store: EventStore) -> ChangeDecisionRef:
    await store.publish(
        AgentStatus(
            agent_id="agent-90",
            change_id="change-90",
            slot="B",
            task="source",
            model="fake",
            state="halted",
            step=0,
            max_steps=30,
            detail=None,
            base_commit="base",
        )
    )
    receipt = await store.publish(
        ErrorReport(
            source="server",
            agent_id="agent-90",
            change_id="change-90",
            fatal=False,
            message=format_needs_you(0, "base", "reviewer_missing", "Reviewer unavailable."),
        )
    )
    return ChangeDecisionRef(
        kind="checks_unavailable",
        change_id="change-90",
        revision=0,
        head_commit="base",
        evidence_seq=receipt.seq,
    )


def resolution(**updates: Any) -> dict[str, Any]:
    return (
        dict(
            change_id="change-90",
            agent_id="agent-90",
            revision=0,
            head_commit="base",
            decision="rejected",
            decided_by="person",
            comment=None,
            reason=None,
            merge=None,
            merge_commit=None,
            conflict=None,
        )
        | updates
    )
