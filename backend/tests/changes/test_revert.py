"""Service integration with real F7a primitives, fixed local suites and SQL receipts."""

import asyncio
import json
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from test_revert_preflight import Repo
from test_revert_preflight import repo as repo

from oversight.changes import (
    ChangeManager,
    Person,
    RevertRefused,
    RevertService,
    RevertTarget,
    _revert,
)
from oversight.events import EventStore, StoredEvent
from oversight.graph import WorkspaceGraph
from oversight.workspace import apply_inverse
from oversight_contracts.commands import RevertChange
from oversight_contracts.events import ChangeResolvedEvent, RevertResolvedEvent, SuiteResult
from oversight_contracts.graph import GraphDelta
from oversight_contracts.policy import EvidenceRef

pytestmark = pytest.mark.anyio


@dataclass
class ServiceRepo:
    repo: Repo
    service: RevertService
    manager: ChangeManager
    target: RevertTarget
    command: RevertChange
    store: EventStore
    events: list
    policies: list

    @property
    def ws(self):
        return self.repo.ws

    async def run(self, **kwargs):
        principal = kwargs.pop("principal", Person)
        return await self.service.revert(self.target, self.command, principal=principal, **kwargs)

    async def head(self):
        if self.manager.main_suspect:
            # Observe the disposable repository without bypassing the production suspension latch.
            return (self.ws.main.root / ".git/refs/heads/main").read_text().strip()
        return (await self.repo.git("rev-parse", "HEAD")).strip()

    async def clean(self):
        assert await self.repo.git("status", "--porcelain", "-z") == ""
        assert not list(self.ws.tmp_dir.glob("revert-*"))
        listing = await self.repo.git("worktree", "list", "--porcelain")
        assert "revert-" not in listing
        assert self.ws.operations.idle


@pytest.fixture
async def system(repo, tmp_path, monkeypatch):
    root = repo.ws.main.root
    (root / "tests").mkdir()
    h = await repo.commit(
        "tests/test_value.py",
        "from pathlib import Path\n\ndef test_inverse():\n"
        '    assert Path("value.py").read_text() == "value = 1\\n"\n',
    )
    graph = WorkspaceGraph(root)
    snapshot = graph.scan(h)
    store = EventStore(tmp_path / "service.sqlite", "run-test")
    await store.start()
    events, policies = [], []

    async def publish(payload):
        event = await store.publish(payload)
        events.append(event)
        return event

    await publish(snapshot)
    payload = repo.event.payload.model_copy(deep=True)
    for rule in payload.assessment.rules:
        rule.evidence[:] = [EvidenceRef(git_object=repo.proposal)]
    resolution = await publish(payload)
    command = repo.command.model_copy(update=dict(main_commit=h, resolution_seq=resolution.seq))
    manager = ChangeManager(repo.ws, publish, main_graph=graph, main_snapshot=snapshot)
    service = RevertService(ctx=manager._ctx, run_id="run-test", test_timeout_s=10)
    repo.ws._demo_python = Path(sys.executable)

    def isolated(policy, injected, open_runner):
        # Only the namespace adapter is scripted. Real suite, reports and Git guards remain.
        policies.append(policy)
        assert policy == "required"
        assert repo.ws.operations.holds(main=True, agent_id="agent-1")
        assert not manager._ctx.mode._lock.locked()
        return open_runner

    monkeypatch.setattr(repo.ws._isolation, "select", isolated)
    s = ServiceRepo(
        repo,
        service,
        manager,
        RevertTarget("run-test", "change-1", resolution),
        command,
        store,
        events,
        policies,
    )
    try:
        yield s
    finally:
        await service.aclose()
        await manager.aclose()
        await store.close()


async def test_revert_non_person_refused(system):
    with pytest.raises(RevertRefused, match="person") as error:
        await system.run(principal="model")
    assert error.value.code == "invalid_principal"
    assert len(system.events) == 2


