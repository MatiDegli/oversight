"""The agent step limit defaults to 30 when absent or empty and fixes run.started limits."""

import asyncio
from pathlib import Path
from typing import Any

import pytest
from live_helpers import DEADLINE_S, live_server, live_settings

from oversight.app import LIMITS
from oversight.settings import REPO_ROOT, Settings, read_env_file
from oversight.settings_file import SettingsFile, SettingsRejected

VARIABLE = "OVERSIGHT_MAX_STEPS"


@pytest.mark.parametrize(
    "value,expected",
    [(None, 30), ("", 30), ("5", 5), ("30", 30), ("050", 50), ("100", 100)],
)
@pytest.mark.parametrize("source", ["file", "environment"])
def test_step_limit_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str | None, expected: int, source: str
) -> None:
    env = tmp_path / "config"
    monkeypatch.delenv(VARIABLE, raising=False)
    env.write_text(f"{VARIABLE}={value}\n" if source == "file" and value is not None else "")
    if source == "environment" and value is not None:
        monkeypatch.setenv(VARIABLE, value)
    assert Settings.load(env).max_steps == expected
    assert Settings().max_steps == 30


@pytest.mark.parametrize(
    "value",
    [
        "0",
        "4",
        "101",
        "1000",
        "abc",
        "9.5",
        "-5",
        " 70",
        "\uff11\uff10",
        pytest.param("9" * 5000, id="oversized"),
    ],
)
@pytest.mark.parametrize("source", ["file", "environment"])
def test_invalid_step_limit_names_the_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str, source: str
) -> None:
    env = tmp_path / "config"
    monkeypatch.delenv(VARIABLE, raising=False)
    env.write_text(f'{VARIABLE}="{value}"\n' if source == "file" else "")
    if source == "environment":
        monkeypatch.setenv(VARIABLE, value)
    with pytest.raises(ValueError, match=f"{VARIABLE} must be a whole number from 5 to 100"):
        Settings.load(env)


def test_env_example_documents_the_empty_default() -> None:
    assert read_env_file(REPO_ROOT / ".env.example")[VARIABLE] == ""


@pytest.mark.parametrize("value", [4, 101, True, 30.0])
def test_direct_settings_validate_the_step_limit(value: Any) -> None:
    with pytest.raises(ValueError, match=VARIABLE):
        Settings(max_steps=value)


@pytest.mark.anyio
async def test_settings_file_saves_refuses_and_clears_the_step_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(VARIABLE, raising=False)
    env = tmp_path / "config"
    env.write_text("OVERSIGHT_BUDGET_USD=5\n")
    editor = SettingsFile(Settings.load(env))
    view = await editor.save({VARIABLE: "50"})
    [row] = [row for row in view.settings if row.name == VARIABLE]
    assert (row.kind, row.state, row.value) == ("number", "set", "50")
    assert view.restart_required
    assert Settings.load(env).max_steps == 50
    for value in ("4", "101", "abc"):
        with pytest.raises(SettingsRejected, match=f"{VARIABLE}: must be a whole number from 5"):
            await editor.save({VARIABLE: value})
    assert Settings.load(env).max_steps == 50
    view = await editor.save({VARIABLE: ""})
    assert VARIABLE not in env.read_text()
    assert Settings.load(env).max_steps == 30
    env.write_text(f"{VARIABLE}=200\n")
    [row] = [row for row in (await editor.read()).settings if row.name == VARIABLE]
    assert (row.state, row.value) == ("invalid", "200")


@pytest.mark.anyio
@pytest.mark.parametrize("steps", [7, 30])
async def test_run_started_limits_follow_the_step_limit(tmp_path: Path, steps: int) -> None:
    settings = live_settings(tmp_path, max_steps=steps)
    async with asyncio.timeout(DEADLINE_S), live_server(settings) as server:
        started = server.events()[0]
    assert started.type == "run.started"
    assert started.payload.limits.max_steps == steps
    assert started.payload.limits.model_copy(update={"max_steps": LIMITS.max_steps}) == LIMITS
