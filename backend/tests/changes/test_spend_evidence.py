"""Durable Meta truth and owner-scoped committed provenance at all four readers."""

from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import TypeAdapter
from test_assessment import latest, make_clock, propose, rec, settled, terminals, wait
from test_mode import available, select
from test_policy_profiles import policy_inputs
from test_rules_transaction import configure_transaction

from oversight.agents import LedgerFile, LedgerState, UnknownUsage
from oversight.app import LiveRun
from oversight.changes import RulesEvidence, SpendSnapshot, _rules, _transaction
from oversight_contracts.events import ChangeResolved, ServerEvent, UsageTick
from oversight_contracts.policy import EvidenceRef

pytestmark = pytest.mark.anyio
UNKNOWN = UnknownUsage("run", "agent-2", "model", "2026-10-31T12:00:00Z")


def spend_rule(assessment):
    return next(r for r in assessment.rules if r.rule == "known_spend")


async def receipt(h, total=900):
    stored = await h.publish(
        UsageTick(
            agent_id="agent-2",
            change_id="change-2",
            role="agent",
            model="meta-test",
            input_tokens=1,
            output_tokens=1,
            cached_tokens=0,
            cost_usd=0.01,
            spent_usd=total,
            budget_usd=1000,
            brake="ok",
        )
    )
    h.manager.spend_received(stored)
    return stored


async def seed_receipt(h):
    from oversight_contracts.events import Budget, Limits, RunStarted

    run_id = h.store.run_id
    h.manager._ctx.grants.run_id = run_id
    seed = await h.publish(
        RunStarted(
            run_id=run_id,
            contract_version="1.8.0",
            prompt_version="test",
            workspace="demo-repo",
            base_commit=h.base_commit,
            models=[],
            slots=[],
            limits=Limits(
                max_steps=1,
                max_output_tokens=1,
                max_wall_clock_s=1,
                test_timeout_s=1,
                cone_max_depth=1,
                cone_max_nodes=1,
            ),
            budget=Budget(budget_usd=1, spent_usd=0, warn_at=[0.5, 0.8], block_at=0.9),
        )
    )
    h.manager.spend_received(seed)
    return seed


def complete_inputs(collect, w):
    original = collect(w)
    ready = policy_inputs()
    identity = {
        name: getattr(original, name)
        for name in (
            "change_id",
            "agent_id",
            "revision",
            "base_commit",
            "head_commit",
            "main_commit",
            "candidate_tree",
            "mode",
            "mode_revision",
        )
    }
    assert ready.review is not None
    review = ready.review.model_copy(
        update={k: v for k, v in identity.items() if k in type(ready.review).model_fields}
    )
    return replace(
        ready,
        **identity,
        review=review,
        suite=None,
        evidence={
            r: (EvidenceRef(event_seq=w.proposal_seq),)
            for r in (
                "small_change",
                "small_cone",
                "existing_tests_untouched",
                "cross_review",
                "test_integrity",
                "added_tests_fail_before",
            )
        },
    )


@pytest.fixture
async def spend(h, monkeypatch, tmp_path):
    clock = make_clock(h, monkeypatch)
    available(h)
    await select(h, "merge_when_safe", 0)
    ctx = h.manager._ctx
    ledger = LedgerFile(tmp_path / "meta-ledger.json")
    await ledger.load()
    owner: Any = SimpleNamespace(
        ledger=ledger, _settings=SimpleNamespace(budget_usd=1), _ledger_lost=False
    )
    ctx.ports.spend_ledger = ledger
    ctx.ports.spend_lost = lambda: owner._ledger_lost

    def producer(inputs):
        state = owner.ledger.state
        return RulesEvidence(
            replace(
                inputs,
                evidence={**inputs.evidence, "known_spend": (EvidenceRef(event_seq=seed.seq),)},
                spend=SpendSnapshot(
                    spent_usd=state.spent_usd if state is not None else None,
                    budget_usd=owner._settings.budget_usd,
                    unknown_costs=len(state.unknown) if state is not None else None,
                    ledger_ok=owner.ledger.loaded and state is not None and not owner._ledger_lost,
                ),
            )
        )

    ctx.ports.rules_evidence = producer
    unwatch = ledger.watch(h.manager.spend_changed)
    collect = ctx.assessment.inputs

    monkeypatch.setattr(ctx.assessment, "inputs", lambda w: complete_inputs(collect, w))
    seed = await seed_receipt(h)
    # Start with a cached receipt so baseline tests can reach the later guard being tested.
    # Out-of-cache tests explicitly evict it; the production scheduler never caches usage.
    ctx.assessment.events[seed.seq] = TypeAdapter(ServerEvent).validate_json(seed.envelope)
    await propose(h)
    monkeypatch.setattr(h.workspace._isolation, "_born", True)
    monkeypatch.setattr(h.workspace._isolation, "_checked", True)
    decisions = []
    decide = h.manager._decide

    def track(record, work):
        task = decide(record, work)
        decisions.append(task)
        return task

    monkeypatch.setattr(h.manager, "_decide", track)
    try:
        yield SimpleNamespace(
            ctx=ctx, ledger=ledger, owner=owner, seed=seed, clock=clock, decisions=decisions
        )
    finally:
        unwatch()