async def test_revert_rules_success_user_identity_event_then_delta(system):
    s = system
    old = s.manager.snapshot
    result = await s.run(principal=Person)
    assert result.result == "reverted" and result.reason is None
    assert result.main_commit == result.revert_commit == await s.head()
    assert result.resolution_seq == s.target.resolution.seq
    assert result.revision == s.repo.event.payload.revision
    assert result.tests.outcome == "passed" and result.tests.passed == 1
    assert s.policies == ["required"]
    assert [event.type for event in s.events[-2:]] == ["revert.resolved", "graph.delta"]
    event = RevertResolvedEvent.model_validate_json(s.events[-2].envelope, context={"live": True})
    assert event.payload == result and event.payload.decided_by == "person"
    delta = json.loads(s.events[-1].envelope)["payload"]
    assert delta["cause"] == "revert" and delta["from_version"] == old.version
    assert s.manager.snapshot is s.service.ctx.state.snapshot
    assert s.manager.snapshot.commit == result.main_commit
    assert s.service._attempts[-1].receipts == {"outcome": s.events[-2], "delta": s.events[-1]}
    assert (s.ws.main.root / "value.py").read_text() == "value = 1\n"
    assert (s.ws.main.root / "tests/test_value.py").exists()  # unrelated later work survives
    assert (await s.repo.git("show", "-s", "--format=%P", "HEAD")).strip() == s.command.main_commit
    assert (await s.repo.git("show", "-s", "--format=%an|%ae|%cn|%ce", "HEAD")).strip() == (
        "User|user@oversight.invalid|User|user@oversight.invalid"
    )
    await s.clean()


async def test_revert_conflict_needs_you_no_commit(system):
    s = system
    h = await s.repo.commit("value.py", "value = 3\n")
    s.command = s.command.model_copy(update={"main_commit": h})
    before = await s.repo.snapshot()
    result = await s.run()
    assert result.result == "needs_you" and result.reason and result.tests is None
    assert result.main_commit == h and result.revert_commit is None
    assert await s.repo.snapshot() == before
    assert s.events[-1].type == "revert.resolved"
    assert not any(event.type == "graph.delta" for event in s.events)
    await s.clean()


@pytest.mark.parametrize("actor", ["person", None, "cross_review", "lead_agent", "future"])
async def test_revert_non_rules_refused(system, actor):
    s = system
    event = s.repo.event.model_copy(
        update={"payload": s.repo.event.payload.model_copy(update={"decided_by": actor})}
    )
    stored = StoredEvent(event.seq, event.ts, event.type, event.model_dump_json())
    target = replace(s.target, resolution=stored)
    command = s.command.model_copy(update={"resolution_seq": event.seq})
    with pytest.raises(RevertRefused) as error:
        await s.service.revert(target, command, principal=Person)
    assert error.value.code == "invalid_target"
    assert await s.head() == s.command.main_commit
    assert len(s.events) == 2


@pytest.mark.parametrize("field", ["run", "change", "seq", "merge", "envelope", "assessment"])
async def test_revert_identity_refused(system, field):
    s = system
    target, command = s.target, s.command
    if field == "run":
        target = replace(target, run_id="other")
    elif field == "change":
        target = replace(target, change_id="change-2")
    elif field in ("seq", "merge"):
        command = command.model_copy(
            update={
                "resolution_seq" if field == "seq" else "merge_commit": 99
                if field == "seq"
                else "f" * 40
            }
        )
    else:
        stored = target.resolution
        data = json.loads(stored.envelope)
        if field == "assessment":
            data["payload"]["assessment"] = None
        else:
            data["seq"] += 1
        target = replace(target, resolution=replace(stored, envelope=json.dumps(data)))
    with pytest.raises(RevertRefused):
        await s.service.revert(target, command, principal=Person)
    assert len(s.events) == 2


async def test_revert_second_success_refused(system):
    s = system
    first = await s.run()
    s.command = s.command.model_copy(update={"main_commit": first.main_commit})
    with pytest.raises(RevertRefused) as error:
        await s.run()
    assert error.value.code == "duplicate"
    assert len(s.events) == 4


