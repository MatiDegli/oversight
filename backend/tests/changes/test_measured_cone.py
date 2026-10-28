"""Matching is by test/caller/entry ordinal, and differences need fresh confirmation."""

from dataclasses import replace

import pytest
from changes_helpers import EDIT, Harness

from oversight.changes._measured import MeasuredEvidence, MeasuredSuite, project_measured
from oversight.workspace import CallReport, Invocation, SuiteRun
from oversight_contracts.events import CaseResult, ChangeProposed, SuiteResult
from oversight_contracts.graph import ImpactCone, ImpactEntry
from oversight_contracts.review import ChangeFacts, ChangeTarget

NODE = "shop.mod:twice"
TARGET = ChangeTarget(
    change_id="change-1",
    revision=1,
    base_commit="base",
    head_commit="head",
    main_commit="base",
    candidate_tree="head-tree",
)
CONE = ImpactCone(
    seeds=["shop.mod:value"],
    entries=[ImpactEntry(node_id=NODE, distance=1)],
    edges_followed="exact",
    max_depth=3,
    max_nodes=20,
    omitted_by_depth=0,
    omitted_by_limit=0,
    tests_reaching=1,
)


def invocation(
    result='["value",["int","0x1"]]',
    *,
    ordinal=1,
    test="test.py::test_x",
    inputs: str | None = '["dict",[]]',
    problem=None,
):
    return Invocation(test, NODE, ordinal, inputs, result, problem)


def suite(side, calls, *, complete=True, commit=None):
    run = SuiteRun(
        SuiteResult(
            outcome="passed", passed=1, failed=0, skipped=0, duration_ms=1, failed_tests=[]
        ),
        "",
        cases=(CaseResult(test_id="test.py::test_x", outcome="passed"),),
        problems=(),
        tested_tree=side + "-tree",
        tested_commit=commit or side,
        measured=CallReport(tuple(calls), complete, () if complete else ("partial",)),
    )
    return MeasuredSuite(side, side + "-tree", commit or side, run)


def evidence(before, after, *repeat, problems=()):
    return MeasuredEvidence(
        TARGET,
        CONE,
        ("test.py::test_x",),
        (),
        (suite("base", before), suite("head", after), *repeat),
        problems,
    )


def test_stable_change_and_observed_unchanged_counts_validate():
    before, after = [invocation()], [invocation('["value",["int","0x2"]]')]
    measured = evidence(before, after, suite("base", before), suite("head", after))
    [result] = project_measured(measured, TARGET)
    assert (result.outcome, result.compared_count, result.differing_count) == ("changed", 1, 1)
    [same] = project_measured(evidence(before, before), TARGET)
    assert same.outcome == "unchanged" and same.complete and same.compared_count == 1


def test_unequal_inputs_order_missing_calls_and_tests_never_realign():
    old = [
        invocation(ordinal=1),
        invocation(ordinal=2, inputs='["int","0x2"]'),
        invocation(ordinal=3, test="test.py::test_other"),
    ]
    new = [invocation(inputs='["int","0x2"]'), invocation(ordinal=2), invocation(ordinal=4)]
    [result] = project_measured(evidence(old, new), TARGET)
    assert result.outcome == "not_compared" and result.compared_count == 0
    assert result.unmatched_before == result.unmatched_after == 3
    assert not result.complete and result.not_compared


def test_unsupported_inputs_exception_return_and_incomplete_repeat():
    unsupported = invocation(inputs=None, problem="unsupported value type")
    [result] = project_measured(evidence([unsupported], [unsupported]), TARGET)
    assert result.compared_count == 0 and result.not_compared
    before, after = [invocation()], [invocation('["raised","builtins.ValueError",["tuple",[]]]')]
    [result] = project_measured(evidence(before, after), TARGET)
    assert result.differing_count == 0 and not result.complete
    assert any("repetition" in reason for reason in result.problems)
    [result] = project_measured(
        evidence(before, after, suite("base", before), suite("head", after, complete=False)), TARGET
    )
    assert result.differing_count == 0 and result.outcome == "not_compared"


