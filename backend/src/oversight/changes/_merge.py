from typing import TYPE_CHECKING

from oversight.changes import _delivery as delivery
from oversight.changes import _recovery as recovery
from oversight.changes._resolution_validation import validate_resolution_evidence
from oversight.changes._state import ChangeRecord
from oversight.changes._suite_evidence import publish_suite
from oversight.changes._support import ChangeContext, RulesFailure
from oversight.events import EventRejected
from oversight.workspace import (
    MergeGuard,
    SuiteInterrupted,
    SuiteRun,
    SuiteUnavailable,
    WorkspaceError,
    prepare_merge_guard,
    verify_merge_abort,
    verify_merge_guard,
)
from oversight_contracts.events import (
    ChangeResolved,
    Conflict,
)

if TYPE_CHECKING:
    from oversight.changes._transaction import Transaction


async def merge(
    ctx: ChangeContext,
    rec: ChangeRecord,
    comment: str | None,
    pre: str,
    *,
    transaction: "Transaction | None" = None,
) -> None:
    main = ctx.workspace.main
    head = rec.head_commit or rec.base_commit
    if transaction is None:
        rec.fallback = "broken"  # rules owns verified recovery and its fallback
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
        guard = await prepare_merge_guard(
            ctx.workspace, main, pre=pre, head=head, timeout_s=ctx.timeout
        )
        if transaction is not None:
            transaction.guard = guard
            if guard.candidate_tree == transaction.tree:
                raise RulesFailure("policy_invalid", "The rules combination is empty.")
        tested_tree = (await ctx.git(main, "rev-parse", f"{head}^{{tree}}")).strip()
        run = None
        if transaction is not None or pre != rec.base_commit:
            run = await combined_tests_pass(
                ctx, rec, comment, pre, guard, required=transaction is not None
            )
            if run is None:
                return
            tested_tree = run.tested_tree
        authorization = (
            transaction.authorize(guard, run)
            if transaction is not None and run is not None
            else ctx.workspace.git_lease()
        )
        async with authorization:
            if transaction is None:
                await verify_merge_guard(
                    ctx.workspace, main, guard, tested_tree=tested_tree, timeout_s=ctx.timeout
                )
            await ctx.git(
                main,
                "commit",
                "-q",
                "-m",
                f"Merge {rec.change_id} ({rec.agent_id})"
                + ("\n\nMerged-by: rules" if transaction is not None else ""),
                commit_as="rules" if transaction is not None else "user",
            )
            if transaction is not None:
                await recovery.verify_rules_merge(ctx, rec, guard)
    elif transaction is not None:
        raise RulesFailure("policy_invalid", "The rules combination is empty or already merged.")
    facts = await recovery.read_facts(ctx, head)
    if not facts.merged:
        raise WorkspaceError("the merge did not land on main")
    if transaction is not None:
        await transaction.record(facts.head)
    else:
        await delivery.record_merge(ctx, rec, comment, facts.head)


async def conflict(ctx: ChangeContext, rec: ChangeRecord, comment: str | None, pre: str) -> bool:
    main = ctx.workspace.main
    names = await ctx.git(main, "diff", "--name-only", "--diff-filter=U", "-z")
    paths = sorted(p for p in names.split("\0") if p)
    if not paths:
        return False
    await verify_merge_abort(
        ctx.workspace, main, pre=pre, head=rec.head_commit or rec.base_commit, timeout_s=ctx.timeout
    )
    await ctx.git(main, "merge", "--abort")
    if not (await recovery.read_facts(ctx, rec.head_commit or rec.base_commit)).unchanged(pre):
        raise WorkspaceError("main did not return to its HEAD after the conflict")
    await conflicted(ctx, rec, comment, Conflict(kind="merge", paths=paths, tests=None))
    return True


async def combined_tests_pass(
    ctx: ChangeContext,
    rec: ChangeRecord,
    comment: str | None,
    pre: str,
    guard: MergeGuard,
    *,
    required: bool = False,
) -> SuiteRun | None:
    """R5: main moved since the change's base, so the merge, staged on main, is tested before
    its commit. A failure or timeout aborts it and resolves as a tests conflict. A cancel
    during the run is `_recover`'s, which aborts on `MERGE_HEAD`."""
    main = ctx.workspace.main
    try:
        if required:
            run = await ctx.workspace.run_tests(
                main, timeout_s=rec.task.limits.test_timeout_s, isolation="required"
            )
        else:
            run = await ctx.workspace.run_tests(main, timeout_s=rec.task.limits.test_timeout_s)
    except (SuiteInterrupted, SuiteUnavailable) as error:
        await publish_suite(ctx, rec, error.run, phase="combined", revision=rec.revision)
        raise
    if run.result.outcome == "passed":
        await verify_merge_guard(
            ctx.workspace, main, guard, tested_tree=run.tested_tree, timeout_s=ctx.timeout
        )
    execution = await publish_suite(ctx, rec, run, phase="combined", revision=rec.revision)
    if run.result.outcome == "passed":
        return run
    if required:
        raise RulesFailure("suite_failed", "The required combined suite did not pass.")
    await verify_merge_abort(
        ctx.workspace, main, pre=pre, head=rec.head_commit or rec.base_commit, timeout_s=ctx.timeout
    )
    await ctx.git(main, "merge", "--abort")
    if not (await recovery.read_facts(ctx, rec.head_commit or rec.base_commit)).unchanged(pre):
        raise WorkspaceError("main did not return to its HEAD after the combined tests")
    await conflicted(ctx, rec, comment, Conflict(kind="tests", paths=[], tests=execution.suite))
    return None


async def conflicted(
    ctx: ChangeContext, rec: ChangeRecord, comment: str | None, conflict: Conflict
) -> None:
    """Main is back at `pre`: send the approval's conflict and record it, as one `_whole` step,
    so a cancel never leaves a resolution in the store that the record, and so a second
    Approve or a Request changes, does not know. The change stays `deciding`, with `conflict`
    as its fallback: the decision's end, or its recovery, makes it `conflict`, and no other
    decision enters before."""
    if rec.evidence is not None and rec.evidence.actor == "rules":
        raise RulesFailure("git_failed", f"The rules combination has a {conflict.kind} conflict.")
    resolution = delivery.resolution(
        ctx, rec, "approved", comment, decided_by="person", conflict=conflict
    )
    await ctx.whole(record_conflict(ctx, rec, resolution, conflict))


async def record_conflict(
    ctx: ChangeContext, rec: ChangeRecord, resolution: ChangeResolved, conflict: Conflict
) -> None:
    rec.resolution = resolution
    rec.conflict = conflict
    assert rec.evidence is not None
    validate_resolution_evidence(resolution, rec.evidence)
    try:
        await ctx.publish(resolution)
    except EventRejected as error:
        await delivery.rejected(ctx, rec, error)
        raise
    rec.conflict, rec.fallback = conflict, "conflict"
