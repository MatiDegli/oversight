"""Pure slot defaults shared by runtime admission and setup checks; no script I/O."""

from dataclasses import dataclass
from pathlib import Path

from oversight.agents import FAKE_MODEL, FAKE_MODEL_B, MUSE_SPARK, MUSE_SPARK_OPEN
from oversight.settings import Settings
from oversight_contracts.common import Slot
from oversight_contracts.events import ModelProfile

PROFILES = {p.id: p for p in (MUSE_SPARK, MUSE_SPARK_OPEN, FAKE_MODEL, FAKE_MODEL_B)}


@dataclass(frozen=True)
class SlotDefault:
    profile: ModelProfile
    script: Path | None


def resolve_profile(name: str) -> ModelProfile:
    """Resolve author and reviewer names from the same provider profiles, without I/O."""
    if name not in PROFILES:
        raise ValueError("unknown model profile")
    return PROFILES[name]


def resolve_slots(settings: Settings) -> dict[Slot, SlotDefault]:
    """Offer the first N letters, skipping fake slots other than A without their own script.

    A is always offered; runtime script loading still rejects a fake A without its script.
    Paths are only carried here: setup checks never load scripts or create a runtime.
    """
    configured: tuple[tuple[Slot, str | None, Path | None], ...] = (
        ("A", settings.agent_model, settings.fake_script),
        ("B", settings.agent_model_b, settings.fake_script_b),
        ("C", settings.agent_model_c, settings.fake_script_c),
        ("D", settings.agent_model_d, settings.fake_script_d),
        ("E", settings.agent_model_e, settings.fake_script_e),
        ("F", settings.agent_model_f, settings.fake_script_f),
    )
    offered: dict[Slot, SlotDefault] = {}
    for slot, override, script in configured[: settings.max_agents]:
        name = override or settings.agent_model
        if name not in PROFILES:
            variable = "OVERSIGHT_AGENT_MODEL" + (f"_{slot}" if slot != "A" else "")
            raise ValueError(f"{variable} must be one of {', '.join(PROFILES)}, not {name!r}")
        profile = PROFILES[name]
        if slot != "A" and profile.provider == "fake" and script is None:
            continue
        offered[slot] = SlotDefault(profile, script)
    return offered
