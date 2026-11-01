"""Runtime-owned review lifecycle, isolated transport scope and provider accounting."""

# These helpers are part of LiveRun's composition, with access to its owned registries.
# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Sequence
from typing import TYPE_CHECKING, Literal, Protocol

from oversight.agents import (
    ConeEntry,
    ModelClient,
    ReviewInputs,
    ReviewOutcome,
    ReviewTests,
    RunEnd,
    run_review_call,
)
from oversight.api import ApiError
from oversight.changes import ChangeNotFound, ReviewCapture, WrongState, repeat_verdict
from oversight.events import NEEDS_YOU_CODES, format_needs_you
from oversight.review_config import ReviewerUnavailable, configured_reviewer
from oversight.runtime_accounting import ReviewAccounting, ReviewLedgerFailure
from oversight.runtime_revisions import request_revision
from oversight_contracts.commands import RequestChanges
from oversight_contracts.events import ChangeProposed, ModelProfile
from oversight_contracts.review import ChangeTarget

if TYPE_CHECKING:
    from oversight.app import LiveRun


class ReviewerCall(Protocol):
    """Optional lifecycle test seam; production uses run_review_call with its own accounting."""

    async def __call__(
        self,
        client: ModelClient,
        target: ChangeTarget,
        inputs: ReviewInputs,
        *,
        author: ModelProfile,
        author_reply_models: Sequence[str | None] = (),
    ) -> ReviewOutcome: ...


def facts_ready(
    live: LiveRun,
    capture: ReviewCapture,
    proposal: ChangeProposed,
    *,
    author: Literal["agent", "user"],
) -> None:
    if not live._settings.cross_review or live._closing or author != "agent":
        return
    # No task yet: finish still owns the run, and may end halted after proposing.
    live._review_pending[proposal.agent_id] = (capture, proposal)


def run_ended(live: LiveRun, agent_id: str, end: RunEnd | None) -> None:
    pending = live._review_pending.pop(agent_id, None)
    manager = live._manager
    if pending is None or live._closing or manager is None or end is None or agent_id in live._runs:
        return
    capture, proposal = pending
    if not manager.review_current(capture, require_finished=False):
        return
    finished = end.state == "ready_for_review"
    task = asyncio.create_task(
        review(
            live, capture, proposal, finished=finished, ready_time=asyncio.get_running_loop().time()
        ),
        name=f"review-{capture.target.change_id}",
        context=contextvars.Context(),
    )
    live._reviews[capture.target.change_id] = task
    # Attach diagnostics for halted authors too: human admission must cancel any notice
    # already in flight. They still receive no reviewer call or revision budget.
    manager.track_review(capture, task, require_finished=finished)
    task.add_done_callback(lambda done: live._forget(live._reviews, capture.target.change_id, done))


def eligible(live: LiveRun, capture: ReviewCapture, agent_id: str) -> bool:
    return (
        not live._closing
        and live._manager is not None
        and agent_id not in live._runs
        and live._manager.review_current(capture)
    )


async def notice(
    live: LiveRun,
    capture: ReviewCapture,
    proposal: ChangeProposed,
    code: str,
    reason: str,
) -> None:
    if (
        live._closing
        or live._manager is None
        or not live._manager.review_current(capture, require_finished=False)
    ):
        return
    await live._report(
        "server",
        format_needs_you(proposal.revision, proposal.head_commit, code, reason),
        agent_id=proposal.agent_id,
        change_id=proposal.change_id,
    )


def review_inputs(client: ModelClient, proposal: ChangeProposed, diff: str) -> ReviewInputs:
    return ReviewInputs(
        reviewer_model=client.profile.model,
        summary=proposal.summary,
        diff=diff,
        files=tuple(f.path for f in proposal.files),
        task=proposal.task,
        tests=ReviewTests(
            proposal.tests.outcome,
            proposal.tests.passed,
            proposal.tests.failed,
            proposal.tests.skipped,
        ),
        cone=tuple(ConeEntry(e.node_id, e.distance) for e in proposal.impact.entries),
        omitted_by_depth=proposal.impact.omitted_by_depth,
        omitted_by_limit=proposal.impact.omitted_by_limit,
    )


