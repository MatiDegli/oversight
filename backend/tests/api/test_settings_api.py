"""File-only settings editing, with no provider calls or real environment file."""

import asyncio
import os
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from live_helpers import Scripts

from oversight.app import ReplayConfig, create_app
from oversight.settings import REPO_ROOT, Settings
from oversight_contracts.commands import SettingsView

pytestmark = pytest.mark.anyio
SECRET = "synthetic-private-marker"


@pytest.fixture
def settings_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # No test ever reads the person's .env or inherits a provider credential.
    for name in tuple(os.environ):
        if name.startswith(("OVERSIGHT_", "MUSE_SPARK_", "BASETEN_")) or name == "META_API_KEY":
            monkeypatch.delenv(name)
    path = tmp_path / "settings.env"
    path.write_text(f"# Keep this comment\nMETA_API_KEY={SECRET}\nOTHER=unchanged\n")
    monkeypatch.setenv("OVERSIGHT_ENV_FILE", str(path))
    return path


def client_for(path: Path, *, replay: bool = False) -> httpx.AsyncClient:
    settings = replace(
        Settings.load(path), runtime_dir=path.parent / ".runtime", frontend_dist=None
    )
    app = create_app(
        settings,
        ReplayConfig(REPO_ROOT / "fixtures/events/handwritten-level1.jsonl") if replay else None,
        client_factory=Scripts(),
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url=f"http://localhost:{settings.port}"
    )


async def test_get_withholds_secrets_and_reports_file_state(settings_file: Path) -> None:
    await asyncio.to_thread(
        settings_file.write_text,
        (await asyncio.to_thread(settings_file.read_text))
        + 'BASETEN_API_KEY="bad key"\nOVERSIGHT_BUDGET_USD=nope\n',
    )
    async with client_for(settings_file) as client:
        response = await client.get("/settings")
    assert response.status_code == 200
    assert SECRET not in response.text and "bad key" not in response.text
    view = SettingsView.model_validate(response.json())
    rows = {entry.name: entry for entry in view.settings}
    assert len(rows) == 11
    assert rows["META_API_KEY"].state == "set" and rows["META_API_KEY"].value is None
    assert rows["BASETEN_API_KEY"].state == "invalid"
    assert rows["OVERSIGHT_BUDGET_USD"].state == "invalid"
    assert rows["MUSE_SPARK_INPUT_USD_PER_M"].state == "missing"
    assert not view.restart_required
    assert view.apply == "desktop_reload"


async def test_save_preserves_lines_deduplicates_clears_and_tracks_restart(
    settings_file: Path,
) -> None:
    await asyncio.to_thread(
        settings_file.write_text,
        (await asyncio.to_thread(settings_file.read_text))
        + "OVERSIGHT_BUDGET_USD=5\n# Stay here\nOVERSIGHT_BUDGET_USD=6\n",
    )
    await asyncio.to_thread(settings_file.chmod, 0o640)
    async with client_for(settings_file) as client:
        response = await client.put(
            "/settings",
            json={
                "values": {
                    "META_API_KEY": "",
                    "OVERSIGHT_BUDGET_USD": "20",
                    "OVERSIGHT_MAX_AGENTS": "8",
                }
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["restart_required"]
        assert SECRET not in response.text
        assert (await asyncio.to_thread(settings_file.read_text)) == (
            "# Keep this comment\nMETA_API_KEY=\nOTHER=unchanged\n"
            "OVERSIGHT_BUDGET_USD=20\n# Stay here\nOVERSIGHT_MAX_AGENTS=8\n"
        )
        assert (await client.get("/settings")).json()["restart_required"]
        await client.put(
            "/settings",
            json={
                "values": {
                    "META_API_KEY": SECRET,
                    "OVERSIGHT_BUDGET_USD": "6",
                    "OVERSIGHT_MAX_AGENTS": "4",
                }
            },
        )
    if os.name == "posix":
        assert (await asyncio.to_thread(settings_file.stat)).st_mode & 0o777 == 0o640
    assert list(settings_file.parent.glob("*.tmp")) == []


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("META_API_KEY", "bad key"),
        ("META_API_KEY", "bad\tkey"),
        ("META_API_KEY", "bad\nkey"),
        ("META_API_KEY", "bad\rkey"),
        ("META_API_KEY", "bad\x00key"),
        ("META_API_KEY", "bad\x1bkey"),
        ("META_API_KEY", "bad\x7fkey"),
        ("META_API_KEY", "bad\u2028key"),
        ("OVERSIGHT_CROSS_REVIEW", "bad\nvalue"),
        ("META_API_KEY", "bad\u0085key"),
        ("META_API_KEY", "x" * 513),
        ("OVERSIGHT_BUDGET_USD", "bad-amount"),
        ("OVERSIGHT_BUDGET_USD", "-7"),
        ("OVERSIGHT_BUDGET_USD", "nan"),
        ("OVERSIGHT_BASETEN_BUDGET_USD", "invalid-budget"),
        ("BASETEN_INPUT_USD_PER_M", "-1"),
        ("BASETEN_OUTPUT_USD_PER_M", "inf"),
        ("OVERSIGHT_MAX_AGENTS", "1.5"),
        ("OVERSIGHT_MAX_AGENTS", "0"),
    ],
)
async def test_bad_values_write_nothing_and_never_echo(
    settings_file: Path,
    name: str,
    value: str,
) -> None:
    before = await asyncio.to_thread(settings_file.read_bytes)
    async with client_for(settings_file) as client:
        response = await client.put(
            "/settings",
            json={
                "values": {
                    name: value,
                    "MUSE_SPARK_INPUT_USD_PER_M": "3",
                }
            },
        )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_request"
    assert name in response.json()["error"]["message"]
    assert value not in response.text
    assert (await asyncio.to_thread(settings_file.read_bytes)) == before


