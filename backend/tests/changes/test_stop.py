"""Stop: the run is cancelled and waited for, then the change is discarded as Reject does, with
`agent.status` `stopped` before `change.resolved` (`rejected`, reason "stopped") and no `done`."""

import asyncio
from collections.abc import AsyncIterator, Coroutine
from typing import Any

import pytest
from changes_helpers import (
    TEMPLATE,
    Harness,
    bounded,
    cancel_twice,
    queued_callbacks_ran,
)
from decision_helpers import observed_target

from oversight.changes import ChangeNotFound, WrongState
from oversight_contracts.events import (
    AgentStatus,
    AgentStep,
    ChangeProposed,
    ChangeResolved,
    ErrorReport,
)

pytestmark = pytest.mark.anyio

EDIT = ("shop/mod.py", "return 1", "return 3")
STOPPED = "Stopped by the user."


_started: list[asyncio.Task[Any]] = []


def start[T](work: Coroutine[Any, Any, T]) -> asyncio.Task[T]:
    task = asyncio.get_running_loop().create_task(work)
    _started.append(task)
    return task


@pytest.fixture(autouse=True)
async def cancel_leftovers(h: Harness) -> AsyncIterator[None]:
    """After each test, before the harness closes: open the publish gates, cancel what the test
    started and wait for it, so a failing test leaves no run waiting for the model or a held
    publish, and no Stop waiting for that run."""
    yield
    h.open_gate.set()
    h.release.set()
    leftovers = [task for task in _started if not task.done()]
    _started.clear()
    for task in leftovers:
        task.cancel()
    if leftovers:
        async with asyncio.timeout(60):
            await asyncio.wait(leftovers)


async def fake_run(h: Harness, n: int, steps: int, *, propose: bool = False) -> None:
    """A run as the app drives one: it proposes if asked, then waits for the model until
    cancelled. The cancel sends the interrupted step, as the runtime does, before the run's end is
    reported with its steps."""
    try:
        if propose:
            await h.manager.proposed(f"agent-{n}", "Done.")
        await asyncio.Event().wait()  # the model's turn
    except asyncio.CancelledError:
        step = AgentStep(
            agent_id=f"agent-{n}",
            step=steps,
            tool="read_file",
            summary="read_file shop/mod.py",
            result="error",
            error="Stopped.",
            duration_ms=0,
            tests=None,
        )
        await h.publish(step)
        raise
    finally:
        h.manager.run_ended(f"agent-{n}", None, steps=steps)


async def commit_on_main(h: Harness, path: str, content: str) -> None:
    """Commit one file on main, as an earlier approval would."""
    async with h.workspace.locks(main=True):
        old = (await h.workspace.main.read_text(path)).hash
        await h.workspace.main.write_text(path, content, expected_hash=old)
        await h.workspace.git_ok(h.workspace.main, "commit", "-qam", "Edit on main")


def resolutions(h: Harness) -> list[tuple[str, str | None, int]]:
    return [(r.decision, r.reason, r.revision) for r in h.payloads(ChangeResolved)]


def errors(h: Harness, words: str) -> list[ErrorReport]:
    return [e for e in h.payloads(ErrorReport) if words in e.message]


async def assert_stopped(h: Harness, n: int, *, step: int, max_steps: int = 20) -> None:
    """agent n's change closed by a Stop: `stopped`, then the resolution, nothing after; no `done`
    and no delta; its worktree and branch gone."""
    agent = f"agent-{n}"
    assert h.types()[-2:] == ["agent.status", "change.resolved"]
    [status] = [s for s in h.payloads(AgentStatus) if s.agent_id == agent]
    assert (status.state, status.step, status.max_steps, status.detail) == (
        "stopped",
        step,
        max_steps,
        STOPPED,
    )
    resolved = h.payloads(ChangeResolved)[-1]
    assert (resolved.agent_id, resolved.decision, resolved.reason) == (agent, "rejected", "stopped")
    assert (resolved.comment, resolved.merge, resolved.merge_commit) == (None, None, None)
    assert "graph.delta" not in h.types()
    assert not h.worktree_dir(agent).exists() and not await h.has_branch(agent)
    with pytest.raises(WrongState, match="is closed"):
        h.manager.stop(agent, None)


@bounded
async def test_stop_a_running_agent(h: Harness) -> None:
    """Stop cancels the run in the same call and waits for its end, so `stopped` carries the run's
    steps and follows its interrupted step; main is untouched."""
    await h.open(1)
    assert await h.edit(1, *EDIT)
    run = start(fake_run(h, 1, steps=4))
    await queued_callbacks_ran()  # the run waits for the model
    pre = await h.main_head()

    stopping = h.manager.stop("agent-1", run)

    if not run.cancelling():
        run.cancel()  # so this failure does not leave the run, and the Stop, waiting forever
        pytest.fail("Stop did not cancel the run in the same call")
    await stopping
    assert run.cancelled()
    assert h.types()[-3:] == ["agent.step", "agent.status", "change.resolved"]
    await assert_stopped(h, 1, step=4)
    assert resolutions(h) == [("rejected", "stopped", 0)]
    assert await h.main_head() == pre


