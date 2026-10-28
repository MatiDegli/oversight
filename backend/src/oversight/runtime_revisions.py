"""Synchronous admission shared by HTTP and automatic Request changes."""

# These helpers are part of LiveRun's composition, with access to its owned registries.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from oversight.agents import ReviewOutcome
from oversight.api import ApiError
from oversight.changes import ChangeNotFound, ReviewCapture
from oversight_contracts.commands import RequestChanges

if TYPE_CHECKING:
    from oversight.app import LiveRun


def request_revision(
    live: LiveRun,
    change_id: str,
    request: RequestChanges,
    *,
    review: tuple[ReviewCapture, ReviewOutcome, int] | None = None,
) -> None:
    """Check, reserve and register without yielding: Stop/shutdown always find both tasks."""
    manager = live._manager
    if manager is None or live._closing:
        raise ApiError("wrong_state", "the run is not open")
    if not request.comment.strip():
        raise ApiError("invalid_request", "the comment is empty")
    owner = next(
        ((agent_id, a) for agent_id, a in live._agents.items() if a.change_id == change_id),
        None,
    )
    if owner is None:
        raise ChangeNotFound(change_id)
    agent_id, agent = owner
    if manager.main_suspect:
        raise ApiError(
            "wrong_state", "main's state is unknown: reject the change and restart the server"
        )
    live._check_spending(agent.slot, agent.profile)
    run = live._kept.get(agent_id)
    if run is None:
        raise ApiError("wrong_state", f"{change_id} has no run to resume")
    # Manual admission keeps its existing manager state/target checks. Automatic admission
    # additionally requires the author's earlier run to have released the runtime registry.
    if review is None:
        decision = manager.request_changes(
            change_id, request.comment, revision=request.revision, head_commit=request.head_commit
        )
    else:
        capture, outcome, seq = review
        if agent_id in live._runs or not manager.review_current(capture):
            raise ApiError("wrong_state", "the author is not free and ready for review")
        decision = manager.request_cross_review(capture, outcome, review_seq=seq)
    live._decisions[agent_id] = decision
    live._runs[agent_id] = asyncio.create_task(
        live._revise(manager, agent, run, decision), name=f"revise-{agent_id}"
    )
