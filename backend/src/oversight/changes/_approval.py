import asyncio

from oversight.changes import _delivery as delivery
from oversight.changes import _merge as merge
from oversight.changes import _proposal as proposal
from oversight.changes import _recovery as recovery
from oversight.changes import _rules as rules
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


async def approve_rules(ctx: ChangeContext, reservation: rules.RulesReservation) -> None:
    """Keep main -> worktree through preflight and the transaction port.

    worktree_unverifiable buckets unexpected preflight errors; it is not a diagnosis.
    """
    rec, evidence = reservation.wait.rec, reservation.evidence
    try:
        async with ctx.workspace.locks(main=True, agent_id=rec.agent_id):
            if not rules.current(ctx, reservation):
                return
            failure = rules.revalidate(ctx, reservation)
            if failure is None:
                async with ctx.workspace.operations.git_lease():
                    failure = await rules_tree(ctx, reservation)
            if not rules.current(ctx, reservation):
                return
            failure = failure or rules.revalidate(ctx, reservation)
            if failure:
                await rules.terminal(ctx, reservation, *failure)
                return
            reason = await ctx.ports.rules_transaction(rec, evidence)
            if reason:
                await rules.terminal(ctx, reservation, "policy_invalid", reason)
    except asyncio.CancelledError:
        await rules.terminal(ctx, reservation, "cancelled", "Rules approval was cancelled.")
        raise
    except Exception as error:
        await rules.terminal(
            ctx, reservation, "worktree_unverifiable", f"Rules preflight failed: {error}"
        )


async def rules_tree(
    ctx: ChangeContext, reservation: rules.RulesReservation
) -> tuple[str, str] | None:
    rec, evidence = reservation.wait.rec, reservation.evidence
    assessment = evidence.assessment
    assert assessment is not None
    main = (await ctx.git(ctx.workspace.main, "rev-parse", "HEAD")).strip()
    if main != assessment.main_commit:
        return "main_moved", "Main moved during rules evaluation; review this change yourself."
    head = (await ctx.git(rec.tree, "rev-parse", "HEAD")).strip()
    if head != evidence.head_commit:
        return "target_changed", "The worktree head differs from the reserved proposal."
    tree = (await ctx.git(rec.tree, "rev-parse", "HEAD^{tree}")).strip()
    if tree != assessment.candidate_tree:
        return "worktree_unverifiable", "The worktree tree differs from the assessed candidate."
    if (await ctx.git(rec.tree, "status", "--porcelain")).strip():
        return "worktree_unverifiable", "The reserved worktree has unproposed edits."
    return None


async def approve(ctx: ChangeContext, rec: ChangeRecord, comment: str | None) -> None:
    await delivery.finish_pending(ctx)
    async with ctx.workspace.locks(main=True, agent_id=rec.agent_id):
        # A close-out publish left running by a cancel may still move `snapshot`, which this
        # merge's delta starts from, and must find its own delta still there: wait for it.
        await delivery.drain_pending(ctx)
        if ctx.state.suspect or ctx.workspace.integrity.main_suspect:
            # M5: main turned suspect while this queued. No git call; the decision's end puts
            # the change back to `ready`.
            await ctx.error(
                "server",
                "code=main_suspect; reason=Main became suspect before this approval ran; "
                "reject the change or restart.",
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
