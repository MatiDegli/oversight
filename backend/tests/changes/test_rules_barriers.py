"""L48-L52 on constructor-wired rules: real Git/store and deterministic await boundaries."""

import asyncio
import time
from dataclasses import replace

import pytest
import test_rules_transaction as tx_tests
from changes_helpers import assert_main_clean, bounded
from git_attack_helpers import malicious_sub
from test_assessment import head, rec, settled, terminals, wait
from test_git_integrity import (
    monitor_every_server_git,
)
from test_git_integrity import (
    test_agent_suite_to_proposal_capture_regression as agent_capture_regression,
)
from test_merge_integrity import ATTACKS, damage
from test_mode import select
from test_rules_reservation import start, terminal_code

from oversight.changes import WrongState, _approval, _transaction
from oversight.changes._mode import closed_start_capability
from oversight.workspace import IntegrityError, run_process, suite_guard, test_tree
from oversight.workspace.suite import run_suite
from oversight_contracts.events import ChangeResolved, SuiteExecution
from oversight_contracts.policy import ChangeAssessment

pytestmark = pytest.mark.anyio
reserved, transaction = tx_tests.reserved, tx_tests.transaction


def final(payload):
    return isinstance(payload, ChangeAssessment) and all(
        r.observed is not None for r in payload.rules
    )


@pytest.mark.parametrize("boundary", ["suite", "persist", "commit"])
@pytest.mark.parametrize("attack", ATTACKS)
@bounded
async def test_rules_reuses_every_merge_attack(
    h, reserved, transaction, monkeypatch, boundary, attack
):
    publish, suite = h.manager._ctx.publish, h.workspace.run_tests
    injected, lock = False, None
    with (h.workspace.main.root / ".git/info/exclude").open("a") as stream:
        stream.write(".gitmodules\n")

    async def inject():
        nonlocal injected, lock
        injected = True
        lock = await damage(h, attack)
        if attack in {"HEAD", "ORIG_HEAD"}:
            path = "refs/heads/main" if attack == "HEAD" else "ORIG_HEAD"
            (h.workspace.main.root / ".git" / path).write_text(f"{head(h)}\n")
        if boundary == "commit" and attack not in {"unstaged", "untracked", "gitmodules"}:
            # Verify the raw guard independently: later tree/checkout checks must not mask
            # the loss of a metadata/index/lock check in the mutation audit.
            guard = transaction[2][0].guard
            assert guard is not None
            with pytest.raises(IntegrityError):
                await asyncio.to_thread(guard.verify, time.monotonic() + 5)

    async def run(*args, **kwargs):
        result = await suite(*args, **kwargs)
        if boundary == "suite":
            await inject()
        return result

    async def record(payload):
        result = await publish(payload)
        if (
            boundary == "persist"
            and isinstance(payload, SuiteExecution)
            and payload.phase == "combined"
        ) or (boundary == "commit" and final(payload)):
            await inject()
        return result

    monkeypatch.setattr(h.workspace, "run_tests", run)
    monkeypatch.setattr(h.manager._ctx, "publish", record)
    calls = len(h.workspace.calls)
    await tx_tests.execute(h, reserved)
    assert injected and not h.payloads(ChangeResolved)
    assert not any(c.tree == "main" and c.args[0] == "commit" for c in h.workspace.calls[calls:])
    assert terminal_code(h) in {"integrity_invalid", "main_suspect"}
    assert h.workspace.integrity.rules_suspended is (
        attack not in {"unstaged", "untracked", "gitmodules"}
    )
    if lock:
        assert lock.read_text() == "unknown owner\n"
    if attack not in {"HEAD", "HEAD_symbolic"}:
        assert await h.main_head() == h.base_commit
    if boundary == "persist":
        assert len(h.payloads(ChangeAssessment)) == 1, "invalid candidate was persisted"