def test_unstable_result_never_counts_as_stable_difference():
    before, after = [invocation()], [invocation('["value",["int","0x2"]]')]
    [result] = project_measured(
        evidence(
            before,
            after,
            suite("base", before),
            suite("head", [invocation('["value",["int","0x3"]]')]),
        ),
        TARGET,
    )
    assert result.differing_count == 0 and result.rows[0].unstable
    assert not result.complete and result.outcome == "not_compared"


def test_partial_recording_cannot_certify_unchanged_and_wrong_identity_rejected():
    partial = evidence([invocation()], [invocation()])
    partial = replace(
        partial, suites=(suite("base", [invocation()], complete=False), partial.suites[1])
    )
    [result] = project_measured(partial, TARGET)
    assert result.compared_count == 1 and not result.complete and result.outcome == "not_compared"
    for bad in (
        replace(partial, target=TARGET.model_copy(update={"revision": 2})),
        replace(partial, suites=(suite("head", []),)),
        replace(partial, suites=(suite("base", [], commit="foreign"),)),
    ):
        with pytest.raises(ValueError):
            project_measured(bad, TARGET)


@pytest.mark.anyio
async def test_real_chain_publishes_each_retained_caller_and_validates_evidence(h: Harness):
    await h.workspace.check_isolation()
    h.manager._ctx.measured_cone = True
    await h.open(1)
    assert await h.edit(
        1,
        "shop/mod.py",
        "return value() + value()",
        "return value() + value()\n\ndef absorbs():\n    return twice() > 0",
    )
    assert await h.edit(
        1, "tests/test_mod.py", "from shop.mod import value", "from shop.mod import value, absorbs"
    )
    assert await h.edit(
        1, "tests/test_mod.py", "assert value() > 0", "assert value() > 0\n    assert absorbs()"
    )
    # First revision introduces the chain; second changes the central function.
    assert await h.finish(1)
    from decision_helpers import observed_target

    await h.manager.approve("change-1", None, **observed_target(h.manager, "change-1"))
    await h.open(2)
    assert await h.edit(2, *EDIT)
    assert await h.finish(2)
    facts = h.payloads(ChangeFacts)[-1]
    proposal = h.payloads(ChangeProposed)[-1]
    assert facts.measured_cone is not None
    measured = {item.node_id: item for item in facts.measured_cone}
    assert measured[NODE].outcome == "changed" and measured[NODE].differing_count == 1
    assert measured["shop.mod:absorbs"].outcome == "unchanged"
    assert set(measured) == {entry.node_id for entry in proposal.impact.entries}
    assert not list(h.workspace.tmp_dir.glob("base-probe-*"))
    assert not list(h.workspace.tmp_dir.glob("*.calls*"))
    h.events()  # every stored event validates as ServerEvent


def test_repeat_with_added_calls_cannot_certify_unchanged():
    initial = [invocation()]
    [result] = project_measured(
        evidence(
            initial,
            initial,
            suite("base", initial),
            suite("head", [*initial, invocation(ordinal=2)]),
        ),
        TARGET,
    )
    assert result.outcome == "not_compared" and not result.complete


def test_rows_cap_counts_all_stable_differences_and_keeps_static_limits():
    old = [invocation(ordinal=i) for i in range(1, 49)]
    new = [invocation('["value",["int","0x2"]]', ordinal=i) for i in range(1, 49)]
    measured = evidence(old, new, suite("base", old), suite("head", new))
    [caller] = project_measured(measured, TARGET)
    assert caller.differing_count == caller.compared_count == 48 and len(caller.rows) == 8
    assert measured.impact == CONE


@pytest.mark.parametrize("depth,count", [(1, 2), (2, 1), (10, 2)])
def test_deep_cycle_and_fanout_project_only_retained_static_entries(depth, count):
    from oversight.graph import change_cone, change_graph, parse_version

    source = b"""def seed(): return 1

def a(): return seed() + b(False)
def b(run=True): return a() if run else 0
def outer(): return b()
def fan1(): return seed()
def fan2(): return seed()
def fan3(): return seed()
"""
    base = parse_version({"app.py": source}, "base")
    head = parse_version({"app.py": source.replace(b"return 1", b"return 2")}, "head")
    cone = change_cone(change_graph(base, head), head.graph, max_depth=depth, max_nodes=count)
    measured = replace(evidence([], [], problems=("no recording",)), impact=cone)
    results = project_measured(measured, TARGET)
    assert [row.node_id for row in results] == [entry.node_id for entry in cone.entries]
    assert len(results) <= count and all(entry.distance <= depth for entry in cone.entries)
    assert cone.omitted_by_limit > 0
    assert cone.omitted_by_depth == (2 if depth == 1 else 1 if depth == 2 else 0)
    assert measured.impact == cone and all(r.outcome == "not_compared" for r in results)


