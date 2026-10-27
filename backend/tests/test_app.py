import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from live_helpers import DEADLINE_S, live_server, live_settings

from oversight.app import LIMITS, ReplayConfig, create_app, new_run_id, pytest_pin
from oversight.events import run_store_path
from oversight.settings import REPO_ROOT, Settings, positive_amount, read_env_file
from oversight_contracts.commands import GraphResponse, Health
from oversight_contracts.graph import Point

REPLAY_LOG = REPO_ROOT / "fixtures" / "events" / "handwritten-level1.jsonl"


def test_the_live_app_has_no_graph_before_its_run_starts(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(runtime_dir=tmp_path / ".runtime")))
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


def test_a_relative_layout_starts_at_the_repo_root_and_an_absolute_one_is_taken_as_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("OVERSIGHT_LAYOUT", "my/layout.json")
    assert Settings.load(tmp_path / "no-env-file").layout == REPO_ROOT / "my" / "layout.json"
    # The same from the .env file, which the process environment would override.
    monkeypatch.delenv("OVERSIGHT_LAYOUT")
    env = tmp_path / ".env"
    env.write_text("OVERSIGHT_LAYOUT=my/layout.json\n")
    assert Settings.load(env).layout == REPO_ROOT / "my" / "layout.json"
    # A default that is already absolute stays as it is, whatever the working directory.
    assert Settings.load(tmp_path / "no-env-file").layout == Settings().layout
    outside = tmp_path / "outside" / "layout.json"
    monkeypatch.setenv("OVERSIGHT_LAYOUT", str(outside))
    assert Settings.load(env).layout == outside


def test_the_live_app_reads_the_layout_at_start(tmp_path: Path) -> None:
    layout = tmp_path / "layout.json"
    layout.write_text('{"a": {"x": 1, "y": 2}}')
    runtime = tmp_path / ".runtime"
    app = create_app(Settings(layout=layout, runtime_dir=runtime))
    assert app.state.layout == {"a": Point(x=1, y=2)}
    missing = Settings(layout=tmp_path / "missing.json", runtime_dir=runtime)
    assert create_app(missing).state.layout == {}


def test_an_unusable_layout_does_not_stop_the_live_app(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    layout = tmp_path / "layout.json"
    layout.write_text("not json")
    with caplog.at_level(logging.DEBUG):
        app = create_app(Settings(layout=layout, runtime_dir=tmp_path / ".runtime"))
    assert app.state.layout == {}
    [record] = caplog.records
    assert str(layout) in record.getMessage()
    # A replay's positions are in its recorded snapshot, so it does not read the file.
    caplog.clear()
    with caplog.at_level(logging.DEBUG):
        replay = create_app(
            Settings(layout=layout, runtime_dir=tmp_path), ReplayConfig(log=REPLAY_LOG)
        )
    assert not hasattr(replay.state, "layout")
    assert caplog.records == []


@pytest.mark.anyio
async def test_the_live_run_starts_with_run_started_then_the_snapshot(tmp_path: Path) -> None:
    settings = live_settings(tmp_path, budget_usd=150.0)
    settings.runtime_dir.mkdir()
    (settings.runtime_dir / "ledger.json").write_text('{"spent_usd": 12.5, "unknown": []}')
    async with asyncio.timeout(DEADLINE_S), live_server(settings) as s:
        started, snapshot = s.events()[:2]
        health = Health.model_validate((await s.client.get("/health")).json())
        graph = GraphResponse.model_validate((await s.client.get("/graph")).json())
    assert (started.seq, started.type, snapshot.seq, snapshot.type) == (
        1,
        "run.started",
        2,
        "graph.snapshot",
    )
    run = started.payload
    assert (run.run_id, run.workspace, run.limits) == (s.live.run_id, "demo-repo", LIMITS)
    assert (run.budget.budget_usd, run.budget.spent_usd) == (150.0, 12.5)
    assert [m.id for m in run.models] == ["fake"]
    assert [(slot.slot, slot.default_model) for slot in run.slots] == [("A", "fake")]
    assert snapshot.payload.commit == run.base_commit
    assert snapshot.payload.positions and "shop.pricing:apply_coupon" in snapshot.payload.positions
    assert health.mode == "live" and health.run_id == run.run_id
    assert graph.seq >= 2 and graph.graph.version == snapshot.payload.version


def test_a_second_server_on_the_same_runtime_fails_to_start(tmp_path: Path) -> None:
    settings = live_settings(tmp_path)
    second = TestClient(create_app(settings))
    with TestClient(create_app(settings)), pytest.raises(Exception, match="another Oversight"):
        second.__enter__()


def test_run_ids_take_the_next_second_when_taken(tmp_path: Path) -> None:
    now = datetime(2026, 10, 27, 14, 3, 12, 500_000, tzinfo=UTC)
    taken = run_store_path(tmp_path, "run-20261027-140312")
    taken.parent.mkdir(parents=True)
    taken.write_text("")
    assert new_run_id(tmp_path, now) == "run-20261027-140313"


def test_the_pytest_pin_is_the_demo_venv_requirement(tmp_path: Path) -> None:
    assert pytest_pin() == "9.1.1"
    other = tmp_path / "requirements.txt"
    other.write_text("# pins\npytest-cov==7.0\n")
    with pytest.raises(ValueError, match="pins no pytest"):
        pytest_pin(other)


@pytest.mark.parametrize(
    ("value", "amount"),
    [
        ("150", 150.0),
        ("0.10", 0.10),
        (None, None),
        ("", None),
        ("abc", None),
        ("0", None),
        ("-1", None),
        ("inf", None),
        ("nan", None),
    ],
)
def test_budget_and_prices_are_positive_amounts_or_unset(
    value: str | None, amount: float | None
) -> None:
    assert positive_amount(value) == amount


def test_the_brake_settings_load_from_the_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "OVERSIGHT_BUDGET_USD",
        "MUSE_SPARK_INPUT_USD_PER_M",
        "MUSE_SPARK_OUTPUT_USD_PER_M",
        "OVERSIGHT_AGENT_MODEL",
        "OVERSIGHT_FAKE_SCRIPT",
    ):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "OVERSIGHT_BUDGET_USD=150\nMUSE_SPARK_INPUT_USD_PER_M=0.10\n"
        "MUSE_SPARK_OUTPUT_USD_PER_M=zero\nOVERSIGHT_AGENT_MODEL=fake\n"
        "OVERSIGHT_FAKE_SCRIPT=fixtures/fake-model/x.json\n"
    )
    settings = Settings.load(env)
    assert (settings.budget_usd, settings.input_usd_per_m, settings.output_usd_per_m) == (
        150.0,
        0.10,
        None,
    )
    assert settings.agent_model == "fake"
    assert settings.fake_script == REPO_ROOT / "fixtures" / "fake-model" / "x.json"
