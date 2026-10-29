"""Capacity and slot defaults are shared by the runtime and setup without provider calls."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from live_helpers import FAKE_SCRIPT, FAKE_SCRIPT_B, live_settings

from oversight.agents import FAKE_MODEL, FAKE_MODEL_B, MUSE_SPARK, MUSE_SPARK_OPEN
from oversight.app import LiveRun
from oversight.settings import REPO_ROOT, Settings
from oversight.setup import offered_profiles
from oversight.slots import resolve_slots
from oversight_contracts.common import Slot


@pytest.mark.parametrize("value,expected", [(None, 4), ("1", 1), ("6", 6)])
def test_capacity_loads_from_file_and_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str | None, expected: int
) -> None:
    env = tmp_path / "config"
    monkeypatch.delenv("OVERSIGHT_MAX_AGENTS", raising=False)
    env.write_text("" if value is None else f"OVERSIGHT_MAX_AGENTS={value}\n")
    assert Settings.load(env).max_agents == expected
    assert Settings().max_agents == 4
    monkeypatch.setenv("OVERSIGHT_MAX_AGENTS", "2")
    assert Settings.load(env).max_agents == 2


@pytest.mark.parametrize(
    "value", ["0", "7", "four", "", "1.5", pytest.param("9" * 5000, id="oversized-integer")]
)
@pytest.mark.parametrize("source", ["file", "environment"])
def test_invalid_capacity_names_the_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str, source: str
) -> None:
    env = tmp_path / "config"
    monkeypatch.delenv("OVERSIGHT_MAX_AGENTS", raising=False)
    env.write_text(f"OVERSIGHT_MAX_AGENTS={value}\n" if source == "file" else "")
    if source == "environment":
        monkeypatch.setenv("OVERSIGHT_MAX_AGENTS", value)
    with pytest.raises(ValueError, match="OVERSIGHT_MAX_AGENTS"):
        Settings.load(env)


@pytest.mark.parametrize("value", [0, 7, True, 1.5])
def test_direct_settings_validate_capacity(value: Any) -> None:
    with pytest.raises(ValueError, match="OVERSIGHT_MAX_AGENTS"):
        Settings(max_agents=value)


@pytest.mark.parametrize("letter", list("CDEF"))
def test_slot_overrides_load_with_b_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, letter: str
) -> None:
    env = tmp_path / "config"
    model, script = f"OVERSIGHT_AGENT_MODEL_{letter}", f"OVERSIGHT_FAKE_SCRIPT_{letter}"
    monkeypatch.delenv(model, raising=False)
    monkeypatch.delenv(script, raising=False)
    env.write_text(f"{model}=\n{script}=\n")
    unset = Settings.load(env)
    assert getattr(unset, f"agent_model_{letter.lower()}") is None
    assert getattr(unset, f"fake_script_{letter.lower()}") is None
    env.write_text(f"{model}=fake-b\n{script}=scripts/{letter}.json\n")
    settings = Settings.load(env)
    assert getattr(settings, f"agent_model_{letter.lower()}") == "fake-b"
    assert (
        getattr(settings, f"fake_script_{letter.lower()}") == REPO_ROOT / f"scripts/{letter}.json"
    )
    monkeypatch.setenv(model, "muse-spark-open")
    monkeypatch.setenv(script, str(tmp_path / "absolute.json"))
    settings = Settings.load(env)
    assert getattr(settings, f"agent_model_{letter.lower()}") == "muse-spark-open"
    assert getattr(settings, f"fake_script_{letter.lower()}") == tmp_path / "absolute.json"


@pytest.mark.parametrize("maximum", [1, 2, 4, 6])
def test_runtime_and_setup_use_the_same_mixed_defaults(tmp_path: Path, maximum: int) -> None:
    settings = live_settings(
        tmp_path,
        max_agents=maximum,
        agent_model="muse-spark",
        agent_model_b="muse-spark-open",
        agent_model_c="fake-b",
        fake_script_c=FAKE_SCRIPT_B,
        agent_model_e="fake",
        fake_script_e=FAKE_SCRIPT,
    )
    expected = dict(
        list(
            {
                "A": MUSE_SPARK,
                "B": MUSE_SPARK_OPEN,
                "C": FAKE_MODEL_B,
                "D": MUSE_SPARK,
                "E": FAKE_MODEL,
                "F": MUSE_SPARK,
            }.items()
        )[:maximum]
    )
    defaults = resolve_slots(settings)
    live = LiveRun(settings)
    assert list(defaults) == list(expected)
    assert offered_profiles(settings) == expected
    assert live._slot_profiles == expected  # pyright: ignore[reportPrivateUsage]
    assert {slot: d.profile for slot, d in defaults.items()} == expected


def test_resolver_omits_only_unscripted_fake_slots_without_io(tmp_path: Path) -> None:
    # These scripts deliberately do not exist: pure resolution never reads them.
    settings = Settings(
        max_agents=6,
        agent_model="fake",
        fake_script=tmp_path / "a.json",
        fake_script_c=tmp_path / "c.json",
        agent_model_d="muse-spark-open",
        agent_model_e="fake-b",
        fake_script_f=tmp_path / "f.json",
    )
    assert list(resolve_slots(settings)) == ["A", "C", "D", "F"]
    assert offered_profiles(settings) == {
        "A": FAKE_MODEL,
        "C": FAKE_MODEL,
        "D": MUSE_SPARK_OPEN,
        "F": FAKE_MODEL,
    }
    with pytest.raises(ValueError, match="OVERSIGHT_FAKE_SCRIPT"):
        LiveRun(replace(settings, fake_script=None))


@pytest.mark.parametrize("slot", ["C", "D", "E", "F"])
def test_unknown_slot_model_names_its_variable(tmp_path: Path, slot: Slot) -> None:
    settings = live_settings(tmp_path, max_agents=6, **{f"agent_model_{slot.lower()}": "bad"})
    for resolve in (resolve_slots, offered_profiles, LiveRun):
        with pytest.raises(ValueError, match=f"OVERSIGHT_AGENT_MODEL_{slot} must be one of"):
            resolve(settings)
    # Overrides outside the configured capacity cannot affect admission or setup.
    assert list(resolve_slots(replace(settings, max_agents=1))) == ["A"]
