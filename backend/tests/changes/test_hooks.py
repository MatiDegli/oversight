"""The hooks the API needs: `main_suspect` once a change is broken, `check_main` on main's state,
and `aclose` draining the close-outs before the store closes."""

import asyncio
import logging

import pytest
from changes_helpers import (
    ADD_THRICE,
    GIT_TIMEOUT_S,
    Harness,
    Mode,
    bounded,
    cancel_twice,
    queued_callbacks_ran,
)
from pydantic import BaseModel

from oversight.changes import WrongState
from oversight_contracts.events import AgentStatus, ChangeResolved, ErrorReport
from oversight_contracts.graph import GraphDelta

pytestmark = pytest.mark.anyio

EDIT = ("shop/mod.py", "return 1", "return 3")


# --- main_suspect ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("faults", "suspect"),
    [
        ([("merge", "timeout")], False),
        ([("merge", "timeout"), ("status", "fail")], True),
        ([("merge", "after"), ("merge", "fail")], True),
        ([("merge", "cancel"), ("merge", "cancel")], True),
    ],
    ids=["main-unchanged", "facts-unreadable", "abort-failed", "cancelled-in-recovery"],
)
@bounded
async def test_broken_sets_main_suspect(
    h: Harness, faults: list[tuple[str, Mode]], suspect: bool
) -> None:
    """A failed approval that leaves main at `pre` is `ready`, with main trusted. One whose facts
    cannot be read, whose `merge --abort` fails, or which is cancelled again while it recovers, so
    the done-callback settles it, is `broken`: main stays suspect, even after Reject closes it."""
    await h.run_agent(1, EDIT)
    for subcommand, mode in faults:
        h.workspace.arm(subcommand, mode=mode)
    assert not h.manager.main_suspect

    approval = h.manager.approve("change-1", None)
    if "cancel" in faults[0]:
        with pytest.raises(asyncio.CancelledError):
            await approval
    else:
        await approval

    assert all(fault.left == 0 for fault in h.workspace.faults)
    assert h.manager.main_suspect is suspect
    if not suspect:
        await h.manager.approve("change-1", None)  # `ready` again
        assert not h.manager.main_suspect
        return
    with pytest.raises(WrongState):
        h.manager.approve("change-1", None)
    await h.manager.reject("change-1", None)
    assert [r.decision for r in h.payloads(ChangeResolved)] == ["rejected"]
    assert h.manager.main_suspect  # until the process restarts


# --- check_main -----------------------------------------------------------------------------


async def merge_without_changes(h: Harness) -> None:
    """Leave main mid-merge with a clean status: `merge --no-commit` of an empty commit."""
    tree = await h.open(1)
    async with h.workspace.locks(agent_id="agent-1"):
        await h.workspace.git_ok(tree, "commit", "-q", "--allow-empty", "-m", "Nothing")
    assert (await h.main_git("merge", "--no-ff", "--no-commit", "agent-1")).returncode == 0
    assert (await h.main_git("rev-parse", "-q", "--verify", "MERGE_HEAD")).returncode == 0
    assert (await h.main_git("status", "--porcelain")).stdout == ""


async def edit_main(h: Harness) -> None:
    """Leave an uncommitted edit on main."""
    main = h.workspace.main
    async with h.workspace.locks(main=True):
        read = await main.read_text("shop/mod.py")
        await main.write_text("shop/mod.py", read.content + "\n", expected_hash=read.hash)


@pytest.mark.parametrize(
    ("state", "expected"), [("clean", True), ("merging", False), ("dirty", False)]
)
@bounded
async def test_check_main(h: Harness, state: str, expected: bool) -> None:
    if state == "merging":
        await merge_without_changes(h)
    elif state == "dirty":
        await edit_main(h)
    first = len(h.workspace.calls)

    assert await h.manager.check_main() is expected

    calls = h.workspace.calls[first:]
    assert [c.args[0] for c in calls] == ["rev-parse", "status"]
    assert all(c.held == ("main",) and c.timeout_s == GIT_TIMEOUT_S for c in calls)


@pytest.mark.parametrize(
    ("subcommand", "mode"), [("rev-parse", "timeout"), ("status", "fail"), ("status", "timeout")]
)
@bounded
async def test_check_main_is_unknown_when_git_fails(
    h: Harness, subcommand: str, mode: Mode
) -> None:
    h.workspace.arm(subcommand, mode=mode)

    assert await h.manager.check_main() is None

    assert h.workspace.faults[0].left == 0


# --- aclose ---------------------------------------------------------------------------------


