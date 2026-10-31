"""Person-only admission and a tracked drain of one rules-owned decision."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any, Literal

from oversight.changes._decision_validation import validate_decision_target
from oversight.changes._evidence import WriteEvidence
from oversight.changes._resolution_validation import validate_resolution_intent
from oversight.changes._state import (
    REJECTABLE,
    REVISABLE,
    ChangeRecord,
    Revision,
    State,
    WrongState,
)
from oversight.changes._support import ChangeContext
from oversight_contracts.review import ReviewCard


@dataclass(frozen=True)
class Owner:
    """Narrow callbacks into the manager; no helper imports the facade backwards."""

    ctx: ChangeContext
    answers: Callable[[ChangeRecord, list[ReviewCard] | None], tuple[ReviewCard, ...] | None]
    take: Callable[[str, frozenset[State], str], ChangeRecord]
    decide: Callable[[ChangeRecord, Coroutine[Any, Any, Any]], asyncio.Task[Any]]
    reject: Callable[[ChangeRecord, str | None], Coroutine[Any, Any, None]]
    request_changes: Callable[[ChangeRecord, str], Coroutine[Any, Any, Revision | None]]


@dataclass(frozen=True)
class PersonIntent:
    evidence: WriteEvidence
    rules_task: asyncio.Task[None]
    decision: int
    allowed: frozenset[State]

    @property
    def label(self) -> str:
        return "Reject" if self.evidence.decision == "rejected" else "Request changes"


def merged_by_rules(rec: ChangeRecord | None) -> bool:
    return (
        rec is not None
        and rec.merged
        and rec.evidence is not None
        and rec.evidence.actor == "rules"
    )


def claim(
    owner: Owner,
    rec: ChangeRecord,
    decision: Literal["rejected", "changes_requested"],
    comment: str | None,
    revision: int,
    head_commit: str,
    answers: list[ReviewCard] | None,
) -> asyncio.Task[Any] | None:
    """No await: validate, claim, disable following and cancel exactly the owned task."""
    if rec.person_intent is not None:
        raise WrongState(
            f"{rec.change_id}: a decision is already being applied; wait for its result"
        )
    if rec.state == "deciding" and rec.rules_task is not None:
        if rec.rules_phase not in {"queued", "preflight", "suite"}:
            raise WrongState(
                f"{rec.change_id}: rules authorization has started; wait for its result; "
                "if merged, use Revert"
            )
    else:
        if merged_by_rules(rec):
            raise WrongState(f"{rec.change_id} was merged by rules; use Revert to undo it")
        return None
    allowed = REJECTABLE if decision == "rejected" else REVISABLE
    validate_decision_target(rec, revision, head_commit, allowed, reserved=True)
    accepted = owner.answers(rec, answers)
    evidence = WriteEvidence(
        rec.change_id,
        rec.agent_id,
        revision,
        rec.base_commit,
        head_commit,
        "person",
        decision,
        comment,
        answers=accepted,
    )
    validate_resolution_intent(evidence)
    intent = PersonIntent(evidence, rec.rules_task, rec.decision, allowed)
    rec.person_intent = intent
    rec.person_intervened()
    owner.ctx.assessment.invalidate(rec)
    intent.rules_task.cancel()
    coordinator = owner.ctx.spawn(finish(owner, rec, intent))

    async def result() -> Revision | None:
        # The runtime's _revise (or an HTTP caller) owns only this cancellable waiter.
        return await asyncio.shield(coordinator)

    return owner.ctx.spawn(result())


def restore(
    ctx: ChangeContext, rec: ChangeRecord, task: asyncio.Task[Any] | None, decision: int
) -> None:
    """Both restoration paths may run; neither may retire a later rules owner."""
    ctx.state.restore(rec, decision)
    if rec.rules_task is task and rec.decision == decision:
        rec.rules_phase = "finished"
        rec.rules_task = None


def take(owner: Owner, rec: ChangeRecord, intent: PersonIntent) -> asyncio.Task[Any]:
    """Single synchronous handoff; pending guards cover the preceding restored state."""
    ctx, evidence = owner.ctx, intent.evidence
    if rec.person_intent is not intent or rec.decision != intent.decision:
        raise WrongState("The accepted decision no longer owns this change")
    if ctx.state.suspect or ctx.workspace.integrity.main_suspect or rec.state == "broken":
        raise WrongState("Main restoration could not be verified; the change needs you")
    validate_decision_target(rec, evidence.revision, evidence.head_commit, intent.allowed)
    if rec.base_commit != evidence.base_commit or rec.merged:
        raise WrongState("The accepted target changed while rules settled")
    # _take must refuse every competing token. No await opens this private handoff.
    rec.person_intent = None
    try:
        owner.take(rec.change_id, intent.allowed, intent.label)
    finally:
        rec.person_intent = intent
    rec.evidence = evidence
    work = (
        owner.reject(rec, evidence.comment)
        if evidence.decision == "rejected"
        else owner.request_changes(rec, evidence.comment or "")
    )
    return owner.decide(rec, work)


async def finish(owner: Owner, rec: ChangeRecord, intent: PersonIntent) -> Revision | None:
    """Hold no locks while the rules subprocess, recovery and old restore finish."""
    ctx = owner.ctx
    reason: str | None = None
    try:
        await asyncio.wait([intent.rules_task])
        # Retrieve failures too; cancellation is the expected rules result.
        if not intent.rules_task.cancelled():
            intent.rules_task.result()
        restore(ctx, rec, intent.rules_task, intent.decision)
        task = take(owner, rec, intent)
        result = await asyncio.shield(task)
        applied = (
            (
                rec.resolution is not None
                and rec.resolution.decided_by == "person"
                and rec.resolution.decision == "rejected"
            )
            if intent.evidence.decision == "rejected"
            else rec.requests > 0 and result is not None
        )
        if applied:
            return result
        reason = "The ordinary decision could not complete; review the change's result"
    except (Exception, asyncio.CancelledError) as error:
        reason = str(error) or "The accepted decision was interrupted"
    finally:
        if reason is not None:
            await ctx.error(
                "server",
                f"{intent.label} of {rec.change_id} did not run: {reason}",
                rec,
                fatal=False,
            )
        if rec.person_intent is intent:
            rec.person_intent = None
    return None