async def test_revert_duplicate_reserved_before_main_lock(system):
    s = system
    async with s.ws.locks(main=True):
        first = asyncio.create_task(s.run())
        await asyncio.sleep(0)
        with pytest.raises(RevertRefused) as error:
            await s.run()
        assert error.value.code == "duplicate"
    assert (await first).result == "reverted"


@pytest.mark.parametrize("when", ["before", "after_suite", "at_apply"])
async def test_revert_main_moved_before_effect(system, monkeypatch, when):
    s = system
    real_suite, real_apply = s.ws.run_tests, apply_inverse
    changed = None

    async def move():
        nonlocal changed
        changed = await s.repo.commit("later.txt", "preserved\n")

    if when == "before":
        await move()
    elif when == "after_suite":

        async def suite(*args, **kwargs):
            result = await real_suite(*args, **kwargs)
            await move()
            return result

        monkeypatch.setattr(s.ws, "run_tests", suite)
    else:

        async def apply(*args, **kwargs):
            await move()
            return await real_apply(*args, **kwargs)

        monkeypatch.setattr(_revert, "apply_inverse", apply)
    result = await s.run()
    assert result.result == "needs_you" and result.main_commit == changed
    assert await s.head() == changed
    assert (s.ws.main.root / "value.py").read_text() == "value = 2\n"
    assert not s.service._consumed
    await s.clean()


@pytest.mark.parametrize(
    "problem",
    ["failed", "timeout", "error", "empty", "skip", "tree", "problems", "unavailable", "mutated"],
)
async def test_revert_suite_failed_timeout_unavailable_or_mutated(system, monkeypatch, problem):
    s = system
    real = s.ws.run_tests
    runs = []

    async def suite(*args, **kwargs):
        run = await real(*args, **kwargs)
        outcome = problem if problem in {"failed", "timeout", "error"} else "passed"
        result = SuiteResult(
            outcome=outcome,
            passed=0 if problem in {"empty", "skip"} else 1,
            failed=int(problem == "failed"),
            skipped=int(problem == "skip"),
            duration_ms=1,
            failed_tests=[],
        )
        run = replace(
            run,
            result=result,
            tested_tree="f" * 40 if problem == "tree" else run.tested_tree,
            problems=("integrity lost",) if problem == "problems" else (),
        )
        runs.append(run)
        if problem == "unavailable":
            from oversight.workspace import SuiteUnavailable

            raise SuiteUnavailable(run, OSError(5, "unavailable"))
        if problem == "mutated":
            # Mutation of main is an integrity failure, never an eligible result.
            (s.ws.main.root / "value.py").write_text("unexplained\n")
        return run

    monkeypatch.setattr(s.ws, "run_tests", suite)
    if problem == "mutated":
        with pytest.raises(RevertRefused):
            await s.run()
        assert s.manager.main_suspect
    else:
        result = await s.run()
        assert result.result == "needs_you" and result.tests == runs[0].result
        assert result.reason
        assert not s.service._reserved
        # A fresh command is retryable after confirmed no effect and settled receipt.
        monkeypatch.setattr(s.ws, "run_tests", real)
        assert (await s.run()).result == "reverted"
    assert not any(e.type == "graph.delta" for e in s.events[:3])


@pytest.mark.parametrize("original_removed", [False, True])
async def test_revert_temp_lock_key_held_across_suite(system, monkeypatch, original_removed):
    s = system
    real = s.ws.run_tests
    calls = []
    if original_removed:
        original = await s.ws.create_worktree("agent-1")
        assert original.root.exists()
        await s.ws.cleanup_merged_worktree("agent-1", delete_branch=True)
        assert not original.root.exists()
        assert "agent-1" not in await s.repo.git("worktree", "list", "--porcelain")

    async def suite(tree, **kwargs):
        assert tree.agent_id == "agent-1"
        assert not (s.ws.runtime_root / "worktrees" / "agent-1").exists()
        assert s.ws.operations.holds(main=True, agent_id="agent-1")
        async with s.ws.locks(main=True, agent_id="agent-1"):
            run = await real(tree, **kwargs)
        calls.append(tree.root)
        return run

    monkeypatch.setattr(s.ws, "run_tests", suite)
    await s.run()
    assert calls and not calls[0].exists()
    await s.clean()


