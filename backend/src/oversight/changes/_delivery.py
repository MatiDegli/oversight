import asyncio
import functools
import logging
import os
from typing import Literal

from oversight.agents import validate_agent_status
from oversight.changes._resolution_validation import validate_resolution_evidence
from oversight.changes._state import (
    CLOSE_OUT_ATTEMPTS,
    STOPPED,
    ChangeRecord,
)
from oversight.changes._support import ChangeContext
from oversight.changes._versions import (
    main_delta,
    with_positions,
)
from oversight.events import EventPayload, EventRejected
from oversight_contracts.events import (
    AgentStatus,
    ChangeResolved,
    Conflict,
)

_log = logging.getLogger("oversight.changes._manager")

EVENT_TYPES = {
    "requested": "change.resolved changes_requested",
    "stopped": "agent.status stopped",
    "resolved": "change.resolved",
    "delta": "graph.delta",
    "done": "agent.status done",
}
"""Close-out events by their names in `ChangeRecord.sent`, as an `error` names them."""


async def record_merge(
    ctx: ChangeContext, rec: ChangeRecord, comment: str | None, post: str
) -> None:
    """The merge is a fact: rescan main and build the resolution and delta, sending nothing."""
    await project_merge(ctx, rec, post)
    rec.merged = True
    rec.resolution = resolution(
        ctx, rec, "approved", comment, decided_by="person", merge_commit=post
    )


async def project_merge(ctx: ChangeContext, rec: ChangeRecord, post: str) -> None:
    """Shared graph projection; a scan failure preserves the previous snapshot."""
    try:
        new = await ctx.scan(post)
    except Exception as error:
        # The snapshot stays, so the next delta starts from what the UI holds.
        await ctx.error("graph", f"Main's graph was not rescanned: {error}", rec, fatal=False)
    else:
        rec.delta = main_delta(ctx.state.snapshot, new, post, "merge")
        rec.next_snapshot = with_positions(new, ctx.state.snapshot)
        supersede_pending(ctx, rec)


def resolution(
    ctx: ChangeContext,
    rec: ChangeRecord,
    decision: Literal["approved", "rejected"],
    comment: str | None,
    *,
    decided_by: Literal["person", "rules", "cross_review", "lead_agent"],
    merge_commit: str | None = None,
    conflict: Conflict | None = None,
    reason: Literal["stopped"] | None = None,
) -> ChangeResolved:
    merge: Literal["merged", "conflict"] | None = None
    if decision == "approved":
        merge = "conflict" if conflict is not None else "merged"
    payload = ChangeResolved(
        change_id=rec.change_id,
        agent_id=rec.agent_id,
        revision=rec.revision,
        decision=decision,
        decided_by=decided_by,
        head_commit=rec.evidence.head_commit
        if rec.evidence
        else rec.head_commit or rec.base_commit,
        reason=reason,
        comment=comment,
        merge=merge,
        merge_commit=merge_commit,
        conflict=conflict,
        answers=list(rec.evidence.answers)
        if rec.evidence and rec.evidence.answers is not None
        else None,
    )

    if rec.evidence is None:
        raise ValueError("resolution lacks pre-effect evidence")
    validate_resolution_evidence(payload, rec.evidence)
    return payload


def status(ctx: ChangeContext, rec: ChangeRecord, state: Literal["done", "stopped"]) -> AgentStatus:
    task = rec.task
    payload = AgentStatus(
        agent_id=task.agent_id,
        slot=task.slot,
        change_id=task.change_id,
        task=task.task,
        model=rec.model_id,
        base_commit=rec.base_commit,
        state=state,
        step=rec.steps,
        max_steps=task.limits.max_steps * (rec.requests + 1),
        detail=STOPPED if state == "stopped" else None,
        answering=task.answering,
    )

    validate_agent_status(payload, base_commit=rec.base_commit, answering=task.answering)
    return payload


