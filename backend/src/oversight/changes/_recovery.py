from oversight.changes import _delivery as delivery
from oversight.changes._state import ChangeRecord, Facts
from oversight.changes._support import ChangeContext
from oversight.events import EventRejected
from oversight.workspace import IntegrityError, WorkspaceError, verify_merge_abort


async def read_facts(ctx: ChangeContext, head_commit: str) -> Facts:
    main = ctx.workspace.main
    return Facts(
        head=(await ctx.git(main, "rev-parse", "HEAD")).strip(),
        clean=not (await ctx.git(main, "status", "--porcelain")).strip(),
        merging=await ctx.yes_no(main, "rev-parse", "-q", "--verify", "MERGE_HEAD"),
        contains=await ctx.yes_no(main, "merge-base", "--is-ancestor", head_commit, "HEAD"),
    )


async def recover(
    ctx: ChangeContext,
    rec: ChangeRecord,
    comment: str | None,
    pre: str | None,
    error: Exception | None,
) -> None:
    """Decide from git facts after a failed approval, or a cancelled one (`error` is `None`):
    abort an unfinished merge, record a landed one; a conflict already sent stays `conflict`. No
    `reset --hard`, no lock removal. A cancelled approval always reports; a landed merge closes
    out first and leaves its cleanup to the next decision or `open`."""
    if isinstance(error, EventRejected) or rec.delivery_rejected:
        if isinstance(error, EventRejected):
            await delivery.rejected(ctx, rec, error)
        return
    main = ctx.workspace.main
    facts: Facts | None = None
    try:
        merging = await ctx.yes_no(main, "rev-parse", "-q", "--verify", "MERGE_HEAD")
        if merging or isinstance(error, IntegrityError):
            if pre is None:
                raise WorkspaceError("cannot verify the main preceding this merge")
            await verify_merge_abort(
                ctx.workspace,
                main,
                pre=pre,
                head=rec.head_commit or rec.base_commit,
                timeout_s=ctx.timeout,
            )
        if merging:
            await ctx.git(main, "merge", "--abort")
        facts = await read_facts(ctx, rec.head_commit or rec.base_commit)
    except Exception:
        facts = None
    if facts is not None and facts.merged:
        await delivery.record_merge(ctx, rec, comment, facts.head)
        if error is not None:
            return  # the approval closes out and cleans up
        await delivery.close_out(ctx, rec)
        note = "the merge landed and is recorded"
    elif facts is not None and facts.unchanged(pre):
        if rec.fallback == "conflict":  # set only by `_record_conflict`: the store has it
            rec.state = "conflict"
            note = "main is unchanged and the conflict is recorded; request changes or reject"
        else:
            rec.state = "ready"
            note = "main is unchanged; approve again or reject"
    else:
        ctx.state.break_change(rec)
        note = "code=main_suspect; reason=main's state is unknown; reject the change or restart"
    what = "was cancelled" if error is None else f"failed: {error}"
    await ctx.error("git", f"Approving {rec.change_id} {what}. {note}.", rec, fatal=False)
