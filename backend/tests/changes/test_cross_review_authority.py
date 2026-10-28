"""The automatic server actor is driven directly: no scheduler, provider or API credits."""

import asyncio
from copy import copy

import pytest
from changes_helpers import EDIT, PATH, Harness, queued_callbacks_ran
from decision_helpers import observed_target

from oversight.agents import (
    FAKE_MODEL,
    FAKE_MODEL_B,
    ModelReply,
    ReviewInputs,
    ReviewOutcome,
    ReviewTests,
    Script,
    ScriptedModel,
    review_candidate,
)
from oversight.changes import ReviewCapture, WrongState
from oversight.events import parse_needs_you
from oversight_contracts.events import ChangeResolved, ErrorReport

pytestmark = pytest.mark.anyio


async def adverse(h: Harness, capture: ReviewCapture) -> tuple[ReviewOutcome, int]:
    client = ScriptedModel(
        Script(
            "review",
            [
                ModelReply(
                    f"SHOULD-FIX {PATH}:2: Return the expected value.\nVERDICT: request changes",
                    [],
                    model=FAKE_MODEL_B.model,
                )
            ],
        ),
        FAKE_MODEL_B,
    )
    author = FAKE_MODEL
    outcome = await review_candidate(
        client,
        capture.target,
        ReviewInputs(
            reviewer_model=client.profile.model,
            summary="Change value",
            diff="+ return 3",
            files=(PATH,),
            tests=ReviewTests("passed", 1, 0, 0),
            cone=(),
        ),
        author=author,
    )
    assert outcome.decision.action == "request_changes"
    # The shared harness does not normally need fake advisory reviews.
    h.store.allow_fake_provider = True
    seq = (await h.publish(outcome.review)).seq
    return outcome, seq


async def test_automatic_first_blocks_every_human_decision(h: Harness) -> None:
    await h.run_agent(1, EDIT)
    capture = await h.manager.capture_review("change-1")
    outcome, seq = await adverse(h, capture)
    rec = h.manager._agents["agent-1"]
    # No await between reservation and human admission attempts.
    automatic = h.manager.request_cross_review(capture, outcome, review_seq=seq)
    assert rec.state == "deciding" and rec.review_reserved
    for decide in (h.manager.approve, h.manager.reject, h.manager.request_changes):
        with pytest.raises(WrongState, match="deciding"):
            decide("change-1", "Mine", **observed_target(h.manager, "change-1"))
    read = await rec.tree.read_text(PATH)
    with pytest.raises(WrongState, match="deciding"):
        await h.manager.save_for_review("agent-1", PATH, read.content, read.hash)
    assert not rec.person_acted
    assert await automatic is not None
    assert not rec.person_acted and rec.review_consumed and not rec.review_reserved
    [resolved] = h.payloads(ChangeResolved)
    assert (
        resolved.decided_by,
        resolved.review_seq,
        resolved.comment,
        tuple(resolved.reasons),
    ) == (
        "cross_review",
        seq,
        outcome.decision.comment,
        outcome.decision.reasons,
    )
    assert rec.base_commit == capture.target.base_commit


@pytest.mark.parametrize("verb", ["approve", "reject", "request_changes", "stop"])
async def test_person_first_cancels_review_before_await(h: Harness, verb: str) -> None:
    await h.run_agent(1, EDIT)
    capture = await h.manager.capture_review("change-1")
    outcome, seq = await adverse(h, capture)
    review = asyncio.create_task(asyncio.Event().wait())
    h.manager.track_review(capture, review)
    if verb == "stop":
        decision = h.manager.stop("agent-1", None)
    else:
        decision = getattr(h.manager, verb)(
            "change-1", "Mine", **observed_target(h.manager, "change-1")
        )
    assert h.manager._agents["agent-1"].person_acted and review.cancelling()
    with pytest.raises(WrongState):
        h.manager.request_cross_review(capture, outcome, review_seq=seq)
    await decision
    await asyncio.gather(review, return_exceptions=True)
    if verb == "request_changes":
        assert await h.edit(1, PATH, "return 3", "return 4")
        assert await h.finish(1)
        assert not h.manager.review_current(capture)
        with pytest.raises(WrongState):
            await h.manager.capture_review("change-1")


@pytest.mark.parametrize("fail", [False, True])
async def test_admitted_save_disarms_even_identical_or_failed(h: Harness, fail: bool) -> None:
    tree = await h.run_agent(1, EDIT)
    capture = await h.manager.capture_review("change-1")
    review = asyncio.create_task(asyncio.Event().wait())
    h.manager.track_review(capture, review)
    read = await tree.read_text(PATH)
    before = list(h.log)
    if fail:
        from oversight.workspace import StaleFile

        with pytest.raises(StaleFile):
            await h.manager.save_for_review("agent-1", PATH, read.content, "stale")
    else:
        assert (
            await h.manager.save_for_review("agent-1", PATH, read.content, read.hash) == read.hash
        )
    assert h.manager._agents["agent-1"].person_acted and review.cancelling()
    assert not h.manager.review_current(capture)
    assert await tree.read_text(PATH) == read
    assert h.log == before
    await asyncio.gather(review, return_exceptions=True)


async def test_refused_decision_does_not_mark_person(h: Harness) -> None:
    await h.run_agent(1, EDIT)
    with pytest.raises(WrongState):
        h.manager.approve("change-1", None, revision=99, head_commit="old")
    assert not h.manager._agents["agent-1"].person_acted
    await h.manager.capture_review("change-1")


