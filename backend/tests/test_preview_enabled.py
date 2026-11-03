"""Strict startup opt-in; enabling preview grants no source or executable authority."""

import os
from dataclasses import FrozenInstanceError, replace
from typing import Any

import pytest
from workspace.preview_fakes import profile

from oversight.settings import Settings, resolve_preview_profile
from oversight.workspace import PreviewProfile


@pytest.mark.parametrize("raw,expected", [(None, False), ("true", True), ("false", False)])
def test_preview_enabled_strict_and_frozen(tmp_path, monkeypatch, raw, expected):
    for name in tuple(os.environ):
        if name.startswith("OVERSIGHT_"):
            monkeypatch.delenv(name)
    env_file = tmp_path / ".env"
    env_file.write_text("")
    values = {} if raw is None else {"OVERSIGHT_PREVIEW_ENABLED": raw}
    if raw is not None:
        monkeypatch.setenv("OVERSIGHT_PREVIEW_ENABLED", raw)
    resolved = resolve_preview_profile(values, 8710, None)
    loaded = Settings.load(env_file)
    explicit = Settings(preview_profile=PreviewProfile(enabled=expected))
    assert Settings().preview_profile.enabled is False
    for settings_profile in (resolved, loaded.preview_profile, explicit.preview_profile):
        assert settings_profile.enabled is expected
        assert settings_profile.production_open is expected
        with pytest.raises(FrozenInstanceError):
            settings_profile.__setattr__("enabled", not expected)
    monkeypatch.setenv("OVERSIGHT_PREVIEW_ENABLED", "false" if expected else "true")
    values["OVERSIGHT_PREVIEW_ENABLED"] = "false" if expected else "true"
    assert resolved.enabled is loaded.preview_profile.enabled is explicit.preview_profile.enabled
    assert resolved.enabled is expected
    assert Settings.load(env_file).preview_profile.enabled is not expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        " ",
        "\t",
        "\n",
        " true",
        "true ",
        " true ",
        "\ttrue\t",
        " false",
        "false ",
        " false ",
        '"true"',
        "'true'",
        '"false"',
        "'false'",
        '"true',
        "true'",
        "True",
        "TRUE",
        "False",
        "fAlSe",
        "1",
        "0",
        "yes",
    ],
)
def test_preview_enabled_rejects_malformed_values(tmp_path, monkeypatch, raw):
    message = "^OVERSIGHT_PREVIEW_ENABLED must be true or false$"
    with pytest.raises(ValueError, match=message):
        resolve_preview_profile({"OVERSIGHT_PREVIEW_ENABLED": raw}, 8710, None)
    monkeypatch.setenv("OVERSIGHT_PREVIEW_ENABLED", raw)
    with pytest.raises(ValueError, match=message):
        Settings.load(tmp_path / "absent.env")
    monkeypatch.delenv("OVERSIGHT_PREVIEW_ENABLED")
    env_file = tmp_path / ".env"
    env_file.write_text(f"OVERSIGHT_PREVIEW_ENABLED={raw}\n")
    with pytest.raises(ValueError, match=message):
        Settings.load(env_file)
    for override in ("true", "false"):
        monkeypatch.setenv("OVERSIGHT_PREVIEW_ENABLED", override)
        assert Settings.load(env_file).preview_profile.enabled is (override == "true")


@pytest.mark.parametrize("enabled", [None, 0, 1, "true", "false", [], {}])
def test_preview_enabled_rejects_non_boolean_explicit_values(enabled: Any):
    with pytest.raises(ValueError, match=r"^preview enabled flag must be boolean$"):
        Settings(preview_profile=PreviewProfile(enabled=enabled))


@pytest.mark.parametrize("file_value", ["true", "false"])
def test_preview_enabled_environment_wins_over_file(tmp_path, monkeypatch, file_value):
    monkeypatch.delenv("OVERSIGHT_PREVIEW_ENABLED", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(f"OVERSIGHT_PREVIEW_ENABLED={file_value}\n")
    from_file = Settings.load(env_file)
    assert from_file.preview_profile.enabled is (file_value == "true")
    environment_value = "false" if file_value == "true" else "true"
    monkeypatch.setenv("OVERSIGHT_PREVIEW_ENABLED", environment_value)
    from_environment = Settings.load(env_file)
    assert from_environment.preview_profile.enabled is (environment_value == "true")
    assert from_file.preview_profile.enabled is (file_value == "true")
    env_file.write_text("OVERSIGHT_PREVIEW_ENABLED=invalid\n")
    assert Settings.load(env_file).preview_profile.enabled is (environment_value == "true")
    monkeypatch.setenv("OVERSIGHT_PREVIEW_ENABLED", "")
    with pytest.raises(ValueError, match=r"^OVERSIGHT_PREVIEW_ENABLED must be true or false$"):
        Settings.load(env_file)
    monkeypatch.delenv("OVERSIGHT_PREVIEW_ENABLED")
    with pytest.raises(ValueError, match=r"^OVERSIGHT_PREVIEW_ENABLED must be true or false$"):
        Settings.load(env_file)


def test_enabled_profile_still_requires_configuration():
    with pytest.raises(ValueError, match=r"^preview profile is disabled$"):
        PreviewProfile().validate()
    with pytest.raises(ValueError, match=r"^preview profile is disabled$"):
        PreviewProfile(enabled=True).validate()
    with pytest.raises(ValueError, match=r"^preview profile is disabled$"):
        replace(profile(), enabled=False).validate()
    assert not replace(PreviewProfile(enabled=True), enabled=False).production_open


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"ports": (19681, 19681)}, "invalid preview port pool"),
        ({"ports": (True,)}, "invalid preview port pool"),
        ({"origins": ("https://127.0.0.1:8710",)}, "invalid preview origin"),
        ({"origins": ("http://foreign.example:8710",)}, "invalid preview origin"),
        ({"readiness_s": 0}, "invalid preview deadline"),
        ({"lease_s": float("nan")}, "invalid preview deadline"),
        ({"heartbeat_s": 30}, "invalid preview limits"),
        ({"memory_limit_bytes": 0}, "invalid preview limits"),
        ({"memory_limit_bytes": True}, "invalid preview limits"),
    ],
)
def test_enabled_preserves_profile_refusals(changes: dict[str, Any], message):
    with pytest.raises(ValueError, match=f"^{message}$"):
        replace(profile(), **changes).validate()
