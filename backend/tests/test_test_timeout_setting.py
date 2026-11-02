"""The person's test deadline is parsed once, before a run starts."""

from pathlib import Path
from typing import Any

import pytest

from oversight.settings import REPO_ROOT, Settings, read_env_file, run_time_limits

VARIABLE = "OVERSIGHT_TEST_TIMEOUT_S"
ERROR = "OVERSIGHT_TEST_TIMEOUT_S must be a whole number of seconds from 1 to 999999999"


@pytest.mark.parametrize("value", [None, "", "1", "60", "0075", "900", "2000000", "999999999"])
def test_load_and_process_precedence(tmp_path: Path, monkeypatch, value: str | None) -> None:
    env = tmp_path / "config"
    monkeypatch.delenv(VARIABLE, raising=False)
    env.write_text("" if value is None else f"{VARIABLE}={value}\n")
    assert Settings.load(env).test_timeout_s == (int(value) if value else 60)
    monkeypatch.setenv(VARIABLE, "75")
    assert Settings.load(env).test_timeout_s == 75
    monkeypatch.setenv(VARIABLE, "")
    assert Settings.load(env).test_timeout_s == 60


@pytest.mark.parametrize("source", ["file", "environment"])
@pytest.mark.parametrize(
    "value", ["0", "000", "-60", "+90", "60.5", "abc", " 90", "9" * 10, "9" * 30, "٩٠"]
)
def test_invalid_value(tmp_path: Path, monkeypatch, source: str, value: str) -> None:
    env = tmp_path / "config"
    monkeypatch.delenv(VARIABLE, raising=False)
    env.write_text(f'{VARIABLE}="{value}"\n' if source == "file" else "")
    if source == "environment":
        monkeypatch.setenv(VARIABLE, value)
    with pytest.raises(ValueError) as exc:
        Settings.load(env)
    assert str(exc.value) == ERROR


@pytest.mark.parametrize("value", [True, False, 60.0, 0, -1, 1000000000, "60"])
def test_direct_construction(value: Any) -> None:
    with pytest.raises(ValueError) as exc:
        Settings(test_timeout_s=value)
    assert str(exc.value) == ERROR


def test_leading_zeros_are_not_a_size_bound(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(VARIABLE, "0" * 5000 + "75")
    assert Settings.load(tmp_path / "absent").test_timeout_s == 75


@pytest.mark.parametrize(
    "setting,wall", [(1, 600), (60, 600), (100, 600), (101, 603), (900, 3000), (2000000, 6000300)]
)
def test_derived_limits(setting: int, wall: int) -> None:
    assert run_time_limits(True, setting) == (setting, wall)
    assert run_time_limits(False, setting) == (60, 600)


def test_example_documents_empty_default() -> None:
    assert read_env_file(REPO_ROOT / ".env.example")[VARIABLE] == ""
