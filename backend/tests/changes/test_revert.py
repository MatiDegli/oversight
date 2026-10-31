"""Service integration with real F7a primitives, fixed local suites and SQL receipts."""

import asyncio
import json
from dataclasses import replace

import pytest
from changes_revert_helpers import install_spend_evidence as install_spend_evidence
from changes_revert_helpers import pytestmark as pytestmark
from changes_revert_helpers import system as system
from test_revert_preflight import repo as repo

from oversight.changes import (
    Person,
    RevertRefused,
    RevertService,
    _revert,
)
from oversight.events import StoredEvent
from oversight.workspace import apply_inverse
from oversight_contracts.events import ChangeResolvedEvent, RevertResolvedEvent, SuiteResult


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