async def test_revert_shared_snapshot_drains_before_build(system, monkeypatch):
    from functools import partial

    from changes_helpers import Harness

    from oversight.changes import _delivery
    from oversight.changes._versions import main_delta, with_positions

    s, ctx = system, system.service.ctx
    await s.manager.open(Harness.task(2), "fake")
    rec = ctx.state.agents["agent-2"]
    h = await s.repo.commit("later.py", "later = True\n")
    s.command = s.command.model_copy(update={"main_commit": h})
    new = await ctx.scan(h)
    old = ctx.state.snapshot
    rec.delta = main_delta(old, new, h, "merge")
    rec.next_snapshot = with_positions(new, old)
    ctx.state.pending[rec.agent_id] = rec
    # Another older delta is unsent, and must never be published after the inverse.
    other = replace(rec, task=Harness.task(3), sending=None, sent=set())
    ctx.state.pending[other.agent_id] = other
    publish = ctx.publish
    earlier, inverse = asyncio.Event(), asyncio.Event()
    release_earlier, release_inverse = asyncio.Event(), asyncio.Event()

    async def gated(payload):
        if isinstance(payload, GraphDelta):
            if payload.cause == "merge":
                earlier.set()
                await release_earlier.wait()
            else:
                inverse.set()
                await release_inverse.wait()
        return await publish(payload)

    monkeypatch.setattr(ctx, "publish", gated)
    rec.sending = ctx.spawn(_delivery.deliver(ctx, rec.delta))
    rec.sending.add_done_callback(partial(_delivery.receipt, ctx, rec, "delta"))
    await asyncio.wait_for(earlier.wait(), 10)
    task = asyncio.create_task(s.run())
    try:
        await asyncio.sleep(0)
        assert not task.done() and ctx.state.snapshot is old
        release_earlier.set()
        await asyncio.wait_for(inverse.wait(), 10)
        assert ctx.state.snapshot is rec.next_snapshot
        assert ctx.state.snapshot.commit == h
        assert other.delta is other.next_snapshot is None
        release_inverse.set()
        result = await task
        inverse_delta = json.loads(s.events[-1].envelope)["payload"]
        assert inverse_delta["from_version"] == rec.next_snapshot.version
        assert s.manager.snapshot.commit == result.revert_commit
        assert s.service.ctx.state is s.manager._ctx.state
    finally:
        release_earlier.set()
        release_inverse.set()
        await task
        ctx.state.pending.clear()


