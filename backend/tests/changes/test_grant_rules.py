"""Grants remove one measured unknown, while both gates and live barriers retain authority."""

import asyncio
from dataclasses import replace

import pytest
from decision_helpers import observed_target
from test_assessment import settled
from test_grant_creation import RUN, prepare
from test_grant_creation import request as grant_request
from test_policy_profiles import PROFILE, policy_inputs

from oversight.changes import _candidate, _transaction
from oversight.changes._policy import assess, merges_alone
from oversight.changes._rules import pending_policy
from oversight.events import export_run, read_jsonl
from oversight_contracts.events import ChangeResolved
from oversight_contracts.grants import GrantCreated, GrantRevoked

pytestmark = pytest.mark.anyio


@pytest.fixture
async def granted(h, monkeypatch, request):
    await prepare(h, monkeypatch)
    await h.manager.approve(
        "change-1",
        None,
        grant=getattr(request, "param", grant_request()),
        **observed_target(h.manager, "change-1"),
    )
    monkeypatch.setattr(h.workspace._isolation, "_born", True)
    monkeypatch.setattr(h.workspace._isolation, "_checked", True)
    monkeypatch.setattr(h.workspace._isolation, "select", lambda policy, injected, runner: runner)
    [created] = h.payloads(GrantCreated)
    return created


async def later(h, monkeypatch, *, extra=()):
    decisions = []
    decide = h.manager._decide

    def track(record, work):
        task = decide(record, work)
        decisions.append(task)
        return task

    monkeypatch.setattr(h.manager, "_decide", track)
    await h.run_agent(2, ("shop/mod.py", "return 8", "return 9"), *extra)
    await settled(h)
    return decisions


async def test_later_module_change_is_merged_by_rules_with_unchanged_unknown(
    h, granted, monkeypatch
):
    decisions = await later(h, monkeypatch)
    assert len(decisions) == 1
    await decisions[0]
    resolved = h.payloads(ChangeResolved)[-1]
    assert resolved.change_id == "change-2" and resolved.decided_by == "rules"
    [use] = resolved.grants
    assert use.grant_id == granted.grant_id and use.rule == "small_cone"
    assert use.caller_ids == ["shop.mod:indirect"] and use.caller_count == 1
    assert use.authorization.run_id == RUN and use.authorization.active_at_effect
    assert next(r for r in resolved.assessment.rules if r.rule == "small_cone").result == "unknown"
    await h.manager.revoke_grant(granted.grant_id)
    assert h.manager._agents["agent-2"].cleaned
    path = h.tmp_path / "replay.jsonl"
    export_run(h.tmp_path / "run.sqlite", path)
    replay = read_jsonl(path)
    assert [e.payload for e in replay if e.type == "grant.created"] == [granted]
    assert [e.payload for e in replay if e.type == "grant.revoked"] == [
        GrantRevoked(grant_id=granted.grant_id)
    ]
    assert [e.payload for e in replay if e.type == "change.resolved"][-1] == resolved


@pytest.mark.parametrize("lost", ["revoke", "run"])
async def test_inactive_grants_leave_later_changes_waiting(h, granted, monkeypatch, lost):
    if lost == "revoke":
        await h.manager.revoke_grant(granted.grant_id)
    else:
        h.manager._ctx.grants.run_id = "run-20261026-120001"
    before = await h.main_head()
    decisions = await later(h, monkeypatch)
    assert decisions == [] and await h.main_head() == before
    assert h.manager._agents["agent-2"].state == "ready"
    assert len(h.payloads(ChangeResolved)) == 1


async def test_revoke_during_combined_suite_stops_without_use_and_cannot_revive(
    h, granted, monkeypatch
):
    suite = h.workspace.run_tests

    async def revoked(*args, **kwargs):
        run = await suite(*args, **kwargs)
        if args[0].agent_id is None:
            await h.manager.revoke_grant(granted.grant_id)
        return run

    monkeypatch.setattr(h.workspace, "run_tests", revoked)
    before = await h.main_head()
    decisions = await later(h, monkeypatch)
    assert len(decisions) == 1
    await decisions[0]
    record = h.manager._agents["agent-2"]
    assert record.state == "ready" and await h.main_head() == before
    assert len(h.payloads(ChangeResolved)) == 1 and len(h.payloads(GrantRevoked)) == 1
    assert record.rules_consumed == {1}
    h.manager._ctx.assessment.wake(record)
    await settled(h)
    assert len(decisions) == 1
    monkeypatch.setattr(h.workspace, "run_tests", suite)
    await h.run_agent(3, ("shop/mod.py", "return 8", "return 10"))
    await settled(h)
    await h.manager.approve(
        "change-3", None, grant=grant_request(), **observed_target(h.manager, "change-3")
    )
    assert len(h.payloads(GrantCreated)) == 2
    count = len(decisions)
    h.manager._admit_rules(record, record.evidence.assessment)
    h.manager._ctx.assessment.wake(record)
    await settled(h)
    assert len(decisions) == count and record.state == "ready" and not record.merged