@pytest.mark.parametrize("kind", ["revision", "worktree", "human", "closing", "fallback", "run"])
async def test_stale_capture_queued_before_preflight(h: Harness, kind: str) -> None:
    await h.run_agent(1, EDIT)
    capture = await h.manager.capture_review("change-1")
    outcome, seq = await adverse(h, capture)
    rec = h.manager._agents["agent-1"]
    # Block the automatic decision at main, mutate eligibility, then release it.
    async with h.workspace.locks(main=True):
        decision = h.manager.request_cross_review(capture, outcome, review_seq=seq)
        await queued_callbacks_ran()
        if kind == "revision":
            rec.revision += 1
        elif kind == "worktree":
            rec.tree = copy(rec.tree)
        elif kind == "human":
            rec.person_intervened()
        elif kind == "closing":
            h.manager._ctx.state.closing = True
        elif kind == "fallback":
            rec.fallback = "conflict"
        else:
            rec.review_ready = False
    assert await decision is None
    assert not rec.review_consumed and not rec.review_reserved
    assert not h.payloads(ChangeResolved)


async def test_dirty_tree_preflight_releases_once_and_never_retries(h: Harness) -> None:
    tree = await h.run_agent(1, EDIT)
    capture = await h.manager.capture_review("change-1")
    outcome, seq = await adverse(h, capture)
    read = await tree.read_text(PATH)
    await tree.write_text(PATH, read.content + "# unproposed\n", expected_hash=read.hash)
    assert await h.manager.request_cross_review(capture, outcome, review_seq=seq) is None
    rec = h.manager._agents["agent-1"]
    assert rec.state == "ready" and not rec.review_reserved and not rec.review_consumed
    before = len(h.workspace.calls)
    with pytest.raises(WrongState, match="already attempted"):
        h.manager.request_cross_review(capture, outcome, review_seq=seq)
    assert len(h.workspace.calls) == before and not h.payloads(ChangeResolved)


async def test_pending_resolution_consumes_round_before_delivery(h: Harness) -> None:
    await h.run_agent(1, EDIT)
    capture = await h.manager.capture_review("change-1")
    outcome, seq = await adverse(h, capture)
    h.fail_once, h.fail_times = ChangeResolved, 2
    assert await h.manager.request_cross_review(capture, outcome, review_seq=seq) is not None
    rec = h.manager._agents["agent-1"]
    assert rec.review_consumed and rec.requested is not None
    assert rec.requested.decided_by == "cross_review"
    assert not rec.person_acted and not h.payloads(ChangeResolved)
    assert await h.edit(1, PATH, "return 3", "return 4")
    assert await h.finish(1)
    [resolution] = h.payloads(ChangeResolved)
    assert resolution.review_seq == seq and resolution.reasons == list(outcome.decision.reasons)
    capture2 = await h.manager.capture_review("change-1")
    outcome2, seq2 = await adverse(h, capture2)
    assert await h.manager.request_cross_review(capture2, outcome2, review_seq=seq2) is None
    assert rec.state == "ready" and not rec.person_acted
    note = parse_needs_you(h.payloads(ErrorReport)[-1].message)
    assert note and (note.code, note.revision, note.head) == (
        "round_exhausted",
        2,
        capture2.target.head_commit,
    )
    assert len(h.payloads(ChangeResolved)) == 1


async def test_main_moved_alone_keeps_review_and_request_base(h: Harness) -> None:
    await h.open(1)
    await h.open(2)
    assert await h.edit(1, PATH, "return 1", "return 3")
    assert await h.finish(1)
    assert await h.edit(2, PATH, "return 1", "return 4")
    assert await h.finish(2)
    capture = await h.manager.capture_review("change-2")
    outcome, seq = await adverse(h, capture)
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    assert await h.main_head() != capture.target.main_commit
    assert h.manager.review_current(capture)
    assert await h.manager.request_cross_review(capture, outcome, review_seq=seq) is not None
    rec = h.manager._agents["agent-2"]
    assert rec.base_commit == capture.target.base_commit and not rec.person_acted


@pytest.mark.parametrize("conflict", [False, True])
async def test_approval_fallback_keeps_person_authority(h: Harness, conflict: bool) -> None:
    from changes_helpers import TEMPLATE
    from test_request_changes import commit_on_main, resolve_markers

    await h.run_agent(1, EDIT)
    capture = await h.manager.capture_review("change-1")
    if conflict:
        await commit_on_main(h, {PATH: TEMPLATE[PATH].replace("return 1", "return 5")})
    else:
        h.workspace.arm("rev-parse")  # approval fails before touching main; fallback is ready
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    rec = h.manager._agents["agent-1"]
    assert rec.state == ("conflict" if conflict else "ready") and rec.person_acted
    assert not h.manager.review_current(capture)
    if conflict:
        await h.manager.request_changes(
            "change-1", "Resolve", **observed_target(h.manager, "change-1")
        )
        await resolve_markers(h, "    return 3\n")
        await h.finish(1)
        assert rec.state == "ready" and rec.person_acted
        with pytest.raises(WrongState):
            await h.manager.capture_review("change-1")


async def test_save_marks_and_cancels_before_waiting_for_main(h: Harness) -> None:
    tree = await h.run_agent(1, EDIT)
    capture = await h.manager.capture_review("change-1")
    outcome, seq = await adverse(h, capture)
    read = await tree.read_text(PATH)
    review = asyncio.create_task(asyncio.Event().wait())
    h.manager.track_review(capture, review)
    async with h.workspace.locks(main=True):
        save = asyncio.create_task(
            h.manager.save_for_review("agent-1", PATH, read.content, read.hash)
        )
        await queued_callbacks_ran()
        assert not save.done()
        assert h.manager._agents["agent-1"].person_acted and review.cancelling()
        with pytest.raises(WrongState):
            h.manager.request_cross_review(capture, outcome, review_seq=seq)
    assert await save == read.hash
    await asyncio.gather(review, return_exceptions=True)
