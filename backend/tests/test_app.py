from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from oversight.app import create_app
from oversight.settings import Settings, read_env_file


def test_the_app_starts_with_no_routes_yet() -> None:
    client = TestClient(create_app(Settings()))
    assert client.get("/graph").status_code == 404


def test_env_file_parsing_skips_comments_and_strips_quotes(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text('# comment\n\nOVERSIGHT_PORT="9001"\nOVERSIGHT_HOST = 0.0.0.0\nbroken\n')
    assert read_env_file(env) == {"OVERSIGHT_PORT": "9001", "OVERSIGHT_HOST": "0.0.0.0"}


def test_the_environment_wins_over_the_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text("OVERSIGHT_PORT=9001\n")
    monkeypatch.setenv("OVERSIGHT_PORT", "9002")
    assert Settings.load(env).port == 9002


def test_provider_keys_are_read_but_never_shown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text("META_API_KEY=test-key\nBASETEN_API_KEY=\n")
    monkeypatch.delenv("META_API_KEY", raising=False)
    monkeypatch.delenv("BASETEN_API_KEY", raising=False)
    settings = Settings.load(env)
    assert settings.meta_api_key == "test-key"
    assert settings.baseten_api_key is None
    assert "test-key" not in repr(settings)
