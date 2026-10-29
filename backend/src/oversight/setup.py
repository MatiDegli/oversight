"""Shared configuration readiness for agent starts, health and the demo preflight."""

import argparse
import math
from collections.abc import Mapping
from pathlib import Path

from oversight.settings import REPO_ROOT, Settings
from oversight.slots import resolve_profile as resolve_profile
from oversight.slots import resolve_slots
from oversight_contracts.commands import MissingSetting, SettingName
from oversight_contracts.common import Slot
from oversight_contracts.events import ModelProfile

PURPOSES: dict[SettingName, str] = {
    "META_API_KEY": "authenticate Meta agents and reviewers",
    "BASETEN_API_KEY": "authenticate Baseten agents and reviewers",
    "OVERSIGHT_BUDGET_USD": "set the Meta spending limit in USD",
    "MUSE_SPARK_INPUT_USD_PER_M": "price Meta input tokens per million in USD",
    "MUSE_SPARK_OUTPUT_USD_PER_M": "price Meta output tokens per million in USD",
}


def missing_settings(
    settings: Settings, profiles: Mapping[Slot, ModelProfile]
) -> list[MissingSetting]:
    """Group unmet requirements by name, in stable order, with only the slots they block.

    Keys are opaque: only empty or whitespace-containing keys can be rejected locally. This
    checks no credential against a provider. Parsed amounts retain only their problem kind.
    When cross-review is on, reviewer requirements apply to every supplied author slot.
    """
    reviewer = (
        resolve_profile(settings.review_model) if settings.cross_review and profiles else None
    )
    values: dict[SettingName, str | float | None] = {
        "META_API_KEY": settings.meta_api_key,
        "BASETEN_API_KEY": settings.baseten_api_key,
        "OVERSIGHT_BUDGET_USD": settings.budget_usd,
        "MUSE_SPARK_INPUT_USD_PER_M": settings.input_usd_per_m,
        "MUSE_SPARK_OUTPUT_USD_PER_M": settings.output_usd_per_m,
    }
    result: list[MissingSetting] = []
    for name, value in values.items():
        slots: list[Slot] = [
            slot
            for slot, profile in sorted(profiles.items())
            if any(
                (p.provider == "meta" and name != "BASETEN_API_KEY")
                or (p.provider == "baseten" and name == "BASETEN_API_KEY")
                for p in (profile, reviewer)
                if p is not None
            )
        ]
        if not slots:
            continue
        if value is None or value == "":
            problem = settings.setting_problems.get(name, "missing")
        elif isinstance(value, str):
            if not any(char.isspace() for char in value):
                continue
            problem = "invalid"
        elif math.isfinite(value) and value > 0:
            continue
        else:
            problem = "invalid"
        result.append(MissingSetting(name=name, problem=problem, slots=slots))
    return result


def offered_profiles(settings: Settings) -> dict[Slot, ModelProfile]:
    """Runtime defaults from the shared resolver, without script loading or runtime creation."""
    return {slot: default.profile for slot, default in resolve_slots(settings).items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", required=True, help="check demo readiness")
    parser.add_argument("--env-file", type=Path, default=REPO_ROOT / ".env")
    args = parser.parse_args()
    try:
        settings = Settings.load(args.env_file)
        missing = missing_settings(settings, offered_profiles(settings))
    except ValueError:
        print("Cannot check setup: check the model and server settings in .env.")
        return 1
    if not missing:
        print("Setup is ready.")
        return 0
    print("Setup is incomplete. Set these entries in .env:")
    for entry in missing:
        print(f"  {entry.name} ({entry.problem}): {PURPOSES[entry.name]}.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