async def test_waiting_rules_current_main_barrier_fails_after_revert(system, monkeypatch):
    from changes_helpers import Harness

    from oversight.changes import _rules
    from oversight.changes._assessment import AssessmentWait
    from oversight.changes._mode import StartCapability
    from oversight.events import NeedsYouWait
    from oversight_contracts.events import ChangeProposedEvent
    from oversight_contracts.policy import MergeModeState

    s, ctx = system, system.service.ctx
    await s.manager.open(Harness.task(2), "fake")
    with monkeypatch.context() as proposal_adapter:
        proposal_adapter.setattr(s.ws._isolation, "select", lambda policy, injected, runner: runner)
        assert await s.manager.proposed("agent-2", "Pending rules work")
    s.manager.run_ended("agent-2", None)
    rec = ctx.state.agents["agent-2"]
    proposal = next(
        ChangeProposedEvent.model_validate_json(e.envelope).payload
        for e in s.events
        if e.type == "change.proposed"
    )
    mode = MergeModeState(mode="merge_when_safe", revision=1)
    ctx.mode._state = mode
    monkeypatch.setattr(ctx.mode, "_availability", lambda: StartCapability(True, "test capability"))
    monkeypatch.setattr(s.ws._isolation, "_born", True)
    monkeypatch.setattr(s.ws._isolation, "_checked", True)
    assessment = s.repo.event.payload.assessment.model_copy(
        deep=True,
        update=dict(
            change_id=rec.change_id,
            agent_id=rec.agent_id,
            revision=rec.revision,
            base_commit=rec.base_commit,
            head_commit=rec.head_commit,
            main_commit=s.command.main_commit,
            mode_revision=1,
        ),
    )
    for rule in assessment.rules:
        rule.evidence[:] = [EvidenceRef(git_object=rec.base_commit)]
        if rule.rule == "combined_tests":
            object.__setattr__(rule, "result", "unknown")
            object.__setattr__(rule, "observed", None)
    wait = AssessmentWait(
        rec,
        NeedsYouWait(rec.change_id, rec.revision, rec.head_commit, 0),
        mode,
        proposal,
        None,
        s.command.main_commit,
        latest=assessment,
    )
    ctx.assessment.waits[rec.change_id] = wait
    rec.proposal_mode = mode
    reservation = _rules.capture(ctx, wait, assessment)
    rec.state, rec.fallback = "deciding", "ready"
    rec.taken, rec.evidence, rec.decision = rec.revision, reservation.evidence, reservation.decision
    rec.rules_consumed.add(rec.revision)
    assert _rules.revalidate(ctx, reservation) is None
    result = await s.run()
    assert result.result == "reverted"
    failure = _rules.revalidate(ctx, reservation)
    assert failure is not None and failure[0] == "main_moved"
    assert await s.head() == result.revert_commit
    assert reservation.evidence.assessment is not None
    assert ctx.state.snapshot.commit != reservation.evidence.assessment.main_commit


async def test_revert_requires_explicit_person_principal(system):
    import inspect

    method = system.service.revert
    assert inspect.signature(method).parameters["principal"].default is inspect.Parameter.empty
    with pytest.raises(TypeError, match="principal"):
        await method(system.target, system.command)  # pyright: ignore[reportCallIssue]
    assert len(system.events) == 2 and not system.service._reserved


async def test_revert_requires_server_suite_timeout(system):
    import inspect

    assert (
        inspect.signature(RevertService).parameters["test_timeout_s"].default
        is inspect.Parameter.empty
    )
    with pytest.raises(TypeError, match="test_timeout_s"):
        RevertService(ctx=system.manager._ctx, run_id="run-test")  # pyright: ignore[reportCallIssue]


@pytest.mark.parametrize("damage", ["trailer", "parents", "tree"])
async def test_revert_forged_git_provenance_has_no_effect(system, damage):
    from oversight.workspace import Author

    s, ctx = system, system.service.ctx
    old = await s.head()
    assessed = s.repo.event.payload.assessment
    tree = assessed.candidate_tree
    parents = [s.repo.parent, s.repo.proposal]
    message = "Merge change-1 (agent-1)\n\nMerged-by: rules"
    if damage == "trailer":
        message += "\nUnverified trailer"
    elif damage == "parents":
        parents.reverse()
    else:
        tree = (await s.repo.git("rev-parse", s.repo.parent + "^{tree}")).strip()
    async with s.ws.locks(main=True):
        forged = (
            await s.ws.git_ok(
                s.ws.main,
                "commit-tree",
                tree,
                "-p",
                parents[0],
                "-p",
                parents[1],
                "-m",
                message,
                author=Author("rules", "rules@oversight.invalid"),
            )
        ).strip()
    await s.repo.git("update-ref", "refs/heads/main", forged, old)
    await s.repo.git("read-tree", "-m", "-u", old, forged)
    payload = ChangeResolvedEvent.model_validate_json(s.target.resolution.envelope).payload
    payload = payload.model_copy(update={"merge_commit": forged})
    original = await ctx.publish(payload)
    s.target = replace(s.target, resolution=original)
    s.command = s.command.model_copy(
        update=dict(resolution_seq=original.seq, merge_commit=forged, main_commit=forged)
    )
    before = await s.repo.snapshot()
    result = await s.run()
    assert result.result == "needs_you" and result.main_commit == forged and result.reason
    assert result.revert_commit is None and result.tests is None and not s.policies
    assert s.service._attempts[-1].candidate is None
    assert not s.service._reserved and not s.manager.main_suspect
    assert await s.repo.snapshot() == before
    assert not any(e.type == "graph.delta" for e in s.events)


