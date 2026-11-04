"""Notepad-compatible settings reads and saves, using synthetic files only."""

import asyncio
import os
from pathlib import Path

import pytest

from oversight.settings import Settings, read_env_file
from oversight.settings_file import SettingsFile

CONTENT = (
    b"META_API_KEY=synthetic-notepad-marker\r\n"
    b"OVERSIGHT_BUDGET_USD=5\r\nOVERSIGHT_MAX_AGENTS=8\r\n"
    b"# Keep this LF comment\nUNRELATED=unchanged\r\nLAST=unterminated"
)
BOM = b"\xef\xbb\xbf"


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for name in tuple(os.environ):
        if name.startswith(("OVERSIGHT_", "MUSE_SPARK_", "BASETEN_")) or name == "META_API_KEY":
            monkeypatch.delenv(name)
    path = tmp_path / "settings.env"
    path.write_bytes(CONTENT)
    monkeypatch.setenv("OVERSIGHT_ENV_FILE", str(path))
    return path


def test_read_env_file_crlf_strips_only_line_terminators(config_file: Path) -> None:
    values = read_env_file(config_file)
    assert values["META_API_KEY"] == "synthetic-notepad-marker"
    assert values["OVERSIGHT_MAX_AGENTS"] == "8"
    assert Settings.load(config_file).max_agents == 8


def test_read_env_file_utf8_bom_does_not_hide_first_variable(config_file: Path) -> None:
    config_file.write_bytes(BOM + CONTENT)
    values = read_env_file(config_file)
    assert values["META_API_KEY"] == "synthetic-notepad-marker"
    assert Settings.load(config_file).meta_api_key == "synthetic-notepad-marker"


@pytest.mark.anyio
async def test_crlf_keys_are_set_without_trailing_control_characters(config_file: Path) -> None:
    settings = await asyncio.to_thread(Settings.load, config_file)
    view = await SettingsFile(settings).read()
    rows = {row.name: row for row in view.settings}
    assert rows["META_API_KEY"].state == "set" and rows["META_API_KEY"].value is None
    assert rows["OVERSIGHT_MAX_AGENTS"].state == "set"
    assert rows["OVERSIGHT_MAX_AGENTS"].value == "8"
    assert "synthetic-notepad-marker" not in view.model_dump_json()


@pytest.mark.anyio
async def test_save_preserves_other_lines_and_mixed_line_endings(config_file: Path) -> None:
    settings = await asyncio.to_thread(Settings.load, config_file)
    view = await SettingsFile(settings).save({"OVERSIGHT_BUDGET_USD": "20"})
    assert await asyncio.to_thread(config_file.read_bytes) == CONTENT.replace(
        b"OVERSIGHT_BUDGET_USD=5\r\n", b"OVERSIGHT_BUDGET_USD=20\r\n"
    )
    assert not view.restart_required and view.apply == "desktop_reload"
    assert next(row for row in view.settings if row.name == "META_API_KEY").state == "set"


@pytest.mark.anyio
@pytest.mark.parametrize("comment_first", [False, True])
async def test_save_utf8_bom_updates_first_variable_without_duplication(
    config_file: Path, comment_first: bool
) -> None:
    content = BOM + (b"# Notepad comment\r\n" if comment_first else b"") + CONTENT
    await asyncio.to_thread(config_file.write_bytes, content)
    settings = await asyncio.to_thread(Settings.load, config_file)
    view = await SettingsFile(settings).save({"META_API_KEY": "synthetic-replacement"})
    expected = content.replace(b"synthetic-notepad-marker", b"synthetic-replacement")
    assert await asyncio.to_thread(config_file.read_bytes) == expected
    loaded = await asyncio.to_thread(Settings.load, config_file)
    assert loaded.meta_api_key == "synthetic-replacement"
    assert next(row for row in view.settings if row.name == "META_API_KEY").state == "set"
    assert "synthetic-replacement" not in view.model_dump_json()


@pytest.mark.anyio
async def test_unresolved_installed_env_path_keeps_desktop_reload(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = config_file.parent / ".." / config_file.parent.name / config_file.name
    monkeypatch.setenv("OVERSIGHT_ENV_FILE", str(path))
    monkeypatch.setenv("OVERSIGHT_WORKSPACE_SOURCE", r"C:\private\repository")
    runtime, ledger = config_file.parent / "own-runtime", config_file.parent / "ledger"
    monkeypatch.setenv("OVERSIGHT_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("OVERSIGHT_LEDGER_DIR", str(ledger))
    settings = await asyncio.to_thread(Settings.load, path)
    assert settings.env_file == path and settings.env_file != path.resolve()
    assert settings.workspace_source == r"C:\private\repository"
    assert settings.runtime_dir == runtime and settings.ledger_root == ledger
    view = await SettingsFile(settings).save({"OVERSIGHT_BUDGET_USD": "20"})
    assert view.apply == "desktop_reload" and not view.restart_required
    assert b"OVERSIGHT_BUDGET_USD=20\r\n" in await asyncio.to_thread(config_file.read_bytes)