async def prepared(h):
    from oversight_contracts.review import ChangeTarget

    await h.open(1)
    assert await h.edit(
        1, "tests/test_mod.py", "from shop.mod import value", "from shop.mod import value, twice"
    )
    assert await h.edit(1, "tests/test_mod.py", "assert value() > 0", "assert twice() > 0")
    assert await h.edit(1, *EDIT)
    assert await h.finish(1)
    facts = h.payloads(ChangeFacts)[-1]
    target = ChangeTarget.model_validate(facts.model_dump(include=set(ChangeTarget.model_fields)))
    return (
        h.manager._ctx,
        h.manager._agents["agent-1"],
        target,
        h.payloads(ChangeProposed)[-1].impact,
    )


@pytest.mark.anyio
async def test_absent_isolation_records_reasons_without_spawning(h, monkeypatch):
    from unittest.mock import AsyncMock

    from oversight.changes._measured import measure_cone

    ctx, rec, target, impact = await prepared(h)
    runner = AsyncMock(side_effect=AssertionError("no open-network fallback"))
    monkeypatch.setattr(h.workspace, "run_tests", runner)
    async with h.workspace.locks(main=True, agent_id=rec.agent_id):
        measured = await measure_cone(ctx, rec, target=target, impact=impact)
    [result] = project_measured(measured, target)
    assert result.outcome == "not_compared" and "isolation unavailable" in result.problems
    runner.assert_not_awaited()
    assert not list(h.workspace.tmp_dir.glob("base-probe-*"))


@pytest.mark.anyio
@pytest.mark.parametrize("cancel", [True, False])
async def test_cancel_or_shared_timeout_cleans_checkout_and_keeps_incomplete_evidence(
    h, monkeypatch, cancel
):
    import asyncio

    from oversight.changes._measured import measure_cone

    ctx, rec, target, impact = await prepared(h)
    await h.workspace.check_isolation()
    rec.task = replace(rec.task, limits=rec.task.limits.model_copy(update={"test_timeout_s": 1}))
    entered = asyncio.Event()
    runner = h.workspace.run_tests
    timeouts = []

    async def delayed(tree, **kwargs):
        assert kwargs["isolation"] == "required"
        assert h.workspace.operations.holds(main=True, agent_id=rec.agent_id)
        timeouts.append(kwargs["timeout_s"])
        entered.set()
        await asyncio.Future()
        return await runner(tree, **kwargs)

    monkeypatch.setattr(h.workspace, "run_tests", delayed)

    async def measure():
        async with h.workspace.locks(main=True, agent_id=rec.agent_id):
            return await measure_cone(ctx, rec, target=target, impact=impact)

    task = asyncio.create_task(measure())
    await asyncio.wait_for(entered.wait(), 5)
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        measured = rec.measured_cones[target.revision]
    else:
        measured = await task
    [result] = project_measured(measured, target)
    assert not result.complete and result.outcome == "not_compared" and result.problems
    assert 0 < timeouts[0] < 2
    assert not list(h.workspace.tmp_dir.glob("base-probe-*"))
    assert not list(h.workspace.tmp_dir.glob("*.calls*"))