async def review(
    live: LiveRun,
    capture: ReviewCapture,
    proposal: ChangeProposed,
    *,
    finished: bool,
    ready_time: float,
) -> None:
    accounting: ReviewAccounting | None = None
    try:
        if not finished:
            await notice(
                live,
                capture,
                proposal,
                "author_halted",
                "The author did not finish ready for review.",
            )
            return
        if not eligible(live, capture, proposal.agent_id):
            return
        agent = live._agents[proposal.agent_id]
        client = configured_reviewer(live._settings, agent.slot, agent.profile)
        if client is None:
            return
        live._check_spending(agent.slot, client.profile)
        assert live._manager is not None
        diff = await live._manager.review_diff(capture)
        inputs = review_inputs(client, proposal, diff)
        if not eligible(live, capture, proposal.agent_id):
            return
        models = tuple(live._author_models.get(proposal.agent_id, ()))
        if live._reviewer is None:
            accounting = ReviewAccounting(live, capture.target, client.profile, proposal.agent_id)
            outcome = await run_review_call(
                client,
                capture.target,
                inputs,
                author=agent.profile,
                author_reply_models=models,
                ready_time=ready_time,
                record=accounting.record,
                agent_id=proposal.agent_id,
                publish=live._publish,
            )
        else:
            outcome = await live._reviewer(
                client,
                capture.target,
                inputs,
                author=agent.profile,
                author_reply_models=models,
            )
        if not eligible(live, capture, proposal.agent_id):
            return
        # The confirmed receipt is the only authority for a subsequent automatic decision.
        stored = await live._publish(outcome.review)
        assert live._manager is not None
        live._manager.rules_received(stored)
        if not eligible(live, capture, proposal.agent_id):
            return
        await apply_outcome(live, capture, proposal, outcome, stored.seq)
    except asyncio.CancelledError:
        if accounting is not None and accounting.failure is not None:
            await accounting.report_failure()
        raise
    except ReviewLedgerFailure:
        assert accounting is not None
        await accounting.report_failure()
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError from accounting.failure
    except TimeoutError:
        await notice(
            live, capture, proposal, "review_timeout", "The review exceeded ready + 90 seconds."
        )
    except WrongState:
        pass  # Target invalidation drops a late review; it is never retried.
    except ReviewerUnavailable as error:
        await notice(live, capture, proposal, error.code, str(error))
    except ApiError as error:
        if error.code == "wrong_state":
            return  # Admission invalidation drops the review, just like WrongState.
        code = {
            "spend_brake": "spend_invalid",
            "not_configured": "reviewer_credentials",
        }.get(error.code, "review_api_failed")
        await notice(
            live,
            capture,
            proposal,
            code,
            str(error),
        )
    except Exception as error:
        await notice(
            live, capture, proposal, "review_api_failed", f"Review failed ({type(error).__name__})."
        )


async def apply_outcome(
    live: LiveRun,
    capture: ReviewCapture,
    proposal: ChangeProposed,
    outcome: ReviewOutcome,
    review_seq: int,
) -> None:
    if outcome.review.verdict == "unknown":
        code = next(
            (
                p.partition(":")[0]
                for p in outcome.review.problems
                if p.partition(":")[0] in NEEDS_YOU_CODES
            ),
            "review_invalid",
        )
        await notice(
            live,
            capture,
            proposal,
            code,
            "; ".join(outcome.review.problems) or "Review unavailable.",
        )
    elif outcome.review.verdict == "changes_requested":
        try:
            verdict = await asyncio.to_thread(
                repeat_verdict,
                live.store.committed_events(),
                capture.target,
                live.change_graph(proposal.change_id),
            )
        except (ChangeNotFound, WrongState):
            return
        if not eligible(live, capture, proposal.agent_id):
            return
        if verdict.outcome != "clear":
            code = "unchanged_failure" if verdict.outcome == "unchanged" else "reach_unknown"
            await notice(live, capture, proposal, code, verdict.reason)
            return
        request_revision(
            live,
            proposal.change_id,
            RequestChanges(
                comment=outcome.decision.comment,
                revision=proposal.revision,
                head_commit=proposal.head_commit,
            ),
            review=(capture, outcome, review_seq),
        )


async def drain_reviews(live: LiveRun) -> None:
    live._review_pending.clear()
    tasks = list(live._reviews.values())
    for task in tasks:
        task.cancel()
    waiting = asyncio.gather(*tasks, return_exceptions=True)
    cancelled = False
    while not waiting.done():
        try:
            await asyncio.shield(waiting)
        except asyncio.CancelledError:
            cancelled = True
    live._reviews.clear()
    if cancelled:
        raise asyncio.CancelledError
