"""Confirmation admission, durable state, cancellation, races and proposal carry."""

import asyncio
from dataclasses import replace
from typing import Any

import pytest
from changes_helpers import Harness, queued_callbacks_ran
from decision_helpers import observed_target

from oversight.agents import ExampleTarget, RunEnd
from oversight.changes import ChangeNotFound, WrongState
from oversight.examples_confirmation import PendingExamples, confirm_examples
from oversight_contracts.commands import ConfirmExamples
from oversight_contracts.decisions import ChangeDecisionRef
from oversight_contracts.events import AgentStatus, ChangeProposed
from oversight_contracts.examples import ExamplePin, TaskExample

pytestmark = pytest.mark.anyio
EXAMPLE = TaskExample(
    example_id="one",
    inputs='{"node_id":"shop.mod.value","args":[],"kwargs":{}}',
    expected_output="1",
)


def record(h: Harness) -> Any:
    return h.manager._ctx.state.agents["agent-1"]  # pyright: ignore[reportPrivateUsage]


async def prepare(h: Harness) -> tuple[PendingExamples, ConfirmExamples]:
    await h.open(1)
    h.manager.run_ended("agent-1", RunEnd("ready_for_review", None, 3, None))
    p = PendingExamples(
        "run-20261031-120000",
        "agent-1",
        "change-1",
        h.base_commit,
        7,
        "Do it.",
        (EXAMPLE,),
        {"one": "meaning"},
        (ExampleTarget("shop.mod.value", "function", False, True),),
        3,
    )
    c = ConfirmExamples(
        run_id=p.run_id,
        evidence_seq=7,
        base_commit=h.base_commit,
        action="confirm",
        examples=[EXAMPLE],
    )
    return p, c


async def test_examples_confirm(h: Harness) -> None:
    p, c = await prepare(h)
    # Any valid answering reference must survive the task replacement.
    answer = ChangeDecisionRef(
        kind="examples_before_code",
        change_id="change-1",
        revision=0,
        head_commit=h.base_commit,
        evidence_seq=1,
    )
    rec = record(h)
    rec.task = replace(rec.task, answering=answer)
    original = rec.task
    outcome = await confirm_examples(h.manager, "agent-1", p, c)
    assert outcome.task is rec.task and outcome.task is not original
    assert outcome.task.answering == answer
    assert (outcome.task.task, outcome.task.prior_steps, rec.state) == ("Do it.", 3, "working")
    assert not outcome.repeated and not rec.review_ready and not rec.proposed_now
    assert not rec.person_acted
    [status] = h.payloads(AgentStatus)
    assert (status.state, status.step, status.max_steps) == ("starting", 3, 23)
    assert status.examples == list(outcome.task.examples)
    assert status.answering == answer
    assert h.payloads(ChangeProposed) == []


async def test_examples_confirm_is_idempotent(h: Harness) -> None:
    p, c = await prepare(h)
    first = await confirm_examples(h.manager, "agent-1", p, c)
    for state in ("working", "ready", "closed"):
        record(h).state = state
        again = await confirm_examples(h.manager, "agent-1", p, c)
        assert again.repeated and again.task is first.task
    assert len(h.payloads(AgentStatus)) == 1
    c = c.model_copy(update={"action": "edit"})
    with pytest.raises(WrongState):
        await confirm_examples(h.manager, "agent-1", p, c)


@pytest.mark.parametrize(
    "guard",
    [
        "working",
        "ready",
        "deciding",
        "closing",
        "closed",
        "conflict",
        "broken",
        "delivery_failed",
        "halted",
        "revision",
        "head",
        "examples",
        "base",
        "suspect",
        "manager_closing",
        "unknown",
        "change_id",
    ],
)
async def test_examples_confirm_wrong_state(h: Harness, guard: str) -> None:
    p, c = await prepare(h)
    rec = record(h)
    ctx = h.manager._ctx  # pyright: ignore[reportPrivateUsage]
    if guard in {
        "working",
        "ready",
        "deciding",
        "closing",
        "closed",
        "conflict",
        "broken",
        "delivery_failed",
    }:
        rec.state = guard
    elif guard == "halted":
        rec.review_ready = False
    elif guard == "revision":
        rec.revision = 1
    elif guard == "head":
        rec.head_commit = h.base_commit
    elif guard == "examples":
        rec.task = replace(
            rec.task,
            examples=(EXAMPLE.model_copy(update={"pin": ExamplePin(source_task="Do it.")}),),
        )
    elif guard == "base":
        rec.base_commit = "changed"
    elif guard == "suspect":
        ctx.state.suspect = True
    elif guard == "manager_closing":
        ctx.state.closing = True
    elif guard == "change_id":
        p = replace(p, change_id="change-old")
    else:
        del ctx.state.agents["agent-1"]
    with pytest.raises(ChangeNotFound if guard == "unknown" else WrongState):
        await confirm_examples(h.manager, "agent-1", p, c)
    assert not h.payloads(AgentStatus)


