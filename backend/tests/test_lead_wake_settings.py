"""Wakes extend the person-owned run snapshot without a configured maximum."""

from pathlib import Path
from typing import Any

import pytest

from oversight.app import LiveRun
from oversight.settings import REPO_ROOT, Settings, read_env_file, resolve_lead_wakes


def test_wakes_default_zero_unbounded_and_invalid_values(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("OVERSIGHT_LEAD_WAKES", raising=False)
    config = tmp_path / "nonsecret-config"
    config.write_text("OVERSIGHT_LEAD_WAKES=0\n")
    assert Settings().lead_wakes == resolve_lead_wakes({}) == 3
    assert Settings.load(config).lead_wakes == 0
    monkeypatch.setenv("OVERSIGHT_LEAD_WAKES", "10000000000")
    assert Settings.load(config).lead_wakes == 10**10
    assert resolve_lead_wakes({"OVERSIGHT_LEAD_WAKES": "1" + "0" * 5000}) == 10**5000
    for raw in ("-1", "+1", " 1", "1 ", "1.0", "abc", "٢"):
        monkeypatch.setenv("OVERSIGHT_LEAD_WAKES", raw)
        with pytest.raises(ValueError, match="OVERSIGHT_LEAD_WAKES"):
            Settings.load(config)
    invalid: list[Any] = [True, False, "1", 1.0, -1]
    for value in invalid:
        with pytest.raises(ValueError, match="OVERSIGHT_LEAD_WAKES"):
            Settings(lead_wakes=value)


def test_an_empty_wakes_value_uses_the_default(tmp_path: Path, monkeypatch):
    assert resolve_lead_wakes({"OVERSIGHT_LEAD_WAKES": ""}) == 3
    config = tmp_path / "nonsecret-config"
    config.write_text("OVERSIGHT_LEAD_WAKES=\n")
    monkeypatch.delenv("OVERSIGHT_LEAD_WAKES", raising=False)
    assert Settings.load(config).lead_wakes == 3
    monkeypatch.setenv("OVERSIGHT_LEAD_WAKES", "")
    assert Settings.load(config).lead_wakes == 3
    assert read_env_file(REPO_ROOT / ".env.example")["OVERSIGHT_LEAD_WAKES"] == ""


def test_wakes_resolved_once_with_run_policy(tmp_path: Path, monkeypatch):
    settings = Settings(runtime_dir=tmp_path / ".runtime", lead_wakes=7)
    live = LiveRun(settings)
    policy = live.leads.policy
    assert policy.limits is live._lead_limits and policy.wakes == 7
    monkeypatch.setenv("OVERSIGHT_LEAD_WAKES", "0")
    monkeypatch.setenv("OVERSIGHT_RUNTIME_DIR", str(tmp_path / "other" / ".runtime"))
    other = LiveRun(Settings.load(tmp_path / "absent"))
    assert other.leads.policy.wakes == 0
    assert live.leads.policy is policy and policy.wakes == 7
