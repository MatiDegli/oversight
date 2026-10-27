from oversight.changes import _delivery as delivery
from oversight.changes import _recovery as recovery
from oversight.changes._state import ChangeRecord
from oversight.changes._support import ChangeContext
from oversight.workspace import (
    WorkspaceError,
)
from oversight_contracts.events import (
    ChangeResolved,
    Conflict,
)


async def merge(ctx: ChangeContext, rec: ChangeRecord, comment: str | None, pre: str) -> None:
    main = ctx.workspace.main
    head = rec.head_commit or rec.base_commit
    rec.fallback = "broken"  # from here on, main may change
    # --no-ff on every merge: --no-commit alone fast-forwards a head that descends from main,
    # leaving HEAD moved and nothing for `merge --abort` to undo.
    result = await ctx.run(main, "merge", "--no-ff", "--no-commit", head)
    if result.returncode != 0:
        if (
            result.returncode == 1
            and not result.timed_out
            and await conflict(ctx, rec, comment, pre)
        ):
            return
        detail = "timed out" if result.timed_out else ctx.workspace.redact(result.stderr)
        raise WorkspaceError(f"git merge failed: {detail.strip()[-500:]}")
    if await ctx.yes_no(main, "rev-parse", "-q", "--verify", "MERGE_HEAD"):
        if pre != rec.base_commit and not await combined_tests_pass(ctx, rec, comment, pre):
            return
        await ctx.git(
            main,
            "commit",
            "-q",
            "-m",
            f"Merge {rec.change_id} ({rec.agent_id})",
            commit_as="user",
        )
    facts = await recovery.read_facts(ctx, head)
    if not facts.merged:
        raise WorkspaceError("the merge did not land on main")
    await delivery.record_merge(ctx, rec, comment, facts.head)


async def conflict(ctx: ChangeContext, rec: ChangeRecord, comment: str | None, pre: str) -> bool:
    main = ctx.workspace.main
    names = await ctx.git(main, "diff", "--name-only", "--diff-filter=U", "-z")
    paths = sorted(p for p in names.split("\0") if p)
    if not paths:
        return False
    await ctx.git(main, "merge", "--abort")
    if not (await recovery.read_facts(ctx, rec.head_commit or rec.base_commit)).unchanged(pre):
        raise WorkspaceError("main did not return to its HEAD after the conflict")
    await conflicted(ctx, rec, comment, Conflict(kind="merge", paths=paths, tests=None))
    return True


async def combined_tests_pass(
    ctx: ChangeContext, rec: ChangeRecord, comment: str | None, pre: str
) -> bool:
    """R5: main moved since the change's base, so the merge, staged on main, is tested before
    its commit. A failure or timeout aborts it and resolves as a tests conflict. A cancel
    during the run is `_recover`'s, which aborts on `MERGE_HEAD`."""
    main = ctx.workspace.main
    run = await ctx.workspace.run_tests(main, timeout_s=rec.task.limits.test_timeout_s)
    if run.result.outcome == "passed":
        return True
    await ctx.git(main, "merge", "--abort")
    if not (await recovery.read_facts(ctx, rec.head_commit or rec.base_commit)).unchanged(pre):
        raise WorkspaceError("main did not return to its HEAD after the combined tests")
    await conflicted(ctx, rec, comment, Conflict(kind="tests", paths=[], tests=run.result))
    return False


async def conflicted(
    ctx: ChangeContext, rec: ChangeRecord, comment: str | None, conflict: Conflict
) -> None:
    """Main is back at `pre`: send the approval's conflict and record it, as one `_whole` step,
    so a cancel never leaves a resolution in the store that the record, and so a second
    Approve or a Request changes, does not know. The change stays `deciding`, with `conflict`
    as its fallback: the decision's end, or its recovery, makes it `conflict`, and no other
    decision enters before."""
    resolution = delivery.resolution(ctx, rec, "approved", comment, conflict=conflict)
    await ctx.whole(record_conflict(ctx, rec, resolution, conflict))


async def record_conflict(
    ctx: ChangeContext, rec: ChangeRecord, resolution: ChangeResolved, conflict: Conflict
) -> None:
    await ctx.publish(resolution)
    rec.conflict, rec.fallback = conflict, "conflict"
