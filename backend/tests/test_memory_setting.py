"""Memory admission configuration from the environment, file and direct construction."""

from pathlib import Path
from typing import Any

import pytest

from oversight.settings import REPO_ROOT, Settings, read_env_file

VARIABLE = "OVERSIGHT_MEMORY_HOLD_PERCENT"


@pytest.mark.parametrize("value,expected", [(None, 90), ("1", 1), ("100", 100), ("85", 85)])
def test_memory_setting_loads_with_environment_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str | None, expected: int
) -> None:
    env = tmp_path / "config"
    monkeypatch.delenv(VARIABLE, raising=False)
    env.write_text("" if value is None else f"{VARIABLE}={value}\n")
    assert Settings.load(env).memory_hold_percent == expected
    assert Settings().memory_hold_percent == 90
    monkeypatch.setenv(VARIABLE, "75")
    assert Settings.load(env).memory_hold_percent == 75


@pytest.mark.parametrize(
    "value",
    ["", "0", "101", "9.5", "abc", " 5", "\u0665", pytest.param("9" * 5000, id="oversized")],
)
@pytest.mark.parametrize("source", ["file", "environment"])
def test_invalid_memory_setting_names_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str, source: str
) -> None:
    env = tmp_path / "config"
    monkeypatch.delenv(VARIABLE, raising=False)
    # Quotes preserve a leading space through the shared dotenv parser.
    env.write_text(f'{VARIABLE}="{value}"\n' if source == "file" else "")
    if source == "environment":
        monkeypatch.setenv(VARIABLE, value)
    with pytest.raises(ValueError, match=VARIABLE):
        Settings.load(env)


@pytest.mark.parametrize("value", [True, 0, 101, 9.5, "90"])
def test_direct_memory_setting_validates_type_and_range(value: Any) -> None:
    with pytest.raises(ValueError, match=VARIABLE):
        Settings(memory_hold_percent=value)


def test_env_example_documents_memory_default() -> None:
    assert read_env_file(REPO_ROOT / ".env.example")[VARIABLE] == "90"
