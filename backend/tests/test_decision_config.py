"""Decision settings never select author/reviewer profiles or a scripted fake."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from oversight.agents import MUSE_SPARK_OPEN, DecisionClient, DecisionUnavailable
from oversight.decision_config import configured_decision
from oversight.settings import Settings


def test_dedicated_profile_and_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = Mock(spec=DecisionClient)
    monkeypatch.setattr("oversight.decision_config.DecisionClient", factory)
    configured_decision(Settings(baseten_api_key="test", review_model="muse-spark"))
    args, kwargs = factory.call_args
    assert args[0] == MUSE_SPARK_OPEN and args[0].reasoning == "low"
    assert kwargs["base_url"] == "https://inference.baseten.co/v1"
    assert kwargs["api_key"] == "test"


@pytest.mark.parametrize("model", ["fake", "fake-b", "muse-spark", "unknown"])
def test_settings_cannot_select_another_profile(model: str) -> None:
    with pytest.raises(DecisionUnavailable, match="profile"):
        configured_decision(Settings(decision_model=model, baseten_api_key="test"))


@pytest.mark.parametrize("key", [None, "", "bad key"])
def test_requires_own_credential(key: str | None) -> None:
    with pytest.raises(DecisionUnavailable, match="credentials"):
        configured_decision(Settings(baseten_api_key=key, meta_api_key="test-meta"))


@pytest.mark.parametrize("amount", ["", "0", "-1", "NaN", "inf", "bad", "2.5"])
def test_loads_without_an_invented_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    amount: str,
) -> None:
    env = tmp_path / "config"
    env.write_text(
        f"OVERSIGHT_DECISION_MODEL=muse-spark-open\nOVERSIGHT_BASETEN_BUDGET_USD={amount}"
    )
    monkeypatch.delenv("OVERSIGHT_DECISION_MODEL", raising=False)
    monkeypatch.delenv("OVERSIGHT_BASETEN_BUDGET_USD", raising=False)
    settings = Settings.load(env)
    assert settings.decision_model == "muse-spark-open"
    assert settings.baseten_budget_usd == (2.5 if amount == "2.5" else None)
    monkeypatch.setenv("OVERSIGHT_DECISION_MODEL", "fake")
    assert Settings.load(env).decision_model == "fake"
