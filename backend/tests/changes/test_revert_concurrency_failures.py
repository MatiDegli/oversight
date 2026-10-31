"""Inverse serialization, stale commands and interrupted publication."""

import asyncio
import json

import pytest
from changes_revert_helpers import pytestmark as pytestmark
from changes_revert_helpers import system as system
from test_revert_preflight import repo as repo
from test_rules_transaction import reserved as reserved

from oversight.changes import Person, RevertRefused, _revert
from oversight.workspace import (
    ProcessResult,
    apply_inverse,
)
from oversight_contracts.events import RevertResult


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


@pytest.mark.parametrize("target", ["stale", "empty"])
@pytest.mark.parametrize("interruption", ["cancel", "error"])
async def test_pre_candidate_interruption_preserves_first_diagnostic(
    system, monkeypatch, target, interruption
):
    s = system
    if target == "stale":
        head = await s.repo.commit("later.txt", "later work\n")
        expected = "observed main is stale"
    else:
        head = await s.repo.commit("value.py", "value = 1\n")
        s.command = s.command.model_copy(update={"main_commit": head})
        expected = "Inverse is empty"
    main = s.service._main
    entered = asyncio.Event()
    calls = 0

    async def interrupted_verification():
        nonlocal calls
        calls += 1
        if calls == 1:
            if interruption == "error":
                raise OSError("injected verification interruption")
            entered.set()
            await asyncio.Event().wait()
        return await main()

    monkeypatch.setattr(s.service, "_main", interrupted_verification)
    task = asyncio.create_task(s.run())
    if interruption == "cancel":
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        await task
    result = json.loads(s.events[-1].envelope)["payload"]
    assert result["result"] == "needs_you" and result["reason"] == expected
    assert result["main_commit"] == head and result["tests"] is None
    attempt = s.service._attempts[-1]
    assert attempt.candidate is None and not attempt.prepared
    assert not s.service._reserved and not s.manager.main_suspect


@pytest.mark.parametrize("edge", ["at_c_clean", "at_c_dirty", "mismatch_c", "head_read_failure"])
async def test_revert_integrates_hardened_workspace_outcomes(system, monkeypatch, edge):
    s = system
    git = s.ws.git
    outcomes, calls = [], []

    async def injected_git(tree, *args, **kwargs):
        if edge == "head_read_failure" and args == ("symbolic-ref", "HEAD") and not calls:
            calls.append(args)
            return ProcessResult(1, "", "unreadable HEAD", False, False, 0)
        if edge == "mismatch_c" and args[0] == "update-ref" and not calls:
            calls.append(args)
            await git(tree, *args, **kwargs)
            return ProcessResult(1, "", f"is at {args[2]} but expected {args[3]}", False, False, 0)
        return await git(tree, *args, **kwargs)

    async def recorded_apply(ws, **kwargs):
        assert ws.operations.holds(main=True, agent_id="agent-1")
        if edge.startswith("at_c"):
            await ws.git_ok(
                ws.main,
                "update-ref",
                "refs/heads/main",
                kwargs["inverse_commit"],
                kwargs["main_commit"],
            )
            if edge == "at_c_dirty":
                (ws.main.root / "value.py").write_text("unexplained checkout\n")
        outcome = await apply_inverse(ws, **kwargs)
        outcomes.append(outcome)
        return outcome

    monkeypatch.setattr(s.ws, "git", injected_git)
    monkeypatch.setattr(_revert, "apply_inverse", recorded_apply)
    if edge == "at_c_dirty":
        with pytest.raises(RevertRefused):
            await s.run()
        assert s.manager.main_suspect and s.ws.integrity.main_suspect
        assert s.service._reserved and s.service._attempts[-1].blocked
        assert not any(e.type == "revert.resolved" for e in s.events)
        assert (s.ws.main.root / "value.py").read_text() == "unexplained checkout\n"
    else:
        result = await s.run()
        if edge == "head_read_failure":
            assert result.result == "needs_you" and await s.head() == s.command.main_commit
            assert not s.service._reserved and not s.service._consumed
        else:
            assert result.result == "reverted" and result.revert_commit == await s.head()
            assert s.service._consumed
            assert (s.ws.main.root / "value.py").read_text() == "value = 1\n"
        assert not s.manager.main_suspect
        await s.clean()
    assert len(outcomes) == 1
    assert outcomes[0].kind == (
        "unknown" if edge in {"at_c_dirty", "head_read_failure"} else "landed"
    )
