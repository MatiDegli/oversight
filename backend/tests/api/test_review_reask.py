"""Formatting correction through the runtime, durable receipts and existing authority gates."""

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from completion_helpers import completion
from openai.types.chat import ChatCompletion

from oversight.agents import MUSE_SPARK, MUSE_SPARK_OPEN, LedgerError, _review_call
from oversight.events import parse_needs_you
from oversight_contracts.events import ModelProfile

from .test_cross_review_lifecycle import events, start, until
from .test_review_accounting import production

pytestmark = pytest.mark.anyio


def response(text: str, profile: ModelProfile = MUSE_SPARK) -> ChatCompletion:
    result = completion().model_dump()
    result["model"] = profile.model
    result["choices"][0]["message"]["content"] = text
    return ChatCompletion.model_validate(result)


@pytest.mark.parametrize(
    "profile,final",
    [
        (MUSE_SPARK, "accept"),
        (MUSE_SPARK_OPEN, "accept"),
        (MUSE_SPARK, "fix"),
        (MUSE_SPARK, "invalid"),
        (MUSE_SPARK_OPEN, "invalid"),
    ],
)
async def test_runtime_publishes_only_final_review_and_preserves_single_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, profile: ModelProfile, final: str
) -> None:
    requests: list[Any] = []
    admissions: list[int] = []
    forwarded: list[int] = []

    async def create(**params: Any) -> ChatCompletion:
        requests.append(params)
        count = len(requests)
        es = events(live)
        reviews = [e for e in es if e.type == "change.reviewed"]
        assert len(reviews) == (count - 1) // 2
        assert not any(e.type == "error" for e in es)
        if count % 2:
            return response("First invalid verdict", profile)
        assert admissions[-1] == count - 1
        ticks = [e for e in es if e.type == "usage.tick" and e.payload.role == "review"]
        assert len(ticks) == count - 1  # durable tick precedes admission and the re-ask
        assert len(params["messages"]) == 3
        assert params["reasoning_effort"] == "low" and "tools" not in params
        text = {
            "accept": "VERDICT: accept",
            "fix": "SHOULD-FIX shop/pricing.py:2: Shorten it.\nVERDICT: request changes",
            "invalid": "NIT missing.py:0: Fix.\nVERDICT: accept",
        }[final]
        return response(text, profile)

    async with production(tmp_path, monkeypatch, create, reviewer_profile=profile) as live:
        real_check = live._check_spending

        def check(slot: Any, selected: ModelProfile) -> None:
            if selected == profile:
                admissions.append(len(requests))
            real_check(slot, selected)

        monkeypatch.setattr(live, "_check_spending", check)
        manager = live._manager
        assert manager is not None
        real_received = manager.rules_received

        def received(stored: Any) -> None:
            if stored.type == "change.reviewed":
                forwarded.append(stored.seq)
            real_received(stored)

        monkeypatch.setattr(manager, "rules_received", received)
        await start(live)
        if final == "accept":
            await until(live, "change.reviewed")
        else:
            await until(live, "error")
        await asyncio.gather(*list(live._runs.values()), *list(live._reviews.values()))
        assert_final_events(live, profile, final, forwarded, len(requests))


def assert_final_events(
    live: Any, profile: ModelProfile, final: str, forwarded: list[int], request_count: int
) -> None:
    manager = live._manager
    assert manager is not None
    es = events(live)
    reviews = [e for e in es if e.type == "change.reviewed"]
    ticks = [e for e in es if e.type == "usage.tick" and e.payload.role == "review"]
    rounds = [e for e in es if e.type == "change.resolved"]
    expected = 2 if final == "fix" else 1
    assert request_count == len(ticks) == expected * 2
    assert len(reviews) == expected and forwarded == [e.seq for e in reviews]
    for index, reviewed in enumerate(reviews):
        assert ticks[index * 2].seq < ticks[index * 2 + 1].seq < reviewed.seq
        assert (
            reviewed.payload.verdict
            == {"accept": "clean", "fix": "changes_requested", "invalid": "unknown"}[final]
        )
    assert all(t.payload.model == profile.model and t.payload.agent_id == "agent-1" for t in ticks)
    assert all(t.payload.change_id == "change-1" and t.payload.input_tokens == 700 for t in ticks)
    assert len(rounds) == (1 if final == "fix" else 0)
    assert manager._agents["agent-1"].review_consumed == (final == "fix")
    if final == "fix":
        assert rounds[0].payload.review_seq == reviews[0].seq
        assert rounds[0].payload.decided_by == "cross_review"
    notes = [parse_needs_you(e.payload.message) for e in es if e.type == "error"]
    assert [n.code for n in notes if n] == {
        "accept": [],
        "fix": ["round_exhausted"],
        "invalid": ["review_invalid"],
    }[final]
    if final == "invalid":
        assert "last non-empty line" not in " ".join(reviews[0].payload.problems)
    if profile.provider == "meta":
        assert live.ledger.read().spent_usd == pytest.approx(0.00009 * expected * 2)
        assert not live.ledger.read().unknown
    else:
        assert all(t.payload.cost_usd is None for t in ticks)


