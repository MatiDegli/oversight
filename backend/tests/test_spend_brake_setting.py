"""The person's spending share defaults only when absent or empty; invalid inputs close it."""

from pathlib import Path

import pytest

from oversight.settings import Settings

VARIABLE = "OVERSIGHT_SPEND_BRAKE_PERCENT"


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, 90),
        ("", 90),
        ("090", 90),
        ("1", 1),
        ("100", 100),
        pytest.param("0" * 5000 + "70", 70, id="leading-zeros"),
    ],
)
@pytest.mark.parametrize("source", ["file", "environment"])
def test_spend_brake_setting_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str | None, expected: int, source: str
) -> None:
    env = tmp_path / "config"
    monkeypatch.delenv(VARIABLE, raising=False)
    env.write_text(f"{VARIABLE}={value}\n" if source == "file" and value is not None else "")
    if source == "environment" and value is not None:
        monkeypatch.setenv(VARIABLE, value)
    settings = Settings.load(env)
    assert settings.spend_brake_percent == expected
    assert VARIABLE not in settings.setting_problems
    assert Settings().spend_brake_percent == 90


@pytest.mark.parametrize(
    "value",
    [
        "0",
        "101",
        "abc",
        "9.5",
        "-5",
        "\uff11\uff10",
        "1000",
        " 70",
        "70 ",
        "0001000",
        pytest.param("9" * 5000, id="oversized"),
    ],
)
@pytest.mark.parametrize("source", ["file", "environment"])
def test_invalid_spend_brake_setting_loads_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str, source: str
) -> None:
    env = tmp_path / "config"
    monkeypatch.delenv(VARIABLE, raising=False)
    env.write_text(f'{VARIABLE}="{value}"\n' if source == "file" else "")
    if source == "environment":
        monkeypatch.setenv(VARIABLE, value)
    settings = Settings.load(env)
    assert settings.spend_brake_percent is None
    assert settings.setting_problems[VARIABLE] == "invalid"


def test_environment_wins_over_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / "config"
    env.write_text(f"{VARIABLE}=70\n")
    monkeypatch.setenv(VARIABLE, "invalid")
    assert Settings.load(env).spend_brake_percent is None
    monkeypatch.setenv(VARIABLE, "")
    assert Settings.load(env).spend_brake_percent == 90
