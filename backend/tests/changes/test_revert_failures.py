"""In-process inverse settlement: cancellation, ambiguous Git and delayed SQL receipts."""

import asyncio
import json
import os
import sys
from dataclasses import replace

import pytest
from test_revert import system as system
from test_revert_preflight import repo as repo
from test_rules_transaction import reserved as reserved

from oversight.changes import Person, RevertRefused, _revert
from oversight.events import EventRejected
from oversight.workspace import (
    InverseError,
    InverseOutcome,
    ProcessResult,
    apply_inverse,
    run_process,
)
from oversight_contracts.events import ErrorReport, RevertResult
from oversight_contracts.graph import GraphDelta

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "fault",
    [
        "cancel_before",
        "cancel_after",
        "timeout_before",
        "timeout_after",
        "inverse_error_after",
        "main_moved_c",
    ],
)
async def test_revert_cas_unknown_inspects_h_c(system, monkeypatch, fault):
    s = system
    git = s.ws.git
    calls = []

    async def injected(tree, *args, **kwargs):
        if args[0] != "update-ref" or calls:
            return await git(tree, *args, **kwargs)
        calls.append(args)
        if fault.endswith("after") or fault == "main_moved_c":
            result = await git(tree, *args, **kwargs)
        else:
            result = ProcessResult(1, "", "", False, False, 0)
        if fault.startswith("cancel"):
            current = asyncio.current_task()
            assert current is not None
            current.cancel()
            await asyncio.Event().wait()
        if fault == "inverse_error_after":
            raise InverseError("raised after CAS")
        return replace(result, returncode=None, timed_out=True)

    monkeypatch.setattr(s.ws, "git", injected)
    if fault == "main_moved_c":

        async def moved_c(ws, **kwargs):
            await apply_inverse(ws, **kwargs)
            return InverseOutcome("main_moved", kwargs["inverse_commit"])

        monkeypatch.setattr(_revert, "apply_inverse", moved_c)
    task = asyncio.create_task(s.run())
    if fault.startswith("cancel"):
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await task
    landed = fault.endswith("after") or fault == "main_moved_c"
    assert len(calls) == 1
    assert (await s.head() != s.command.main_commit) == landed
    results = [json.loads(e.envelope)["payload"] for e in s.events if e.type == "revert.resolved"]
    assert len(results) == 1
    assert results[0]["result"] == ("reverted" if landed else "needs_you")
    assert bool(s.service._consumed) == landed
    assert not s.manager.main_suspect
    if landed:
        assert s.manager.snapshot.commit == await s.head()
    await s.clean()


async def test_revert_cas_third_head_suspends(system, monkeypatch):
    s = system
    real = s.ws.git
    foreign = None
    calls = 0

    async def injected(tree, *args, **kwargs):
        nonlocal foreign, calls
        if args[0] != "update-ref":
            return await real(tree, *args, **kwargs)
        calls += 1
        await real(tree, *args, **kwargs)
        # Unexplained third ref, even with an identical tree, cannot establish H or C.
        foreign = (
            await s.ws.git_ok(
                tree, "commit-tree", args[2] + "^{tree}", "-p", args[2], "-m", "foreign"
            )
        ).strip()
        await real(tree, "update-ref", "refs/heads/main", foreign, args[2], **kwargs)
        raise OSError("unconfirmed CAS")

    monkeypatch.setattr(s.ws, "git", injected)
    with pytest.raises(RevertRefused):
        await s.run()
    assert calls == 1 and await s.head() == foreign
    assert s.manager.main_suspect and s.service._reserved
    assert not any(e.type == "revert.resolved" for e in s.events)
    assert json.loads(s.events[-1].envelope)["payload"]["fatal"]
    with pytest.raises(RevertRefused):
        await s.run()


async def test_revert_cas_won_checkout_failure_suspends(system, monkeypatch):
    s = system
    real = s.ws.git
    writes = []

    async def injected(tree, *args, **kwargs):
        if args[0] == "update-ref":
            writes.append(args)
        if args[0] == "read-tree" and tree is s.ws.main:
            # Foreign dirty checkout: classification must never overwrite it.
            (tree.root / "value.py").write_text("unexplained partial checkout\n")
            raise OSError("checkout failed")
        return await real(tree, *args, **kwargs)

    monkeypatch.setattr(s.ws, "git", injected)
    with pytest.raises(RevertRefused):
        await s.run()
    assert await s.head() == writes[0][2]
    assert len(writes) == 1 and s.service._reserved
    assert s.manager.main_suspect and s.ws.integrity.main_suspect
    assert (s.ws.main.root / "value.py").read_text() == "unexplained partial checkout\n"
    assert not any(e.type == "revert.resolved" for e in s.events)


