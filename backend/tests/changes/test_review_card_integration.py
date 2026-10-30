"""A scripted author, real captured measurements and F1 transport/accounting; no provider IO."""

import asyncio
import json
import shutil
from unittest.mock import Mock

import pytest
from decision_client_helpers import FakeApi, body, client, ok
from live_helpers import Scripts, live_server, live_settings
from test_behavior_live import PRICING, run, script
from test_review_card_lifecycle import settled

from oversight import runtime_decisions
from oversight.agents import validate_answering
from oversight.events import EventStore, play, read_store
from oversight_contracts.decisions import ReviewCardDecisionRef

pytestmark = pytest.mark.anyio


def final_cards(event):
    return event.type == "change.facts_recorded" and any(
        p.startswith("review cards: finished;") for p in event.payload.problems
    )


def classified_nodes(job, first, final, choice):
    if choice != "asked_for":
        assert not job.asked_for
        return {card.node_id for card in final.review_cards}
    assert final.review_cards == [] and job.asked_for
    for entry in job.asked_for:
        assert any(
            entry.node_id == node.node_id
            and (entry.differing_count, entry.compared_count)
            == (node.differing_count, node.compared_count)
            for node in [*first.behavior_diff, *first.measured_cone]
        )
    assert "asked for:" in final.summary[2]
    assert "0 questions" in final.summary[2]
    return {entry.node_id for entry in job.asked_for}