@pytest.mark.parametrize("state", ["ready", "run-ended", "unproposed", "conflict", "broken"])
@bounded
async def test_stop_idle(h: Harness, state: str) -> None:
    """From each state Reject takes, with no run or one that already ended: the last proposed
    revision (0 if none) is closed, and a broken change keeps main suspect."""
    revision, step = 1, 3
    if state == "unproposed":
        await h.open(1)
        h.manager.run_ended("agent-1", None, steps=2)
        revision, step = 0, 2
    else:
        await h.run_agent(1, EDIT)
    if state == "conflict":
        await commit_on_main(h, "shop/mod.py", TEMPLATE["shop/mod.py"].replace("1", "5"))
        await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    elif state == "broken":
        h.workspace.arm("merge", mode="timeout")
        h.workspace.arm("status", mode="fail")  # the recovery cannot read main
        await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
        assert h.manager.main_suspect
    run: asyncio.Task[None] | None = None
    if state == "run-ended":
        run = start(asyncio.sleep(0))
        await run

    await h.manager.stop("agent-1", run)

    await assert_stopped(h, 1, step=step)
    expected: list[tuple[str, str | None, int]] = [("rejected", "stopped", revision)]
    if state == "conflict":
        expected.insert(0, ("approved", None, 1))
    assert resolutions(h) == expected
    assert h.manager.main_suspect is (state == "broken")


@bounded
async def test_stop_mid_revision(h: Harness) -> None:
    """Stop during a revision run whose request for changes the store refused twice, and twice
    more before the Stop's close-out: the request goes out first, then `stopped`, then the
    resolution, which names revision 1. `max_steps` counts the revision."""
    await h.run_agent(1, EDIT)
    h.fail_once, h.fail_times = ChangeResolved, 4
    assert (
        await h.manager.request_changes(
            "change-1", "Make it 4.", **observed_target(h.manager, "change-1")
        )
        is not None
    )
    run = start(fake_run(h, 1, steps=6))
    await queued_callbacks_ran()

    await h.manager.stop("agent-1", run)

    assert h.fail_once is None  # every armed failure was used
    await assert_stopped(h, 1, step=6, max_steps=40)
    assert resolutions(h) == [("changes_requested", None, 1), ("rejected", "stopped", 1)]
    assert h.payloads(ChangeResolved)[0].comment == "Make it 4."
    # The run's interrupted step, then the Stop's close-out: the request first.
    assert h.types()[-4:] == ["agent.step", "change.resolved", "agent.status", "change.resolved"]


@bounded
async def test_stop_races(h: Harness) -> None:
    """Stop and the other decisions: the first taken wins and the second is refused at once.
    During Request changes' task Stop is refused; once `working`, Stop cancels the revision."""
    await h.run_agent(1, EDIT)
    await h.run_agent(2, ("shop/mod.py", "return 1", "return 4"))
    await h.run_agent(3, ("shop/mod.py", "return 1", "return 6"))
    with pytest.raises(ChangeNotFound):
        h.manager.stop("agent-9", None)

    stopping = h.manager.stop("agent-1", None)
    for decide in (h.manager.approve, h.manager.reject, h.manager.request_changes):
        with pytest.raises(WrongState, match="is deciding"):
            decide("change-1", "Too late.", **observed_target(h.manager, "change-1"))
    with pytest.raises(WrongState, match="is deciding"):
        h.manager.stop("agent-1", None)
    request = h.manager.request_changes(
        "change-2", "Make it 5.", **observed_target(h.manager, "change-2")
    )
    with pytest.raises(WrongState, match="is deciding"):
        h.manager.stop("agent-2", None)
    approval = h.manager.approve("change-3", None, **observed_target(h.manager, "change-3"))
    with pytest.raises(WrongState, match="is deciding"):
        h.manager.stop("agent-3", None)
    await stopping
    assert await request is not None
    await approval

    revision = start(fake_run(h, 2, steps=5))
    await queued_callbacks_ran()
    await h.manager.stop("agent-2", revision)

    assert revision.cancelled()
    by_change = [(r.change_id, r.decision, r.reason) for r in h.payloads(ChangeResolved)]
    assert [r for r in by_change if r[0] == "change-1"] == [("change-1", "rejected", "stopped")]
    assert [r for r in by_change if r[0] == "change-2"] == [
        ("change-2", "changes_requested", None),
        ("change-2", "rejected", "stopped"),
    ]
    assert [r for r in by_change if r[0] == "change-3"] == [("change-3", "approved", None)]
    with pytest.raises(WrongState, match="is closed"):
        h.manager.stop("agent-3", None)