@pytest.mark.parametrize("failure", ["scan", "outcome", "delta", "lost_receipt", "permanent"])
async def test_revert_cas_won_publication_failure_suspends(system, monkeypatch, failure):
    s = system
    ctx = s.service.ctx
    publish = ctx.publish
    calls = []
    old = ctx.state.snapshot

    async def injected(payload):
        calls.append(type(payload))
        if failure == "permanent" or (failure == "outcome" and isinstance(payload, RevertResult)):
            raise OSError("store unavailable")
        if failure == "delta" and isinstance(payload, GraphDelta):
            raise OSError("delta rejected")
        result = await publish(payload)
        if failure == "lost_receipt" and isinstance(payload, RevertResult):
            raise OSError("committed but receipt lost")
        return result

    async def broken_scan(commit):
        raise OSError("scan failed")

    monkeypatch.setattr(ctx, "publish", injected)
    if failure == "scan":
        monkeypatch.setattr(ctx, "scan", broken_scan)
    with pytest.raises(RevertRefused):
        await s.run()
    assert await s.head() != s.command.main_commit
    assert s.service._consumed and s.manager.main_suspect
    assert not s.ws.integrity.main_suspect and not s.ws.integrity.rules_suspended
    async with s.ws.locks(main=True):
        assert (await ctx.git(s.ws.main, "rev-parse", "HEAD")).strip() != s.command.main_commit
    assert ctx.state.snapshot is old
    assert calls.count(RevertResult) <= 1 and calls.count(GraphDelta) <= 1
    assert not any(e.type == "graph.delta" for e in s.events)
    results = [e for e in s.events if e.type == "revert.resolved"]
    assert len(results) == int(failure in {"delta", "lost_receipt"})
    if failure != "permanent":
        assert ErrorReport in calls
    with pytest.raises(RevertRefused):
        await s.run()


async def test_revert_cancel_mid_suite_main_untouched(system, monkeypatch, tmp_path):
    s = system
    entered, cleaned = asyncio.Event(), asyncio.Event()
    pid_file = tmp_path / "child.pid"
    source = f"import os,time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(60)"

    async def sleeping(argv, **kwargs):
        entered.set()
        try:
            return await run_process([sys.executable, "-c", source], **kwargs)
        finally:
            assert s.ws.operations.holds(main=True, agent_id="agent-1")
            cleaned.set()

    monkeypatch.setattr(s.ws._isolation, "select", lambda *args: sleeping)
    task = asyncio.create_task(s.run())
    await asyncio.wait_for(entered.wait(), 10)
    async with asyncio.timeout(10):
        while not pid_file.exists() or not pid_file.read_text():  # noqa: ASYNC110
            await asyncio.sleep(0.005)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 15)
    assert cleaned.is_set()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
    assert await s.head() == s.command.main_commit
    assert (s.ws.main.root / "value.py").read_text() == "value = 2\n"
    assert not s.manager.main_suspect and not s.service._reserved
    await s.clean()


@pytest.mark.parametrize("phase", ["outcome", "delta"])
async def test_revert_cancel_after_cas_records_success(system, monkeypatch, phase):
    s = system
    ctx, publish = s.service.ctx, s.service.ctx.publish
    entered, release = asyncio.Event(), asyncio.Event()
    kind = RevertResult if phase == "outcome" else GraphDelta
    old = ctx.state.snapshot
    calls = []

    async def gated(payload):
        calls.append(type(payload))
        receipt = await publish(payload)  # SQL commit precedes the lost caller wait.
        if isinstance(payload, kind):
            entered.set()
            await release.wait()
        return receipt

    monkeypatch.setattr(ctx, "publish", gated)
    task = asyncio.create_task(s.run())
    await asyncio.wait_for(entered.wait(), 10)
    assert ctx.state.snapshot is old
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ctx.state.snapshot.commit == await s.head() != s.command.main_commit
    assert calls.count(RevertResult) == calls.count(GraphDelta) == 1
    assert s.service._consumed and not s.manager.main_suspect
    await s.clean()


