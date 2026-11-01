"""Park idle rules waits at verified main commits; renew only on graph delivery."""

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING

from oversight.events import NeedsYouWait

if TYPE_CHECKING:
    from oversight.changes._assessment import AssessmentWait, RulesScheduler
    from oversight.changes._rules import RulesReservation
    from oversight.changes._state import ChangeRecord
    from oversight.changes._support import ChangeContext


def following(sched: "RulesScheduler", wait: "AssessmentWait") -> bool:
    return (
        sched.valid(wait, check_main=False, parked_ok=True, working_ok=True)
        and not wait.rec.person_acted
    )


def changed(sched: "RulesScheduler", post: str) -> None:
    if sched.committed_main == post:
        return
    sched.committed_main = post
    for wait in list(sched.waits.values()):
        if wait.main_commit != post and not wait.parked and following(sched, wait):
            park(sched, wait)


def park(sched: "RulesScheduler", wait: "AssessmentWait") -> None:
    # Keep the deadline timer alive even if the graph never reaches this commit.
    wait.parked, wait.dirty = True, False
    if wait.task is not None and wait.task is not asyncio.current_task():
        wait.task.cancel()


def delivered(sched: "RulesScheduler", post: str) -> None:
    if post != sched.committed_main or post != sched.ctx.state.snapshot.commit:
        return
    for wait in list(sched.waits.values()):
        if wait.parked:
            renew(sched, wait, post)


def renew(sched: "RulesScheduler", old: "AssessmentWait", post: str) -> None:
    if not following(sched, old):
        sched.invalidate(old.rec)
        return
    if old.deadline is not None and sched.monotonic() >= old.deadline:
        return  # The old timer owns expiry, including a delivery at the deadline.
    sched.generation += 1
    new = replace(
        old,
        target=NeedsYouWait(
            old.rec.change_id, old.target.revision, old.target.head, sched.generation
        ),
        main_commit=post,
        parked=False,
        timer=None,
        task=None,
        dirty=False,
        latest=None,
        terminal_seq=None,
        spend_pin=None,
    )
    # Copy immutable source captures before invalidation retires the old generation.
    sched.invalidate(old.rec)
    sched.waits[old.rec.change_id] = new
    if new.deadline is not None:
        sched.start_timer(new)
    sched.wake(old.rec)


async def queue(ctx: "ChangeContext", reservation: "RulesReservation") -> bool:
    """Called under main lock, before any Git effect; never retarget a reservation."""
    from oversight.changes import _rules as rules

    rec, assessment = reservation.wait.rec, reservation.evidence.assessment
    assert assessment is not None
    async with ctx.workspace.operations.git_lease():
        head = (await ctx.git(ctx.workspace.main, "rev-parse", "HEAD")).strip()
    if (
        ctx.assessment.committed_main is None
        or head != ctx.assessment.committed_main
        or head == assessment.main_commit
        or ctx.state.suspect
        or ctx.workspace.integrity.main_suspect
        or rules.availability(ctx) is not None
        or rec.person_intent is not None
        or rec.person_acted
        or not rules.current(ctx, reservation)
    ):
        return False
    rec.follow_pending = rec.decision
    return True


def decision_finished(sched: "RulesScheduler", rec: "ChangeRecord", decision: int) -> None:
    """Renew only after the old owner and verified recovery have completely settled."""
    if rec.follow_pending != decision:
        return
    rec.follow_pending = None
    old = sched.waits.get(rec.change_id)
    ctx = sched.ctx
    if (
        old is None
        or old.terminal_seq is not None
        or rec.decision != decision
        or rec.state != "ready"
        or rec.rules_task is not None
        or rec.person_intent is not None
        or rec.person_acted
        or ctx.state.closing
        or ctx.state.suspect
        or ctx.workspace.integrity.main_suspect
        or rec.resolution is not None
        or rec.delivery_rejected
        or (rec.revision, rec.head_commit, rec.base_commit)
        != (old.target.revision, old.target.head, old.proposal.base_commit)
        or rec.proposal_mode != old.mode
        or old.mode != ctx.mode.snapshot()
        or ctx.mode.authority_suspended()
    ):
        return
    sched.generation += 1
    new = replace(
        old,
        target=NeedsYouWait(rec.change_id, old.target.revision, old.target.head, sched.generation),
        active=True,
        main_commit=ctx.state.snapshot.commit,
        parked=sched.committed_main not in (None, ctx.state.snapshot.commit),
        timer=None,
        task=None,
        dirty=False,
        latest=None,
        terminal_seq=None,
        spend_pin=None,
    )
    sched.waits[rec.change_id] = new
    if new.deadline is not None:
        sched.start_timer(new)
        if sched.monotonic() >= new.deadline:
            return  # Its own timer publishes expiry; no assessment or admission may run.
    sched.wake(rec)