@pytest.mark.parametrize(
    "body",
    [
        {"values": {SECRET: SECRET}},
        {"values": {"META_API_KEY": {SECRET: SECRET}}},
        {"values": {}, SECRET: SECRET},
        {"values": SECRET},
    ],
)
async def test_validation_paths_cannot_echo_unknown_names_or_values(
    settings_file: Path,
    body: object,
) -> None:
    before = await asyncio.to_thread(settings_file.read_bytes)
    async with client_for(settings_file) as client:
        response = await client.put("/settings", json=body)
    assert response.status_code == 422
    assert SECRET not in response.text
    assert (await asyncio.to_thread(settings_file.read_bytes)) == before


async def test_override_and_origin_guard(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("META_API_KEY", "synthetic-environment-secret")
    async with client_for(settings_file) as client:
        response = await client.get("/settings")
        assert response.status_code == 200
        assert "synthetic-environment-secret" not in response.text
        assert response.json()["settings"][0]["overridden"]
        before = await asyncio.to_thread(settings_file.read_bytes)
        refused = await client.put(
            "/settings",
            headers={"Origin": "https://foreign.example"},
            json={"values": {"META_API_KEY": SECRET}},
        )
        assert refused.status_code == 403
        assert (await asyncio.to_thread(settings_file.read_bytes)) == before


async def test_replay_refuses_read_and_write_before_body_validation(settings_file: Path) -> None:
    async with client_for(settings_file, replay=True) as client:
        for response in (
            await client.get("/settings"),
            await client.put("/settings", content="invalid-json"),
        ):
            assert response.status_code == 501
            assert response.json()["error"]["code"] == "not_implemented"


async def test_new_file_mode_and_concurrent_partial_writes(settings_file: Path) -> None:
    await asyncio.to_thread(settings_file.unlink)
    async with client_for(settings_file) as client:
        first, second = await asyncio.gather(
            client.put("/settings", json={"values": {"OVERSIGHT_BUDGET_USD": "5"}}),
            client.put("/settings", json={"values": {"BASETEN_INPUT_USD_PER_M": "2"}}),
        )
        assert first.status_code == second.status_code == 200
    assert set((await asyncio.to_thread(settings_file.read_text)).splitlines()) == {
        "OVERSIGHT_BUDGET_USD=5",
        "BASETEN_INPUT_USD_PER_M=2",
    }
    if os.name == "posix":
        assert (await asyncio.to_thread(settings_file.stat)).st_mode & 0o777 == 0o600


async def test_restore_snapshot_and_browser_apply(
    settings_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("oversight.settings_file.REPO_ROOT", settings_file.parent)
    async with client_for(settings_file) as client:
        view = (await client.get("/settings")).json()
        assert view["apply"] == "server_restart"
        await client.put("/settings", json={"values": {"META_API_KEY": "replacement"}})
        restored = await client.put("/settings", json={"values": {"META_API_KEY": SECRET}})
        assert not restored.json()["restart_required"]
        cleared = await client.put("/settings", json={"values": {"OVERSIGHT_BUDGET_USD": ""}})
        assert cleared.status_code == 200
        assert not cleared.json()["restart_required"]


async def test_failed_replace_keeps_original_and_removes_temp(
    settings_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = await asyncio.to_thread(settings_file.read_bytes)

    def fail_replace(*_args: object) -> None:
        raise OSError(SECRET)

    monkeypatch.setattr("oversight.settings_file.os.replace", fail_replace)
    async with client_for(settings_file) as client:
        response = await client.put("/settings", json={"values": {"META_API_KEY": "replacement"}})
    assert response.status_code == 422
    assert SECRET not in response.text
    assert await asyncio.to_thread(settings_file.read_bytes) == before
    assert not list(settings_file.parent.glob(".*.tmp"))


async def test_explicit_browser_path_is_not_a_desktop_launch(
    settings_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OVERSIGHT_ENV_FILE")
    async with client_for(settings_file) as client:
        assert (await client.get("/settings")).json()["apply"] == "server_restart"


async def test_get_marks_an_externally_emptied_capacity_invalid(settings_file: Path) -> None:
    async with client_for(settings_file) as client:
        await asyncio.to_thread(settings_file.write_text, "OVERSIGHT_MAX_AGENTS=\n")
        response = await client.get("/settings")
        cap = next(
            row for row in response.json()["settings"] if row["name"] == "OVERSIGHT_MAX_AGENTS"
        )
        assert cap["state"] == "invalid"


@pytest.mark.parametrize("value", ["1", "100", "090"])
async def test_save_brake_uses_startup_percent_parser(settings_file: Path, value: str) -> None:
    async with client_for(settings_file) as client:
        response = await client.put(
            "/settings", json={"values": {"OVERSIGHT_SPEND_BRAKE_PERCENT": value}}
        )
        assert response.status_code == 200
        row = next(
            row
            for row in response.json()["settings"]
            if row["name"] == "OVERSIGHT_SPEND_BRAKE_PERCENT"
        )
        assert row["state"] == "set" and row["kind"] == "number" and row["value"] == value
    assert (await asyncio.to_thread(Settings.load, settings_file)).spend_brake_percent == int(value)


@pytest.mark.parametrize("value", ["0", "101", "9.5", "-5", "bad-percent"])
async def test_bad_brake_is_rejected_without_writing(settings_file: Path, value: str) -> None:
    before = await asyncio.to_thread(settings_file.read_bytes)
    async with client_for(settings_file) as client:
        response = await client.put(
            "/settings", json={"values": {"OVERSIGHT_SPEND_BRAKE_PERCENT": value}}
        )
        assert response.status_code == 422
        assert "OVERSIGHT_SPEND_BRAKE_PERCENT" in response.json()["error"]["message"]
        assert value not in response.text
    assert await asyncio.to_thread(settings_file.read_bytes) == before


async def test_clear_defaulted_settings_removes_all_declarations(settings_file: Path) -> None:
    await asyncio.to_thread(
        settings_file.write_text,
        "# Keep\r\nOVERSIGHT_MAX_AGENTS=8\r\nOTHER=keep\r\n"
        "OVERSIGHT_MAX_AGENTS=9\r\nOVERSIGHT_SPEND_BRAKE_PERCENT=50\r\n"
        "# Stay\r\nOVERSIGHT_SPEND_BRAKE_PERCENT=60\r\n",
    )
    async with client_for(settings_file) as client:
        response = await client.put(
            "/settings",
            json={
                "values": {
                    "OVERSIGHT_MAX_AGENTS": "",
                    "OVERSIGHT_SPEND_BRAKE_PERCENT": "",
                }
            },
        )
        assert response.status_code == 200
        assert response.json()["restart_required"]
    assert (
        await asyncio.to_thread(settings_file.read_bytes) == b"# Keep\r\nOTHER=keep\r\n# Stay\r\n"
    )
    loaded = await asyncio.to_thread(Settings.load, settings_file)
    assert loaded.max_agents == 4 and loaded.spend_brake_percent == 90


@pytest.mark.parametrize("overridden", [False, True])
@pytest.mark.parametrize(
    "unsent", ["OVERSIGHT_BUDGET_USD", "OVERSIGHT_MAX_AGENTS", "OVERSIGHT_SPEND_BRAKE_PERCENT"]
)
async def test_invalid_unsent_setting_does_not_block_key_save(
    settings_file: Path,
    monkeypatch: pytest.MonkeyPatch,
    overridden: bool,
    unsent: str,
) -> None:
    if overridden:
        monkeypatch.setenv(unsent, "4")
    async with client_for(settings_file) as client:
        await asyncio.to_thread(settings_file.write_text, f"{unsent}=invalid-unsent\n")
        response = await client.put(
            "/settings", json={"values": {"META_API_KEY": "replacement-key"}}
        )
        assert response.status_code == 200, response.text
        row = next(row for row in response.json()["settings"] if row["name"] == unsent)
        assert row["state"] == "invalid" and row["overridden"] == overridden
        assert "replacement-key" not in response.text
    assert f"{unsent}=invalid-unsent" in await asyncio.to_thread(settings_file.read_text)


async def test_rejection_names_only_invalid_sent_settings(settings_file: Path) -> None:
    async with client_for(settings_file) as client:
        await asyncio.to_thread(settings_file.write_text, "OVERSIGHT_MAX_AGENTS=bad-unsent\n")
        before = await asyncio.to_thread(settings_file.read_bytes)
        response = await client.put(
            "/settings", json={"values": {"OVERSIGHT_BUDGET_USD": "bad-sent"}}
        )
        assert response.status_code == 422
        assert "OVERSIGHT_BUDGET_USD" in response.json()["error"]["message"]
        assert "OVERSIGHT_MAX_AGENTS" not in response.text
        assert "bad-sent" not in response.text and "bad-unsent" not in response.text
        assert await asyncio.to_thread(settings_file.read_bytes) == before