async def test_revert_repeated_cancel_and_delivery_budget(system, monkeypatch):
    s = system
    ctx, publish = s.service.ctx, s.service.ctx.publish
    entered, release = asyncio.Event(), asyncio.Event()
    old = ctx.state.snapshot
    calls = []

    success = s.service._success

    async def short_delivery(attempt):
        ctx.timeout = 0.2  # Git preparation used the normal budget.
        await success(attempt)

    monkeypatch.setattr(s.service, "_success", short_delivery)

    async def held(payload):
        calls.append(type(payload))
        if isinstance(payload, RevertResult):
            entered.set()
            await release.wait()
        return await publish(payload)

    monkeypatch.setattr(ctx, "publish", held)
    task = asyncio.create_task(s.run())
    await asyncio.wait_for(entered.wait(), 10)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert s.manager.main_suspect and s.service._consumed
    assert ctx.state.snapshot is old
    attempt = s.service._attempts[-1]
    assert not attempt.sending["outcome"].done()
    assert attempt.sending["outcome"] in ctx.state.tasks
    assert calls.count(RevertResult) == 1
    with pytest.raises(RevertRefused, match="settlement"):
        await s.service.aclose()
    assert not s.ws.integrity.main_suspect
    release.set()
    await s.service.aclose()
    assert ctx.state.snapshot is old  # An outcome acknowledgment cannot invent a delta.


@pytest.mark.parametrize("damage", ["reason", "actor", "tests", "commit", "seq"])
async def test_revert_event_validates_contract(system, damage):
    s = system
    result = await s.run()
    data = result.model_dump(mode="json")
    if damage == "reason":
        data.update(result="needs_you", revert_commit=None, reason=None)
    elif damage == "actor":
        data["decided_by"] = "rules"
    elif damage == "tests":
        data["tests"] = None
    elif damage == "commit":
        data["main_commit"] = "wrong"
    else:
        data["resolution_seq"] = 99999
    if damage != "tests":
        data["tests"] = result.tests
    bad = RevertResult.model_construct(**data)
    with pytest.raises(EventRejected):
        await s.store.publish(bad)


async def test_revert_empty_inverse_needs_you_without_suite(system):
    s = system
    h = await s.repo.commit("value.py", "value = 1\n")
    s.command = s.command.model_copy(update={"main_commit": h})
    result = await s.run()
    assert result.result == "needs_you" and "empty" in result.reason
    assert result.tests is None and s.policies == []
    assert await s.head() == h and not s.service._reserved


async def test_revert_unreadable_main_never_fabricates_result(system, monkeypatch):
    s = system
    real = s.ws.git

    async def truncated(tree, *args, **kwargs):
        result = await real(tree, *args, **kwargs)
        return replace(result, truncated=True) if args[0] == "rev-parse" else result

    monkeypatch.setattr(s.ws, "git", truncated)
    with pytest.raises(RevertRefused):
        await s.run()
    assert s.manager.main_suspect
    assert not any(e.type == "revert.resolved" for e in s.events)
    assert json.loads(s.events[-1].envelope)["payload"]["fatal"]


@pytest.mark.parametrize("damaged", ["cleanup", "namespace"])
async def test_revert_unverified_teardown_suspends(system, monkeypatch, damaged):
    s = system
    real = s.ws.run_tests

    async def suite(*args, **kwargs):
        run = await real(*args, **kwargs)
        if damaged == "namespace":
            from oversight.workspace import IntegrityError

            s.ws.integrity.suspend(IntegrityError("namespace not reaped", ".git"), uncertain=True)
        else:
            item = s.service._attempts[-1].candidate
            assert item is not None
            # Cleanup runs for real, but its proof is withheld from the service.
            kind = type(item)

            def unproved(self, name, value):
                object.__setattr__(self, name, False if name == "cleaned" else value)

            monkeypatch.setattr(kind, "__setattr__", unproved)
        return run

    monkeypatch.setattr(s.ws, "run_tests", suite)
    with pytest.raises(RevertRefused):
        await s.run()
    assert s.manager.main_suspect and s.service._reserved
    assert await s.head() == s.command.main_commit
    assert not any(e.type == "revert.resolved" for e in s.events)


