from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from oversight.app import ReplayConfig, create_app
from oversight.graph import LayoutError
from oversight.settings import REPO_ROOT, Settings, read_env_file
from oversight_contracts.graph import Point

REPLAY_LOG = REPO_ROOT / "fixtures" / "events" / "handwritten-level1.jsonl"


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


def test_the_layout_setting_defaults_to_the_demo_repo_preset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OVERSIGHT_LAYOUT", raising=False)
    default = Settings.load(tmp_path / "no-env-file").layout
    assert default.parts[-3:] == ("fixtures", "layouts", "demo-repo.json")
    assert default == Settings().layout
    env = tmp_path / ".env"
    env.write_text("OVERSIGHT_LAYOUT=/somewhere/layout.json\n")
    assert Settings.load(env).layout == Path("/somewhere/layout.json")
    env.write_text("OVERSIGHT_LAYOUT=\n")
    assert Settings.load(env).layout == default


def test_the_live_app_reads_the_layout_at_start(tmp_path: Path) -> None:
    layout = tmp_path / "layout.json"
    layout.write_text('{"a": {"x": 1, "y": 2}}')
    app = create_app(Settings(layout=layout))
    assert app.state.layout == {"a": Point(x=1, y=2)}
    assert create_app(Settings(layout=tmp_path / "missing.json")).state.layout == {}


def test_a_malformed_layout_stops_the_live_app_at_start(tmp_path: Path) -> None:
    layout = tmp_path / "layout.json"
    layout.write_text("not json")
    with pytest.raises(LayoutError, match=str(layout)):
        create_app(Settings(layout=layout))
    # A replay's positions are in its recorded snapshot, so it does not read the file.
    replay = ReplayConfig(log=REPLAY_LOG)
    create_app(Settings(layout=layout, runtime_dir=tmp_path), replay)