@pytest.mark.parametrize("phase", ["suite", "effect"])
async def test_revert_allows_concurrent_ask_me_switch(system, monkeypatch, phase):
    from oversight_contracts.commands import SetMergeMode
    from oversight_contracts.policy import MergeModeState

    s, ctx = system, system.service.ctx
    ctx.mode._state = MergeModeState(mode="merge_when_safe", revision=1)
    entered, release = asyncio.Event(), asyncio.Event()
    run = s.ws.run_tests if phase == "suite" else apply_inverse

    async def held(*args, **kwargs):
        assert s.ws.operations.holds(main=True, agent_id="agent-1")
        assert not ctx.mode._lock.locked()
        entered.set()
        await release.wait()
        return await run(*args, **kwargs)

    monkeypatch.setattr(
        s.ws if phase == "suite" else _revert,
        "run_tests" if phase == "suite" else "apply_inverse",
        held,
    )
    reverting = asyncio.create_task(s.run())
    try:
        await asyncio.wait_for(entered.wait(), 10)
        switched = await asyncio.wait_for(
            s.manager.set_merge_mode(SetMergeMode(mode="ask_me", revision=1)), 10
        )
        assert switched.mode == "ask_me" and switched.revision == 2
        assert not reverting.done()
        release.set()
        assert (await reverting).result == "reverted"
        assert not ctx.state.suspect
    finally:
        release.set()
        await reverting


async def test_revert_second_drain_observes_publish_started_during_suite(system, monkeypatch):
    from functools import partial

    from changes_helpers import Harness

    from oversight.changes import _delivery
    from oversight.changes._versions import main_delta

    s, ctx = system, system.service.ctx
    await s.manager.open(Harness.task(2), "fake")
    rec = ctx.state.agents["agent-2"]
    head = await s.repo.commit("later.py", "later = True\n")
    s.command = s.command.model_copy(update={"main_commit": head})
    old, new = ctx.state.snapshot, await ctx.scan(head)
    rec.delta, rec.next_snapshot = main_delta(old, new, head, "merge"), new
    suite, publish = s.ws.run_tests, ctx.publish
    pending, second, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    drains = 0

    async def gated(payload):
        if isinstance(payload, GraphDelta) and payload.cause == "merge":
            pending.set()
            await release.wait()
        return await publish(payload)

    async def late_pending(tree, **kwargs):
        assert drains == 1 and rec.agent_id not in ctx.state.pending
        ctx.state.pending[rec.agent_id] = rec
        rec.sending = ctx.spawn(_delivery.deliver(ctx, rec.delta))
        rec.sending.add_done_callback(partial(_delivery.receipt, ctx, rec, "delta"))
        await pending.wait()
        return await suite(tree, **kwargs)

    async def drain(ctx):
        nonlocal drains
        drains += 1
        if drains == 2:
            assert not rec.sending.done() and ctx.state.snapshot is old
            second.set()
        await _delivery.drain_pending(ctx)

    monkeypatch.setattr(ctx, "publish", gated)
    monkeypatch.setattr(s.ws, "run_tests", late_pending)
    s.service.drain_pending = drain
    task = asyncio.create_task(s.run())
    try:
        await asyncio.wait_for(second.wait(), 10)
        assert ctx.state.snapshot is old and not task.done()
        release.set()
        result = await task
        delta = json.loads(s.events[-1].envelope)["payload"]
        assert drains == 2 and delta["from_version"] == new.version
        assert delta["from_version"] != old.version
        assert s.manager.snapshot.commit == result.revert_commit
    finally:
        release.set()
        await task
        ctx.state.pending.clear()