@pytest.mark.parametrize("boundary", ["admission", "lease", "persist", "tree", "barrier"])
@pytest.mark.parametrize("guard", ["C", "suspended", "receipt", "revision", "startup"])
@bounded
async def test_authority_loss_at_every_await(
    h, reserved, transaction, monkeypatch, boundary, guard
):
    ctx = h.manager._ctx

    def invalidate():
        if guard == "C":
            h.workspace._isolation.invalidate("Scripted execution discriminator failure")
        elif guard == "startup":
            ctx.mode._availability = closed_start_capability
        elif guard == "suspended":
            h.workspace.integrity.rules_suspended = True
        elif guard == "receipt":
            ctx.assessment.events.clear()
        else:
            rec(h).revision += 1

    if boundary == "admission":

        def admit(record, policy):
            invalidate()
            return h.manager._admit_rules(record, policy)

        ctx.ports.rules_admission = admit
        h.manager.run_ended("agent-1", None)
        await settled(h)
        assert not reserved[1] and not rec(h).rules_consumed
        assert not h.payloads(ChangeResolved) and await h.main_head() == h.base_commit
        assert rec(h).state == "ready"
        if guard != "revision":
            assert terminal_code(h) == (
                "isolation_unavailable" if guard in {"C", "startup"} else "policy_invalid"
            )
        else:
            assert not terminals(h)
        await assert_main_clean(h)
        return
    original = {
        "lease": _transaction.Transaction.authorize,
        "persist": _transaction.Transaction.persist,
        "tree": _approval.rules_tree,
        "barrier": _transaction.Transaction.barriers,
    }[boundary]
    # Hold the final mode acquisition without retaining a lease, or pause after an await.
    if boundary == "lease":
        from contextlib import asynccontextmanager

        held = ctx.mode.held

        @asynccontextmanager
        async def acquire():
            invalidate()
            async with held():
                yield

        monkeypatch.setattr(ctx.mode, "held", acquire)
    else:

        async def raced(*args, **kwargs):
            result = await original(*args, **kwargs)
            if boundary == "tree" and not transaction[2]:
                return result  # Exercise final authorization, after ordinary preflight.
            # The barrier is called before persistence and once at the commit boundary.
            if boundary != "barrier" or len(transaction[2]) == 2:
                invalidate()
            return result

        if boundary == "tree":
            monkeypatch.setattr(_approval, "rules_tree", raced)
        else:
            monkeypatch.setattr(
                _transaction.Transaction, boundary if boundary == "persist" else "barriers", raced
            )
    await tx_tests.execute(h, reserved)
    assert not h.payloads(ChangeResolved) and await h.main_head() == h.base_commit
    assert rec(h).state == "ready"
    if guard != "revision":
        assert terminal_code(h) == (
            "isolation_unavailable" if guard in {"C", "startup"} else "policy_invalid"
        )
    else:
        assert not terminals(h)
    await assert_main_clean(h)


@pytest.mark.parametrize("when", ["before", "during"])
@bounded
async def test_disable_serializes_with_final_authorization(
    h, reserved, transaction, monkeypatch, when
):
    entered, release = asyncio.Event(), asyncio.Event()
    ctx = h.manager._ctx
    if when == "during":
        ctx.mode._timeout_s = 0.02
    original = _transaction.Transaction.persist

    async def persist(tx, *args):
        entered.set()
        await release.wait()
        await original(tx, *args)

    if when == "before":
        original_suite = h.workspace.run_tests

        async def suite(*args, **kwargs):
            run = await original_suite(*args, **kwargs)
            await select(h, "ask_me", 1)
            return run

        monkeypatch.setattr(h.workspace, "run_tests", suite)
    else:
        monkeypatch.setattr(_transaction.Transaction, "persist", persist)
    task = await start(h, reserved)
    if when == "during":
        await entered.wait()
        try:
            with pytest.raises(WrongState, match="timed out"):
                await select(h, "ask_me", 1)
            reserved[0].advance(121)
        finally:
            release.set()
    await task
    if when == "during":
        ctx.mode._timeout_s = 5
        await select(h, "ask_me", 1)
        assert len(h.payloads(ChangeResolved)) == 1 and not terminals(h)
    else:
        assert not h.payloads(ChangeResolved) and await h.main_head() == h.base_commit
    await ctx.assessment.expire(wait(h))
    assert len(terminals(h)) <= 1


@pytest.mark.parametrize(
    "query", ["ls-files", "write-tree", "ls-tree", "diff", "yes_no", "capture"]
)
@bounded
async def test_partial_queries_never_authorize(h, reserved, transaction, monkeypatch, query):
    real, captured, hit = h.workspace.git, run_process, False

    async def git(tree, *args, **kwargs):
        nonlocal hit
        result = await real(tree, *args, **kwargs)
        wanted = "rev-parse" if query == "yes_no" else query
        if not hit and tree is h.workspace.main and args[0] == wanted:
            hit = True
            return replace(result, truncated=True)
        return result

    async def capture(*args, **kwargs):
        nonlocal hit
        result = await captured(*args, **kwargs)
        if not hit:
            hit = True
            return replace(result, truncated=True)
        return result

    monkeypatch.setattr(h.workspace, "git", git)
    if query == "capture":
        monkeypatch.setattr(test_tree, "run_process", capture)
    await tx_tests.execute(h, reserved)
    assert hit and not h.payloads(ChangeResolved) and await h.main_head() == h.base_commit
    assert terminal_code(h) == "git_failed"
    await assert_main_clean(h)


