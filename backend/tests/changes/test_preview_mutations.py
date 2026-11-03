"""Person, agent, rules and standing-grant effects all refuse an unverified preview drain."""

import asyncio

import pytest
from changes_helpers import EDIT
from decision_helpers import observed_target
from test_grant_rules import granted as granted
from test_grant_rules import later
from test_rules_reservation import start
from test_rules_transaction import reserved as reserved
from workspace.preview_fakes import FakeProcess, profile

from oversight.changes import WrongState, _approval
from oversight.workspace import IntegrityError, PreviewError, PreviewManager
from oversight.workspace.preview_types import FakeStatusSink
from oversight_contracts.events import ChangeResolved

pytestmark = pytest.mark.anyio


async def attach(h, record=None):
    record = record or h.manager._agents["agent-1"]
    await h.workspace.check_isolation()
    h.manager._preview_run_id = "run-20261103-000000"
    processes = []

    def factory(registry, port):
        process = FakeProcess(registry, port)
        processes.append(process)
        return process

    previews = PreviewManager(
        h.workspace,
        profile(),
        resolve_candidate=h.manager.preview_candidate,
        process_factory=factory,
    )
    sink = FakeStatusSink()
    previews.set_status_sink(sink)
    created = await previews.start(
        run_id="run-20261103-000000",
        agent_id=record.agent_id,
        revision=record.revision,
        head_commit=record.head_commit,
    )
    await sink.wait("ready", created.preview_id)
    return previews, processes[0], sink, created


@pytest.mark.parametrize(
    "operation",
    ["approve", "reject", "stop", "revision", "user_edit", "agent_edit", "remove", "disable"],
)
async def test_each_mutation_refuses_before_first_effect(h, operation):
    await h.run_agent(1, EDIT)
    record = h.manager._agents["agent-1"]
    previews, process, sink, _created = await attach(h)
    process.stop_failure = True
    target = observed_target(h.manager, "change-1")
    text = (record.tree.root / "shop/mod.py").read_text()
    count = len(h.log)
    expected = WrongState if operation == "user_edit" else PreviewError
    with pytest.raises(expected, match="cleanup is unverified"):
        if operation == "approve":
            await h.manager.approve("change-1", None, **target)
        elif operation == "reject":
            await h.manager.reject("change-1", None, **target)
        elif operation == "stop":
            await h.manager.stop("agent-1", None)
        elif operation == "revision":
            await h.manager.request_changes("change-1", "Please revise.", **target)
        elif operation == "user_edit":
            read = await record.tree.read_text("shop/mod.py")
            await h.manager.save_for_review("agent-1", "shop/mod.py", text + "\n", read.hash)
        elif operation == "agent_edit":
            read = await record.tree.read_text("shop/mod.py")
            await record.tree.write_text("shop/mod.py", text + "\n", expected_hash=read.hash)
        elif operation == "remove":
            await h.workspace.discard_worktree("agent-1", delete_branch=True)
        else:
            await previews.disable_profile()
    assert not any(isinstance(e.payload, ChangeResolved) for e in h.log[count:])
    assert (record.tree.root / "shop/mod.py").read_text() == text
    assert not record.merged and not record.stopped
    assert h.workspace.integrity.main_suspect and previews.has_owner
    assert all(r.state != "stopped" for r in sink.records)
    assert previews._watcher is not None
    previews._watcher.cancel()
    await asyncio.gather(previews._watcher, return_exceptions=True)


async def test_stop_does_not_cancel_other_agent_before_verified_drain(h):
    await h.run_agent(1, EDIT)
    await h.open(2)
    previews, process, _sink, _created = await attach(h)
    process.stop_failure = True
    live = asyncio.create_task(asyncio.Event().wait())
    decision = h.manager.stop("agent-2", live)
    assert not live.cancelling()
    with pytest.raises(PreviewError):
        await decision
    assert not live.done() and not live.cancelling()
    live.cancel()
    assert previews._watcher is not None
    previews._watcher.cancel()
    await asyncio.gather(live, previews._watcher, return_exceptions=True)


@pytest.mark.parametrize("tamper", ["files", "metadata"])
async def test_contamination_blocks_person_before_automatic_reproposal_or_stage(h, tamper):
    await h.run_agent(1, EDIT)
    record = h.manager._agents["agent-1"]
    previews, _process, _sink, created = await attach(h)
    path = record.tree.root / "app-written.txt"
    path.write_text("preserve for review")
    if tamper == "metadata":
        config = h.workspace.main.git_identity.common / "config"
        config.write_bytes(config.read_bytes() + b"\n# preview tamper\n")
        with pytest.raises(IntegrityError):
            await previews.stop(created.preview_id, created.lease_token)
        assert previews.barrier.finalizer is not None
        await previews.barrier.finalizer
        assert h.workspace.integrity.rules_suspended and not h.workspace.integrity.main_suspect
    else:
        await previews.stop(created.preview_id, created.lease_token)
    calls = len(h.workspace.calls)
    with pytest.raises(PreviewError, match="new revision"):
        await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    assert not any(c.args[0] in {"add", "commit", "merge"} for c in h.workspace.calls[calls:])
    assert path.read_text() == "preserve for review" and not record.merged


def intercept_automatic(h, monkeypatch, *, contamination=False):
    original = _approval.approve_rules
    started = []

    async def supervise(ctx, reservation):
        record = reservation.wait.rec
        state = record.state
        record.state = "ready"
        previews, process, sink, _created = await attach(h, record)
        record.state = state
        started.append((previews, process, sink))
        if contamination == "metadata":
            config = h.workspace.main.git_identity.common / "config"
            config.write_bytes(config.read_bytes() + b"\n# preview tamper\n")
        elif contamination:
            (record.tree.root / "app-written.txt").write_text("preserve")
        else:
            process.stop_failure = True
        await original(ctx, reservation)
        if previews.barrier.finalizer is not None:
            await previews.barrier.finalizer
        if previews._watcher is not None:
            previews._watcher.cancel()
            await asyncio.gather(previews._watcher, return_exceptions=True)

    monkeypatch.setattr(_approval, "approve_rules", supervise)
    return started


@pytest.mark.parametrize("contamination", [False, True, "metadata"])
async def test_rules_authority_refuses_cleanup_failure_or_contamination(
    h,
    reserved,
    monkeypatch,
    contamination,
):
    started = intercept_automatic(h, monkeypatch, contamination=contamination)
    decision = await start(h, reserved)
    await decision
    assert len(started) == 1
    assert not h.payloads(ChangeResolved)
    assert not h.manager._agents["agent-1"].merged
    if contamination:
        assert started[0][2].records[-1].reason == "worktree_changed"
    else:
        assert h.workspace.integrity.main_suspect


@pytest.mark.parametrize("contamination", [False, True, "metadata"])
async def test_standing_grant_authority_uses_same_barriers(h, granted, monkeypatch, contamination):
    started = intercept_automatic(h, monkeypatch, contamination=contamination)
    decisions = await later(h, monkeypatch)
    assert len(decisions) == 1
    await decisions[0]
    assert len(started) == 1
    assert len(h.payloads(ChangeResolved)) == 1  # Only the earlier person grant creation merged.
    assert not h.manager._agents["agent-2"].merged
