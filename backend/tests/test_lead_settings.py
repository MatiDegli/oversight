"""Person-owned lead settings have defaults, strict types and no policy maximum."""

from pathlib import Path
from typing import Any

import pytest

from oversight.settings import REPO_ROOT, Settings, build_lead_limits, read_env_file

SETTINGS = [
    ("max_leads", 6),
    ("lead_max_turns", 50),
    ("lead_max_tool_calls", 12),
    ("lead_max_model_calls", 18),
    ("lead_max_wall_clock_s", 300),
    ("lead_max_plans", 3),
    ("lead_max_subtasks", 6),
    ("lead_max_task_chars", 2000),
    ("lead_history_chars", 24000),
]


@pytest.mark.parametrize("field,default", SETTINGS)
@pytest.mark.parametrize("value", [None, "", "1", "0007", "99999", "10000000000"])
def test_defaults_values_and_precedence(tmp_path: Path, monkeypatch, field, default, value):
    name = f"OVERSIGHT_{field.upper()}"
    monkeypatch.delenv(name, raising=False)
    config = tmp_path / "config"
    config.write_text("" if value is None else f"{name}={value}\n")
    expected = int(value) if value else default
    assert getattr(Settings.load(config), field) == expected
    assert getattr(Settings(), field) == default
    monkeypatch.setenv(name, "42")
    assert getattr(Settings.load(config), field) == 42
    monkeypatch.setenv(name, "")
    assert getattr(Settings.load(config), field) == default


@pytest.mark.parametrize("field,default", SETTINGS)
@pytest.mark.parametrize("source", ["file", "environment"])
@pytest.mark.parametrize(
    "value", ["0", "000", "-1", "+2", "1.5", " 2", "2 ", "\t", "٢", "\uff12", "abc"]
)
def test_invalid_values(tmp_path: Path, monkeypatch, field, default, source, value):
    name = f"OVERSIGHT_{field.upper()}"
    monkeypatch.delenv(name, raising=False)
    config = tmp_path / "config"
    config.write_text(f'{name}="{value}"\n' if source == "file" else "")
    if source == "environment":
        monkeypatch.setenv(name, value)
    with pytest.raises(ValueError) as exc:
        Settings.load(config)
    assert str(exc.value) == f"{name} must be a positive whole number"


@pytest.mark.parametrize("field,default", SETTINGS)
@pytest.mark.parametrize("value", [True, False, 1.0, "1", 0, -1])
def test_direct_construction(field: str, default: int, value: Any) -> None:
    with pytest.raises(ValueError) as exc:
        Settings(**{field: value})
    assert str(exc.value) == f"OVERSIGHT_{field.upper()} must be a positive whole number"


@pytest.mark.parametrize("field,default", SETTINGS)
def test_no_interpreter_digit_cap(tmp_path: Path, monkeypatch, field, default):
    name = f"OVERSIGHT_{field.upper()}"
    monkeypatch.setenv(name, "0" * 5000 + "7")
    assert getattr(Settings.load(tmp_path / "absent"), field) == 7
    monkeypatch.setenv(name, "1" + "0" * 5000)
    assert getattr(Settings.load(tmp_path / "absent"), field) == 10**5000
    arguments: dict[str, Any] = {field: 10**5000}
    assert getattr(Settings(**arguments), field) == 10**5000


def test_template_and_independent_model_budget() -> None:
    template = read_env_file(REPO_ROOT / ".env.example")
    for field, _ in SETTINGS:
        assert template[f"OVERSIGHT_{field.upper()}"] == ""
    limits = build_lead_limits(Settings(lead_max_tool_calls=99))
    assert limits.tool_calls == 99 and limits.model_calls == 18
    assert limits.model_dump() == dict(
        turns=50,
        tool_calls=99,
        model_calls=18,
        wall_clock_s=300.0,
        plans=3,
        subtasks=6,
        task_chars=2000,
    )
