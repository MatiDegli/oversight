"""Readiness is shared, slot-specific and contains no configuration values."""

from dataclasses import replace
from pathlib import Path
from typing import Literal, get_args

import pytest

from oversight.agents import FAKE_MODEL, MUSE_SPARK, MUSE_SPARK_OPEN
from oversight.settings import Settings
from oversight.setup import missing_settings, offered_profiles
from oversight_contracts.commands import MissingSetting, SettingName
from oversight_contracts.common import Slot
from oversight_contracts.events import ModelProfile

AMOUNTS = {
    "OVERSIGHT_BUDGET_USD": "budget_usd",
    "MUSE_SPARK_INPUT_USD_PER_M": "input_usd_per_m",
    "MUSE_SPARK_OUTPUT_USD_PER_M": "output_usd_per_m",
}
KEYS = {"META_API_KEY": "meta_api_key", "BASETEN_API_KEY": "baseten_api_key"}
NAMES = tuple(get_args(SettingName.__value__))


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (*NAMES, "OVERSIGHT_AGENT_MODEL", "OVERSIGHT_AGENT_MODEL_B", "OVERSIGHT_PORT"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize(
    ("profile", "names"),
    [
        (MUSE_SPARK, ["META_API_KEY", *AMOUNTS]),
        (MUSE_SPARK_OPEN, ["BASETEN_API_KEY"]),
        (FAKE_MODEL, []),
    ],
)
def test_each_profile_only_requires_its_own_settings(
    profile: ModelProfile, names: list[str]
) -> None:
    found = missing_settings(Settings(), {"B": profile})
    assert [entry.model_dump() for entry in found] == [
        {"name": name, "problem": "missing", "slots": ["B"]} for name in names
    ]


def test_slots_are_grouped_deterministically_and_unoffered_slots_are_absent() -> None:
    settings = Settings()
    found = missing_settings(settings, {"B": MUSE_SPARK, "A": MUSE_SPARK})
    assert all(entry.slots == ["A", "B"] for entry in found)
    mixed = missing_settings(settings, {"B": MUSE_SPARK_OPEN, "A": MUSE_SPARK})
    assert [entry.slots for entry in mixed] == [["A"], ["B"], ["A"], ["A"], ["A"]]
    assert missing_settings(settings, {}) == []
    assert missing_settings(settings, {"A": FAKE_MODEL, "B": FAKE_MODEL}) == []


@pytest.mark.parametrize(
    "slots", [{"A": MUSE_SPARK}, {"B": MUSE_SPARK}, {"B": MUSE_SPARK, "A": MUSE_SPARK}]
)
@pytest.mark.parametrize("key,problem", [(None, "missing"), ("bad key", "invalid")])
def test_cross_review_readiness_includes_default_reviewer_credential(
    slots: dict[Slot, ModelProfile], key: str | None, problem: Literal["missing", "invalid"]
) -> None:
    settings = Settings(
        cross_review=True,
        meta_api_key="test-meta",
        baseten_api_key=key,
        budget_usd=1,
        input_usd_per_m=1,
        output_usd_per_m=1,
    )
    assert missing_settings(settings, slots) == [
        MissingSetting(name="BASETEN_API_KEY", problem=problem, slots=sorted(slots))
    ]
    assert missing_settings(replace(settings, cross_review=False), slots) == []
    assert missing_settings(settings, {}) == []


def test_disabled_readiness_does_not_resolve_reviewer() -> None:
    settings = Settings(review_model="unknown")
    assert missing_settings(settings, {"A": FAKE_MODEL}) == []
    with pytest.raises(ValueError, match="unknown model profile"):
        missing_settings(replace(settings, cross_review=True), {"A": FAKE_MODEL})


def test_readiness_uses_selected_reviewer_provider_and_deduplicates_slots() -> None:
    settings = Settings(cross_review=True, review_model="fake-b")
    assert missing_settings(settings, {"A": FAKE_MODEL}) == []
    settings = replace(settings, review_model="muse-spark")
    found = missing_settings(settings, {"B": MUSE_SPARK_OPEN, "A": MUSE_SPARK})
    assert [entry.name for entry in found] == ["META_API_KEY", "BASETEN_API_KEY", *AMOUNTS]
    assert [entry.slots for entry in found] == [["A", "B"], ["B"], *[["A", "B"]] * 3]


@pytest.mark.usefixtures("clean_env")
@pytest.mark.parametrize("name", list(AMOUNTS))
@pytest.mark.parametrize("value", [None, "", "garbage", "0", "-1", "nan", "inf", "-inf", " "])
def test_amount_missing_and_invalid_survive_settings_parsing(
    tmp_path: Path, name: SettingName, value: str | None
) -> None:
    env_file = tmp_path / ".env"
    values = {"META_API_KEY": "test-key", **dict.fromkeys(AMOUNTS, "123.45")}
    if value is None:
        del values[name]
    else:
        values[name] = value
    env_file.write_text("\n".join(f"{key}='{raw}'" for key, raw in values.items()))
    settings = Settings.load(env_file)
    assert getattr(settings, AMOUNTS[name]) is None
    assert missing_settings(settings, {"A": MUSE_SPARK}) == [
        MissingSetting(
            name=name, problem="missing" if value in (None, "") else "invalid", slots=["A"]
        )
    ]


@pytest.mark.parametrize("name", list(AMOUNTS))
@pytest.mark.parametrize("value", [0.0, -1.0, float("inf"), float("nan")])
def test_invalid_direct_amounts(name: SettingName, value: float) -> None:
    settings = Settings(
        meta_api_key="test-key", budget_usd=1, input_usd_per_m=1, output_usd_per_m=1
    )
    settings = replace(settings, **{AMOUNTS[name]: value})
    assert missing_settings(settings, {"A": MUSE_SPARK}) == [
        MissingSetting(name=name, problem="invalid", slots=["A"])
    ]


@pytest.mark.parametrize("name", list(KEYS))
@pytest.mark.parametrize("value", [None, "", " ", "has space", "has\nnewline"])
def test_each_key_is_missing_or_locally_invalid(name: SettingName, value: str | None) -> None:
    settings = Settings(
        meta_api_key="test-meta",
        baseten_api_key="test-baseten",
        budget_usd=1,
        input_usd_per_m=1,
        output_usd_per_m=1,
    )
    settings = replace(settings, **{KEYS[name]: value})
    assert missing_settings(settings, {"A": MUSE_SPARK, "B": MUSE_SPARK_OPEN}) == [
        MissingSetting(
            name=name,
            problem="missing" if not value else "invalid",
            slots=["A"] if name == "META_API_KEY" else ["B"],
        )
    ]


@pytest.mark.usefixtures("clean_env")
def test_configured_profiles_and_environment_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "META_API_KEY=test-meta\nBASETEN_API_KEY=test-baseten\n"
        + "\n".join(f"{name}=bad" for name in AMOUNTS)
    )
    for name in AMOUNTS:
        monkeypatch.setenv(name, "12.34")
    settings = Settings.load(env_file)
    assert settings.setting_problems == {}
    assert missing_settings(settings, {"A": MUSE_SPARK, "B": MUSE_SPARK_OPEN}) == []
    monkeypatch.setenv("OVERSIGHT_BUDGET_USD", "")
    assert missing_settings(Settings.load(env_file), {"A": MUSE_SPARK}) == [
        MissingSetting(name="OVERSIGHT_BUDGET_USD", problem="missing", slots=["A"])
    ]


def test_offered_profiles_match_live_defaults_without_starting_a_runtime() -> None:
    assert offered_profiles(Settings()) == {slot: MUSE_SPARK for slot in ("A", "B", "C", "D")}
    assert offered_profiles(Settings(max_agents=2, agent_model_b="muse-spark-open")) == {
        "A": MUSE_SPARK,
        "B": MUSE_SPARK_OPEN,
    }
    fake = Settings(agent_model="fake")
    assert offered_profiles(fake) == {"A": FAKE_MODEL}
    assert offered_profiles(replace(fake, fake_script_b=Path("b.json"))) == {
        "A": FAKE_MODEL,
        "B": FAKE_MODEL,
    }
    assert offered_profiles(replace(fake, agent_model_b="muse-spark-open")) == {
        "A": FAKE_MODEL,
        "B": MUSE_SPARK_OPEN,
    }
    with pytest.raises(ValueError, match="OVERSIGHT_AGENT_MODEL must be one of"):
        offered_profiles(Settings(agent_model="unknown"))