@pytest.mark.anyio
async def test_published_measurements_reject_forged_projection_revision_and_execution(h):
    from oversight.changes._facts import FactsEvidence, build_change_facts, validate_change_facts
    from oversight.changes._summary_capture import capture_summary

    await h.workspace.check_isolation()
    h.manager._ctx.measured_cone = True
    _ctx, rec, target, impact = await prepared(h)
    facts = h.payloads(ChangeFacts)[-1]
    captured = FactsEvidence(
        rec.agent_id,
        target,
        rec.declared,
        tuple(facts.edited_paths),
        tuple(facts.integrity),
        tuple(facts.problems),
        rec.executions,
        rec.suite_runs,
        added_tests=tuple(facts.added_tests),
        summary_evidence=capture_summary(rec, added_test_ids=()),
        measured=rec.measured_cones[target.revision],
        impact=impact,
    )
    validate_change_facts(facts, captured)
    assert facts.summary == build_change_facts(replace(captured, measured=None)).summary
    assert facts.summary[-1] == "Other results: not compared; cost unknown"
    assert facts.measured_cone and facts.measured_cone[0].outcome == "changed"
    for update in (
        {"measured_cone": None},
        {"measured_cone": [facts.measured_cone[0].model_copy(update={"compared_count": 999})]},
    ):
        with pytest.raises(ValueError, match="caller measurements"):
            validate_change_facts(facts.model_copy(update=update), captured)
    measured = captured.measured
    assert measured is not None
    for corrupt in (
        replace(measured, target=target.model_copy(update={"revision": 42})),
        replace(measured, suites=(replace(measured.suites[0], seq=None),)),
        replace(measured, impact=impact.model_copy(update={"entries": []})),
    ):
        with pytest.raises(ValueError):
            validate_change_facts(facts, replace(captured, measured=corrupt))
    from decision_helpers import observed_target

    await h.manager.request_changes(
        rec.change_id, "Revise", **observed_target(h.manager, rec.change_id)
    )
    assert await h.edit(1, "shop/mod.py", "return 3", "return (1)")
    assert await h.finish(1)
    revised = h.payloads(ChangeFacts)[-1]
    assert revised.revision == 2 and revised.head_commit != target.head_commit
    assert revised.measured_cone and revised.measured_cone[0].outcome == "unchanged"
    assert rec.measured_cones[1] == measured
    assert rec.measured_cones[2].target.head_commit == revised.head_commit
    h.events()


@pytest.mark.anyio
async def test_broken_recording_keeps_reason_and_captured_git_identity(h, monkeypatch):
    from oversight.changes._measured import measure_cone
    from oversight.workspace import CallReport

    ctx, rec, target, impact = await prepared(h)
    await h.workspace.check_isolation()
    broken = CallReport(problems=("caller report unavailable (JSONDecodeError)",))
    monkeypatch.setattr("oversight.workspace.suite.read_calls", lambda *args: broken)
    async with h.workspace.locks(main=True, agent_id=rec.agent_id):
        measured = await measure_cone(ctx, rec, target=target, impact=impact)
    [result] = project_measured(measured, target)
    assert result.outcome == "not_compared" and not result.complete
    assert "caller report unavailable (JSONDecodeError)" in result.problems
    assert measured.suites[0].implementation == rec.base_commit
    assert measured.suites[1].commit == target.head_commit
    assert measured.suites[1].tree == target.candidate_tree
    assert measured.overlay == ("pyproject.toml", "tests/test_mod.py")
    assert not list(h.workspace.tmp_dir.glob("base-probe-*"))
    assert not list(h.workspace.tmp_dir.glob("*.calls*"))


@pytest.mark.anyio
async def test_real_process_variation_is_unstable_and_not_a_stable_difference(h):
    current = await h.workspace.main.read_text("shop/mod.py")
    await h.manager.save_main(
        "shop/mod.py",
        "import os\n"
        + current.content.replace("return value() + value()", "return value() + os.getpid()"),
        current.hash,
    )
    await h.workspace.check_isolation()
    h.manager._ctx.measured_cone = True
    await prepared(h)
    facts = h.payloads(ChangeFacts)[-1]
    assert facts.measured_cone
    [result] = facts.measured_cone
    assert result.compared_count == 1 and result.differing_count == 0
    assert result.outcome == "not_compared" and result.rows[0].unstable and not result.complete
    measured = h.manager._agents["agent-1"].measured_cones[1]
    assert len(measured.suites) == 4
    assert (
        measured.suites[0].run.measured.calls[0].result
        != measured.suites[2].run.measured.calls[0].result
    )