async def finish(ctx: ChangeContext, rec: ChangeRecord) -> None:
    """Close out, then clean up once `change.resolved` is out, so a worktree or branch is never
    removed before its resolution is sent. The change stays pending until both are done."""
    if rec.delivery_rejected:
        return
    async with ctx.workspace.locks(main=True, agent_id=rec.agent_id):
        await close_out(ctx, rec)
        if not rec.delivery_rejected and "resolved" in rec.sent and not rec.cleaned:
            await cleanup(ctx, rec)
        if rec.state == "closed" and rec.cleaned:
            ctx.state.pending.pop(rec.agent_id, None)


async def finish_pending(ctx: ChangeContext) -> None:
    """Before a decision or an `open`: retry every unfinished close-out and cleanup, and every
    unsent request for changes, which closes nothing."""
    for rec in list(ctx.state.pending.values()):
        if rec.delivery_rejected:
            continue
        if rec.state in ("closing", "closed"):
            await finish(ctx, rec)
        else:
            async with ctx.workspace.locks(main=True, agent_id=rec.agent_id):
                await send_requested(ctx, rec)


async def close_out(ctx: ChangeContext, rec: ChangeRecord) -> None:
    """The only way into `closed`: the resolution, the delta (approve only), then `done`, each
    marked when sent, so nothing goes twice. Until all are out the change is `closing` and
    pending, and a later call sends only what is missing. Never raises, save a cancel."""
    if rec.delivery_rejected:
        return
    rec.state = "closing"
    ctx.state.pending[rec.agent_id] = rec
    try:
        failure = await try_close_out(ctx, rec)
    except EventRejected as error:
        await rejected(ctx, rec, error)
        return
    if failure:
        await ctx.error(
            "server",
            f"The close-out of {rec.change_id} is incomplete ({failure}). "
            "The next decision or agent start sends the rest.",
            rec,
            fatal=False,
        )


async def try_close_out(ctx: ChangeContext, rec: ChangeRecord) -> str:
    """Send what is missing, each event up to `CLOSE_OUT_ATTEMPTS` times; `closed` once all is
    out, else the failure that stopped it. The caller holds main's lock and the change's."""
    failure = await send_close_out(ctx, rec)
    if not failure:
        rec.state = "closed"
    return failure


def unsent_events(ctx: ChangeContext, rec: ChangeRecord) -> list[tuple[str, EventPayload]]:
    """The close-out events not yet sent, in order: an unsent request for changes, the
    resolution, the delta, `done`; for a Stop, the request, `stopped`, then the resolution, so
    the agent's last status precedes the resolution that frees its slot."""
    events: list[tuple[str, EventPayload | None]]
    if rec.stopped:
        events = [
            ("requested", rec.requested),
            ("stopped", status(ctx, rec, "stopped")),
            ("resolved", rec.resolution),
        ]
    else:
        events = [
            ("requested", rec.requested),
            ("resolved", rec.resolution),
            ("delta", rec.delta),
            ("done", status(ctx, rec, "done")),
        ]
    return [(n, p) for n, p in events if p is not None and n not in rec.sent]


async def send_close_out(ctx: ChangeContext, rec: ChangeRecord) -> str:
    """Send what is missing, in order, each event up to `CLOSE_OUT_ATTEMPTS` times; the last
    failure of the first event that never went out, or "" once everything is out. A publish
    that a cancelled close-out left running is waited for first, so it is not resent."""
    if rec.delivery_rejected:
        return rec.delivery_rejected
    await ctx.ports.drain(rec)
    if rec.delivery_rejected:
        raise EventRejected(rec.delivery_rejected)
    for name, payload in unsent_events(ctx, rec):
        failure = ""
        for _ in range(CLOSE_OUT_ATTEMPTS):
            failure = await send_event(ctx, rec, name, payload)
            if not failure:
                break
        if failure:
            return failure  # later events wait: the resolution comes first
    return ""