@bounded
async def test_stop_during_a_save(h: Harness) -> None:
    """Stop is clicked while the save's revision 2 waits before the store: it waits for the save
    and closes revision 2."""
    await h.run_agent(1, EDIT)
    read = await h.workspace.worktree("agent-1").read_text("shop/mod.py")
    content = read.content.replace("return 3", "return 4")
    h.gate = ChangeProposed
    save = start(h.manager.save_for_review("agent-1", "shop/mod.py", content, read.hash))
    await h.at_gate.wait()
    stopping = h.manager.stop("agent-1", None)
    for _ in range(3):
        await queued_callbacks_ran()  # time enough for a Stop that did not wait to resolve
    h.open_gate.set()
    await save

    await stopping

    assert [p.revision for p in h.payloads(ChangeProposed)] == [1, 2]
    assert h.types().index("change.proposed", -4) < h.types().index("agent.status")
    await assert_stopped(h, 1, step=3)
    assert resolutions(h) == [("rejected", "stopped", 2)]


@bounded
async def test_stop_cancelled_while_it_waits_for_the_agents_lock(h: Harness) -> None:
    """The Stop is cancelled in its wait for the lock a save holds, before it sets anything for its
    close-out: the change is `ready`, and the Approve that follows merges it as any other, with
    `done` and a delta, never as a stop."""
    await h.run_agent(1, EDIT)
    read = await h.workspace.worktree("agent-1").read_text("shop/mod.py")
    h.gate = ChangeProposed
    save = start(
        h.manager.save_for_review(
            "agent-1", "shop/mod.py", read.content.replace("return 3", "return 4"), read.hash
        )
    )
    await h.at_gate.wait()
    stopping = h.manager.stop("agent-1", None)
    await queued_callbacks_ran()  # the Stop waits for the agent's lock

    stopping.cancel()

    with pytest.raises(asyncio.CancelledError):
        await stopping
    h.open_gate.set()
    await save
    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    assert h.types()[-3:] == ["change.resolved", "graph.delta", "agent.status"]
    assert [s.state for s in h.payloads(AgentStatus)] == ["done"]
    assert resolutions(h) == [("approved", None, 2)]


@bounded
async def test_stop_during_the_proposal_publish(h: Harness) -> None:
    """R7: the store took revision 1 and the run has not heard when Stop cancels it. The run
    finishes that step first, so the Stop closes revision 1, and `stopped` follows the proposal."""
    await h.open(1)
    assert await h.edit(1, *EDIT)
    h.hold = ChangeProposed
    run = start(fake_run(h, 1, steps=4, propose=True))
    await h.held.wait()  # committed, its receipt not yet back

    stopping = h.manager.stop("agent-1", run)
    await queued_callbacks_ran()
    assert not run.done()  # it waits for the publish's end
    h.release.set()
    await stopping

    assert run.cancelled()
    order = [t for t in h.types() if t != "change.updated"]
    assert order[-4:] == ["change.proposed", "agent.step", "agent.status", "change.resolved"]
    await assert_stopped(h, 1, step=4)
    assert resolutions(h) == [("rejected", "stopped", 1)]


@bounded
async def test_stop_cancelled_while_the_run_ends(h: Harness) -> None:
    """The Stop is cancelled while it waits for the run, which is still in its proposal's publish:
    nothing is resolved, the change stays `working` until the run's end makes it `ready`, and the
    next Stop closes revision 1."""
    await h.open(1)
    assert await h.edit(1, *EDIT)
    h.hold = ChangeProposed
    run = start(fake_run(h, 1, steps=4, propose=True))
    await h.held.wait()
    stopping = h.manager.stop("agent-1", run)
    await queued_callbacks_ran()

    stopping.cancel()

    with pytest.raises(asyncio.CancelledError):
        await stopping
    with pytest.raises(WrongState, match="is working"):
        h.manager.reject("change-1", None, **observed_target(h.manager, "change-1"))
    h.release.set()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert h.payloads(ChangeResolved) == []
    await h.manager.stop("agent-1", run)  # `ready` now, with its run over
    await assert_stopped(h, 1, step=4)
    assert resolutions(h) == [("rejected", "stopped", 1)]


async def assert_unproposed_then_rejected(h: Harness) -> None:
    """A Stop cancelled with no run left to report its end leaves the change as a run that ended
    without proposing does: `unproposed`, which Reject closes, never `working` with no run."""
    with pytest.raises(WrongState, match="is unproposed"):
        h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    await h.manager.reject("change-1", None, **observed_target(h.manager, "change-1"))
    assert resolutions(h) == [("rejected", None, 0)]
    assert h.types()[-2:] == ["change.resolved", "agent.status"]
    assert h.payloads(AgentStatus)[-1].state == "done"  # a Reject, not a Stop
    assert not h.worktree_dir("agent-1").exists() and not await h.has_branch("agent-1")