@pytest.mark.parametrize(
    "decision,first", [(d, f) for d in ("reject", "stop") for f in (True, False)]
)
async def test_examples_confirm_races_reject_and_stop(
    h: Harness, decision: str, first: bool
) -> None:
    p, c = await prepare(h)

    def discard() -> asyncio.Task[None]:
        if decision == "stop":
            return h.manager.stop("agent-1", None)
        return h.manager.reject("change-1", None, **observed_target(h.manager, "change-1"))

    if first:
        h.gate = AgentStatus
        confirming = asyncio.create_task(confirm_examples(h.manager, "agent-1", p, c))
        await h.at_gate.wait()
        with pytest.raises(WrongState):
            discard()
        with pytest.raises(WrongState):
            await confirm_examples(h.manager, "agent-1", p, c)
        h.open_gate.set()
        assert not (await confirming).repeated
    else:
        discarding = discard()  # synchronous reservation wins before the next await
        with pytest.raises(WrongState):
            await confirm_examples(h.manager, "agent-1", p, c)
        await discarding
    assert len([s for s in h.payloads(AgentStatus) if s.state == "starting"]) == int(first)


@pytest.mark.parametrize("cancel", [False, True])
async def test_examples_confirm_persistence_failure(h: Harness, cancel: bool) -> None:
    p, c = await prepare(h)
    original = record(h).task
    if not cancel:
        h.fail_once = AgentStatus
        with pytest.raises(RuntimeError, match="publish failure"):
            await confirm_examples(h.manager, "agent-1", p, c)
        rec = record(h)
        assert rec.state == "unproposed" and rec.review_ready and rec.task is original
        assert rec.examples_digest is None
        assert not h.payloads(AgentStatus)
        assert not (await confirm_examples(h.manager, "agent-1", p, c)).repeated
    else:
        h.gate = AgentStatus
        confirming = asyncio.create_task(confirm_examples(h.manager, "agent-1", p, c))
        await h.at_gate.wait()
        confirming.cancel()
        await queued_callbacks_ran()
        assert not confirming.done()
        assert record(h).task is original
        h.open_gate.set()
        with pytest.raises(asyncio.CancelledError):
            await confirming
        assert record(h).state == "working"
        assert (await confirm_examples(h.manager, "agent-1", p, c)).repeated
    assert len(h.payloads(AgentStatus)) == 1


@pytest.mark.parametrize("stop", [False, True])
async def test_examples_carried_into_change_proposed(h: Harness, stop: bool) -> None:
    p, c = await prepare(h)
    pinned = (await confirm_examples(h.manager, "agent-1", p, c)).task.examples
    await h.edit(1, "shop/mod.py", "return 1", "return 2")
    assert await h.finish(1, steps=5)
    await h.manager.request_changes("change-1", "Again.", **observed_target(h.manager, "change-1"))
    assert await h.finish(1, steps=6)
    tree = h.workspace.worktree("agent-1")
    read = await tree.read_text("shop/mod.py")
    await h.manager.save_for_review(
        "agent-1", "shop/mod.py", read.content.replace("return 2", "return 3"), read.hash
    )
    proposals = h.payloads(ChangeProposed)
    assert len(proposals) == 3
    assert all(proposal.examples == list(pinned) for proposal in proposals)
    if stop:
        await h.manager.stop("agent-1", None)
    else:
        await h.manager.reject("change-1", None, **observed_target(h.manager, "change-1"))
    [done] = [s for s in h.payloads(AgentStatus) if s.state == ("stopped" if stop else "done")]
    assert done.examples == list(pinned) and done.max_steps == 43