async def test_revert_waiting_main_cancellation_releases_reservation(system):
    s = system
    async with s.ws.locks(main=True):
        task = asyncio.create_task(s.run())
        await asyncio.sleep(0)
        assert s.service._reserved
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert not s.service._reserved and not s.manager.main_suspect
    assert (await s.run()).result == "reverted"


async def test_revert_drain_failure_forbids_effect(system):
    s = system

    async def unavailable(ctx):
        raise OSError("old receipt unavailable")

    s.service.drain_pending = unavailable
    result = await s.run()
    assert result.result == "needs_you"
    assert await s.head() == s.command.main_commit
    assert not any(e.type == "graph.delta" for e in s.events)


async def test_revert_no_python_change_emits_commit_carrying_empty_delta(system, monkeypatch):
    from oversight.changes import RevertTarget
    from oversight.changes._versions import main_delta
    from oversight.workspace import Author
    from oversight_contracts.policy import EvidenceRef

    s, ctx = system, system.service.ctx
    await s.run()  # Restore value=1 so the same fixed suite verifies the next inverse.
    base = await s.head()
    await s.repo.git("checkout", "-qb", "notes")
    head = await s.repo.commit("notes.txt", "Added by rules\n")
    await s.repo.git("checkout", "-q", "main")
    async with s.ws.locks(main=True):
        await s.ws.git_ok(
            s.ws.main,
            "merge",
            "--no-ff",
            "-qm",
            "Merge change-3 (agent-3)\n\nMerged-by: rules",
            head,
            author=Author("rules", "rules@oversight.invalid"),
        )
    merged = await s.head()
    tree = (await s.repo.git("rev-parse", "HEAD^{tree}")).strip()
    payload = s.repo.event.payload.model_copy(
        deep=True,
        update=dict(
            change_id="change-3", agent_id="agent-3", merge_commit=merged, head_commit=head
        ),
    )
    assessment = payload.assessment.model_copy(
        deep=True,
        update=dict(
            change_id="change-3",
            agent_id="agent-3",
            base_commit=base,
            main_commit=base,
            head_commit=head,
            candidate_tree=tree,
        ),
    )
    for rule in assessment.rules:
        rule.evidence[:] = [EvidenceRef(git_object=head)]
    object.__setattr__(payload, "assessment", assessment)
    original = await ctx.publish(payload)
    s.target = RevertTarget("run-test", "change-3", original)
    s.command = s.command.model_copy(
        update=dict(resolution_seq=original.seq, merge_commit=merged, main_commit=merged)
    )
    new = await ctx.scan(merged)
    await ctx.publish(main_delta(ctx.state.snapshot, new, merged, "merge"))
    ctx.state.snapshot = new
    monkeypatch.setattr(s.ws._isolation, "select", lambda policy, injected, runner: runner)
    result = await s.run()
    delta = json.loads(s.events[-1].envelope)["payload"]
    assert result.result == "reverted" and result.tests.passed == 1
    assert delta["commit"] == result.revert_commit and delta["cause"] == "revert"
    assert delta["from_version"] == delta["to_version"]
    assert all(
        delta[field] == []
        for field in (
            "nodes_added",
            "nodes_changed",
            "nodes_removed",
            "edges_added",
            "edges_changed",
            "edges_removed",
        )
    )
    assert not (s.ws.main.root / "notes.txt").exists()