@pytest.mark.parametrize("passed", ["a run that never started", "no run"])
@bounded
async def test_stop_cancelled_before_its_body(h: Harness, passed: str) -> None:
    """The Stop task is cancelled before its first step, which runs none of its code, so it
    settles nothing itself. No run will report its end: the one passed was cancelled before its
    first step, or none was passed. The change must not stay `working` with no run."""
    await h.open(1)
    run = start(fake_run(h, 1, steps=0)) if passed == "a run that never started" else None
    stopping = h.manager.stop("agent-1", run)

    stopping.cancel()

    with pytest.raises(asyncio.CancelledError):
        await stopping
    assert run is None or run.cancelled()
    assert h.payloads(ChangeResolved) == []
    await assert_unproposed_then_rejected(h)


@bounded
async def test_stop_cancelled_while_it_waits_for_a_run_that_never_started(h: Harness) -> None:
    """The Stop is cancelled in its wait for the run, which was cancelled before its first step and
    so never reports its end: the wait's cancel must not leave the change `working` with no run."""
    await h.open(1)
    run = start(fake_run(h, 1, steps=0))  # created, never started
    stopping = h.manager.stop("agent-1", run)
    await queued_callbacks_ran()  # the Stop is in its wait, and the run is over

    stopping.cancel()

    with pytest.raises(asyncio.CancelledError):
        await stopping
    assert run.cancelled()
    assert h.payloads(ChangeResolved) == []
    await assert_unproposed_then_rejected(h)


@bounded
async def test_stop_right_after_request_changes(h: Harness) -> None:
    """The revision run is cancelled before its first step, so it never reports its end; the Stop
    settles the change itself. Cancelled in its retry of the unsent request for changes, it leaves
    the change `unproposed`, which Reject closes, never `working` with no run."""
    await h.run_agent(1, EDIT)
    h.fail_once, h.fail_times = ChangeResolved, 2
    assert (
        await h.manager.request_changes(
            "change-1", "Make it 4.", **observed_target(h.manager, "change-1")
        )
        is not None
    )
    run = start(fake_run(h, 1, steps=9))  # created, never started
    h.gate = ChangeResolved
    stopping = h.manager.stop("agent-1", run)
    await h.at_gate.wait()  # the Stop retries the request, past its wait for the run

    stopping.cancel()
    await queued_callbacks_ran()
    h.open_gate.set()
    with pytest.raises(asyncio.CancelledError):
        await stopping

    assert run.cancelled()
    with pytest.raises(WrongState, match="is unproposed"):
        h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    await h.manager.reject("change-1", None, **observed_target(h.manager, "change-1"))
    assert resolutions(h) == [("changes_requested", None, 1), ("rejected", None, 1)]
    done = h.payloads(AgentStatus)[-1]
    assert (done.state, done.step, done.max_steps) == ("done", 3, 40)


@bounded
async def test_stop_close_out_retried(h: Harness) -> None:
    """The store refuses `stopped` twice: the resolution waits behind it and the worktree stays;
    the next agent's start sends both, once each, in order, then cleans up."""
    await h.run_agent(1, EDIT)
    h.fail_once, h.fail_times = AgentStatus, 2

    await h.manager.stop("agent-1", None)

    assert h.payloads(ChangeResolved) == []
    assert errors(h, "The close-out of change-1 is incomplete")
    assert h.worktree_dir("agent-1").exists()
    await h.open(2)
    await assert_stopped(h, 1, step=3)
    assert resolutions(h) == [("rejected", "stopped", 1)]


@bounded
async def test_stop_cancelled_in_its_close_out(h: Harness) -> None:
    """Cancelled twice while the store's receipt for `stopped` is on its way: the next agent's
    start sends only the resolution, never `stopped` twice."""
    await h.run_agent(1, EDIT)
    h.hold = AgentStatus
    stopping = h.manager.stop("agent-1", None)
    await h.held.wait()  # committed, its receipt not yet back
    await cancel_twice(stopping)
    h.release.set()

    await h.open(2)

    await assert_stopped(h, 1, step=3)
    assert resolutions(h) == [("rejected", "stopped", 1)]


@bounded
async def test_stop_unsent_at_shutdown_is_named(h: Harness) -> None:
    await h.run_agent(1, EDIT)
    h.fail_once, h.fail_times = AgentStatus, 4
    await h.manager.stop("agent-1", None)

    await h.manager.aclose()

    [named] = errors(h, "Shutting down")
    assert "agent.status stopped, change.resolved never sent" in named.message
    assert h.payloads(AgentStatus) == [] and h.payloads(ChangeResolved) == []
