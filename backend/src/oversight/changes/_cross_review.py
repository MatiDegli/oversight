"""Server authority for a captured review; no model calls, timers or runtime scheduling."""

import asyncio

from oversight.agents import ReviewOutcome
from oversight.changes._evidence import WriteEvidence
from oversight.changes._resolution_validation import validate_resolution_intent
from oversight.changes._review_port import ReviewCapture
from oversight.changes._state import ChangeRecord, WrongState
from oversight.changes._support import ChangeContext
from oversight.events import format_needs_you
from oversight_contracts.review import ChangeTarget


class RoundExhausted(WrongState):
    """The per-change automatic revision has already been consumed."""


def current(
    ctx: ChangeContext,
    capture: ReviewCapture,
    *,
    reserved: bool = False,
    require_finished: bool = True,
) -> ChangeRecord:
    target = capture.target
    rec = ctx.state.changes.get(target.change_id)
    state = (
        rec.fallback if rec and reserved and rec.state == "deciding" else rec.state if rec else None
    )
    if (
        rec is None
        or ctx.state.closing
        or rec.person_acted
        or (require_finished and not rec.review_ready)
        or rec.delivery_rejected
        or state != "ready"
        or (reserved and (rec.state != "deciding" or not rec.review_reserved))
        or ctx.state.agents.get(rec.agent_id) is not rec
        or rec.tree is not capture.tree
        or ctx.workspace.worktree(rec.agent_id) is not capture.tree
        or (rec.revision, rec.head_commit, rec.base_commit)
        != (target.revision, target.head_commit, target.base_commit)
    ):
        raise WrongState("Cross-review target is no longer ready or eligible")
    return rec


async def capture(ctx: ChangeContext, change_id: str) -> ReviewCapture:
    rec = ctx.state.changes.get(change_id)
    if rec is None or rec.head_commit is None:
        raise WrongState("Cross-review requires a proposed change")
    target = ChangeTarget(
        change_id=change_id,
        revision=rec.revision,
        head_commit=rec.head_commit,
        base_commit=rec.base_commit,
        main_commit=ctx.state.snapshot.version,
        candidate_tree="pending",
    )
    captured = ReviewCapture(target, rec.tree)
    current(ctx, captured)
    async with ctx.workspace.locks(main=True, agent_id=rec.agent_id):
        current(ctx, captured)
        tree = (await ctx.git(rec.tree, "rev-parse", "HEAD^{tree}")).strip()
        main = (await ctx.git(ctx.workspace.main, "rev-parse", "HEAD")).strip()
        captured = ReviewCapture(
            target.model_copy(update={"candidate_tree": tree, "main_commit": main}), rec.tree
        )
        await verify_tree(ctx, captured)
        current(ctx, captured)
        return captured


async def verify_tree(ctx: ChangeContext, capture: ReviewCapture) -> None:
    head = (await ctx.git(capture.tree, "rev-parse", "HEAD")).strip()
    tree = (await ctx.git(capture.tree, "rev-parse", "HEAD^{tree}")).strip()
    dirty = (await ctx.git(capture.tree, "status", "--porcelain")).strip()
    if head != capture.target.head_commit or tree != capture.target.candidate_tree or dirty:
        raise WrongState("Cross-review worktree differs from the captured tree")


def prepare(
    ctx: ChangeContext, capture: ReviewCapture, outcome: ReviewOutcome, review_seq: int
) -> ChangeRecord:
    """All admission checks, without await. The manager immediately reserves `ready`."""
    rec = current(ctx, capture)
    review = outcome.review
    if (
        review_seq < 1
        or ChangeTarget.model_validate(review.model_dump(include=set(ChangeTarget.model_fields)))
        != capture.target
        or review.verdict != "changes_requested"
        or not review.complete
        or outcome.decision.action != "request_changes"
    ):
        raise WrongState("Cross-review requires a confirmed adverse review of this target")
    if review_seq in rec.review_attempts or rec.review_reserved:
        raise WrongState("This cross-review was already attempted")
    if rec.review_consumed:
        raise RoundExhausted("The automatic cross-review round was already used")
    evidence = WriteEvidence(
        rec.change_id,
        rec.agent_id,
        rec.revision,
        rec.base_commit,
        review.head_commit,
        "cross_review",
        "changes_requested",
        outcome.decision.comment,
        review_seq=review_seq,
        reasons=outcome.decision.reasons,
    )
    validate_resolution_intent(evidence)
    rec.evidence = evidence
    rec.review_capture = capture
    rec.review_reserved = True
    rec.review_attempts.add(review_seq)
    return rec


async def preflight(ctx: ChangeContext, rec: ChangeRecord) -> bool:
    """Called under main then worktree locks; never moves the base or repairs a target."""
    assert rec.review_capture is not None
    try:
        current(ctx, rec.review_capture, reserved=True)
        if rec.review_consumed:
            raise WrongState("The automatic cross-review round was already used")
        await verify_tree(ctx, rec.review_capture)
        current(ctx, rec.review_capture, reserved=True)
    except Exception as error:
        await ctx.error("server", str(error), rec, fatal=False)
        return False
    return True


async def exhausted(ctx: ChangeContext, capture: ReviewCapture) -> None:
    try:
        rec = current(ctx, capture)
    except WrongState:
        return
    await ctx.error(
        "server",
        format_needs_you(
            rec.revision,
            capture.target.head_commit,
            "round_exhausted",
            "The automatic cross-review round was already used.",
        ),
        rec,
        fatal=False,
    )


def release(rec: ChangeRecord, _: asyncio.Task[object]) -> None:
    """Consumption survives delivery failure; an unsuccessful preflight only frees the slot."""
    rec.review_reserved = False
