"""Request changes, move an agent's base and settle revision transactions."""

import asyncio
import logging

from oversight.changes import _cross_review as cross_review
from oversight.changes import _delivery as delivery
from oversight.changes import _proposal as proposal
from oversight.changes._decision_validation import validate_decision_target
from oversight.changes._neighbors import graph_context
from oversight.changes._resolution_validation import (
    validate_resolution_evidence,
    validate_resolution_intent,
)
from oversight.changes._state import REVISABLE, ChangeRecord, Move, Revision, WrongState
from oversight.changes._support import ChangeContext
from oversight.changes._versions import parse
from oversight.events import EventRejected
from oversight.graph import change_graph
from oversight.workspace import WorkspaceError
from oversight_contracts.events import ChangeResolved

_log = logging.getLogger(__name__)


def revision_comment(ctx: ChangeContext, rec: ChangeRecord, comment: str) -> str:
    """Enrich only the agent's message; unavailable graph context never blocks a revision."""
    try:
        graph = rec.head.graph
        if graph.parse_errors:
            raise ValueError(f"parse errors in {', '.join(e.path for e in graph.parse_errors)}")
        edited = [n.id for n in rec.graph.nodes_added] + rec.graph.nodes_modified
        block = graph_context(graph, edited)
        return f"{comment}\n\n{block}" if block else comment
    except Exception as error:
        _log.warning(
            "Graph context unavailable for %s; sending comment alone: %s",
            rec.change_id,
            ctx.workspace.redact(str(error)),
        )
        return comment


async def request_changes(ctx: ChangeContext, rec: ChangeRecord, comment: str) -> Revision | None:
    await delivery.finish_pending(ctx)
    note: str | None = None
    if rec.fallback == "conflict":
        # Phase A, cancellable: a cancel before phase B leaves the change as it was.
        async with ctx.workspace.locks(main=True, agent_id=rec.agent_id):
            if await refused_as_suspect(ctx, rec):
                return None
            if not await preflight(ctx, rec):
                return None
            move = Move()
            try:
                note = await move_base(ctx, rec, move)
            except asyncio.CancelledError:
                await settle_move(ctx, rec, move, None)
                raise
            except Exception as error:
                await settle_move(ctx, rec, move, error)
                return None
        return await ctx.whole(requested(ctx, rec, comment, note))
    # From `ready`, under both locks as every decision: a save in review holds the agent's
    # lock until its revision is proposed, so the request names that revision and precedes
    # the revision run.
    async with ctx.workspace.locks(main=True, agent_id=rec.agent_id):
        if await refused_as_suspect(ctx, rec):
            return None
        if not await preflight(ctx, rec):
            return None
        return await ctx.whole(requested(ctx, rec, comment, note))


async def preflight(ctx: ChangeContext, rec: ChangeRecord) -> bool:
    assert rec.evidence is not None
    if rec.evidence.actor == "cross_review" and not await cross_review.preflight(ctx, rec):
        return False
    try:
        validate_decision_target(
            rec, rec.evidence.revision, rec.evidence.head_commit, REVISABLE, reserved=True
        )
    except WrongState as error:
        await ctx.error("server", str(error), rec, fatal=False)
        return False
    validate_resolution_intent(rec.evidence)
    return True


async def refused_as_suspect(ctx: ChangeContext, rec: ChangeRecord) -> bool:
    """F1, under main's lock: main turned suspect while the request queued, so it is refused
    with one `error`, no base move, no `changes_requested` and no revision; the decision's end
    puts the change back to `ready` or `conflict`, where it was taken from."""
    if not ctx.state.suspect:
        return False
    await ctx.error(
        "server",
        "Main became suspect before this request ran; reject it or restart.",
        rec,
        fatal=False,
    )
    return True


async def move_base(ctx: ChangeContext, rec: ChangeRecord, move: Move) -> str:
    """Merge main's HEAD into the agent's branch, committing as the agent; unmerged paths are
    committed with their markers, for the agent, which has no git, to resolve. The note."""
    main, tree = ctx.workspace.main, rec.tree
    move.main_head = (await ctx.git(main, "rev-parse", "HEAD")).strip()
    if (await ctx.git(main, "status", "--porcelain")).strip():
        raise WorkspaceError("main has uncommitted changes")
    move.old_head = (await ctx.git(tree, "rev-parse", "HEAD")).strip()
    message = f"Merge main into {rec.change_id} ({rec.agent_id})"
    result = await ctx.run(tree, "merge", "--no-ff", "--no-commit", move.main_head)
    if result.returncode == 0:
        # Exit 0 with no MERGE_HEAD: the branch already holds main.
        if await ctx.yes_no(tree, "rev-parse", "-q", "--verify", "MERGE_HEAD"):
            await ctx.git(tree, "commit", "-q", "-m", message, commit_as=rec.agent_id)
    elif result.returncode == 1 and (
        await ctx.git(tree, "diff", "--name-only", "--diff-filter=U", "-z")
    ).strip("\0"):
        await ctx.git(tree, "add", "-A")
        await ctx.git(tree, "commit", "-q", "-m", message, commit_as=rec.agent_id)
    else:
        detail = "timed out" if result.timed_out else ctx.workspace.redact(result.stderr)
        raise WorkspaceError(f"git merge failed: {detail.strip()[-500:]}")
    await moved(ctx, rec, move.main_head)
    return move_note(ctx, rec)