@pytest.mark.parametrize("first", ["revert", "person"])
async def test_revert_serializes_with_rules_and_person_decisions(h, reserved, monkeypatch, first):
    from test_rules_transaction import configure_transaction, execute

    from oversight.changes import RevertTarget
    from oversight_contracts.commands import RevertChange

    # The existing test seam supplies evidence and namespace capability, leaving F1 closed.
    with monkeypatch.context() as rules_adapters:
        configure_transaction(h, rules_adapters)
        await execute(h, reserved)
    original = next(e for e in h.log if e.type == "change.resolved")
    command = RevertChange(
        resolution_seq=original.seq,
        merge_commit=json.loads(original.envelope)["payload"]["merge_commit"],
        main_commit=await h.main_head(),
    )
    from test_mode import select

    await select(h, "ask_me", 1)
    # The person path requests best effort; Revert still requests required isolation.
    monkeypatch.setattr(h.workspace._isolation, "select", lambda policy, injected, runner: runner)
    tree = await h.open(2)
    await tree.write_text("later.py", "later = True\n", expected_hash=None)
    assert await h.manager.file_edited("agent-2", "later.py")
    assert await h.finish(2)
    rec = h.manager._ctx.state.agents["agent-2"]
    service = _revert.RevertService(ctx=h.manager._ctx, run_id=h.store.run_id, test_timeout_s=10)
    target = RevertTarget(h.store.run_id, "change-1", original)
    entered, release = asyncio.Event(), asyncio.Event()
    real_suite = h.workspace.run_tests

    async def held_suite(tree, **kwargs):
        if tree.root.name.startswith("revert-") and first == "revert":
            assert kwargs["isolation"] == "required"
            entered.set()
            await release.wait()
        return await real_suite(tree, **kwargs)

    def approve():
        return h.manager.approve(
            rec.change_id, None, revision=rec.revision, head_commit=rec.head_commit
        )

    monkeypatch.setattr(h.workspace, "run_tests", held_suite)
    try:
        if first == "revert":
            reverting = asyncio.create_task(service.revert(target, command, principal=Person))
            await asyncio.wait_for(entered.wait(), 10)
            person = approve()
            await asyncio.sleep(0)
            assert not person.done()
            release.set()
            result = await reverting
            await person
        else:
            h.workspace.arm("commit", mode="hold")
            person = approve()
            await asyncio.wait_for(h.workspace.entered.wait(), 10)
            reverting = asyncio.create_task(service.revert(target, command, principal=Person))
            await asyncio.sleep(0)
            assert not reverting.done()
            h.workspace.resume.set()
            await person
            assert (await reverting).result == "needs_you"
            command = command.model_copy(update={"main_commit": await h.main_head()})
            result = await service.revert(target, command, principal=Person)
        assert result.result == "reverted" and result.decided_by == "person"
        assert (h.workspace.main.root / "shop/mod.py").read_text().count("return 1") == 1
        assert (h.workspace.main.root / "later.py").read_text() == "later = True\n"
        assert h.manager.snapshot.commit == await h.main_head()
        assert rec.resolution.decided_by == "person" and rec.resolution.merge == "merged"
    finally:
        release.set()
        h.workspace.resume.set()
        await service.aclose()


@pytest.mark.parametrize("interruption", ["cancel", "drain_timeout"])
async def test_stale_command_pre_effect_interruption_releases_key(
    system, monkeypatch, interruption
):
    s, ctx = system, system.service.ctx
    actual = await s.repo.commit("later.txt", "unrelated later edit\n")
    entered = asyncio.Event()
    needs_you, drain = s.service._needs_you, s.service.drain_pending
    calls = 0

    async def cancelled_result(attempt, reason):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await asyncio.Event().wait()
        return await needs_you(attempt, reason)

    async def timed_drain(ctx):
        nonlocal calls
        calls += 1
        if calls == 1:
            try:
                await asyncio.Event().wait()
            finally:
                ctx.timeout = 10  # Bound just the drain; Git verification uses its own budget.
        await drain(ctx)

    if interruption == "cancel":
        monkeypatch.setattr(s.service, "_needs_you", cancelled_result)
        task = asyncio.create_task(s.run())
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        ctx.timeout = 0.05
        s.service.drain_pending = timed_drain
        assert (await s.run()).result == "needs_you"
    outcome = json.loads(s.events[-1].envelope)["payload"]
    assert outcome["result"] == "needs_you" and outcome["main_commit"] == actual
    assert s.service._attempts[-1].candidate is None and not s.service._attempts[-1].prepared
    assert not s.service._reserved and not s.service._consumed
    assert not ctx.state.suspect and not s.ws.integrity.main_suspect
    s.command = s.command.model_copy(update={"main_commit": actual})
    assert (await s.run()).result == "reverted"


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
@pytest.mark.parametrize("phase", ["suite", "effect", "unknown"])
async def test_process_interrupt_settles_prepared_inverse(system, monkeypatch, interruption, phase):
    from oversight.workspace import classify_inverse

    s, classified = system, []
    suite = s.ws.run_tests

    async def interrupted_suite(*args, **kwargs):
        await suite(*args, **kwargs)
        raise interruption("injected process interrupt")

    async def interrupted_effect(ws, **kwargs):
        if phase == "unknown":
            await ws.git_ok(
                ws.main,
                "update-ref",
                "refs/heads/main",
                kwargs["inverse_commit"],
                kwargs["main_commit"],
            )
            (ws.main.root / "value.py").write_text("unexplained partial checkout\n")
        else:
            await apply_inverse(ws, **kwargs)
        raise interruption("injected process interrupt")

    async def classify(*args, **kwargs):
        assert s.ws.operations.holds(main=True, agent_id="agent-1")
        result = await classify_inverse(*args, **kwargs)
        classified.append(result)
        return result

    monkeypatch.setattr(_revert, "classify_inverse", classify)
    if phase == "suite":
        monkeypatch.setattr(s.ws, "run_tests", interrupted_suite)
    else:
        monkeypatch.setattr(_revert, "apply_inverse", interrupted_effect)
    with pytest.raises(interruption):
        await s.run()
    assert classified == [{"suite": "untouched", "effect": "landed", "unknown": "unknown"}[phase]]
    assert bool(s.service._consumed) == (phase == "effect")
    assert s.manager.main_suspect == s.ws.integrity.main_suspect == (phase == "unknown")
    assert s.service._attempts[-1].candidate.cleaned
    assert (await s.head() != s.command.main_commit) == (phase != "suite")
    if phase == "unknown":
        assert s.service._reserved and s.service._attempts[-1].blocked
        assert not any(e.type == "revert.resolved" for e in s.events)