async def send_event(
    ctx: ChangeContext, rec: ChangeRecord, name: str, payload: EventPayload
) -> str:
    """One try at one event: its failure, or "" once the store took it."""
    # The publish runs in its own task, kept on the record, and a done-callback records its
    # outcome: the store may commit the event however many cancels the close-out gets, and
    # the next close-out must not send it again.
    if rec.delivery_rejected:
        raise EventRejected(rec.delivery_rejected)
    if isinstance(payload, ChangeResolved):
        evidence = rec.requested_evidence if name == "requested" else rec.evidence
        if evidence is None:
            raise ValueError("resolution lacks pre-effect evidence")
        validate_resolution_evidence(payload, evidence)
    sending = ctx.spawn(deliver(ctx, payload))
    sending.add_done_callback(functools.partial(receipt, ctx, rec, name))
    rec.sending = sending
    cancelled = await ctx.settled(sending)
    if cancelled or sending.cancelled():
        raise asyncio.CancelledError
    failure = sending.exception()
    if isinstance(failure, EventRejected):
        raise failure
    return "" if failure is None else f"{name}: {failure}"


async def deliver(ctx: ChangeContext, payload: EventPayload) -> None:
    """`_publish` as a tracked task's coroutine."""
    await ctx.publish(payload)


def receipt(ctx: ChangeContext, rec: ChangeRecord, name: str, sending: asyncio.Task[None]) -> None:
    """A close-out publish ended: mark its event sent if the store took it, and move `snapshot`
    after a delta. As a done-callback added first, it runs before anyone awaiting the publish
    resumes, cancelled or not. A cancelled publish tells nothing: its event stays unsent."""
    if rec.sending is sending:
        rec.sending = None
    if sending.cancelled():
        return
    failure = sending.exception()
    if isinstance(failure, EventRejected):
        mark_rejected(ctx, rec, failure)
        ctx.spawn(report_rejected(ctx, rec))
        return
    if failure is not None:
        return
    if name == "requested":
        rec.requested = None  # one per request, so never in `sent`
        return
    rec.sent.add(name)
    if name == "delta" and rec.next_snapshot is not None:
        ctx.state.snapshot = rec.next_snapshot


async def drain_pending(ctx: ChangeContext) -> None:
    """Under main's lock, before a delta is built from `snapshot`: wait for every close-out
    publish a cancel left running, which may still move it."""
    for other in list(ctx.state.pending.values()):
        await ctx.ports.drain(other)


def supersede_pending(ctx: ChangeContext, built: ChangeRecord | None = None) -> None:
    """A delta was built from `snapshot`: it starts from the graph the UI holds and covers
    every merge not yet sent. Sent later, an older one would start from a graph the UI no
    longer holds, so each pending change drops its delta (`built`'s own stays)."""
    for other in ctx.state.pending.values():
        if other is not built and "delta" not in other.sent:
            other.delta = other.next_snapshot = None


async def send_requested(ctx: ChangeContext, rec: ChangeRecord) -> str:
    """Send a pending `changes_requested`, up to `CLOSE_OUT_ATTEMPTS` tries: the last failure,
    or "" once the store took it. A change pending only for it then leaves `_pending`."""
    if rec.delivery_rejected:
        return rec.delivery_rejected
    await ctx.ports.drain(rec)
    if rec.delivery_rejected:
        raise EventRejected(rec.delivery_rejected)
    failure = ""
    for _ in range(CLOSE_OUT_ATTEMPTS):
        if rec.requested is None:
            break
        failure = await send_event(ctx, rec, "requested", rec.requested)
    if rec.requested is not None:
        return failure
    if rec.state not in ("closing", "closed"):
        ctx.state.pending.pop(rec.agent_id, None)
    return ""


