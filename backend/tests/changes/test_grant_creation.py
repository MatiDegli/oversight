"""Personal creation validates before taking a change; rejected commands have no effects."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest
from changes_helpers import EDIT
from decision_helpers import observed_target
from test_assessment import full_evidence, latest, make_clock, rec, settled, wait
from test_mode import available, select
from test_policy_profiles import policy_inputs

from oversight.changes import GrantNotFound, InvalidGrant, WrongState
from oversight.graph import possible_callers
from oversight_contracts.events import ChangeResolved, ErrorReport
from oversight_contracts.grants import GrantCreated, GrantRequest, GrantRevoked
from oversight_contracts.policy import MergeModeState

pytestmark = pytest.mark.anyio
RUN = "run-20261026-120000"


def request(kind="module", value="shop.mod", reason="possible_callers", expires="run"):
    return GrantRequest.model_validate(
        dict(reason=reason, scope=dict(kind=kind, value=value, expires=expires))
    )


async def prepare(h, monkeypatch):
    make_clock(h, monkeypatch)
    h.manager._ctx.grants.run_id = RUN
    content = await h.manager.read_file(None, "shop/mod.py")
    await h.manager.save_main(
        "shop/mod.py",
        content.content + "\n\nclass Utility:\n    def helper(self):\n        return 7\n"
        "\ndef indirect(obj):\n    return obj.helper()\n",
        content.hash,
    )
    h.base_commit = await h.main_head()
    available(h)
    await select(h, "merge_when_safe", 0)

    def measured(inputs):
        current = h.manager._ctx.assessment.waits[inputs.change_id]
        proposal = current.proposal
        return replace(
            inputs,
            cone=proposal.impact,
            possible_callers=len(possible_callers(current.rec.head.graph, proposal.impact)),
        )

    full_evidence(h, policy_inputs(), measured)
    await h.run_agent(1, ("shop/mod.py", "return 7", "return 8"))
    await settled(h)
    assert next(r for r in latest(h).rules if r.rule == "small_cone").result == "unknown"


@pytest.fixture
async def possible(h, monkeypatch):
    await prepare(h, monkeypatch)


@pytest.mark.parametrize(
    "grant",
    [
        request(reason="protected_path"),
        request(kind="path"),
        request(kind="agent"),
        request(expires="session"),
        request(value="shop.other"),
        request(kind="run", value="other"),
    ],
)
async def test_invalid_grants_publish_nothing_and_do_not_take(h, possible, grant):
    before, record = list(h.log), rec(h)
    with pytest.raises(InvalidGrant):
        h.manager.approve("change-1", None, grant=grant, **observed_target(h.manager, "change-1"))
    assert h.log == before and record.state == "ready" and not record.person_acted
    assert not h.payloads(GrantCreated)


async def test_ask_me_refuses_grants_without_taking(h):
    await h.run_agent(1, EDIT)
    before = list(h.log)
    with pytest.raises(InvalidGrant):
        h.manager.approve(
            "change-1", None, grant=request(), **observed_target(h.manager, "change-1")
        )
    assert h.log == before and rec(h).state == "ready"


@pytest.mark.parametrize(
    "gap", ["nodes", "parse_errors", "unresolved_removed", "missing", "pass", "fail"]
)
async def test_other_cone_gaps_are_refused(h, possible, gap):
    assessment = latest(h)
    cone = next(r for r in assessment.rules if r.rule == "small_cone")
    if gap in {"pass", "fail"}:
        changed = cone.model_copy(update={"result": gap})
    else:
        observed = None if gap == "missing" else {**cone.observed, gap: 6}
        changed = cone.model_copy(update={"observed": observed})
    wait(h).latest = assessment.model_copy(
        update={"rules": [changed if r.rule == "small_cone" else r for r in assessment.rules]}
    )
    before = list(h.log)
    with pytest.raises(InvalidGrant):
        h.manager.approve(
            "change-1", None, grant=request(), **observed_target(h.manager, "change-1")
        )
    assert h.log == before and rec(h).state == "ready" and not rec(h).person_acted


async def test_unmeasured_callers_are_not_a_waivable_wait(h, possible):
    wait(h).possible_callers = None
    before = list(h.log)
    with pytest.raises(InvalidGrant):
        h.manager.approve(
            "change-1", None, grant=request(), **observed_target(h.manager, "change-1")
        )
    assert h.log == before and rec(h).state == "ready"


async def test_failed_creation_does_not_merge_and_expired_grant_cannot_be_revoked(h, possible):
    h.fail_once = GrantCreated
    before = await h.main_head()
    await h.manager.approve(
        "change-1", None, grant=request(), **observed_target(h.manager, "change-1")
    )
    assert await h.main_head() == before and not h.payloads(ChangeResolved)
    [error] = h.payloads(ErrorReport)
    assert error.source == "server" and not error.fatal
    assert error.change_id == "change-1" and error.agent_id == rec(h).agent_id
    assert "Grant creation failed" in error.message and "publish failure" in error.message
    assert rec(h).state == "ready" and not h.payloads(GrantCreated)
    await h.manager.approve(
        "change-1", None, grant=request(), **observed_target(h.manager, "change-1")
    )
    [created] = h.payloads(GrantCreated)
    h.manager._ctx.grants.run_id = "run-20261026-120001"
    with pytest.raises(WrongState, match="expired"):
        await h.manager.revoke_grant(created.grant_id)
    assert not h.payloads(GrantRevoked)


async def test_creation_rechecks_mode_after_waiting_for_its_lock(h, possible, monkeypatch):
    mode = h.manager._ctx.mode
    waiting = asyncio.Event()
    original = mode.held

    @asynccontextmanager
    async def queued():
        waiting.set()
        async with original():
            yield

    monkeypatch.setattr(mode, "held", queued)
    h.gate = MergeModeState
    before = await h.main_head()
    disabling = asyncio.create_task(select(h, "ask_me", 1))
    approving = None
    try:
        async with asyncio.timeout(5):
            await h.at_gate.wait()
            assert mode.snapshot().mode == "merge_when_safe"
            approving = h.manager.approve(
                "change-1", None, grant=request(), **observed_target(h.manager, "change-1")
            )
            await waiting.wait()
    finally:
        h.open_gate.set()
        await disabling
        if approving is not None:
            await approving
    assert mode.snapshot().mode == "ask_me"
    assert not h.payloads(GrantCreated) and not h.payloads(ChangeResolved)
    assert await h.main_head() == before and rec(h).state == "ready"
    [error] = h.payloads(ErrorReport)
    assert "merge_when_safe" in error.message and not error.fatal


@pytest.mark.parametrize("scope", [request(), request(kind="run", value=RUN)])
async def test_personal_approval_creates_before_merge_and_revoke_is_final(h, possible, scope):
    await h.manager.approve("change-1", None, grant=scope, **observed_target(h.manager, "change-1"))
    [created] = h.payloads(GrantCreated)
    [resolved] = h.payloads(ChangeResolved)
    assert (
        resolved.decided_by == "person" and resolved.merge == "merged" and resolved.grants is None
    )
    assert created.scope == scope.scope and created.run_id == RUN
    kinds = [event.type for event in h.log]
    assert kinds.index("grant.created") < kinds.index("change.resolved")
    with pytest.raises(GrantNotFound):
        await h.manager.revoke_grant("missing")
    await h.manager.revoke_grant(created.grant_id)
    assert h.payloads(GrantRevoked) == [GrantRevoked(grant_id=created.grant_id)]
    with pytest.raises(WrongState):
        await h.manager.revoke_grant(created.grant_id)