@bounded
async def test_aclose_sends_a_failed_delta_in_order_once(h: Harness) -> None:
    """Both tries of the delta fail in the approval: the drain sends it, chained from the graph the
    UI holds, then `done`, and nothing twice."""
    await h.run_agent(1, ADD_THRICE)
    h.fail_once, h.fail_times = GraphDelta, 2
    await h.manager.approve("change-1", None)
    assert h.types()[-2:] == ["change.resolved", "error"]

    await h.manager.aclose()

    # `types` checks each delta's `from_version`.
    assert h.types()[-4:] == ["change.resolved", "error", "graph.delta", "agent.status"]
    assert h.types().count("change.resolved") == 1
    [delta] = h.payloads(GraphDelta)
    assert h.manager.snapshot.version == delta.to_version
    assert len(h.payloads(ErrorReport)) == 1


@bounded
async def test_aclose_gives_each_event_two_tries(h: Harness) -> None:
    """The resolution fails twice in the approval, so nothing goes out. In the drain it fails once
    and then the delta fails once: each event gets two tries, so everything is sent, in order,
    with no second `error`."""
    await h.run_agent(1, ADD_THRICE)
    h.fail_once, h.fail_times = ChangeResolved, 2
    await h.manager.approve("change-1", None)
    assert h.types()[-1] == "error"
    assert not {"change.resolved", "graph.delta", "agent.status"} & set(h.types())
    h.fail_script = [ChangeResolved, GraphDelta]

    await h.manager.aclose()

    assert h.fail_script == []
    # `types` checks each delta's `from_version`.
    assert h.types()[-4:] == ["error", "change.resolved", "graph.delta", "agent.status"]
    assert len(h.payloads(ErrorReport)) == 1
    [delta] = h.payloads(GraphDelta)
    assert h.manager.snapshot.version == delta.to_version


@bounded
async def test_aclose_waits_for_a_publish_left_running(h: Harness) -> None:
    """Cancelled twice while the store's receipt for the delta is on its way: the drain waits for
    that receipt, so it sends only `done`, never the delta the store took."""
    await h.run_agent(1, ADD_THRICE)
    h.hold = GraphDelta
    approval = h.manager.approve("change-1", None)
    await h.held.wait()  # committed, its receipt not yet back
    await cancel_twice(approval)

    closing = asyncio.get_running_loop().create_task(h.manager.aclose())
    await queued_callbacks_ran()
    assert not closing.done()
    h.release.set()
    await closing

    assert h.types()[-3:] == ["change.resolved", "graph.delta", "agent.status"]
    [delta] = h.payloads(GraphDelta)
    assert h.manager.snapshot.version == delta.to_version
    assert h.payloads(ErrorReport) == []


@bounded
async def test_aclose_reports_what_a_failing_store_never_took(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """The store refuses the delta in the approval, which is still in flight when the drain starts,
    and in the drain's two tries: one `error` names each event still unsent."""
    await h.run_agent(1, ADD_THRICE)
    h.fail_once, h.fail_times = GraphDelta, 4
    approval = h.manager.approve("change-1", None)

    with caplog.at_level(logging.DEBUG, logger="oversight.changes"):
        await h.manager.aclose()

    assert caplog.records == []  # the `error` went out, so nothing is left to log
    assert approval.done()
    assert "graph.delta" not in h.types()
    assert h.payloads(AgentStatus) == []
    incomplete, shutdown = h.payloads(ErrorReport)
    assert "next decision" in incomplete.message
    assert (shutdown.change_id, shutdown.agent_id, shutdown.source, shutdown.fatal) == (
        "change-1",
        "agent-1",
        "server",
        False,
    )
    assert "graph.delta, agent.status done never sent" in shutdown.message
    assert "change.resolved" not in shutdown.message
    assert h.types()[-1] == "error"


@bounded
async def test_aclose_logs_what_a_refusing_store_never_took(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    """The store refuses everything, the final `error` included: one log line per change names the
    events it never took, by name and ids only, with no comment and no store message."""
    await h.run_agent(1, EDIT)
    await h.run_agent(2, ("shop/mod.py", "return 1", "return 4"))
    h.fail_once, h.fail_times = BaseModel, 1_000  # every payload is a BaseModel
    await h.manager.reject("change-1", "Not this.")
    await h.manager.reject("change-2", "Nor this.")
    assert h.payloads(ErrorReport) == []

    with caplog.at_level(logging.DEBUG, logger="oversight.changes"):
        await h.manager.aclose()

    first, second = caplog.records
    assert (first.levelno, second.levelno) == (logging.ERROR, logging.ERROR)
    for record, n in ((first, 1), (second, 2)):
        line = record.getMessage()
        assert f"change-{n} (agent agent-{n})" in line
        assert line.endswith("change.resolved, agent.status done")
        assert "graph.delta" not in line
        assert "this." not in line  # the decision's comment
        assert "armed" not in line  # the store's message
    assert h.payloads(ErrorReport) == []