async def pending(h, spend):
    spend.ctx.ports.rules_admission = lambda *_: None
    h.manager.run_ended("agent-1", None)
    await settled(h)
    assert spend_rule(latest(h)).result == "pass"
    return latest(h)


def reserve(h, spend, assessment):
    reservation = _rules.capture(spend.ctx, wait(h), assessment)
    record = h.manager._take("change-1", frozenset({"ready"}), "test", person=False)
    record.taken = record.revision
    record.rules_consumed.add(record.revision)
    record.evidence = reservation.evidence
    return reservation


async def test_capture_and_revalidate_pin_survives_new_receipt_and_eviction(h, spend):
    assessment = await pending(h, spend)
    old_pin = spend.seed
    spend.ctx.assessment.events.pop(old_pin.seq, None)
    newer = await receipt(h, total=990)
    await settled(h)
    # Capture must not rewrite the persisted assessment with the newer spend receipt.
    wait(h).latest, wait(h).spend_pin = assessment, old_pin
    reservation = reserve(h, spend, assessment)
    assert reservation.spend_pin == spend.seed and newer != spend.seed
    assert spend.seed.seq not in spend.ctx.assessment.events
    assert _rules.revalidate(spend.ctx, reservation) is None
    assert spend_rule(reservation.evidence.assessment) == spend_rule(assessment)
    # Ordinary evidence still expires even while the spend receipt remains pinned.
    spend.ctx.assessment.events.clear()
    failure = _rules.revalidate(spend.ctx, reservation)
    assert failure is not None and failure[0] == "policy_invalid"


@pytest.mark.parametrize("reader", ["capture", "reserved", "definitive"])
@pytest.mark.parametrize(
    "kind", ["foreign", "cached_foreign", "missing", "git", "mixed", "path", "wrong_rule"]
)
async def test_each_reader_rejects_unowned_spend(h, spend, monkeypatch, reader, kind):
    configure_transaction(h, monkeypatch)
    original = await pending(h, spend)
    if kind == "wrong_rule":
        spend.ctx.assessment.events.pop(spend.seed.seq, None)
    bad = (
        EvidenceRef(event_seq=spend.seed.seq + 999)
        if kind in {"foreign", "cached_foreign"}
        else (
            EvidenceRef(git_object="abc")
            if kind == "git"
            else EvidenceRef(event_seq=spend.seed.seq, git_object="abc")
            if kind == "mixed"
            else EvidenceRef(event_seq=spend.seed.seq, path="shop.py")
            if kind == "path"
            else EvidenceRef(event_seq=spend.seed.seq)
        )
    )
    if kind == "cached_foreign":
        spend.ctx.assessment.events[bad.event_seq] = TypeAdapter(ServerEvent).validate_json(
            spend.seed.envelope
        )
    rule_name = "small_change" if kind == "wrong_rule" else "known_spend"

    def corrupt(assessment):
        return assessment.model_copy(
            update={
                "rules": [
                    r.model_copy(update={"evidence": [bad]}) if r.rule == rule_name else r
                    for r in assessment.rules
                ]
            }
        )

    if reader == "capture":
        wait(h).latest = corrupt(original)
        if kind == "missing":
            wait(h).spend_pin = None
        with pytest.raises(ValueError):
            _rules.capture(spend.ctx, wait(h), wait(h).latest)
    elif reader == "reserved":
        reservation = reserve(h, spend, original)
        assessment = corrupt(original)
        reservation = replace(
            reservation, evidence=replace(reservation.evidence, assessment=assessment)
        )
        rec(h).evidence = reservation.evidence
        wait(h).latest = assessment
        if kind == "missing":
            reservation.spend_pin = None
        failure = _rules.revalidate(spend.ctx, reservation)
        assert failure is not None and failure[0] == "policy_invalid"
    else:
        real = _transaction.Transaction.persist

        async def corrupt_final(tx, guard, run):
            await real(tx, guard, run)
            tx.definitive = replace(tx.definitive, assessment=corrupt(tx.definitive.assessment))
            if kind == "missing":
                tx.spend_pin = None

        monkeypatch.setattr(_transaction.Transaction, "persist", corrupt_final)
        spend.ctx.ports.rules_admission = h.manager._admit_rules
        assert h.manager._admit_rules(rec(h), original) is None
        await spend.decisions[0]
        assert await h.main_head() == h.base_commit and not h.payloads(ChangeResolved)
        assert "Definitive evidence" in terminals(h)[0].message


