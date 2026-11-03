"""All five composed role entries refuse paid construction and environment fallback."""

import asyncio
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import httpx
import httpx2
import pytest
from answering_helpers import origin
from decision_client_helpers import INPUT
from test_nested_composition import event, inputs, session

from oversight import runtime_answering, runtime_review
from oversight.agents import RunEnd, validate_answering
from oversight.api import ApiError
from oversight.events import parse_needs_you
from oversight.nested_entry import parse_config
from oversight.settings import Settings

pytestmark = pytest.mark.anyio


async def test_all_role_seams_have_no_paid_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    argv = inputs(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text("META_API_KEY=environment-sentinel\nBASETEN_API_KEY=environment-sentinel\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("META_API_KEY", "inherited-meta-sentinel")
    monkeypatch.setenv("BASETEN_API_KEY", "inherited-baseten-sentinel")
    loaders = Mock(side_effect=AssertionError("ordinary settings loading"))
    monkeypatch.setattr(Settings, "load", loaders)
    monkeypatch.setattr("oversight.settings.read_env_file", loaders)
    constructors = Mock(side_effect=AssertionError("paid client constructed"))
    for seam in (
        "oversight.app.OpenAIClient",
        "oversight.review_config.OpenAIClient",
        "oversight.decision_config.DecisionClient",
    ):
        monkeypatch.setattr(seam, constructors)
    configurations = Mock(side_effect=AssertionError("unsupported role configured"))
    monkeypatch.setattr("oversight.runtime_review.configured_reviewer", configurations)
    monkeypatch.setattr("oversight.runtime_decisions.configured_decision", configurations)
    transport = Mock(side_effect=AssertionError("provider transport attempted"))
    for module in (httpx, httpx2):
        monkeypatch.setattr(module.AsyncHTTPTransport, "handle_async_request", transport)
        monkeypatch.setattr(module.HTTPTransport, "handle_request", transport)
    async with session(parse_config(argv)) as (app, live, client):
        assert app.state.settings.meta_api_key is app.state.settings.baseten_api_key is None
        for route in ("agents", "leads"):
            for model in ("muse-spark", "muse-spark-open"):
                response = await client.post(
                    f"/{route}", json={"task": "valid task", "model": model}
                )
                assert response.status_code == 422 and "scripted" in response.text
        assert (await client.post("/leads", json={"task": "propose coupon"})).status_code == 201
        await event(live, "lead.status", state="idle")
        assert (await client.post("/agents", json={"task": "implement coupon"})).status_code == 201
        status = await event(live, "agent.status", state="ready_for_review")
        await asyncio.gather(*list(live._runs.values()))
        proposal = next(
            p for p in live.store.committed_events() if p.type == "change.proposed"
        ).payload
        assert live._manager is not None
        capture = await live._manager.capture_review(status["change_id"])
        assert live._manager.review_current(capture)
        # Scheduling still refuses if an independent reviewer configuration is enabled.
        monkeypatch.setattr(live, "_settings", replace(live._settings, cross_review=True))
        runtime_review.facts_ready(live, capture, proposal, author="agent")
        assert not live._review_pending
        await asyncio.gather(*list(live._reviews.values()))
        assert not live._review_pending and not live._reviews
        live._review_pending[proposal.agent_id] = capture, proposal
        runtime_review.run_ended(
            live, proposal.agent_id, RunEnd("ready_for_review", None, 4, "ready")
        )
        assert not live._review_pending and not live._reviews
        with pytest.raises(ApiError, match="Nested role is not supported"):
            await runtime_review.review(
                live, capture, proposal, finished=True, ready_time=asyncio.get_running_loop().time()
            )
        with pytest.raises(ApiError, match="Nested role is not supported"):
            await live.difference_decisions.classify_difference(
                replace(INPUT, target=capture.target, agent_id=proposal.agent_id),
                deadline=asyncio.get_running_loop().time() + 30,
            )
        reference = await origin(live.store)
        validate_answering(reference, live.store.committed_events(), run_id=live.run_id)
        with pytest.raises(ApiError, match="Nested role is not supported"):
            runtime_answering.admit(reference, live.store, policy=live.nested_policy)
        response = await client.post(
            "/agents", json={"task": "valid linked task", "answering": reference.model_dump()}
        )
        assert response.status_code == 409 and "Nested role is not supported" in response.text
    loaders.assert_not_called()
    configurations.assert_not_called()
    constructors.assert_not_called()
    transport.assert_not_called()


@pytest.mark.parametrize("enabled", [False, True])
async def test_nested_review_scheduling_publishes_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    configured = Mock(side_effect=AssertionError("unsupported review configured"))
    monkeypatch.setattr("oversight.runtime_review.configured_reviewer", configured)
    async with session(parse_config(inputs(tmp_path))) as (_, live, client):
        monkeypatch.setattr(live, "_settings", replace(live._settings, cross_review=enabled))
        assert (await client.post("/agents", json={"task": "implement coupon"})).status_code == 201
        status = await event(live, "agent.status", state="ready_for_review")
        await asyncio.gather(*list(live._runs.values()), *list(live._reviews.values()))
        assert not live._review_pending and not live._reviews
        proposal = next(
            p for p in live.store.committed_events() if p.type == "change.proposed"
        ).payload
        reports = [
            e.payload
            for e in live.store.committed_events()
            if e.type == "error" and e.payload.change_id == status["change_id"]
        ]
        assert len(reports) == 1
        refusal = parse_needs_you(reports[0].message)
        assert reports[0].source == "server" and reports[0].agent_id == proposal.agent_id
        assert not reports[0].fatal
        assert refusal is not None and refusal.code == "reviewer_missing"
        assert refusal.revision == proposal.revision and refusal.head == proposal.head_commit
        assert "Nested role is not supported: review" in refusal.reason
        configured.assert_not_called()


@pytest.mark.parametrize("role", ["coder", "lead"])
@pytest.mark.parametrize("model", ["muse-spark", "muse-spark-open"])
async def test_transport_admission_rechecks_role_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, role: str, model: str
) -> None:
    from oversight.app import PROFILES
    from oversight.runtime_lead_turns import LeadTurn
    from oversight_contracts.commands import StartAgent

    async with session(parse_config(inputs(tmp_path))) as (_, live, client):
        clients = Mock(side_effect=AssertionError("client constructed before role recheck"))
        if role == "coder":
            admission = live._admit(StartAgent(task="valid task"))
            monkeypatch.setattr(live._client_factory, "_factory", clients)
            with pytest.raises(ApiError, match="scripted profile"):
                await live._launch(replace(admission, profile=PROFILES[model]))
            assert not list((live.workspace.runtime_root / "worktrees").glob("*"))
        else:
            assert (await client.post("/leads", json={"task": "valid task"})).status_code == 201
            await event(live, "lead.status", state="idle")
            lead = next(iter(live.leads.leads.values()))
            with monkeypatch.context() as changed:
                changed.setattr(lead, "profile", PROFILES[model])
                changed.setattr(live._lead_factory, "_factory", clients)
                with pytest.raises(ApiError, match="scripted profile"):
                    LeadTurn(live.leads, lead).make_client()
        clients.assert_not_called()
