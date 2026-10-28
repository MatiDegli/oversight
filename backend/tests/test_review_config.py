"""Reviewer profiles and scripts are independent of the agents' editing scripts."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from live_helpers import two_fakes

from oversight.agents import (
    FAKE_MODEL,
    FAKE_MODEL_B,
    MUSE_SPARK,
    MUSE_SPARK_OPEN,
    ModelReply,
    OpenAIClient,
    ScriptedModel,
    load_script,
    parse_script,
)
from oversight.app import LiveRun
from oversight.review_config import ReviewerUnavailable, configured_reviewer
from oversight.settings import REPO_ROOT, Settings
from oversight_contracts.common import Slot
from oversight_contracts.events import ModelProfile, RunStartedEvent


@pytest.mark.anyio
@pytest.mark.parametrize(
    "slot,author,reviewer,verdict",
    [
        ("A", FAKE_MODEL, FAKE_MODEL_B, "VERDICT: accept"),
        ("B", FAKE_MODEL_B, FAKE_MODEL, "VERDICT: request changes"),
    ],
)
async def test_opposite_profile_and_unambiguous_reviewer_scripts(
    tmp_path: Path,
    slot: Slot,
    author: ModelProfile,
    reviewer: ModelProfile,
    verdict: str,
) -> None:
    script_a, script_b = tmp_path / "review-a.json", tmp_path / "review-b.json"
    for path, text in ((script_a, "VERDICT: request changes"), (script_b, "VERDICT: accept")):
        path.write_text(json.dumps({"task": "Review", "turns": [{"text": text}]}))
    settings = two_fakes(
        tmp_path,
        agent_model_b="fake-b",
        cross_review=True,
        reviewer_fake_script=script_a,
        reviewer_fake_b_script=script_b,
    )
    client = configured_reviewer(settings, slot, author)
    assert client is not None and client.profile == reviewer
    reply = await client.complete([], [])
    assert reply.text == verdict and reply.model == client.profile.model
    assert configured_reviewer(settings, slot, author) is not client


@pytest.mark.parametrize("profile", [FAKE_MODEL, FAKE_MODEL_B])
def test_fake_parser_and_loader_report_selected_model(
    tmp_path: Path, profile: ModelProfile
) -> None:
    text = '{"task":"review", "turns":[{"text":"VERDICT: accept"}]}'
    path = tmp_path / "script.json"
    path.write_text(text)
    assert parse_script(text, profile).turns[0].model == profile.model
    assert load_script(path, profile).turns[0].model == profile.model


@pytest.mark.parametrize(
    "slot,author,reviewer",
    [
        ("A", MUSE_SPARK, MUSE_SPARK_OPEN),
        ("B", MUSE_SPARK_OPEN, MUSE_SPARK),
    ],
)
def test_paid_client_is_separate_and_uses_reviewer_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    slot: Slot,
    author: ModelProfile,
    reviewer: ModelProfile,
) -> None:
    factory = Mock(spec=OpenAIClient)
    monkeypatch.setattr("oversight.review_config.OpenAIClient", factory)
    settings = Settings(
        cross_review=True,
        agent_model_b="muse-spark-open",
        meta_api_key="test-meta",
        baseten_api_key="test-baseten",
    )
    configured_reviewer(settings, slot, author)
    args, kwargs = factory.call_args
    assert args[0] == reviewer and args[0].reasoning == "low"
    assert kwargs["max_output_tokens"] == 12_288
    assert kwargs["api_key"] == ("test-baseten" if slot == "A" else "test-meta")


@pytest.mark.parametrize(
    "overrides,code",
    [
        ({"agent_model_b": "fake-b", "fake_script_b": None}, "reviewer_missing"),
        ({"agent_model_b": "fake"}, "reviewer_same"),
        ({"agent_model_b": "muse-spark-open"}, "reviewer_credentials"),
        (
            {"agent_model_b": "muse-spark-open", "baseten_api_key": "bad key"},
            "reviewer_credentials",
        ),
        ({"agent_model_b": "fake-b"}, "reviewer_credentials"),
    ],
)
def test_unavailable_reviewer_never_builds_a_paid_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    overrides: dict[str, object],
    code: str,
) -> None:
    factory = Mock(side_effect=AssertionError("No SDK construction expected"))
    monkeypatch.setattr("oversight.review_config.OpenAIClient", factory)
    settings = replace(two_fakes(tmp_path, cross_review=True), **overrides)
    with pytest.raises(ReviewerUnavailable) as caught:
        configured_reviewer(settings, "A", FAKE_MODEL)
    assert caught.value.code == code and not factory.called


def test_fake_reviewer_refused_in_mixed_configuration(tmp_path: Path) -> None:
    settings = two_fakes(
        tmp_path, agent_model="muse-spark", agent_model_b="fake-b", cross_review=True
    )
    with pytest.raises(ReviewerUnavailable) as caught:
        configured_reviewer(settings, "A", MUSE_SPARK)
    assert caught.value.code == "reviewer_credentials"


def test_disabled_returns_before_any_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fail = Mock(side_effect=AssertionError("Cross-review off must do no work"))
    for name in ("offered_profiles", "load_script", "OpenAIClient"):
        monkeypatch.setattr(f"oversight.review_config.{name}", fail)
    monkeypatch.setattr(asyncio, "create_task", fail)
    monkeypatch.setattr(asyncio, "timeout", fail)
    assert configured_reviewer(Settings(agent_model="unknown"), "A", FAKE_MODEL) is None
    assert not fail.called


def test_settings_load_switch_and_reviewer_script_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = tmp_path / "config"
    env.write_text(
        "OVERSIGHT_CROSS_REVIEW=true\nOVERSIGHT_REVIEWER_FAKE_SCRIPT=a.json\n"
        "OVERSIGHT_REVIEWER_FAKE_B_SCRIPT=b.json\n"
    )
    monkeypatch.delenv("OVERSIGHT_CROSS_REVIEW", raising=False)
    monkeypatch.delenv("OVERSIGHT_REVIEWER_FAKE_SCRIPT", raising=False)
    monkeypatch.delenv("OVERSIGHT_REVIEWER_FAKE_B_SCRIPT", raising=False)
    settings = Settings.load(env)
    assert settings.cross_review
    assert settings.reviewer_fake_script == REPO_ROOT / "a.json"
    assert settings.reviewer_fake_b_script == REPO_ROOT / "b.json"
    monkeypatch.setenv("OVERSIGHT_CROSS_REVIEW", "false")
    assert not Settings.load(env).cross_review
    assert not Settings().cross_review


@pytest.mark.anyio
async def test_second_fake_integrates_live_profiles_and_run_started(tmp_path: Path) -> None:
    live = LiveRun(two_fakes(tmp_path, agent_model_b="fake-b"))
    await live.startup()
    try:
        stored = live.store._events[0]  # pyright: ignore[reportPrivateUsage]
        profiles = RunStartedEvent.model_validate_json(stored.envelope).payload.models
        assert [p.model for p in profiles] == [FAKE_MODEL.model, FAKE_MODEL_B.model]
        client = live._default_client(FAKE_MODEL_B, "B")  # pyright: ignore[reportPrivateUsage]
        assert isinstance(client, ScriptedModel)
        reply: ModelReply = await client.complete([], [])
        assert reply.model == FAKE_MODEL_B.model
    finally:
        await live.shutdown()