@pytest.mark.parametrize("choice", ["cannot_tell", "asked_for"])
async def test_measured_callers_and_edited_nodes_replay_from_sqlite_without_workspace(
    tmp_path,
    monkeypatch,
    choice,
):
    api = FakeApi(*(ok(body(json.dumps({"choice": choice}))) for _ in range(8)))
    monkeypatch.setattr(runtime_decisions, "configured_decision", lambda _: client(api))
    settings = live_settings(
        tmp_path,
        behavior_diff=True,
        measured_cone=True,
        baseten_api_key="test",
        baseten_budget_usd=10,
        baseten_input_usd_per_m=1,
        baseten_output_usd_per_m=2,
    )
    edits = [
        (PRICING, "return base - base * percent // 100", "return base - base * percent // 100 + 1")
    ]
    async with asyncio.timeout(90), live_server(settings, Scripts([script(edits)])) as s:
        rec = await run(s)
        await s.until(final_cards)
        events = s.events()
        final = next(event for event in events if final_cards(event))
        first = next(event for event in events if event.type == "change.facts_recorded")
        assert first.payload.review_cards == []
        assert first.payload.behavior_diff == final.payload.behavior_diff
        assert first.payload.measured_cone == final.payload.measured_cone
        direct = {node.node_id for node in first.payload.behavior_diff}
        callers = {
            node.node_id for node in first.payload.measured_cone if node.outcome == "changed"
        }
        refs = []
        for card in final.payload.review_cards:
            assert card.evidence_seq == first.seq
            question = "Oversight could not tell whether this was asked for"
            if card.node_id in direct:
                node = next(n for n in first.payload.behavior_diff if n.node_id == card.node_id)
                question += (
                    f" Returns a different value, for {node.differing_count} "
                    f"of {node.differing_count} differing inputs."
                )
            assert card.question == question
            ref = ReviewCardDecisionRef(
                kind="review_card",
                change_id=final.payload.change_id,
                revision=final.payload.revision,
                head_commit=final.payload.head_commit,
                evidence_seq=card.evidence_seq,
                node_id=card.node_id,
                question=card.question,
            )
            validate_answering(ref, events[: final.seq], run_id=s.live.run_id)
            refs.append(ref)
        assert s.live._manager is not None and s.live._manager._ctx.cards is not None
        [job] = s.live._manager._ctx.cards.jobs.values()
        nodes = classified_nodes(job, first.payload, final.payload, choice)
        assert nodes & direct and nodes & (callers - direct)
        ticks = [
            event.payload
            for event in events
            if event.type == "usage.tick" and event.payload.role == "review"
        ]
        assert len(ticks) == len(api.bodies) == len(nodes)
        assert all(t.change_id == rec.change_id and t.cost_usd is not None for t in ticks)
        assert s.live.difference_decisions.accounting.ledger.read().spent_usd == pytest.approx(
            sum(t.cost_usd for t in ticks)
        )
        resumed = []
        async with s.client.stream(
            "GET", "/events", headers={"Last-Event-ID": str(first.seq)}
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    event = json.loads(line[5:])
                    resumed.append(event)
                    if event["seq"] == final.seq:
                        break
        expected = events[: final.seq]
        assert resumed == [event.model_dump(mode="json") for event in expected[first.seq :]]
        source, run_id = s.store.path, s.live.run_id
    await replay_without_workspace(
        tmp_path, settings, source, run_id, expected, refs, first.seq, monkeypatch
    )


async def replay_without_workspace(
    tmp_path, settings, source, run_id, expected, refs, after, monkeypatch
):
    recorded = tmp_path / "recorded.sqlite"
    shutil.copyfile(source, recorded)
    shutil.rmtree(settings.runtime_dir)
    forbidden = Mock(side_effect=AssertionError("replay must never classify"))
    monkeypatch.setattr(runtime_decisions, "configured_decision", forbidden)
    rows = read_store(recorded)[: len(expected)]
    from pydantic import TypeAdapter

    from oversight_contracts.events import ServerEvent

    adapter = TypeAdapter(ServerEvent)
    replay_events = [adapter.validate_json(row.envelope) for row in rows]
    async with EventStore(tmp_path / "replay.sqlite", run_id, mode="replay") as replay:
        await play(replay, replay_events, speed=0)
    reconnect = [row async for row in replay.subscribe(after=after)]
    assert [row.envelope for row in reconnect] == [row.envelope for row in rows[after:]]
    for ref in refs:
        validate_answering(ref, replay_events, run_id=run_id)
    assert replay_events == expected and not forbidden.called


@pytest.mark.parametrize(
    "reply,expected",
    [
        ('{"choice":"asked_for"}', "finished"),
        ("invalid JSON", "invalid_response"),
    ],
)
async def test_f1_invalid_json_is_unavailable_and_asked_for_never_approves(
    tmp_path,
    monkeypatch,
    reply,
    expected,
):
    api = FakeApi(ok(body(reply)))
    monkeypatch.setattr(runtime_decisions, "configured_decision", lambda _: client(api))
    settings = live_settings(
        tmp_path,
        behavior_diff=True,
        baseten_api_key="test",
        baseten_budget_usd=10,
        baseten_input_usd_per_m=1,
        baseten_output_usd_per_m=2,
    )
    edits = [(PRICING, "if quantity >= minimum:", "if quantity > minimum:")]
    async with asyncio.timeout(90), live_server(settings, Scripts([script(edits)])) as s:
        rec = await run(s)
        await s.until(final_cards)
        final = next(event.payload for event in s.events() if final_cards(event))
        assert final.review_cards == [] and any(expected in problem for problem in final.problems)
        assert rec.state == "ready" and not any(e.type == "change.resolved" for e in s.events())
        assert len(api.bodies) == 1
        assert (
            len([e for e in s.events() if e.type == "usage.tick" and e.payload.role == "review"])
            == 1
        )


async def test_f2_reuses_f1_fail_closed_budget_admission(tmp_path, monkeypatch):
    forbidden = Mock(side_effect=AssertionError("unconfigured budget must not create a client"))
    monkeypatch.setattr(runtime_decisions, "configured_decision", forbidden)
    settings = live_settings(tmp_path, behavior_diff=True)
    edits = [(PRICING, "if quantity >= minimum:", "if quantity > minimum:")]
    async with asyncio.timeout(90), live_server(settings, Scripts([script(edits)])) as s:
        await run(s)
        await s.until(final_cards)
        final = next(event.payload for event in s.events() if final_cards(event))
        assert final.review_cards == []
        assert any("budget_unconfigured" in problem for problem in final.problems)
        assert not forbidden.called


async def test_f1_queue_wait_uses_the_f2_call_deadline(tmp_path, monkeypatch):
    forbidden = Mock(side_effect=AssertionError("queued deadline must precede transport creation"))
    monkeypatch.setattr(runtime_decisions, "configured_decision", forbidden)
    settings = live_settings(
        tmp_path,
        behavior_diff=True,
        review_card_call_s=0.05,
        review_card_total_s=1,
    )
    edits = [(PRICING, "if quantity >= minimum:", "if quantity > minimum:")]
    async with asyncio.timeout(90), live_server(settings, Scripts([script(edits)])) as s:
        async with s.live.difference_decisions._lock:
            await run(s)
            await s.until(final_cards)
        final = next(event.payload for event in s.events() if final_cards(event))
        assert final.review_cards == [] and any("deadline" in p for p in final.problems)
        assert not forbidden.called
        assert not any(e.type == "usage.tick" and e.payload.role == "review" for e in s.events())


async def test_cancelling_f2_keeps_the_real_f1_receipt_and_usage(tmp_path, monkeypatch):
    api = FakeApi(ok(body('{"choice":"cannot_tell"}')))
    monkeypatch.setattr(runtime_decisions, "configured_decision", lambda _: client(api))
    settings = live_settings(
        tmp_path,
        behavior_diff=True,
        baseten_api_key="test",
        baseten_budget_usd=10,
        baseten_input_usd_per_m=1,
        baseten_output_usd_per_m=2,
    )
    edits = [(PRICING, "if quantity >= minimum:", "if quantity > minimum:")]
    entered, release = asyncio.Event(), asyncio.Event()
    async with asyncio.timeout(90), live_server(settings, Scripts([script(edits)])) as s:
        accounting = s.live.difference_decisions.accounting
        original = accounting.record

        async def held(input, reply):
            entered.set()
            await release.wait()
            await original(input, reply)

        monkeypatch.setattr(accounting, "record", held)
        rec = await run(s)
        await entered.wait()
        rec.person_intervened()
        release.set()
        assert s.live._manager is not None
        await settled(s.live._manager._ctx.cards)
        facts = [e.payload for e in s.events() if e.type == "change.facts_recorded"]
        assert len(facts) == 1 and facts[0].review_cards == []
        ticks = [
            e.payload for e in s.events() if e.type == "usage.tick" and e.payload.role == "review"
        ]
        assert len(api.bodies) == len(ticks) == 1
        assert accounting.ledger.read().spent_usd == pytest.approx(ticks[0].cost_usd)
