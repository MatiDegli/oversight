import asyncio

from oversight.changes import _delivery as delivery
from oversight.changes import _merge as merge
from oversight.changes import _proposal as proposal
from oversight.changes import _recovery as recovery
from oversight.changes._decision_validation import validate_decision_target
from oversight.changes._resolution_validation import validate_resolution_intent
from oversight.changes._state import (
    APPROVABLE,
    ChangeRecord,
    Source,
    WrongState,
)
from oversight.changes._support import ChangeContext
from oversight.events import EventRejected
from oversight.workspace import (
    WorkspaceError,
)


async def approve(ctx: ChangeContext, rec: ChangeRecord, comment: str | None) -> None:
    await delivery.finish_pending(ctx)
    async with ctx.workspace.locks(main=True, agent_id=rec.agent_id):
        # A close-out publish left running by a cancel may still move `snapshot`, which this
        # merge's delta starts from, and must find its own delta still there: wait for it.
        await delivery.drain_pending(ctx)
        if ctx.state.suspect:
            # M5: main turned suspect while this queued. No git call; the decision's end puts
            # the change back to `ready`.
            await ctx.error(
                "server",
                "Main became suspect before this approval ran; reject the change or restart.",
                rec,
                fatal=False,
            )
            return
        assert rec.evidence is not None
        try:
            validate_decision_target(
                rec, rec.evidence.revision, rec.evidence.head_commit, APPROVABLE, reserved=True
            )
        except WrongState:
            await ctx.error(
                "server",
                f"Revision {rec.revision} of {rec.change_id} arrived after you clicked; "
                "review it and approve again.",
                rec,
                fatal=False,
            )
            return
        validate_resolution_intent(rec.evidence)
        if not await as_clicked(ctx, rec):
            return
        pre: str | None = None
        try:
            pre = (await ctx.git(ctx.workspace.main, "rev-parse", "HEAD")).strip()
            await merge.merge(ctx, rec, comment, pre)
        except asyncio.CancelledError:
            # Still under both locks: settle main from its facts, then cancel.
            await recovery.recover(ctx, rec, comment, pre, None)
            raise
        except EventRejected as error:
            await delivery.rejected(ctx, rec, error)
            return
        except Exception as error:
            await recovery.recover(ctx, rec, comment, pre, error)
        if rec.merged:
            await delivery.finish(ctx, rec)


async def as_clicked(ctx: ChangeContext, rec: ChangeRecord) -> bool:
    """R1: merge only the revision Approve was clicked on, with the worktree exactly at its
    head. A save proposed after the click is left for review; one cut short, whose write or
    commit landed without its proposal, is re-proposed from what the worktree holds, leftovers
    committed as the user. Either way an `error` names the revision to review and nothing
    merges: main is untouched, so the decision's end puts the change back to `ready`. The
    caller holds both locks."""
    if rec.revision != rec.taken:
        await ctx.error(
            "server",
            f"Revision {rec.revision} of {rec.change_id} arrived after you clicked; "
            "review it and approve again.",
            rec,
            fatal=False,
        )
        return False
    tree = rec.tree
    try:
        head = (await ctx.git(tree, "rev-parse", "HEAD")).strip()
        if head == rec.head_commit and not (await ctx.git(tree, "status", "--porcelain")).strip():
            return True
        head, files = await proposal.commit(ctx, rec, "user")
        await proposal.proposal(ctx, rec, head, files, "user", rec.summary)
    except Exception as error:
        source: Source = "git" if isinstance(error, WorkspaceError) else "server"
        await ctx.error(
            source,
            f"Approving {rec.change_id} merged nothing: its worktree could not be checked or "
            f"re-proposed ({error}); approve again or reject.",
            rec,
            fatal=False,
        )
        return False
    await ctx.error(
        "server",
        f"Revision {rec.revision} of {rec.change_id} holds edits that were not proposed when "
        "you clicked; review it and approve again.",
        rec,
        fatal=False,
    )
    return False