@pytest.mark.parametrize("boundary", ["target", "preparation", "capture", "suite", "commit"])
@bounded
async def test_nested_filter_rules_path(h, reserved, transaction, monkeypatch, boundary):
    sentinel = h.workspace.tmp_dir / "rules-filter-ran"
    calls = monitor_every_server_git(h, monkeypatch, sentinel)
    real, publish, git = h.workspace.run_tests, h.manager._ctx.publish, h.workspace.git
    injected = False

    async def inject(tree):
        nonlocal injected
        await malicious_sub(tree.root, sentinel)
        injected = True

    async def suite(tree, **kwargs):
        if boundary == "capture":
            await inject(tree)
        run = await real(tree, **kwargs)
        if boundary == "suite":
            await inject(tree)
        return run

    async def record(payload):
        receipt = await publish(payload)
        if boundary == "commit" and final(payload):
            await inject(h.workspace.main)
        return receipt

    async def prepare(tree, *args, **kwargs):
        if boundary == "preparation" and args[:3] == ("merge", "--no-ff", "--no-commit"):
            await inject(tree)
        return await git(tree, *args, **kwargs)

    monkeypatch.setattr(h.workspace, "run_tests", suite)
    monkeypatch.setattr(h.workspace, "git", prepare)
    monkeypatch.setattr(h.manager._ctx, "publish", record)
    if boundary == "target":
        await inject(rec(h).tree)
    await tx_tests.execute(h, reserved)
    assert injected and not sentinel.exists() and not h.payloads(ChangeResolved)
    assert not any(root == str(h.workspace.main.root) and cmd == "commit" for root, cmd in calls)
    if boundary == "target":
        assert terminal_code(h) == "integrity_invalid"


@pytest.mark.parametrize("attack", ["metadata", "ordinary"])
@bounded
async def test_restoration_suspends_rules_but_ordinary_artifacts_are_local(
    h, reserved, transaction, monkeypatch, attack
):
    real, injected = run_suite, False
    with (h.workspace.main.root / ".git/info/exclude").open("a") as stream:
        stream.write(".coverage\n")
    config = h.workspace.main.root / ".git/config"
    before = config.read_bytes()

    async def suite(**kwargs):
        nonlocal injected
        run = await real(**kwargs)
        if not injected:
            injected = True
            if attack == "metadata":
                config.write_bytes(before + b"\n[poison]\nvalue = changed\n")
            else:
                (h.workspace.main.root / ".coverage").write_text("ordinary artifact\n")
        return run

    monkeypatch.setattr(suite_guard, "run_suite", suite)
    await tx_tests.execute(h, reserved)
    assert injected and not h.payloads(ChangeResolved) and not h.manager.main_suspect
    assert config.read_bytes() == before and await h.main_head() == h.base_commit
    assert h.workspace.isolation.available
    assert h.workspace.integrity.rules_suspended is (attack == "metadata")
    assert terminal_code(h) == "integrity_invalid"
    assert rec(h).state == "ready"
    async with h.workspace.locks(main=True, agent_id="agent-1"):
        run = await h.workspace.run_tests(h.workspace.main, timeout_s=10, isolation="required")
    assert run.result.outcome == "passed"


@pytest.mark.parametrize("tested", [None, "f" * 40, "index_query"])
async def test_tested_candidate_and_index_are_identical(
    h, reserved, transaction, monkeypatch, tested
):
    suite, git, hit = h.workspace.run_tests, h.workspace.git, False

    async def run(*args, **kwargs):
        result = await suite(*args, **kwargs)
        return result if tested == "index_query" else replace(result, tested_tree=tested)

    async def index(tree, *args, **kwargs):
        nonlocal hit
        result = await git(tree, *args, **kwargs)
        if tested == "index_query" and transaction[1] and args == ("write-tree",) and not hit:
            hit = True
            return replace(result, stdout="f" * 40 + "\n")
        return result

    monkeypatch.setattr(h.workspace, "run_tests", run)
    monkeypatch.setattr(h.workspace, "git", index)
    await tx_tests.execute(h, reserved)
    assert not h.payloads(ChangeResolved) and await h.main_head() == h.base_commit
    assert terminal_code(h) == "integrity_invalid"
    await assert_main_clean(h)


async def test_agent_suite_to_capture_with_rules_armed(h, monkeypatch):
    from test_assessment import full_evidence
    from test_mode import available
    from test_policy_profiles import policy_inputs

    available(h)
    await select(h, "merge_when_safe", 0)
    full_evidence(h, policy_inputs())
    await agent_capture_regression(h, monkeypatch)
    assert not h.payloads(ChangeResolved) and not rec(h).rules_consumed


async def test_combined_receipt_loss_prevents_definitive_persistence(
    h, reserved, transaction, monkeypatch
):
    real = h.manager._ctx.publish

    async def publish(payload):
        receipt = await real(payload)
        if isinstance(payload, SuiteExecution) and payload.phase == "combined":
            del h.manager._ctx.assessment.events[receipt.seq]
        return receipt

    monkeypatch.setattr(h.manager._ctx, "publish", publish)
    await tx_tests.execute(h, reserved)
    assert len(h.payloads(ChangeAssessment)) == 1
    assert not h.payloads(ChangeResolved) and await h.main_head() == h.base_commit
    assert terminal_code(h) == "suite_failed"
