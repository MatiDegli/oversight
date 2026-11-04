"""Pure slot defaults shared by runtime admission and setup checks; no script I/O."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from oversight.agents import FAKE_MODEL, FAKE_MODEL_B, MUSE_SPARK, MUSE_SPARK_OPEN
from oversight.limits import MAX_EVENT_LINE_BYTES
from oversight_contracts.common import Slot
from oversight_contracts.events import ModelProfile

if TYPE_CHECKING:
    from oversight.settings import Settings

PROFILES = {p.id: p for p in (MUSE_SPARK, MUSE_SPARK_OPEN, FAKE_MODEL, FAKE_MODEL_B)}


CAPACITY_SIZE_ERROR = (
    f"OVERSIGHT_MAX_AGENTS exceeds the {MAX_EVENT_LINE_BYTES}-byte run.started event limit; "
    "the run could not be recorded or replayed"
)
# Even an empty profile id and a one-letter seat need these bytes, before envelope/comma overhead.
_MIN_SLOT_BYTES = len(b'{"slot":"A","default_model":""}')


def slot_name(index: int) -> Slot:
    """Name a zero-based seat by spreadsheet position: A..Z, AA, AB, ..."""
    if type(index) is not int or index < 0:
        raise ValueError("seat index must be a nonnegative integer")
    name = ""
    while index >= 0:
        index, digit = divmod(index, 26)
        name = chr(ord("A") + digit) + name
        index -= 1
    return name


def slot_position(slot: object) -> int:
    """Validate a seat and return its zero-based position for ordering and capacity checks."""
    if not isinstance(slot, str) or not slot or any(not "A" <= c <= "Z" for c in slot):
        raise ValueError("seat must contain only ASCII uppercase letters")
    position = 0
    for char in slot:
        position = position * 26 + ord(char) - ord("A") + 1
    return position - 1


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
    """Offer the first N seats, skipping fake slots other than A without their own script.

    A is always offered; runtime script loading still rejects a fake A without its script.
    Paths are only carried here: setup checks never load scripts or create a runtime.
    """
    # Compare before generating names, dictionaries or SlotInfo objects, even for huge integers.
    if settings.max_agents > MAX_EVENT_LINE_BYTES // _MIN_SLOT_BYTES:
        raise ValueError(CAPACITY_SIZE_ERROR)
    offered: dict[Slot, SlotDefault] = {}
    for index in range(settings.max_agents):
        slot = slot_name(index)
        override = settings.agent_models.get(slot)
        script = settings.fake_scripts.get(slot)
        if slot == "A":
            override, script = settings.agent_model, settings.fake_script
        else:
            # Compatibility for original direct constructor keywords, without limiting seat names.
            override = override or getattr(settings, f"agent_model_{slot.lower()}", None)
            script = script or getattr(settings, f"fake_script_{slot.lower()}", None)
        name = override or settings.agent_model
        if name not in PROFILES:
            variable = "OVERSIGHT_AGENT_MODEL" + (f"_{slot}" if slot != "A" else "")
            raise ValueError(f"{variable} must be one of {', '.join(PROFILES)}, not {name!r}")
        profile = PROFILES[name]
        if slot != "A" and profile.provider == "fake" and script is None:
            continue
        offered[slot] = SlotDefault(profile, script)
    return offered