async def moved(ctx: ChangeContext, rec: ChangeRecord, main_head: str) -> None:
    """R4: the base is `main_head`. Both versions come from the trees, the head from every file
    of the worktree, never only the paths in the new diff, so what main added stays in the
    graph and the cone. Each file whose original or modified changed is shown again. Nothing
    is recorded until every read is done; a file not shown stays in `unpublished`, which the
    next edit or proposal sends."""
    main, tree = ctx.workspace.main, rec.tree
    base_files, base = ctx.state.bases.get(main_head) or await proposal.read_base(
        ctx, main, main_head
    )
    head_files = await proposal.read_files(ctx, tree)
    head = await parse(head_files, main_head, None)
    diff = ("diff", "--name-only", "--no-renames", "-z", main_head, "HEAD")
    names = await ctx.git(tree, *diff)
    touched = {path for path in names.split("\0") if path}
    for path in rec.published.keys() | rec.touched | touched:
        before = rec.base_files.get(path), rec.head_files.get(path)
        if before != (base_files.get(path), head_files.get(path)):
            rec.unpublished.add(path)
    rec.base_commit, rec.base_files, rec.base = main_head, base_files, base
    rec.head_files, rec.head, rec.touched = head_files, head, touched
    rec.graph = change_graph(base, head)
    for path in sorted(rec.unpublished):
        try:
            await proposal.send_file(ctx, rec, path)
        except Exception as error:
            await ctx.error("server", f"{path} was not shown again: {error}", rec, fatal=False)


def move_note(ctx: ChangeContext, rec: ChangeRecord) -> str:
    markers = [
        path
        for path in sorted(rec.touched)
        if any(line.startswith("<<<<<<< ") for line in rec.head_files.get(path, "").split("\n"))
    ]
    note = "Main was merged into your branch: your change now starts from main's latest version."
    if markers:
        note += f" These files have git conflict markers to resolve: {', '.join(markers)}."
    tests = rec.conflict.tests if rec.conflict is not None else None
    if tests is not None:
        failed = ", ".join(tests.failed_tests) or tests.outcome
        note += f" The tests failed with your change on main: {failed}."
    return note


async def settle_move(
    ctx: ChangeContext, rec: ChangeRecord, move: Move, error: Exception | None
) -> None:
    """R3: a base move failed or was cancelled. Still under both locks, decide from the
    worktree's facts: abort an unfinished merge; clean at the old head is unchanged; clean at a
    merge of the old head and main's is a move that landed, recorded from those facts; anything
    else, git failing, or a second cancel cutting this recovery, is `broken`. Main was only
    read, so `main_suspect` stays as it was. The change ends `conflict` or `broken`, with no
    resolution sent; the second cancel is re-raised once the `error` is out."""
    tree = rec.tree
    cut = False
    try:
        if await ctx.yes_no(tree, "rev-parse", "-q", "--verify", "MERGE_HEAD"):
            await ctx.git(tree, "merge", "--abort")
        head = (await ctx.git(tree, "rev-parse", "HEAD")).strip()
        clean = not (await ctx.git(tree, "status", "--porcelain")).strip()
        parents = (await ctx.git(tree, "rev-list", "--parents", "-n", "1", head)).split()[1:]
        if clean and move.old_head in (None, head):  # None: the merge never ran
            note = "its worktree is unchanged; request changes again or reject"
        elif clean and move.main_head and parents == [move.old_head, move.main_head]:
            await moved(ctx, rec, move.main_head)
            note = "main was merged into its worktree; request changes again to revise"
        else:
            raise WorkspaceError("the worktree is neither unchanged nor merged")
        rec.state = "conflict"
    except (Exception, asyncio.CancelledError) as failure:
        # A second cancel may have cut the recovery before its abort ran, so the worktree can
        # still be mid-merge; `_restore` would put such a change back to `conflict`.
        cut = isinstance(failure, asyncio.CancelledError)
        rec.state = "broken"
        note = "its worktree's state is unknown; reject the change"
    what = "was cancelled" if error is None else f"failed: {error}"
    await ctx.error(
        "git", f"Requesting changes on {rec.change_id} {what}; {note}.", rec, fatal=False
    )
    if cut:
        raise asyncio.CancelledError


async def requested(
    ctx: ChangeContext, rec: ChangeRecord, comment: str, note: str | None
) -> Revision | None:
    """Phase B, one `_whole` step: the resolution and `working` go together whatever cancels
    come. R2: a resolution the store refused twice stays pending, and the revision starts."""
    assert rec.evidence is not None
    validate_resolution_intent(rec.evidence)
    evidence = rec.evidence
    if evidence.actor == "cross_review":
        assert rec.review_capture is not None
        try:
            cross_review.current(ctx, rec.review_capture, reserved=True)
        except WrongState as error:
            await ctx.error("server", str(error), rec, fatal=False)
            return None
    rec.requested = ChangeResolved(
        change_id=rec.change_id,
        agent_id=rec.agent_id,
        revision=rec.revision,
        decision="changes_requested",
        decided_by=evidence.actor,
        review_seq=evidence.review_seq,
        requested_by=evidence.requested_by,
        reasons=list(evidence.reasons),
        assessment=evidence.assessment,
        head_commit=evidence.head_commit,
        reason=None,
        comment=comment,
        merge=None,
        merge_commit=None,
        conflict=None,
    )
    validate_resolution_evidence(rec.requested, evidence)
    rec.requested_evidence = evidence
    if evidence.actor == "cross_review":
        rec.review_consumed = True
    rec.review_ready = False
    try:
        failure = await delivery.send_requested(ctx, rec)
    except EventRejected as error:
        await delivery.rejected(ctx, rec, error)
        return None
    if failure:
        ctx.state.pending[rec.agent_id] = rec
        await ctx.error(
            "server",
            f"The request for changes on {rec.change_id} was not sent ({failure}); "
            "it is sent before the next proposal.",
            rec,
            fatal=False,
        )
    rec.state, rec.proposed_now = "working", False
    rec.requests += 1
    return Revision(revision_comment(ctx, rec, comment), note)