@pytest.mark.parametrize(
    "forged_ref",
    [
        EvidenceRef.model_construct(event_seq=None, git_object=None, path=None),
        EvidenceRef(git_object="fake"),
        EvidenceRef(event_seq=99999),
        EvidenceRef(event_seq=99999, git_object="fake"),
    ],
)
async def test_producer_cannot_mutate_nonspend_or_launder_refs(h, spend, forged_ref):
    producer = spend.ctx.ports.rules_evidence

    def forged(inputs):
        supplied = producer(inputs)
        return RulesEvidence(
            replace(
                supplied.inputs,
                files=None,
                head_commit="foreign",
                evidence={"known_spend": (forged_ref,)},
            )
        )

    spend.ctx.ports.rules_evidence = forged
    await pending(h, spend)
    assert all(r.result == "pass" for r in latest(h).rules if r.rule != "combined_tests")
    assert spend_rule(latest(h)).evidence == [EvidenceRef(event_seq=spend.seed.seq)]
    spend.ctx.assessment.spend_receipt = None
    spend.ctx.assessment.wake(rec(h))
    await settled(h)
    assert spend_rule(latest(h)).result == "unknown" and not spend_rule(latest(h)).evidence


@pytest.mark.parametrize(
    "bad",
    [
        "budget",
        "unknown",
        "threshold",
        "above",
        "lost",
        "unloaded",
        "absent",
        "nan",
        "inf",
        "negative",
    ],
)
@pytest.mark.parametrize("reader", ["stage", "capture", "reserved", "barrier", "definitive"])
async def test_fresh_guards_never_pass_bad_durable_truth(h, spend, monkeypatch, bad, reader):
    configure_transaction(h, monkeypatch)
    assessment = await pending(h, spend)

    def poison():
        if bad == "budget":
            spend.owner._settings.budget_usd = None
        elif bad == "unknown":
            spend.ledger.state = LedgerState(0, (UNKNOWN,))
        elif bad == "lost":
            spend.owner._ledger_lost = True
        elif bad == "unloaded":
            spend.ledger.loaded = False
        elif bad == "absent":
            spend.ledger.state = None
        else:
            spend.ledger.state = LedgerState(
                {
                    "threshold": 0.5,
                    "above": 0.6,
                    "nan": float("nan"),
                    "inf": float("inf"),
                    "negative": -1,
                }[bad]
            )

    if reader == "stage":
        poison()
        spend.ctx.assessment.wake(rec(h))
        await settled(h)
        assert spend_rule(latest(h)).result != "pass"
    elif reader == "capture":
        poison()
        assert h.manager._admit_rules(rec(h), assessment) is not None or bad == "absent"
        assert not rec(h).rules_consumed
    elif reader == "reserved":
        reservation = reserve(h, spend, assessment)
        poison()
        failure = _rules.revalidate(spend.ctx, reservation)
        assert failure is not None and failure[0] == "policy_invalid"
    else:
        method = "barriers" if reader == "barrier" else "persist"
        real = getattr(_transaction.Transaction, method)

        async def poisoned(tx, *args):
            poison()
            await real(tx, *args)

        monkeypatch.setattr(_transaction.Transaction, method, poisoned)
        h.manager._admit_rules(rec(h), assessment)
        await spend.decisions[0]
        assert await h.main_head() == h.base_commit and not h.payloads(ChangeResolved)
        assert terminals(h)


