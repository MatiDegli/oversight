"""HTTP decisions and SQLite replay use accepted human answers, without provider IO."""

import json

import pytest
from decision_client_helpers import FakeApi, body, client, ok
from live_helpers import Scripts, error_code, live_server, live_settings
from test_behavior_live import PRICING, run, script
from test_review_card_integration import final_cards, replay_without_workspace

from oversight import runtime_decisions

pytestmark = pytest.mark.anyio


@pytest.fixture
async def proposed_cards(tmp_path, monkeypatch):
    api = FakeApi(*(ok(body('{"choice":"cannot_tell"}')) for _ in range(8)))
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
    async with live_server(settings, Scripts([script(edits)])) as server:
        rec = await run(server)
        await server.until(final_cards)
        facts = next(event.payload for event in server.events() if final_cards(event))
        assert facts.review_cards
        yield server, rec, facts


@pytest.mark.parametrize(
    "verb,value", [("approve", "yes"), ("request-changes", "no"), ("reject", "unsure")]
)
async def test_http_records_decision_answers(proposed_cards, verb, value):
    server, rec, facts = proposed_cards
    answers = [
        card.model_dump(mode="json") | {"answer": value, "answered_by": "person"}
        for card in facts.review_cards
    ]
    response = await server.client.post(
        f"/changes/{rec.change_id}/{verb}",
        json={
            "revision": facts.revision,
            "head_commit": facts.head_commit,
            "comment": "Review",
            "answers": list(reversed(answers)),
        },
    )
    assert response.status_code == 202, response.text
    await server.until(lambda e: e.type == "change.resolved")
    resolved = next(e.payload for e in server.events() if e.type == "change.resolved")
    assert [card.model_dump(mode="json") for card in resolved.answers] == answers
    assert resolved.decided_by == "person"


async def test_http_invalid_principal_names_card_without_publishing(proposed_cards):
    server, rec, facts = proposed_cards
    card = facts.review_cards[0]
    before = server.events()
    response = await server.client.post(
        f"/changes/{rec.change_id}/reject",
        json={
            "revision": facts.revision,
            "head_commit": facts.head_commit,
            "answers": [card.model_dump(mode="json") | {"answer": "yes"}],
        },
    )
    assert error_code(response) == (422, "invalid_request")
    message = response.json()["error"]["message"]
    assert card.node_id in message and card.question in message
    assert server.events() == before and rec.state == "ready"


async def test_http_invalid_card_and_open_approve_are_distinct_refusals(proposed_cards):
    server, rec, facts = proposed_cards
    before = server.events()
    target = {"revision": facts.revision, "head_commit": facts.head_commit}
    response = await server.client.post(f"/changes/{rec.change_id}/approve", json=target)
    assert error_code(response) == (409, "wrong_state")
    card = facts.review_cards[0]
    forged = card.model_dump(mode="json") | {
        "answer": "yes",
        "answered_by": "person",
        "evidence_seq": 999999,
    }
    response = await server.client.post(
        f"/changes/{rec.change_id}/reject", json=target | {"answers": [forged]}
    )
    assert error_code(response) == (422, "invalid_request")
    assert card.node_id in response.text and card.question in response.text
    assert server.events() == before and rec.state == "ready"


async def test_recorded_answers_replay_without_worktree_or_decision_client(tmp_path, monkeypatch):
    api = FakeApi(*(ok(body('{"choice":"cannot_tell"}')) for _ in range(8)))
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
    async with live_server(settings, Scripts([script(edits)])) as server:
        rec = await run(server)
        await server.until(final_cards)
        facts = next(e.payload for e in server.events() if final_cards(e))
        answers = [
            card.model_dump(mode="json") | {"answer": "yes", "answered_by": "person"}
            for card in facts.review_cards
        ]
        assert answers
        response = await server.client.post(
            f"/changes/{rec.change_id}/approve",
            json={"revision": facts.revision, "head_commit": facts.head_commit, "answers": answers},
        )
        assert response.status_code == 202
        await server.until(lambda e: e.type == "change.resolved")
        expected = server.events()
        resolved = next(e for e in expected if e.type == "change.resolved")
        assert json.loads(resolved.model_dump_json())["payload"]["answers"] == answers
        source, run_id = server.store.path, server.live.run_id
    await replay_without_workspace(tmp_path, settings, source, run_id, expected, [], 0, monkeypatch)