async def cleanup(ctx: ChangeContext, rec: ChangeRecord) -> None:
    """Remove the worktree, then the branch, each step skipped once done, so a rerun after a
    failure finishes the job. A failure is reported and retried by the next decision or
    `open`, as the change is still pending."""
    if rec.delivery_rejected:
        return
    workspace, agent = ctx.workspace, rec.agent_id
    main = workspace.main
    try:
        async with workspace.locks(main=True, agent_id=agent):
            listing = await ctx.git(main, "worktree", "list", "--porcelain", "-z")
            if f"worktree {rec.tree.root}" in listing.split("\0"):
                if await asyncio.to_thread(os.path.lexists, rec.tree.root):
                    remove = (
                        workspace.cleanup_merged_worktree
                        if rec.merged
                        else workspace.discard_worktree
                    )
                    await remove(agent, delete_branch=False)
                else:
                    await ctx.git(main, "worktree", "prune")
            if await ctx.yes_no(main, "rev-parse", "--verify", "--quiet", f"refs/heads/{agent}"):
                # -d after a merge, so a branch that is not in main is never deleted.
                await ctx.git(main, "branch", "-d" if rec.merged else "-D", agent)
    except Exception as error:
        await ctx.error(
            "git",
            f"Cleaning up after {rec.change_id} failed: {error}. "
            "The next decision or agent start tries again.",
            rec,
            fatal=False,
        )
        return
    rec.cleaned = True


async def aclose(ctx: ChangeContext) -> None:
    """Drain before the store closes, which the caller does only once this returns: wait for
    the decisions in flight, run the close-out retry once more for each change with unsent
    events, then send one `error` per change naming each event still unsent. If the store
    refuses that `error` too, one log line per change names what was lost. Cleanup is left
    to the next start's `reset`."""
    ctx.state.closing = True
    ctx.assessment.mode_changed()
    for change in ctx.state.changes.values():
        if change.review_task is not None:
            change.review_task.cancel()

    async def drain_tasks() -> None:
        while ctx.state.tasks:
            await asyncio.gather(*ctx.state.tasks, return_exceptions=True)
            # gather may complete synchronously when every task is already done.
            # Let their queued callbacks retire them (and track any final receipts)
            # before checking the set again, or this loop starves even timeout cancels.
            await asyncio.sleep(0)

    await drain_tasks()
    for rec in list(ctx.state.pending.values()):
        if rec.delivery_rejected:
            continue
        async with ctx.workspace.locks(main=True, agent_id=rec.agent_id):
            if rec.state == "closing":
                try:
                    failure = await try_close_out(ctx, rec)
                except EventRejected as error:
                    await rejected(ctx, rec, error)
                    continue
                names = [name for name, _ in unsent_events(ctx, rec)]
            elif rec.requested is not None:
                try:
                    failure, names = await send_requested(ctx, rec), ["requested"]
                except EventRejected as error:
                    await rejected(ctx, rec, error)
                    continue
            else:
                continue  # all sent; only its cleanup is pending
            if failure:
                unsent = ", ".join(EVENT_TYPES[name] for name in names)
                reported = await ctx.error(
                    "server",
                    f"Shutting down with the close-out of {rec.change_id} incomplete "
                    f"({failure}): {unsent} never sent.",
                    rec,
                    fatal=False,
                )
                if not reported:
                    # Only the event names and ids: no payload, no store message.
                    _log.error(
                        "Shutting down with the close-out of %s (agent %s) incomplete; "
                        "never sent, and the store refused the error that names them: %s",
                        rec.change_id,
                        rec.agent_id,
                        unsent,
                    )
    await drain_tasks()


def mark_rejected(ctx: ChangeContext, rec: ChangeRecord, error: EventRejected) -> None:
    """Terminal for this run: retain Git facts, payload, evidence, worktree and occupied slot."""
    rec.delivery_rejected = str(error)
    rec.state = "delivery_failed"
    ctx.state.pending[rec.agent_id] = rec


async def report_rejected(ctx: ChangeContext, rec: ChangeRecord) -> None:
    if not await ctx.error(
        "server",
        f"Delivery rejected for {rec.change_id}; restart required: {rec.delivery_rejected}",
        rec,
        fatal=False,
    ):
        _log.error("Terminal delivery rejection for %s", rec.change_id)


async def rejected(ctx: ChangeContext, rec: ChangeRecord, error: EventRejected) -> None:
    if rec.delivery_rejected is None:
        mark_rejected(ctx, rec, error)
        await report_rejected(ctx, rec)
