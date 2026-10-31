"""Live path authority uses exact committed sources and remeasures the prepared tree."""

import asyncio
from dataclasses import replace

import pytest
from changes_helpers import assert_main_clean
from test_assessment import (
    head,
    latest,
    make_clock,
    propose,
    rec,
    settled,
    terminals,
    wait,
)
from test_grant_creation import RUN
from test_grant_creation import request as grant_request
from test_grant_rules import granted as granted
from test_grant_rules import later
from test_mode import available, select
from test_rules_transaction import configure_transaction, execute
from test_rules_transaction import reserved as reserved

from oversight.changes import ChangeManager, InvalidGrant, _candidate, _rules
from oversight.changes._policy import assess
from oversight.events import parse_needs_you
from oversight.workspace import MergeGuard
from oversight_contracts.events import ChangeResolved, FileStat
from oversight_contracts.policy import ChangeAssessment, EvidenceRef, MergeModeState

pytestmark = pytest.mark.anyio


def path_rule(assessment):
    return next(r for r in assessment.rules if r.rule == "protected_paths")


@pytest.fixture
async def captured(h, monkeypatch):
    clock = make_clock(h, monkeypatch)
    available(h)
    await select(h, "merge_when_safe", 0)
    await propose(h)
    assert h.manager._ctx.assessment.path_sources(wait(h)) is not None
    return clock


@pytest.mark.parametrize(
    "gap",
    [
        "wrong_change",
        "wrong_agent",
        "wrong_revision",
        "wrong_base",
        "wrong_head",
        "missing_proposal",
        "evicted_run",
        "missing_capture",
        "incomplete",
        "malformed",
        "unknown_status",
        "wrong_receipt",
        "wrong_cached_proposal",
    ],
)
async def test_path_sources_require_committed_exact_proposal(h, captured, gap):
    scheduler, w = h.manager._ctx.assessment, wait(h)
    snapshot, inventory = w.paths
    if gap.startswith("wrong_") and gap.removeprefix("wrong_") in {
        "change",
        "agent",
        "revision",
        "base",
        "head",
    }:
        field = {
            "change": "change_id",
            "agent": "agent_id",
            "revision": "revision",
            "base": "base_commit",
            "head": "head_commit",
        }[gap.removeprefix("wrong_")]
        w.proposal = w.proposal.model_copy(update={field: 2 if field == "revision" else "other"})
    elif gap == "missing_proposal":
        del scheduler.events[w.proposal_seq]
    elif gap == "evicted_run":
        del scheduler.events[scheduler.run_started_seq]
    elif gap == "missing_capture":
        w.paths = None
    elif gap == "incomplete":
        w.paths = snapshot, replace(inventory, complete=False)
    elif gap in {"malformed", "unknown_status"}:
        broken = FileStat.model_construct(
            path="../uv.lock" if gap == "malformed" else "shop/mod.py",
            status="modified" if gap == "malformed" else "renamed",
            lines_added=0,
            lines_removed=0,
        )
        # Distrust even constructed/mutated models at this internal boundary.
        w.proposal.files[:] = [broken]
        scheduler.events[w.proposal_seq].payload.files[:] = [broken]
        w.paths = snapshot, replace(inventory, files=(broken,))
    elif gap == "wrong_receipt":
        w.paths = snapshot, replace(inventory, evidence=(EvidenceRef(event_seq=9999),))
    else:
        scheduler.events[w.proposal_seq] = scheduler.events[w.proposal_seq].model_copy(
            update={"payload": w.proposal.model_copy(update={"summary": "different"})}
        )
    item = path_rule(assess(scheduler.inputs(w), "safe-merge-paths-v1"))
    assert item.result == "unknown" and item.observed is None
    assert not _rules.paths_confirmed(
        h.manager._ctx, w, assess(scheduler.inputs(w), "safe-merge-paths-v1")
    )
    assert not h.payloads(ChangeResolved) and await h.main_head() == h.base_commit


@pytest.mark.parametrize(
    "inventory,expected",
    [
        ([], "pass"),
        ([("image.bin", "added")], "pass"),
        ([("old.txt", "deleted"), ("uv.lock", "added")], "fail"),
        ([("uv.lock", "deleted"), ("new.txt", "added")], "fail"),
    ],
)
async def test_committed_empty_binary_and_rename_sources(h, captured, inventory, expected):
    scheduler, w = h.manager._ctx.assessment, wait(h)
    proposal = w.proposal.model_copy(
        update={
            "files": [
                FileStat(path=path, status=status, lines_added=0, lines_removed=0)
                for path, status in inventory
            ]
        }
    )
    await h.manager._ctx.publish(proposal)
    scheduler.proposed(rec(h), proposal, w.mode, armed=True)
    inputs = scheduler.inputs(wait(h))
    assert inputs.changed_files is not None and inputs.changed_files.complete
    assert path_rule(assess(inputs, "safe-merge-paths-v1")).result == expected
    assert (
        path_rule(assess(replace(inputs, changed_files=None), "safe-merge-paths-v1")).result
        == "unknown"
    )