@pytest.mark.parametrize("invalidation", ["delivery_rejected", "review_ready"])
async def test_ineligible_target_drops_reask_without_cancelling_first_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalidation: str
) -> None:
    requests = 0

    async def create(**params: Any) -> ChatCompletion:
        nonlocal requests
        requests += 1
        if requests == 1:
            manager = live._manager
            assert manager is not None
            rec = manager._agents["agent-1"]
            setattr(rec, invalidation, invalidation == "delivery_rejected")
            task = asyncio.current_task()
            assert task is not None and not task.cancelling()
            return response("bad")
        return response("VERDICT: accept")

    async with production(tmp_path, monkeypatch, create) as live:
        await start(live)
        await until(live, "usage.tick", role="review")
        await asyncio.gather(*list(live._reviews.values()))
        es = events(live)
        ticks = [e for e in es if e.type == "usage.tick" and e.payload.role == "review"]
        assert requests == len(ticks) == 1
        assert live.ledger.read().spent_usd == pytest.approx(0.00009)
        assert not live.ledger.read().unknown
        assert not any(e.type in {"change.reviewed", "change.resolved", "error"} for e in es)
        assert live._manager is not None
        assert not live._manager._agents["agent-1"].review_consumed


@pytest.mark.parametrize("brake", ["unknown", "spent"])
async def test_first_receipt_can_refuse_reask_through_existing_spend_invalid_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, brake: str
) -> None:
    requests = 0

    async def create(**params: Any) -> ChatCompletion:
        nonlocal requests
        requests += 1
        result = response("bad")
        if brake == "unknown":
            return result.model_copy(update={"usage": None})
        return result

    async with production(tmp_path, monkeypatch, create) as live:
        if brake == "spent":
            # The first known receipt moves spending above 90% of this small budget.
            monkeypatch.setattr(live, "_settings", replace(live._settings, budget_usd=0.000095))
            live._usage.budget_usd = 0.000095
        await start(live)
        await until(live, "error")
        await asyncio.gather(*list(live._reviews.values()))
        es = events(live)
        [tick] = [e for e in es if e.type == "usage.tick" and e.payload.role == "review"]
        [error] = [e for e in es if e.type == "error"]
        note = parse_needs_you(error.payload.message)
        assert note and note.code == "spend_invalid" and not error.payload.fatal
        assert tick.seq < error.seq and requests == 1
        assert tick.payload.model == MUSE_SPARK.model and tick.payload.agent_id == "agent-1"
        assert tick.payload.cost_usd == (None if brake == "unknown" else pytest.approx(0.00009))
        state = live.ledger.read()
        assert len(state.unknown) == (1 if brake == "unknown" else 0)
        if brake == "unknown":
            assert tick.payload.input_tokens is None and tick.payload.output_tokens is None
            assert state.unknown[0].model == MUSE_SPARK.model
        assert live.brake_reason() is not None
        assert not any(e.type in {"change.reviewed", "change.resolved"} for e in es)


@pytest.mark.parametrize("stage", ["request", "receipt", "ledger", "timeout"])
async def test_reask_cancellation_deadline_and_ledger_failure_drain_without_first_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    requests = 0
    limits: list[asyncio.Timeout] = []
    original_timeout = asyncio.timeout_at

    def timeout_at(deadline: float) -> asyncio.Timeout:
        limit = original_timeout(deadline)
        limits.append(limit)
        return limit

    monkeypatch.setattr(
        _review_call, "asyncio", SimpleNamespace(**(vars(asyncio) | {"timeout_at": timeout_at}))
    )

    async def create(**params: Any) -> ChatCompletion:
        nonlocal requests
        requests += 1
        if requests == 1:
            return response("bad")
        if stage in {"request", "timeout"}:
            entered.set()
            await asyncio.Event().wait()
        return response("VERDICT: accept")

    async with production(tmp_path, monkeypatch, create) as live:
        real_record = live.ledger.record
        writes = 0

        async def record(*args: Any) -> Any:
            nonlocal writes
            writes += 1
            if writes == 2 and stage in {"receipt", "ledger"}:
                entered.set()
                await release.wait()
                if stage == "ledger":
                    raise LedgerError("scripted second receipt failure")
            return await real_record(*args)

        monkeypatch.setattr(live.ledger, "record", record)
        await start(live)
        await entered.wait()
        task = live._reviews["change-1"]
        if stage == "timeout":
            limits[0].reschedule(asyncio.get_running_loop().time())
        else:
            live.stop("agent-1")
        await asyncio.sleep(0)
        if stage in {"receipt", "ledger"}:
            assert not task.done()
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        es = events(live)
        ticks = [e.payload for e in es if e.type == "usage.tick" and e.payload.role == "review"]
        assert requests == writes == 2 and len(limits) == 1
        assert len(ticks) == (1 if stage == "ledger" else 2)
        assert live.ledger.read().spent_usd == pytest.approx(
            0.00009 * (2 if stage == "receipt" else 1)
        )
        assert len(live.ledger.read().unknown) == (1 if stage in {"request", "timeout"} else 0)
        assert not any(e.type == "change.reviewed" for e in es)
        if stage in {"timeout", "ledger"}:
            notes = [parse_needs_you(e.payload.message) for e in es if e.type == "error"]
            assert any(
                n and n.code == ("review_timeout" if stage == "timeout" else "ledger")
                for n in notes
            )
        assert task.cancelled() == (stage != "timeout")