async def test_revert_waits_for_inflight_rules_resolution_and_delta(h, reserved, monkeypatch):
    from test_rules_reservation import start
    from test_rules_transaction import configure_transaction

    from oversight.changes import RevertService, RevertTarget
    from oversight_contracts.commands import RevertChange
    from oversight_contracts.events import ChangeResolved

    configure_transaction(h, monkeypatch)
    h.hold = ChangeResolved  # SQL took the resolution, while its rules owner still holds main.
    decision = await start(h, reserved)
    await asyncio.wait_for(h.held.wait(), 10)
    original = next(e for e in h.log if e.type == "change.resolved")
    merged = json.loads(original.envelope)["payload"]["merge_commit"]
    command = RevertChange(resolution_seq=original.seq, merge_commit=merged, main_commit=merged)
    service = RevertService(ctx=h.manager._ctx, run_id=h.store.run_id, test_timeout_s=10)
    reverting = asyncio.create_task(
        service.revert(
            RevertTarget(h.store.run_id, "change-1", original), command, principal=Person
        )
    )
    try:
        await asyncio.sleep(0)
        assert not decision.done() and not reverting.done()
        assert service._reserved and not service._attempts[-1].prepared
        assert h.workspace.operations._main.owner is decision
        h.release.set()
        await decision
        result = await reverting
        assert result.result == "reverted"
        assert [e.type for e in h.log[-2:]] == ["revert.resolved", "graph.delta"]
        assert not h.worktree_dir("agent-1").exists()
        assert h.manager.snapshot.commit == result.revert_commit
        h.events()  # Rules' acknowledged delta is chained before the inverse delta.
    finally:
        h.release.set()
        await decision
        await reverting
        await service.aclose()


async def test_unconfirmed_stale_needs_you_blocks_main_but_allows_tree_cleanup(system, monkeypatch):
    s, ctx = system, system.service.ctx
    actual = await s.repo.commit("later.txt", "later work\n")
    tree = await s.ws.create_worktree("agent-2")
    publish = ctx.publish

    async def missing_receipt(payload):
        if isinstance(payload, RevertResult):
            raise OSError("needs_you receipt unavailable")
        return await publish(payload)

    monkeypatch.setattr(ctx, "publish", missing_receipt)
    with pytest.raises(RevertRefused):
        await s.run()
    assert s.service._attempts[-1].candidate is None
    assert s.service._reserved and ctx.state.suspect
    assert not s.ws.integrity.main_suspect and not s.ws.integrity.rules_suspended
    async with s.ws.locks(agent_id="agent-2"):
        assert (await ctx.git(tree, "rev-parse", "HEAD")).strip() == actual
    await s.ws.cleanup_merged_worktree("agent-2", delete_branch=True)
    assert not tree.root.exists()
    assert not any(e.type == "revert.resolved" for e in s.events)
