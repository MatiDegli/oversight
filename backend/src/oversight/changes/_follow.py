"""Park idle rules waits at verified main commits; renew only on graph delivery."""

import asyncio
from dataclasses import replace
from typing import TYPE_CHECKING

from oversight.events import NeedsYouWait

if TYPE_CHECKING:
    from oversight.changes._assessment import AssessmentWait, RulesScheduler


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
