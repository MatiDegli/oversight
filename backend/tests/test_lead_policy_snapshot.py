"""A run captures the person-owned lead configuration once, before its services."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from oversight import app
from oversight.settings import Settings, build_lead_limits


def test_one_snapshot_per_run_survives_later_configuration(tmp_path: Path, monkeypatch):
    settings = Settings(
        runtime_dir=tmp_path / "first" / ".runtime",
        max_leads=9,
        lead_max_turns=7,
        lead_max_tool_calls=2,
        lead_max_model_calls=4,
        lead_max_wall_clock_s=10,
        lead_max_plans=5,
        lead_max_subtasks=8,
        lead_max_task_chars=3000,
        lead_history_chars=48000,
    )
    build = Mock(wraps=build_lead_limits)
    monkeypatch.setattr(app, "build_lead_limits", build)
    live = app.LiveRun(settings)
    build.assert_called_once_with(settings)
    snapshot = live._lead_limits
    assert snapshot == build_lead_limits(settings)
    assert live._max_leads == 9 and live._lead_history_chars == 48000
    monkeypatch.setenv("OVERSIGHT_MAX_LEADS", "1")
    monkeypatch.setenv("OVERSIGHT_LEAD_MAX_SUBTASKS", "2")
    monkeypatch.setenv("OVERSIGHT_LEAD_HISTORY_CHARS", "1")
    monkeypatch.setenv("OVERSIGHT_RUNTIME_DIR", str(tmp_path / "later" / ".runtime"))
    later = Settings.load(tmp_path / "absent")
    other = app.LiveRun(later)
    assert build.call_count == 2
    assert other._max_leads == 1 and other._lead_limits.subtasks == 2
    assert other._lead_history_chars == 1
    assert live._lead_limits is snapshot and snapshot.subtasks == 8
    assert live._max_leads == 9 and live._lead_history_chars == 48000
    # The lead snapshot never replaces the separate coding-agent test deadlines.
    assert live._limits.test_timeout_s == 60 and live._limits.max_wall_clock_s == 600
    with pytest.raises(ValueError, match="frozen"):
        snapshot.subtasks = 1