async def test_revoke_serializes_with_the_effect(h, granted, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original = _transaction.Transaction.persist

    async def held(tx, *args):
        await original(tx, *args)
        entered.set()
        await release.wait()

    monkeypatch.setattr(_transaction.Transaction, "persist", held)
    decisions = await later(h, monkeypatch)
    assert len(decisions) == 1
    await entered.wait()
    revoke = asyncio.create_task(h.manager.revoke_grant(granted.grant_id))
    try:
        await asyncio.sleep(0)
        assert not revoke.done() and not h.payloads(GrantRevoked)
    finally:
        release.set()
    await decisions[0]
    await revoke
    assert h.payloads(ChangeResolved)[-1].decided_by == "rules"
    assert len(h.payloads(GrantRevoked)) == 1


@pytest.mark.parametrize("query,tree", [("rev-parse", "agent-2"), ("ls-tree", "main")])
async def test_revoke_during_preflight_or_candidate_queries(h, granted, monkeypatch, query, tree):
    # Arm only after the second proposal, so it pauses the rules transaction.
    ended = h.manager.run_ended

    def arm(*args, **kwargs):
        h.workspace.arm(query, tree=tree, mode="hold")
        ended(*args, **kwargs)

    monkeypatch.setattr(h.manager, "run_ended", arm)
    before = await h.main_head()
    decisions = await later(h, monkeypatch)
    assert len(decisions) == 1
    await h.workspace.entered.wait()
    try:
        await h.manager.revoke_grant(granted.grant_id)
    finally:
        h.workspace.resume.set()
    await decisions[0]
    assert await h.main_head() == before and len(h.payloads(ChangeResolved)) == 1
    assert h.manager._agents["agent-2"].state == "ready"


@pytest.mark.parametrize("gap", ["scope", "parse_errors", "unmeasured"])
async def test_candidate_remeasurement_stops_an_uncovered_wait(h, granted, monkeypatch, gap):
    measure = _candidate.measure

    async def changed(*args):
        measured = await measure(*args)
        if gap == "scope":
            return replace(measured, grants=replace(measured.grants, files=("other.py",)))
        rules = tuple(
            r.model_copy(
                update={
                    "observed": None
                    if gap == "unmeasured"
                    else {
                        **(r.observed or {}),
                        "parse_errors": 1,
                    }
                }
            )
            if r.rule == "small_cone"
            else r
            for r in measured.rules
        )
        return replace(measured, rules=rules)

    monkeypatch.setattr(_candidate, "measure", changed)
    before = await h.main_head()
    decisions = await later(h, monkeypatch)
    assert len(decisions) == 1
    await decisions[0]
    assert await h.main_head() == before and len(h.payloads(ChangeResolved)) == 1
    assert h.manager._agents["agent-2"].state == "ready"


async def test_module_grant_does_not_cover_a_second_production_file(h, granted):
    before = await h.main_head()
    await h.run_agent(
        2,
        ("shop/mod.py", "return 8", "return 9"),
        ("shop/__init__.py", "", "def unrelated():\n    return 0\n"),
    )
    await settled(h)
    assert h.manager._agents["agent-2"].state == "ready" and await h.main_head() == before
    assert len(h.payloads(ChangeResolved)) == 1


@pytest.mark.parametrize("granted", [grant_request(kind="run", value=RUN)], indirect=True)
async def test_run_grant_covers_the_run_across_modules(h, granted, monkeypatch):
    decisions = await later(
        h, monkeypatch, extra=(("shop/__init__.py", "", "def unrelated():\n    return 0\n"),)
    )
    assert len(decisions) == 1
    await decisions[0]
    record = h.manager._agents["agent-2"]
    assert record.state == "closed" and record.resolution.grants[0].grant_id == granted.grant_id


def test_both_gates_still_require_measurements_without_a_grant():
    unknown = assess(replace(policy_inputs(), possible_callers=1), PROFILE)
    assert not merges_alone(unknown) and not pending_policy(unknown)