async def test_definitive_uses_fresh_state_and_latest_committed_provenance(h, spend, monkeypatch):
    configure_transaction(h, monkeypatch)
    original = _transaction.Transaction.persist
    forwarded = []

    async def persisted(tx, guard, run):
        spend.ledger.state = LedgerState(0.25)
        forwarded.append(await receipt(h, total=999))
        await original(tx, guard, run)

    monkeypatch.setattr(_transaction.Transaction, "persist", persisted)
    h.manager.run_ended("agent-1", None)
    await settled(h)
    await spend.decisions[0]
    [resolved] = h.payloads(ChangeResolved)
    final = spend_rule(resolved.assessment)
    assert final.observed["spent_usd"] == 0.25
    assert final.evidence == [EvidenceRef(event_seq=forwarded[0].seq)]
    assert spend_rule(latest(h)).evidence == [EvidenceRef(event_seq=spend.seed.seq)]
    assert rec(h).state == "closed" and not terminals(h)
    assert wait(h).spend_pin is None


async def test_producer_is_synchronous_io_free_and_reads_state_once(h, spend, monkeypatch):
    class Once:
        loaded = True
        reads = 0

        @property
        def state(self):
            self.reads += 1
            assert self.reads == 1
            return LedgerState(0.1)

        def read(self):
            pytest.fail("producer did I/O")

        def load(self):
            pytest.fail("producer did I/O")

    ledger = Once()
    spend.owner.ledger = ledger
    result = LiveRun._rules_evidence(spend.owner, policy_inputs())
    assert result.inputs.spend is not None
    assert result.inputs.spend.spent_usd == 0.1 and ledger.reads == 1


async def test_seed_only_positive_budget_can_pass(h, spend):
    seed = spend.seed
    spend.ctx.assessment.events.pop(seed.seq, None)
    assert seed.type == "run.started"
    assert not any(event.type == "usage.tick" for event in h.log)
    await pending(h, spend)
    assert spend_rule(latest(h)).evidence == [EvidenceRef(event_seq=seed.seq)]
    assert seed.seq not in spend.ctx.assessment.events


@pytest.mark.parametrize(
    "budget,loaded,lost,state",
    [
        (None, True, False, LedgerState()),
        (1, False, False, LedgerState()),
        (1, True, True, LedgerState()),
        (1, True, False, None),
        (1, True, False, LedgerState(0, (UNKNOWN,))),
        (1, True, False, LedgerState(float("nan"))),
        (1, True, False, LedgerState(float("inf"))),
        (float("inf"), True, False, LedgerState()),
        (1, True, False, LedgerState(0.5)),
    ],
)
async def test_actual_producer_preserves_unknown_and_unhealthy_truth(budget, loaded, lost, state):
    from oversight.changes._policy import assess

    owner: Any = SimpleNamespace(
        ledger=SimpleNamespace(state=state, loaded=loaded),
        _settings=SimpleNamespace(budget_usd=budget),
        _ledger_lost=lost,
    )
    inputs = replace(policy_inputs(), evidence={"known_spend": (EvidenceRef(event_seq=1),)})
    produced = LiveRun._rules_evidence(owner, inputs).inputs
    assert produced.spend is not None
    assert produced.spend.budget_usd is budget
    assert produced.spend.spent_usd == (state.spent_usd if state else None) or (
        state is not None and state.spent_usd != state.spent_usd
    )
    assert spend_rule(assess(produced)).result != "pass"