@pytest.mark.parametrize("gap", ["missing", "evicted"])
async def test_path_capture_cancel_timeout_restart_fail_closed(h, captured, gap):
    scheduler = h.manager._ctx.assessment
    if gap == "missing":
        wait(h).paths = None
    else:
        del scheduler.events[wait(h).proposal_seq]
    h.manager.run_ended("agent-1", None)
    await settled(h)
    assert path_rule(latest(h)).result == "unknown" and not terminals(h)
    deadline = wait(h).deadline
    captured.advance(deadline)
    await wait(h).timer
    note = parse_needs_you(terminals(h)[0].message)
    assert note is not None and note.code == "policy_invalid"
    scheduler.wake(rec(h))
    await scheduler.expire(wait(h))
    assert len(terminals(h)) == 1 and rec(h).state == "ready"
    assert not h.payloads(ChangeResolved) and await h.main_head() == h.base_commit
    fresh = ChangeManager(
        h.workspace,
        h.publish,
        main_graph=h.main_graph,
        main_snapshot=h.manager.snapshot,
        run_id="new-run",
    )
    assert fresh.merge_mode() == MergeModeState()
    assert not fresh._ctx.assessment.waits and not fresh._ctx.grants.entries
    await fresh.aclose()


@pytest.mark.parametrize("outcome", ["hit", "incomplete", "truncated", "cancel"])
async def test_candidate_protected_inventory_is_remeasured(h, reserved, monkeypatch, outcome):
    configure_transaction(h, monkeypatch)
    original = _candidate.changed_inventory

    async def inventory(ctx, guard, seq):
        measured = await original(ctx, guard, seq)
        if outcome == "cancel":
            raise asyncio.CancelledError
        if outcome == "truncated":
            return replace(measured, files=(), complete=False)
        return replace(
            measured,
            files=(
                *measured.files,
                FileStat(
                    path="packages/app/uv.lock",
                    status="added",
                    lines_added=0,
                    lines_removed=0,
                ),
            ),
            complete=outcome != "incomplete",
        )

    monkeypatch.setattr(_candidate, "changed_inventory", inventory)
    if outcome == "cancel":
        with pytest.raises(asyncio.CancelledError):
            await execute(h, reserved)
        note = parse_needs_you(terminals(h)[0].message)
        assert note is not None and note.code == "cancelled"
    else:
        await execute(h, reserved)
        pending, final = h.payloads(ChangeAssessment)
        assert path_rule(pending).result == "pass"
        assert path_rule(final).result == ("fail" if outcome == "hit" else "unknown")
        assert final.policy_version == "safe-merge-paths-v1" and len(final.rules) == 9
        if outcome == "hit":
            assert EvidenceRef(git_object=final.candidate_tree) in path_rule(final).evidence
        assert not terminals(h)
    assert rec(h).state == "ready" and not h.payloads(ChangeResolved)
    assert await h.main_head() == h.base_commit
    await assert_main_clean(h)


@pytest.mark.parametrize("granted", [grant_request(kind="run", value=RUN)], indirect=True)
async def test_protected_hit_with_grant_still_waits(h, granted, monkeypatch):
    before = await h.main_head()
    decisions = await later(
        h,
        monkeypatch,
        extra=(
            (
                "pyproject.toml",
                "[tool.pytest.ini_options]",
                "# updated\n[tool.pytest.ini_options]",
            ),
        ),
    )
    record = h.manager._agents["agent-2"]
    w = h.manager._ctx.assessment.waits["change-2"]
    assert not decisions and record.state == "ready"
    assert path_rule(w.latest).result == "fail"
    assert next(r for r in w.latest.rules if r.rule == "small_cone").result == "unknown"
    uses = h.manager._ctx.grants.uses(h.manager._ctx, w.latest, w.grant_measurement)
    assert len(uses) == 1 and uses[0].rule == "small_cone" and uses[0].grant_id == granted.grant_id
    events = list(h.log)
    with pytest.raises(InvalidGrant):
        h.manager.approve(
            "change-2",
            None,
            revision=record.revision,
            head_commit=record.head_commit,
            grant=grant_request(reason="protected_path"),
        )
    assert h.log == events and await h.main_head() == before


@pytest.mark.parametrize("lost", ["table", "proposal"])
async def test_paths_and_spend_pins_survive_only_their_valid_receipts(
    h, reserved, monkeypatch, lost
):
    transaction = configure_transaction(h, monkeypatch)
    original = h.manager._ctx.publish

    async def publish(payload):
        receipt = await original(payload)
        if isinstance(payload, ChangeAssessment) and all(r.result == "pass" for r in payload.rules):
            scheduler = h.manager._ctx.assessment
            pin = transaction[2][0].spend_pin
            assert pin is not None and pin.seq not in scheduler.events
            seq = scheduler.run_started_seq if lost == "table" else wait(h).proposal_seq
            del scheduler.events[seq]
        return receipt

    h.manager._ctx.publish = publish
    await execute(h, reserved)
    assert rec(h).state == "ready" and await h.main_head() == h.base_commit
    assert not h.payloads(ChangeResolved) and len(terminals(h)) == 1
    await assert_main_clean(h)


@pytest.mark.parametrize("path", [".github", ".github/workflows/link"])
async def test_gitlink_at_protected_path_is_never_authorized(h, captured, path):
    ctx, tree = h.manager._ctx, h.workspace.main
    async with h.workspace.locks(main=True):
        await ctx.git(tree, "update-index", "--add", "--cacheinfo", f"160000,{head(h)},{path}")
        candidate = (await ctx.git(tree, "write-tree")).strip()
        guard = MergeGuard(tree.git_identity, h.base_commit, head(h), candidate, b"")
        inventory = await _candidate.changed_inventory(ctx, guard, wait(h).proposal_seq)
        inputs = replace(ctx.assessment.inputs(wait(h)), changed_files=inventory)
        item = path_rule(assess(inputs, "safe-merge-paths-v1"))
        assert item.result == "fail" and item.observed == {"violations": 1, "complete": True}
        await ctx.git(tree, "read-tree", "HEAD")
    assert await h.main_head() == h.base_commit and not h.payloads(ChangeResolved)
